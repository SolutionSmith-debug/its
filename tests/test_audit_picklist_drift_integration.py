"""Live-API §30 integration test for scripts/audit_picklist_drift.py --apply.

Per Op Stds v16 §30 (SDK-vs-Live discipline): the Phase 3b `--apply` reconcile
issues live picklist writes via `ensure_picklist_options`, so it needs at least
one live round-trip that a SimpleNamespace mock can't give — specifically that
the additive option-add lands on the live column, that DRY-RUN writes nothing,
that re-running is a no-op, and that a registered-but-absent column is skipped
(not crashed).

`scripts/` is not a package; we use the same sys.path-insert pattern as
tests/test_audit_picklist_drift.py / test_watchdog.py so `import
audit_picklist_drift` resolves to ONE module name (importing it as
`scripts.audit_picklist_drift` would make mypy see the file under two names).

The REGISTRY is monkeypatched to point at a throwaway sandbox sheet so the test
is hermetic w.r.t. the real ITS sheets and self-cleans the sheet in `finally`.

Skipped automatically when ITS_SMARTSHEET_TOKEN is unavailable.
Run with: pytest -m integration tests/test_audit_picklist_drift_integration.py
"""
from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from shared import keychain, picklist_validation, sheet_ids, smartsheet_client

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import audit_picklist_drift  # noqa: E402 — sys.path-driven import

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def _token_available() -> bool:
    try:
        token = keychain.get_secret("ITS_SMARTSHEET_TOKEN")
    except Exception as e:
        pytest.skip(f"ITS_SMARTSHEET_TOKEN unavailable: {e!r}")
    if not token:
        pytest.skip("ITS_SMARTSHEET_TOKEN returned empty")
    return True


@pytest.fixture(scope="module", autouse=True)
def _reset_smartsheet_client():
    """Force a fresh real-token client (see test_smartsheet_client_integration)."""
    smartsheet_client._client = None
    yield
    smartsheet_client._client = None


def _sandbox_name() -> str:
    return f"_int_apply_{datetime.now(UTC).strftime('%H%M%S_%f')}"


def test_apply_reconcile_additive_idempotent_live(_token_available, monkeypatch):
    """Live: preview writes nothing → commit adds only missing → re-run is no-op."""
    sheet_id = smartsheet_client.create_sheet_in_folder(
        sheet_ids.FOLDER_SYSTEM_CONFIG,
        _sandbox_name(),
        [
            {"title": "id_col", "type": "TEXT_NUMBER", "primary": True},
            {"title": "pl_col", "type": "PICKLIST", "options": ["seed_a", "seed_b"]},
        ],
    )
    try:
        # Registry asks for the 2 seeds + 2 new values on the sandbox column.
        monkeypatch.setattr(
            picklist_validation, "REGISTRY",
            {sheet_id: {"pl_col": frozenset({"seed_a", "seed_b", "new_x", "new_y"})}},
        )

        # DRY-RUN (commit=False): reports the 2 adds, writes nothing.
        changed, added, skipped = audit_picklist_drift.apply_reconcile(commit=False)
        assert (changed, added, skipped) == (1, 2, [])
        live = smartsheet_client.list_columns_with_options(sheet_id)
        pl = next(c for c in live if c["title"] == "pl_col")
        assert set(pl["options"]) == {"seed_a", "seed_b"}  # preview did not mutate

        # COMMIT: the two missing options land; seeds preserved (additive).
        changed, added, skipped = audit_picklist_drift.apply_reconcile(commit=True)
        assert (changed, added, skipped) == (1, 2, [])
        live = smartsheet_client.list_columns_with_options(sheet_id)
        pl = next(c for c in live if c["title"] == "pl_col")
        assert set(pl["options"]) == {"seed_a", "seed_b", "new_x", "new_y"}

        # IDEMPOTENT: re-running commits nothing.
        changed, added, skipped = audit_picklist_drift.apply_reconcile(commit=True)
        assert (changed, added, skipped) == (0, 0, [])
    finally:
        smartsheet_client.delete_sheet_settling(sheet_id)


def test_apply_reconcile_skips_absent_column_live(_token_available, monkeypatch):
    """Live: a registered-but-absent column is logged + skipped, never crashes."""
    sheet_id = smartsheet_client.create_sheet_in_folder(
        sheet_ids.FOLDER_SYSTEM_CONFIG,
        _sandbox_name(),
        [{"title": "id_col", "type": "TEXT_NUMBER", "primary": True}],
    )
    try:
        monkeypatch.setattr(
            picklist_validation, "REGISTRY",
            {sheet_id: {"NonexistentCol": frozenset({"a", "b"})}},
        )
        changed, added, skipped = audit_picklist_drift.apply_reconcile(commit=True)
        assert (changed, added) == (0, 0)
        assert len(skipped) == 1 and "NonexistentCol" in skipped[0]
    finally:
        smartsheet_client.delete_sheet_settling(sheet_id)
