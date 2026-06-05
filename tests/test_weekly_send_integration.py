"""Live-API integration test for safety_reports/weekly_send.py (Op Stds §30).

Phase-5: weekly_send repointed WPR_Pending_Review → WSR_human_review. This exercises
the new Smartsheet WRITE path against a real sandbox via the HELD path — which sends
NO email and hits NO Box:

  - smartsheet_client.add_rows / get_row (a sandbox WSR row),
  - active_jobs.get_job (an intentionally-UNKNOWN sandbox job_id → HELD),
  - the HELD status update (Send Status → HELD, never SENT).

The actual Graph send is NOT exercised here (it would email a real recipient); the
end-to-end send is the deploy session's live smoke. Default `pytest -q` SKIPS this
file (`-m 'not integration'`). Run with `pytest -m integration`. NOT in CI.

The sandbox WSR row is written to the REAL WSR sheet with a unique, clearly-fake
Job ID + a 1970 week, and deleted in `finally`.
"""
from __future__ import annotations

import pytest

from safety_reports import weekly_send, wsr_review
from shared import keychain, smartsheet_client

pytestmark = pytest.mark.integration

SANDBOX_JOB_ID = "_INT_WS_NONEXISTENT_JOB"


@pytest.fixture
def _token() -> str:
    try:
        token = keychain.get_secret("ITS_SMARTSHEET_TOKEN")
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"ITS_SMARTSHEET_TOKEN unavailable: {e!r}")
    if not token:
        pytest.skip("ITS_SMARTSHEET_TOKEN returned empty")
    return token


def test_unknown_job_helds_without_sending(_token):
    """A WSR row whose Job ID isn't an Active job → HELD (no send, no Box)."""
    [row_id] = smartsheet_client.add_rows(
        wsr_review.SHEET_ID,
        [{
            wsr_review.COL_JOB_PROJECT: "_int_ws_sandbox",
            wsr_review.COL_JOB_ID: SANDBOX_JOB_ID,
            wsr_review.COL_WEEK_OF: "1970-01-03",
            wsr_review.COL_EMAIL_BODY: "integration test — should never send",
            wsr_review.COL_SEND_STATUS: wsr_review.STATUS_PENDING,
        }],
    )
    try:
        result = weekly_send.send_one_row(row_id)
        # Unknown job → HELD refusal; no email was sent.
        assert result.status == "held_no_recipient"
        refreshed = smartsheet_client.get_row(wsr_review.SHEET_ID, row_id)
        assert refreshed.get(wsr_review.COL_SEND_STATUS) == wsr_review.STATUS_HELD
    finally:
        smartsheet_client.delete_rows(wsr_review.SHEET_ID, [row_id])
