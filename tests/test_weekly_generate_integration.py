"""Live-API integration test for safety_reports/weekly_generate.py (Op Stds §30).

Phase-5 rewrite: weekly_generate is now the DETERMINISTIC compile (no Anthropic).
This exercises the new Smartsheet WRITE paths against a real sandbox via the
EMPTY-WEEK path — which needs NO Box files and NO LLM:

  - active_jobs.list_active_jobs (patched to one sandbox job),
  - week_sheet.ensure_week_sheet (creates the sandbox week sheet),
  - week_sheet.upsert_rollup_row (the read-only Rollup snapshot row),
  - wsr_review.upsert_row (the WSR_human_review row — the real Phase-5 review sheet).

The full Box-merge e2e (real submission PDFs → merge → packet upload) is
deploy-gated (the next session's live smoke).

Default `pytest -q` SKIPS this file (`-m 'not integration'`). Run with
`pytest -m integration`. Requires ITS_SMARTSHEET_TOKEN in Keychain. NOT in CI.

Sandbox isolation: a synthetic project mapped to FOLDER_SYSTEM_CONFIG; a unique
sandbox Job ID + a 1970 week so the WSR row + week sheet are collision-free.
Try/finally deletes the week sheet + the WSR row.
"""
from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pytest
import requests  # type: ignore[import-untyped]

from safety_reports import week_sheet, weekly_generate, wsr_review
from shared import keychain, safety_week, sheet_ids, smartsheet_client

pytestmark = pytest.mark.integration

SANDBOX_PROJECT = "_int_weekly_generate_sandbox"
SANDBOX_JOB_ID = "_INT_WG_JOB"
SANDBOX_ANCHOR = date(1970, 1, 7)  # Wed → Sat→Fri week opening 1970-01-03


@pytest.fixture
def _token() -> str:
    try:
        token = keychain.get_secret("ITS_SMARTSHEET_TOKEN")
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"ITS_SMARTSHEET_TOKEN unavailable: {e!r}")
    if not token:
        pytest.skip("ITS_SMARTSHEET_TOKEN returned empty")
    return token


@pytest.fixture
def sandbox(mocker):
    original = dict(sheet_ids.FIELD_REPORTS_FOLDER_BY_PROJECT)
    sheet_ids.FIELD_REPORTS_FOLDER_BY_PROJECT[SANDBOX_PROJECT] = sheet_ids.FOLDER_SYSTEM_CONFIG
    job = SimpleNamespace(
        project_name=SANDBOX_PROJECT, job_id=SANDBOX_JOB_ID,
        safety_reports_contact_email="int@evergreenmirror.com",
        safety_reports_contact_name="Int Tester", cc_emails=(),
        is_active=True, active_status="Active",
    )
    mocker.patch.object(weekly_generate.active_jobs, "list_active_jobs", return_value=[job])
    try:
        yield
    finally:
        sheet_ids.FIELD_REPORTS_FOLDER_BY_PROJECT.clear()
        sheet_ids.FIELD_REPORTS_FOLDER_BY_PROJECT.update(original)


def test_empty_week_compile_writes_rollup_and_wsr(_token, sandbox):
    week = safety_week.week_bounds(SANDBOX_ANCHOR)
    sheet_name = week_sheet.week_sheet_name(SANDBOX_PROJECT, SANDBOX_ANCHOR)
    wsr_row_id: int | None = None
    week_sheet_id: int | None = None
    try:
        out = weekly_generate._run_pipeline(week_start_override=SANDBOX_ANCHOR)
        assert out["empty_weeks"] == 1
        assert out["wsr_written"] == 1

        # The sandbox week sheet now exists with a Rollup row.
        week_sheet_id = smartsheet_client.find_sheet_by_name_in_folder(
            sheet_ids.FOLDER_SYSTEM_CONFIG, sheet_name
        )
        assert week_sheet_id is not None
        rollup = week_sheet.get_rollup_row(week_sheet_id)
        assert rollup is not None
        assert "empty-week" in str(rollup.get(week_sheet.COL_NOTES) or "")

        # The WSR row exists for (job, week) with Send Status PENDING + a seeded body.
        wsr = wsr_review.find_row(wsr_review.SHEET_ID, SANDBOX_JOB_ID, week.start)
        assert wsr is not None
        wsr_row_id = int(wsr["_row_id"])
        assert wsr.get(wsr_review.COL_SEND_STATUS) == wsr_review.STATUS_PENDING
        assert "Good morning" in str(wsr.get(wsr_review.COL_EMAIL_BODY) or "")
    finally:
        if wsr_row_id is not None:
            smartsheet_client.delete_rows(wsr_review.SHEET_ID, [wsr_row_id])
        if week_sheet_id is not None:
            requests.delete(
                f"https://api.smartsheet.com/2.0/sheets/{week_sheet_id}",
                headers={"Authorization": f"Bearer {_token}"},
            )
