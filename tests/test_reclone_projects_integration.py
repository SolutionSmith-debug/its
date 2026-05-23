"""Live-API integration test for reclone_projects_from_1111b.

Per Op Stds v11 §30 (SDK-vs-Live discipline) — exercises the clone +
verify cycle against a real Box API on a **disposable parent**, NOT
against the real ITS DATA root. The disposable parent is created
under ITS DATA itself but with a date-stamped name so it can't
collide with real production folders.

Default `pytest -q` SKIPS this file (`-m integration` runs it).
Requires Box OAuth credentials in macOS Keychain.

What this test does NOT exercise:
  - The full 6-project sequential cutover (would take ~60 min of
    Box API time + cost real deep-copy resources).
  - The legacy-archive flow (covered by unit tests + the actual
    live cutover that happens during PR execution).

What it DOES exercise:
  - `copy_with_lock_retry` against the live 1111B source.
  - `wait_for_deep_copy_complete` against a real async deep-copy.
  - Compliance verification (descendant count + RENAME_MAP target
    presence) against a freshly-cloned 1111B.

The disposable parent is deleted recursively in `finally`.
"""
from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

_MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "scripts" / "migrations"
if str(_MIGRATIONS_DIR) not in sys.path:
    sys.path.insert(0, str(_MIGRATIONS_DIR))

import reclone_projects_from_1111b as reclone_mod  # noqa: E402
from reclone_projects_from_1111b import (  # noqa: E402
    EXPECTED_DESCENDANT_COUNT,
    PARENT_FOLDER_ID,
    SOURCE_1111B_ID,
    copy_with_lock_retry,
    verify_clone,
    wait_for_deep_copy_complete,
)

from shared import box_client, keychain  # noqa: E402

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def _box_credentials() -> None:
    try:
        keychain.get_secret("ITS_BOX_REFRESH_TOKEN")
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"Box credentials unavailable: {e!r}")


def _delete_recursive(client: Any, folder_id: str) -> None:
    try:
        client.folder(folder_id).delete(recursive=True)
    except Exception:  # noqa: BLE001
        pass


def test_clone_1111b_to_disposable_parent_passes_compliance(
    _box_credentials: None,
) -> None:
    """Clone 1111B into a disposable parent + verify against blueprint + cleanup."""
    client = box_client.get_client()

    fixture_name = (
        f"_int_reclone_1111b_{datetime.now(UTC).strftime('%Y%m%dT%H%M%S')}"
    )
    disposable_parent_id: str | None = None
    cloned_id: str | None = None

    try:
        # 1. Create disposable parent under ITS DATA.
        disposable = client.folder(PARENT_FOLDER_ID).create_subfolder(fixture_name)
        disposable_parent_id = str(disposable.id)

        # 2. Clone 1111B into the disposable parent (under a non-canonical
        #    name so we don't pretend to be a project clone).
        cloned_id = copy_with_lock_retry(
            client,
            source_id=SOURCE_1111B_ID,
            parent_id=disposable_parent_id,
            name="1111B_test_clone",
        )

        # 3. Wait for deep-copy top-level to populate.
        completed, top_count = wait_for_deep_copy_complete(
            client, cloned_id, expected_count=14
        )
        assert completed, (
            f"Deep-copy top-level did not populate within budget; got {top_count}/14"
        )

        # 4. Wait for full descendant count (Box deep-copy continues async after top-level).
        import time

        deadline = time.time() + 1200  # 20 min budget
        while time.time() < deadline:
            descendants = reclone_mod._count_all_descendants(client, cloned_id)
            if descendants >= EXPECTED_DESCENDANT_COUNT:
                break
            time.sleep(15)

        # 5. Verify compliance.
        passed, report = verify_clone(client, cloned_id, "1111B_test_clone")
        assert passed, f"Compliance check failed:\n{report}"
    finally:
        # 6. Cleanup — delete the disposable parent (cascades to children).
        if disposable_parent_id is not None:
            _delete_recursive(client, disposable_parent_id)
