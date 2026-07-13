"""Live-API integration test for shared/job_sheet.py (Op Stds §30).

Default `pytest -q` SKIPS this file (per pyproject.toml addopts:
-m 'not integration'). Run with `pytest -m integration`. Requires
ITS_SMARTSHEET_TOKEN in macOS Keychain.

Parent-folder choice:
    The synthetic "Jobs" parent is FOLDER_SYSTEM_CONFIG — matches the parent
    already used by tests/test_week_folder_integration.py and
    tests/test_smartsheet_client_integration.py; avoids minting a new
    sandbox-only constant in shared.sheet_ids. The synthetic job-folder name
    carries the `_int_` prefix so a residual artifact from a failed run is
    visually distinct from anything real.

Template choice:
    The per-job sheet clones SHEET_SUBCONTRACT_LOG structure-only
    (`include=[]`) — a READ of the live flat Log's schema, never a write to
    it; exactly what the production call sites do.

Readiness-probe exercise:
    The create → IMMEDIATE append sequence below is the live re-run of the
    2026-07-13 live-smoke finding (add_rows 404/errorCode-1006 a few seconds
    after create_sheet_in_folder_from_template). `ensure_job_sheet`'s create
    path now absorbs that window via `_wait_until_readable` before returning,
    so the append is expected to succeed first try — this test failing on a
    1006 add_rows 404 means the probe regressed.

Cleanup:
    `finally` deletes the sheet first, then the folder (folder-delete
    cascades, but the explicit sheet-delete keeps the resource trail tight
    if the folder-delete fails).
"""
from __future__ import annotations

import pytest
import requests  # type: ignore[import-untyped]

from shared import keychain, sheet_ids
from shared.job_sheet import ensure_job_sheet
from subcontracts import subcontract_log

pytestmark = pytest.mark.integration

SANDBOX_JOB_FOLDER = "_int_job_sheet_sandbox"
SANDBOX_SHEET_NAME = "_int_Subcontracts"
SANDBOX_SC_NUMBER = "1970.001.1.0.0"  # collision-free against any real allocation


@pytest.fixture(scope="module")
def _token_available() -> str:
    try:
        token = keychain.get_secret("ITS_SMARTSHEET_TOKEN")
    except Exception as e:
        pytest.skip(f"ITS_SMARTSHEET_TOKEN unavailable: {e!r}")
    if not token:
        pytest.skip("ITS_SMARTSHEET_TOKEN returned empty")
    return token


def _delete_sheet_rest(sheet_id: int, token: str) -> None:
    requests.delete(
        f"https://api.smartsheet.com/2.0/sheets/{sheet_id}",
        headers={"Authorization": f"Bearer {token}"},
    )


def _delete_folder_rest(parent_folder_id: int, name: str, token: str) -> None:
    """Delete the sandbox job folder by re-finding it under the parent (the
    helper returns only the sheet id, so the folder id is re-resolved)."""
    from shared import smartsheet_client

    folder_id = smartsheet_client.find_folder_by_name_in_folder(parent_folder_id, name)
    if folder_id is not None:
        requests.delete(
            f"https://api.smartsheet.com/2.0/folders/{folder_id}",
            headers={"Authorization": f"Bearer {token}"},
        )


def test_ensure_job_sheet_round_trip(_token_available):
    """Create → idempotent re-find → immediate append (probe exercise) → cleanup."""
    sid = ensure_job_sheet(
        sheet_ids.FOLDER_SYSTEM_CONFIG,
        sheet_ids.SHEET_SUBCONTRACT_LOG,
        SANDBOX_JOB_FOLDER,
        SANDBOX_SHEET_NAME,
        workspace_id=sheet_ids.WORKSPACE_SYSTEM,
        workstream="global",
    )
    try:
        assert sid > 0

        # Second call must be idempotent — same id, no new state created.
        sid2 = ensure_job_sheet(
            sheet_ids.FOLDER_SYSTEM_CONFIG,
            sheet_ids.SHEET_SUBCONTRACT_LOG,
            SANDBOX_JOB_FOLDER,
            SANDBOX_SHEET_NAME,
            workspace_id=sheet_ids.WORKSPACE_SYSTEM,
            workstream="global",
        )
        assert sid2 == sid

        # IMMEDIATE append to the just-created sheet — the live readiness-probe
        # exercise (this is the exact sequence that 404'd pre-probe).
        row_id = subcontract_log.append_filed_row(
            sc_number=SANDBOX_SC_NUMBER,
            job_project="_int_ job-sheet round trip",
            job_id="JOB-_INT_",
            subcontractor_name="_int_ Sandbox Sub",
            sub_key="SUB-_INT_",
            total_cents=1,
            pdf_link="https://example.invalid/int",
            supersedes_display="",
            terms_profile="standard_subcontract",
            created_by="_int_test",
            created_at_iso="1970-01-05",
            notes=subcontract_log.notes_for_filed_row(0, extra="_int_ probe row"),
            sheet_id=sid,
        )
        assert row_id > 0

        # The target-sheet idempotency guard finds the probe row IN the per-job
        # sheet (and only there — the flat Log is untouched by this test).
        found = subcontract_log.find_row_by_sc_number(SANDBOX_SC_NUMBER, sheet_id=sid)
        assert found is not None
        assert str(found[subcontract_log.COL_SC_NUMBER]) == SANDBOX_SC_NUMBER
    finally:
        _delete_sheet_rest(sid, _token_available)
        _delete_folder_rest(
            sheet_ids.FOLDER_SYSTEM_CONFIG, SANDBOX_JOB_FOLDER, _token_available
        )
