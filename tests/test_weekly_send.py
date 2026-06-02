"""Unit tests for safety_reports/weekly_send.py.

All external services mocked. Tests call `send_one_row()` directly to
exercise the 7-stage pipeline. Capability gating (no anthropic) is
exercised by tests/test_capability_gating.py.

Structure mirrors tests/test_weekly_generate.py — central fixture
patches the external surface; per-test overrides shape each scenario.
"""
from __future__ import annotations

import json
from datetime import UTC, date, datetime
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from safety_reports.weekly_send import (
    STATUS_FAILED,
    STATUS_PENDING,
    STATUS_SENT,
    _compute_late_send,
    _parse_recipients,
    _parse_retry_count,
    _update_notes_tags,
    _validate_recipients,
    send_one_row,
)
from shared.error_log import Severity
from shared.graph_client import GraphAuthError, GraphError
from shared.smartsheet_client import SmartsheetError, SmartsheetNotFoundError

# ---- Fixtures -----------------------------------------------------------


def _row(
    *,
    row_id: int = 100,
    job: str = "Bradley 1",
    week: Any = date(2026, 5, 18),
    approved: bool = True,
    send_status: str = STATUS_PENDING,
    recipients: Any = None,
    notes: str = "",
    draft_body: str = "WPR draft body for testing.",
) -> dict[str, Any]:
    """Construct a WPR_Pending_Review row dict matching the live schema."""
    if recipients is None:
        recipients = json.dumps(["seths@evergreenmirror.com"])
    return {
        "_row_id": row_id,
        "Customer": "Forefront",
        "Job": job,
        "Week": week,
        "Draft Body": draft_body,
        "Recipients": recipients,
        "Approved for Send": approved,
        "Send Status": send_status,
        "Late Send": False,
        "Notes": notes,
    }


@pytest.fixture
def _patch_all(mocker):
    """Default mock surface for all weekly_send tests."""
    mocks = {
        "get_row": mocker.patch(
            "safety_reports.weekly_send.smartsheet_client.get_row",
            return_value=_row(),
        ),
        "update_rows": mocker.patch(
            "safety_reports.weekly_send.smartsheet_client.update_rows",
            return_value=None,
        ),
        "send_mail": mocker.patch(
            "safety_reports.weekly_send.graph_client.send_mail",
            return_value=None,  # Graph returns None on 202 Accepted
        ),
        "get_setting": mocker.patch(
            "safety_reports.weekly_send.smartsheet_client.get_setting",
            side_effect=SmartsheetNotFoundError("default test stub"),
        ),
        "error_log": mocker.patch(
            "safety_reports.weekly_send.error_log.log",
            return_value=None,
        ),
        "alert_critical": mocker.patch(
            "safety_reports.weekly_send.error_log._alert_critical",
            return_value=None,
        ),
    }
    return mocks


def _critical_logged(error_log_mock, error_code: str) -> int:
    """Count CRITICAL log() calls with the given error_code.

    A3: log(CRITICAL) IS the operator-page signal (it fires Resend + Sentry),
    so a paging assertion checks for the CRITICAL record with its dedupe-key
    error_code rather than a separate _alert_critical call.
    """
    return sum(
        1
        for c in error_log_mock.call_args_list
        if c.args
        and c.args[0] == Severity.CRITICAL
        and c.kwargs.get("error_code") == error_code
    )


# ---- Helper-function tests ----------------------------------------------


def test_parse_retry_count_absent_returns_zero():
    assert _parse_retry_count(None) == 0
    assert _parse_retry_count("") == 0
    assert _parse_retry_count("generated=2026-05-22T14:00:00") == 0


def test_parse_retry_count_present_returns_value():
    notes = "generated=... [SEND_RETRY_COUNT: 2]"
    assert _parse_retry_count(notes) == 2


def test_parse_retry_count_malformed_returns_zero():
    notes = "[SEND_RETRY_COUNT: not_a_number]"
    assert _parse_retry_count(notes) == 0


def test_update_notes_tags_inserts_retry_count_when_missing():
    notes = "[ZERO_DATA_WEEK] generated=2026-05-22T14:00:00"
    new = _update_notes_tags(notes, new_retry_count=1)
    assert "[SEND_RETRY_COUNT: 1]" in new
    assert "[ZERO_DATA_WEEK]" in new


def test_update_notes_tags_replaces_existing_retry_count():
    notes = "[ZERO_DATA_WEEK] [SEND_RETRY_COUNT: 1] generated=..."
    new = _update_notes_tags(notes, new_retry_count=2)
    assert "[SEND_RETRY_COUNT: 2]" in new
    assert "[SEND_RETRY_COUNT: 1]" not in new


def test_update_notes_tags_inserts_last_error():
    notes = "generated=..."
    new = _update_notes_tags(notes, new_last_error="GraphError: 500")
    assert "[LAST_SEND_ERROR: GraphError: 500]" in new


def test_update_notes_tags_sanitizes_brackets_in_last_error():
    notes = ""
    new = _update_notes_tags(notes, new_last_error="something [weird] happened")
    assert "[LAST_SEND_ERROR: something (weird) happened]" in new


def test_update_notes_tags_appends_sent_timestamp():
    notes = "[ZERO_DATA_WEEK] generated=2026-05-22T14:00:00"
    new = _update_notes_tags(notes, append_sent_timestamp=True)
    assert "sent=" in new


def test_parse_recipients_valid_json_list():
    raw = json.dumps(["a@b.com", "c@d.com"])
    assert _parse_recipients(raw) == ["a@b.com", "c@d.com"]


def test_parse_recipients_empty_string_returns_empty_list():
    assert _parse_recipients("") == []
    assert _parse_recipients(None) == []


def test_parse_recipients_malformed_returns_none():
    assert _parse_recipients("not json") is None


def test_parse_recipients_strips_whitespace():
    raw = json.dumps(["  a@b.com  ", "c@d.com"])
    assert _parse_recipients(raw) == ["a@b.com", "c@d.com"]


def test_validate_recipients_all_valid():
    ok, bad = _validate_recipients(["a@b.com", "c@d.com"])
    assert ok is True
    assert bad is None


def test_validate_recipients_first_bad_returned():
    ok, bad = _validate_recipients(["a@b.com", "not-an-email"])
    assert ok is False
    assert bad == "not-an-email"


def test_compute_late_send_inside_deadline():
    # Week of Mon 2026-03-16; deadline default `MON 12:00` (Mon 2026-03-23 12:00).
    # Now = Fri 2026-03-20 17:00 local — well inside the deadline.
    week_start = date(2026, 3, 16)
    now_local = datetime(2026, 3, 20, 17, 0, tzinfo=ZoneInfo("America/Los_Angeles"))
    assert _compute_late_send(week_start, now_local, "MON 12:00") is False


def test_compute_late_send_past_deadline():
    # Now = Tue 2026-03-24 — past the Mon 2026-03-23 12:00 deadline.
    week_start = date(2026, 3, 16)
    now_local = datetime(2026, 3, 24, 9, 0, tzinfo=ZoneInfo("America/Los_Angeles"))
    assert _compute_late_send(week_start, now_local, "MON 12:00") is True


# ---- send_one_row pipeline tests ----------------------------------------


def test_send_one_row_happy_path(_patch_all):
    """Approved PENDING row with valid Recipients → SENT."""
    result = send_one_row(100)
    assert result.status == "sent"
    assert result.row_id == 100
    assert result.project_name == "Bradley 1"
    _patch_all["send_mail"].assert_called_once()
    send_kwargs = _patch_all["send_mail"].call_args.kwargs
    assert send_kwargs["to"] == ["seths@evergreenmirror.com"]
    assert "Bradley 1" in send_kwargs["subject"]
    assert send_kwargs["content_type"] == "Text"
    # Row updated to SENT
    _patch_all["update_rows"].assert_called_once()
    updates = _patch_all["update_rows"].call_args[0][1]
    assert updates[0]["Send Status"] == STATUS_SENT
    assert updates[0]["Sent At"]
    assert updates[0]["Late Send"] in (True, False)
    assert "sent=" in updates[0]["Notes"]


def test_send_skips_already_sent(_patch_all):
    """Send Status=SENT → skip, no send, no row update."""
    _patch_all["get_row"].return_value = _row(send_status=STATUS_SENT)
    result = send_one_row(100)
    assert result.status == "skipped_already_sent"
    _patch_all["send_mail"].assert_not_called()
    _patch_all["update_rows"].assert_not_called()


def test_send_skips_not_approved(_patch_all):
    """Approved for Send=False → skip."""
    _patch_all["get_row"].return_value = _row(approved=False)
    result = send_one_row(100)
    assert result.status == "skipped_not_approved"
    _patch_all["send_mail"].assert_not_called()


def test_send_skips_empty_recipients(_patch_all):
    """Empty Recipients (`[NO_RECIPIENTS]` hold) → skip silently, NOT FAILED."""
    _patch_all["get_row"].return_value = _row(recipients="")
    result = send_one_row(100)
    assert result.status == "skipped_no_recipients"
    _patch_all["send_mail"].assert_not_called()
    _patch_all["update_rows"].assert_not_called()  # not marked FAILED


def test_send_skips_generation_failed_tag(_patch_all):
    """[GENERATION_FAILED: ...] tag refuses even when Approved=True."""
    _patch_all["get_row"].return_value = _row(
        notes="[GENERATION_FAILED: SmartsheetNotFoundError] generated=..."
    )
    result = send_one_row(100)
    assert result.status == "skipped_generation_failed"
    _patch_all["send_mail"].assert_not_called()


def test_send_invalid_recipient_marks_failed(_patch_all):
    """One malformed address in Recipients → FAILED + Last Send Error tagged."""
    _patch_all["get_row"].return_value = _row(
        recipients=json.dumps(["seths@evergreenmirror.com", "not-an-email"])
    )
    result = send_one_row(100)
    assert result.status == "invalid_recipients"
    assert "not-an-email" in (result.error or "")
    _patch_all["send_mail"].assert_not_called()
    _patch_all["update_rows"].assert_called_once()
    updates = _patch_all["update_rows"].call_args[0][1]
    assert updates[0]["Send Status"] == STATUS_FAILED
    assert "[LAST_SEND_ERROR: invalid_recipient:" in updates[0]["Notes"]
    assert "[SEND_RETRY_COUNT: 1]" in updates[0]["Notes"]


def test_send_graph_error_marks_failed_increments_retry(_patch_all):
    """GraphError raised → FAILED + retry incremented + ERROR logged (not CRITICAL)."""
    _patch_all["send_mail"].side_effect = GraphError("HTTP 503: Service Unavailable")
    result = send_one_row(100)
    assert result.status == "send_failed"
    assert result.retry_count == 1
    _patch_all["update_rows"].assert_called_once()
    updates = _patch_all["update_rows"].call_args[0][1]
    assert updates[0]["Send Status"] == STATUS_FAILED
    assert "[SEND_RETRY_COUNT: 1]" in updates[0]["Notes"]
    # Below retries-exhaust threshold — logs ERROR, not CRITICAL → no page (A3).
    assert _critical_logged(_patch_all["error_log"], "weekly_send.retries_exhausted") == 0


def test_send_graph_auth_error_fires_critical(_patch_all):
    """GraphAuthError → CRITICAL triple-fire + FAILED."""
    _patch_all["send_mail"].side_effect = GraphAuthError("HTTP 401: Unauthorized")
    result = send_one_row(100)
    assert result.status == "send_failed"
    # A3: the CRITICAL log IS the page (log(CRITICAL) fires Resend + Sentry),
    # with the dedupe-key error_code intact (Op Stds §3.1).
    assert _critical_logged(_patch_all["error_log"], "weekly_send.graph_auth_failed") == 1
    # Double-fire guard: paging is via log(CRITICAL) ONLY — a re-introduced
    # explicit error_log._alert_critical would trip this (it patches that name).
    _patch_all["alert_critical"].assert_not_called()


def test_send_retry_count_exhausted_fires_critical(_patch_all):
    """Row at retry=2 + GraphError → retry becomes 3 + CRITICAL fired."""
    _patch_all["get_row"].return_value = _row(
        notes="[SEND_RETRY_COUNT: 2] generated=..."
    )
    _patch_all["send_mail"].side_effect = GraphError("HTTP 503")
    result = send_one_row(100)
    assert result.status == "send_failed"
    assert result.retry_count == 3
    assert _critical_logged(_patch_all["error_log"], "weekly_send.retries_exhausted") == 1
    _patch_all["alert_critical"].assert_not_called()  # double-fire guard (A3)


def test_send_skipped_when_retries_already_exhausted(_patch_all):
    """Row at retry=3 + Send Status=FAILED → terminal skip; no send attempt."""
    _patch_all["get_row"].return_value = _row(
        send_status=STATUS_FAILED,
        notes="[SEND_RETRY_COUNT: 3] generated=...",
    )
    result = send_one_row(100)
    assert result.status == "skipped_retries_exhausted"
    _patch_all["send_mail"].assert_not_called()


def test_send_marks_late_when_past_deadline(_patch_all, mocker):
    """When now_local is past Mon-noon deadline → Late Send=True; send still fires."""
    # Force the row's Week to be a week whose Monday-after is in the past.
    _patch_all["get_row"].return_value = _row(week=date(2026, 3, 16))
    # Mock datetime.now so it's well past Mon 2026-03-23 12:00.
    fake_now_local = datetime(2026, 3, 30, 9, 0, tzinfo=ZoneInfo("America/Los_Angeles"))
    fake_now_utc = datetime(2026, 3, 30, 16, 0, tzinfo=UTC)

    class _FakeDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            if tz is UTC:
                return fake_now_utc
            return fake_now_local

    mocker.patch("safety_reports.weekly_send.datetime", _FakeDatetime)

    result = send_one_row(100)
    assert result.status == "sent"
    assert result.late is True
    updates = _patch_all["update_rows"].call_args[0][1]
    assert updates[0]["Late Send"] is True


def test_send_marks_on_time_when_inside_deadline(_patch_all, mocker):
    """now_local before deadline → Late Send=False."""
    _patch_all["get_row"].return_value = _row(week=date(2026, 5, 18))
    fake_now_local = datetime(2026, 5, 22, 17, 0, tzinfo=ZoneInfo("America/Los_Angeles"))
    fake_now_utc = datetime(2026, 5, 23, 0, 0, tzinfo=UTC)

    class _FakeDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            if tz is UTC:
                return fake_now_utc
            return fake_now_local

    mocker.patch("safety_reports.weekly_send.datetime", _FakeDatetime)

    result = send_one_row(100)
    assert result.status == "sent"
    assert result.late is False


def test_send_row_not_found(_patch_all):
    """get_row 404 → row_not_found result; no further calls."""
    _patch_all["get_row"].side_effect = SmartsheetNotFoundError("row deleted")
    result = send_one_row(100)
    assert result.status == "row_not_found"
    _patch_all["send_mail"].assert_not_called()
    _patch_all["update_rows"].assert_not_called()


def test_send_zero_data_week_row_sends_normally(_patch_all):
    """[ZERO_DATA_WEEK] tag is advisory; approved → send fires."""
    _patch_all["get_row"].return_value = _row(
        notes="[ZERO_DATA_WEEK] generated=...",
    )
    result = send_one_row(100)
    assert result.status == "sent"
    _patch_all["send_mail"].assert_called_once()


def test_send_security_trigger_row_sends_normally(_patch_all):
    """[SECURITY_TRIGGER] tag is advisory once reviewer approves."""
    _patch_all["get_row"].return_value = _row(
        notes="[SECURITY_TRIGGER] generated=...",
    )
    result = send_one_row(100)
    assert result.status == "sent"
    _patch_all["send_mail"].assert_called_once()


def test_send_subject_format(_patch_all):
    """Subject line matches the documented format."""
    _patch_all["get_row"].return_value = _row(
        job="Bradley 1",
        week=date(2026, 3, 16),
    )
    send_one_row(100)
    send_kwargs = _patch_all["send_mail"].call_args.kwargs
    assert send_kwargs["subject"] == "WPR — Bradley 1 — Week of March 16, 2026"


def test_send_uses_from_mailbox_from_config(_patch_all):
    """When ITS_Config from_mailbox row is set, it overrides the default."""
    _patch_all["get_setting"].side_effect = None
    _patch_all["get_setting"].return_value = "production@evergreenrenewables.com"
    send_one_row(100)
    send_kwargs = _patch_all["send_mail"].call_args.kwargs
    assert send_kwargs["from_mailbox"] == "production@evergreenrenewables.com"


def test_send_post_send_row_update_failure_fires_critical(_patch_all):
    """Row update failing after send fires CRITICAL (double-send risk)."""
    _patch_all["update_rows"].side_effect = SmartsheetError("HTTP 500")
    result = send_one_row(100)
    assert result.status == "sent"  # send DID happen
    assert "row_update_failed" in (result.error or "")
    assert _critical_logged(
        _patch_all["error_log"], "weekly_send.post_send_row_update_failed"
    ) == 1
    _patch_all["alert_critical"].assert_not_called()  # double-fire guard (A3)
