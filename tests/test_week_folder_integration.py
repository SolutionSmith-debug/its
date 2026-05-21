"""Live-API integration test for safety_reports/week_folder.py.

Default `pytest -q` SKIPS this file (per pyproject.toml addopts:
-m 'not integration'). Run with `pytest -m integration`. Requires
ITS_SMARTSHEET_TOKEN in macOS Keychain.

Parent-folder choice (Phase 4 open clarification):
    The synthetic 'Field Reports folder' is FOLDER_SYSTEM_CONFIG.
    Matches the parent already used by tests/test_smartsheet_client_integration.py;
    avoids minting a new sandbox-only constant in shared.sheet_ids.
    The synthetic project name has the `_int_` prefix to keep the
    sandbox entry visually distinct from real ones in
    FIELD_REPORTS_FOLDER_BY_PROJECT.

Week-start choice:
    The test pins `week_start=date(1970, 1, 5)` (a Monday) so the
    folder name `Week of 1970-01-05` is collision-free against any
    real week-folder under any project, including residual artifacts
    from a previous failed run.

Cleanup:
    `finally` block deletes the two sheets first, then the folder.
    Smartsheet's folder-delete cascades to child folders/sheets, but
    explicit sheet-delete keeps the resource trail tight if the
    folder-delete fails (network blip, permission issue, etc.).
"""
from __future__ import annotations

from datetime import date

import pytest
import requests  # type: ignore[import-untyped]

from safety_reports.week_folder import ensure_current_week_folder
from shared import keychain, sheet_ids

pytestmark = pytest.mark.integration

SANDBOX_PROJECT = "_int_week_folder_sandbox"
SANDBOX_WEEK_START = date(1970, 1, 5)  # arbitrary Monday far from any real week.


@pytest.fixture(scope="module")
def _token_available() -> str:
    try:
        token = keychain.get_secret("ITS_SMARTSHEET_TOKEN")
    except Exception as e:
        pytest.skip(f"ITS_SMARTSHEET_TOKEN unavailable: {e!r}")
    if not token:
        pytest.skip("ITS_SMARTSHEET_TOKEN returned empty")
    return token


@pytest.fixture
def sandbox_project_map():
    """Inject a sandbox project entry into FIELD_REPORTS_FOLDER_BY_PROJECT.

    Restored in teardown so other tests see the canonical map.
    """
    original = dict(sheet_ids.FIELD_REPORTS_FOLDER_BY_PROJECT)
    sheet_ids.FIELD_REPORTS_FOLDER_BY_PROJECT[SANDBOX_PROJECT] = (
        sheet_ids.FOLDER_SYSTEM_CONFIG
    )
    try:
        yield
    finally:
        sheet_ids.FIELD_REPORTS_FOLDER_BY_PROJECT.clear()
        sheet_ids.FIELD_REPORTS_FOLDER_BY_PROJECT.update(original)


def _delete_sheet_rest(sheet_id: int, token: str) -> None:
    requests.delete(
        f"https://api.smartsheet.com/2.0/sheets/{sheet_id}",
        headers={"Authorization": f"Bearer {token}"},
    )


def _delete_folder_rest(folder_id: int, token: str) -> None:
    requests.delete(
        f"https://api.smartsheet.com/2.0/folders/{folder_id}",
        headers={"Authorization": f"Bearer {token}"},
    )


def test_ensure_current_week_folder_round_trip(_token_available, sandbox_project_map):
    """Create → idempotent re-find → cleanup."""
    scaffold = ensure_current_week_folder(SANDBOX_PROJECT, week_start=SANDBOX_WEEK_START)
    try:
        # First call created everything; IDs must be plausible Smartsheet IDs.
        assert scaffold.folder_id > 0
        assert scaffold.daily_reports_sheet_id > 0
        assert scaffold.weekly_rollup_sheet_id > 0

        # Second call must be idempotent — same IDs, no new state created.
        scaffold2 = ensure_current_week_folder(
            SANDBOX_PROJECT, week_start=SANDBOX_WEEK_START
        )
        assert scaffold2 == scaffold
    finally:
        _delete_sheet_rest(scaffold.daily_reports_sheet_id, _token_available)
        _delete_sheet_rest(scaffold.weekly_rollup_sheet_id, _token_available)
        _delete_folder_rest(scaffold.folder_id, _token_available)
