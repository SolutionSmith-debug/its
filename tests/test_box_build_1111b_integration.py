"""Live-API integration test for box_build_1111b_blueprint.

Per Op Stds v11 §30 (SDK-vs-Live Integration Test Discipline) — exercises
the rename traversal logic against a real Box API on a **disposable
fixture tree**, NOT against the real 1111A. This keeps the live-API
verification ON without putting the real blueprint or any project clone
at risk.

Default `pytest -q` SKIPS this file (per pyproject.toml addopts:
-m 'not integration'). Run with `pytest -m integration`. Requires Box
OAuth credentials in macOS Keychain.

Test fixture:
  - Creates a disposable parent folder under ITS DATA named
    `_int_box_build_1111b_<utc>`.
  - Inside that parent, manually creates a mini fixture tree mirroring
    a small slice of 1111A's structure (~5 folders with letter-prefix
    naming).
  - Invokes `apply_renames` against the fixture parent with a
    test-scoped rename map.
  - Verifies the renames landed correctly.
  - Cleans up the disposable parent (which cascades to all children)
    in finally.

Note: We do NOT exercise the full clone-of-1111A path in integration
since that would (a) take 10+ minutes for the deep-copy and (b) cost
real Box API time. The clone primitive is already integration-tested
by `box_clone_1111a_to_projects` (PR #56). This test exercises the
rename traversal — the new logic in this PR.
"""
from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

# sys.path-driven import (matches tests/test_box_build_1111b.py) — avoids
# the mypy "Source file found twice" error when the same file is
# reachable via both bare and dotted module names.
_MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "scripts" / "migrations"
if str(_MIGRATIONS_DIR) not in sys.path:
    sys.path.insert(0, str(_MIGRATIONS_DIR))

from box_build_1111b_blueprint import (  # noqa: E402 — sys.path-driven import
    PARENT_FOLDER_ID,
    apply_renames,
)

from shared import box_client, keychain  # noqa: E402

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def _box_credentials() -> None:
    try:
        keychain.get_secret("ITS_BOX_REFRESH_TOKEN")
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"Box credentials unavailable: {e!r}")


def _create_folder(client: Any, parent_id: str, name: str) -> str:
    """Create a folder under `parent_id` and return its ID."""
    new = client.folder(parent_id).create_subfolder(name)
    return str(new.id)


def _find_child_id(client: Any, parent_id: str, name: str) -> str | None:
    items = client.folder(parent_id).get_items(limit=100, fields=["id", "name", "type"])
    for item in items:
        if item.type == "folder" and item.name == name:
            return str(item.id)
    return None


def _delete_folder_recursive(client: Any, folder_id: str) -> None:
    """Best-effort recursive delete."""
    try:
        client.folder(folder_id).delete(recursive=True)
    except Exception:  # noqa: BLE001 — cleanup best-effort
        pass


def test_apply_renames_against_disposable_fixture(
    _box_credentials: None,
) -> None:
    """Build a tiny fixture tree, run apply_renames with a tailored map, verify."""
    client = box_client.get_client()

    fixture_name = f"_int_box_build_1111b_{datetime.now(UTC).strftime('%Y%m%dT%H%M%S')}"
    fixture_parent_id: str | None = None

    try:
        # 1. Create disposable fixture parent + mini tree.
        fixture_parent_id = _create_folder(client, PARENT_FOLDER_ID, fixture_name)
        # Build: <fixture>/(Project # & Name) Field/A. Onsite Reporting & Tracking
        field_id = _create_folder(client, fixture_parent_id, "(Project # & Name) Field")
        onsite_id = _create_folder(client, field_id, "A. Onsite Reporting & Tracking")
        _create_folder(client, onsite_id, "A. Safety Plan & Reports")

        # 2. Patch RENAME_MAP for this test scope. Use a tiny custom map that
        #    targets the fixture's specific paths.
        test_map = {
            ("(Project # & Name) Field", "A. Onsite Reporting & Tracking"):
                "01. Onsite Reporting & Tracking",
            ("(Project # & Name) Field/01. Onsite Reporting & Tracking", "A. Safety Plan & Reports"):
                "01. Safety Plan & Reports",
        }

        # Run apply_renames with a monkey-patched RENAME_MAP via the module.
        import box_build_1111b_blueprint as build_mod

        original_map = build_mod.RENAME_MAP
        build_mod.RENAME_MAP = test_map
        try:
            counters = apply_renames(client, fixture_parent_id)
        finally:
            build_mod.RENAME_MAP = original_map

        # 3. Verify the renames landed.
        assert counters["renamed"] == 2, (
            f"Expected 2 renames, got: {counters}"
        )
        # Top-level: 01. Onsite Reporting & Tracking
        renamed_onsite = _find_child_id(client, field_id, "01. Onsite Reporting & Tracking")
        assert renamed_onsite is not None, "Top-level rename did not land"
        # Nested: 01. Safety Plan & Reports under the renamed parent
        renamed_safety = _find_child_id(client, renamed_onsite, "01. Safety Plan & Reports")
        assert renamed_safety is not None, "Nested rename did not land"

        # 4. Re-run apply_renames — must be idempotent (already-renamed branch).
        build_mod.RENAME_MAP = test_map
        try:
            counters_second = apply_renames(client, fixture_parent_id)
        finally:
            build_mod.RENAME_MAP = original_map

        assert counters_second["already_renamed"] == 2, (
            f"Second run should report 2 already_renamed, got: {counters_second}"
        )
        assert counters_second["renamed"] == 0
    finally:
        # 5. Cleanup.
        if fixture_parent_id is not None:
            _delete_folder_recursive(client, fixture_parent_id)
