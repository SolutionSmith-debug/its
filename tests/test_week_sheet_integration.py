"""Live-API integration test for safety_reports/week_sheet.py (Op Stds §30).

Default `pytest -q` SKIPS this file (pyproject addopts `-m 'not integration'`).
Run with `pytest -m integration`. Requires ITS_SMARTSHEET_TOKEN in Keychain.
NOT executed in CI.

Exercises the Smartsheet WRITE paths the portal flow now uses under the ITS — Safety
Portal workspace: create_folder_in_workspace (the per-job folder, AUTO-PROVISIONED),
create_sheet_in_folder (columns-via-API), add_rows (submission row), update_rows
(amend supersede). Doubles as the **service-account permission probe** — a 403 on
create_folder_in_workspace means the ITS token lacks Admin on WORKSPACE_SAFETY_PORTAL
(folder-create at the workspace surface needs Admin, not Editor). The work-date pins
a Saturday in 1970 so the sheet name is collision-free; the sandbox project name is
distinctive so its auto-created folder is unmistakable + cleaned up after.
"""
from __future__ import annotations

from datetime import date

import pytest
import requests  # type: ignore[import-untyped]

from safety_reports import week_sheet
from shared import keychain, sheet_ids, smartsheet_client

pytestmark = pytest.mark.integration

SANDBOX_PROJECT = "_int_week_sheet_sandbox"
SANDBOX_WORK_DATE = date(1970, 1, 7)  # a Wednesday → week opens Sat 1970-01-03


@pytest.fixture
def _token() -> str:
    try:
        token = keychain.get_secret("ITS_SMARTSHEET_TOKEN")
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"ITS_SMARTSHEET_TOKEN unavailable: {e!r}")
    if not token:
        pytest.skip("ITS_SMARTSHEET_TOKEN returned empty")
    return token


def _delete_sheet(sheet_id: int, token: str) -> None:
    requests.delete(
        f"https://api.smartsheet.com/2.0/sheets/{sheet_id}",
        headers={"Authorization": f"Bearer {token}"},
    )


def _delete_folder(folder_id: int, token: str) -> None:
    requests.delete(
        f"https://api.smartsheet.com/2.0/folders/{folder_id}",
        headers={"Authorization": f"Bearer {token}"},
    )


def test_week_sheet_round_trip(_token):
    """auto-provision folder → create sheet → write → dedupe → amend → idempotent re-ensure."""
    sheet_id = week_sheet.ensure_week_sheet(SANDBOX_PROJECT, SANDBOX_WORK_DATE)
    # Capture the auto-created per-job folder up front so the finally can clean it.
    folder_id = smartsheet_client.find_folder_by_name_in_workspace(
        sheet_ids.WORKSPACE_SAFETY_PORTAL, SANDBOX_PROJECT
    )
    try:
        assert sheet_id > 0
        # The per-job folder was auto-provisioned at the WORKSPACE_SAFETY_PORTAL surface.
        assert folder_id is not None
        # Name keys on the Saturday that opens the work-date's week.
        assert week_sheet.week_sheet_name(SANDBOX_PROJECT, SANDBOX_WORK_DATE).endswith(
            "week of 1970-01-03"
        )

        # Write the original submission row, then find it by UUID (dedupe authority).
        week_sheet.write_submission_row(
            sheet_id,
            submission_uuid="int-u1",
            form_code="jha-v1",
            work_date=SANDBOX_WORK_DATE,
            title="Job Hazard Analysis",
            box_link="https://app.box.com/file/int1",
            submitted_at="1970-01-07T08:00:00-08:00",
        )
        found = week_sheet.find_submission_row(sheet_id, "int-u1")
        assert found is not None
        assert found[week_sheet.COL_SUBMISSION_PDF] == "https://app.box.com/file/int1"

        # Amend: a second submission supersedes the first.
        week_sheet.write_submission_row(
            sheet_id,
            submission_uuid="int-u2",
            form_code="jha-v1",
            work_date=SANDBOX_WORK_DATE,
            title="Job Hazard Analysis (amended)",
            box_link="https://app.box.com/file/int2",
            submitted_at="1970-01-07T09:00:00-08:00",
        )
        assert week_sheet.supersede_row(sheet_id, "int-u1", "int-u2") is True
        prior = week_sheet.find_submission_row(sheet_id, "int-u1")
        assert prior is not None
        assert prior[week_sheet.COL_STATUS] == week_sheet.STATUS_SUPERSEDED
        assert prior[week_sheet.COL_SUPERSEDED_BY] == "int-u2"

        # Idempotent re-ensure returns the same sheet (find, not re-create).
        assert week_sheet.ensure_week_sheet(SANDBOX_PROJECT, SANDBOX_WORK_DATE) == sheet_id
    finally:
        _delete_sheet(sheet_id, _token)
        if folder_id is not None:
            _delete_folder(folder_id, _token)


def test_compile_now_selection_round_trip(_token):
    """Part B SDK-vs-Live guard: the per-submission Compile-Now SELECTION round-trips through
    the live Smartsheet CHECKBOX column — `selected_submission_row_ids` reads a checked box,
    `clear_compile_now` unchecks it (Op Stds §30; mocks can't prove the SDK accepts a bool on
    a CHECKBOX cell + returns it truthy)."""
    sheet_id = week_sheet.ensure_week_sheet(SANDBOX_PROJECT, SANDBOX_WORK_DATE)
    folder_id = smartsheet_client.find_folder_by_name_in_workspace(
        sheet_ids.WORKSPACE_SAFETY_PORTAL, SANDBOX_PROJECT
    )
    try:
        week_sheet.write_submission_row(
            sheet_id,
            submission_uuid="int-sel",
            form_code="jha-v1",
            work_date=SANDBOX_WORK_DATE,
            title="Job Hazard Analysis",
            box_link="https://app.box.com/file/intsel",
            submitted_at="1970-01-07T08:00:00-08:00",
        )
        row = week_sheet.find_submission_row(sheet_id, "int-sel")
        assert row is not None
        rid = int(row["_row_id"])

        # Unchecked → not in the selection.
        subs = week_sheet.list_submission_rows(sheet_id, active_only=True)
        assert rid not in week_sheet.selected_submission_row_ids(subs)

        # Operator checks Compile Now on the submission row → it IS the selection.
        smartsheet_client.update_rows(sheet_id, [{"_row_id": rid, week_sheet.COL_COMPILE_NOW: True}])
        subs = week_sheet.list_submission_rows(sheet_id, active_only=True)
        assert week_sheet.selected_submission_row_ids(subs) == {rid}

        # clear_compile_now (the post-compile consume) unchecks it.
        week_sheet.clear_compile_now(sheet_id, {rid})
        subs = week_sheet.list_submission_rows(sheet_id, active_only=True)
        assert rid not in week_sheet.selected_submission_row_ids(subs)
    finally:
        _delete_sheet(sheet_id, _token)
        if folder_id is not None:
            _delete_folder(folder_id, _token)
