"""Tests for safety_reports/weekly_send.py — the Phase-5 WSR send path.

All external services mocked. The legacy WPR-path tests were retired with the
repoint. Live coverage: tests/test_weekly_send_integration.py.
"""
from __future__ import annotations

import re
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from safety_reports import weekly_send, wsr_review
from shared.graph_client import GraphAuthError, GraphError


def _row(**kw):
    base = {
        "_row_id": 50,
        wsr_review.COL_JOB_PROJECT: "Bradley 1",
        wsr_review.COL_JOB_ID: "JOB-1",
        wsr_review.COL_WEEK_OF: "2026-05-30",
        wsr_review.COL_COMPILED_PDF: "https://app.box.com/file/77",
        wsr_review.COL_EMAIL_BODY: "Good morning Dana — packet attached.",
        wsr_review.COL_SEND_STATUS: wsr_review.STATUS_PENDING,
        wsr_review.COL_NOTES: "",
        # WSR display columns — deliberately STALE to prove send-time resolution
        # uses active_jobs, NOT these:
        wsr_review.COL_RECIPIENT_TO: "STALE@display.example",
        wsr_review.COL_CC: "STALE-cc@display.example",
    }
    base.update(kw)
    return base


def _job(**kw):
    base = dict(
        project_name="Bradley 1", job_id="JOB-1",
        safety_reports_contact_email="pm@evergreenmirror.com",
        safety_reports_contact_name="Dana", cc_emails=("cc1@x.com", "cc2@x.com"),
        is_active=True, active_status="Active",
    )
    base.update(kw)
    return SimpleNamespace(**base)


@pytest.fixture
def stub(mocker) -> dict[str, MagicMock]:
    return {
        "get_row": mocker.patch.object(weekly_send.smartsheet_client, "get_row", return_value=_row()),
        "update_rows": mocker.patch.object(weekly_send.smartsheet_client, "update_rows"),
        "get_job": mocker.patch.object(weekly_send.active_jobs, "get_job", return_value=_job()),
        "download": mocker.patch.object(weekly_send.box_client, "download_file", return_value=b"%PDF-packet"),
        "send_mail": mocker.patch.object(weekly_send.graph_client, "send_mail"),
        "from_mailbox": mocker.patch.object(weekly_send, "_read_str_setting", return_value="safety@evergreenmirror.com"),
        "log": mocker.patch.object(weekly_send.error_log, "log"),
    }


# ---- happy send ----------------------------------------------------------


def test_send_resolves_recipients_from_active_jobs_and_attaches_pdf(stub):
    result = weekly_send.send_one_row(50)
    assert result.status == "sent"
    kw = stub["send_mail"].call_args.kwargs
    # Recipients from active_jobs, NOT the stale WSR display columns.
    assert kw["to"] == ["pm@evergreenmirror.com"]
    assert kw["cc"] == ["cc1@x.com", "cc2@x.com"]
    assert "STALE" not in str(kw["to"]) and "STALE" not in str(kw["cc"])
    # Body = the WSR Email Body (source of truth); compiled PDF attached.
    assert kw["body"] == "Good morning Dana — packet attached."
    assert kw["attachments"][0]["contentBytes"] == b"%PDF-packet"
    assert kw["attachments"][0]["contentType"] == "application/pdf"
    assert "Bradley 1" in kw["subject"] and "2026-05-30" in kw["subject"]
    # Marked SENT, with a naive-Pacific Sent At (ABSTRACT_DATETIME rejects an offset — a
    # rejected write here fires the CRITICAL double-send path, so this format is load-bearing).
    upd = stub["update_rows"].call_args.args[1][0]
    assert upd[wsr_review.COL_SEND_STATUS] == wsr_review.STATUS_SENT
    assert re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}$", upd[wsr_review.COL_SENT_AT])


def test_download_uses_compiled_pdf_box_id(stub):
    weekly_send.send_one_row(50)
    stub["download"].assert_called_once_with("77")


# ---- idempotency / state gates -------------------------------------------


def test_already_sent_is_skipped(stub):
    stub["get_row"].return_value = _row(**{wsr_review.COL_SEND_STATUS: wsr_review.STATUS_SENT})
    result = weekly_send.send_one_row(50)
    assert result.status == "skipped_already_sent"
    stub["send_mail"].assert_not_called()


def test_held_row_is_skipped(stub):
    stub["get_row"].return_value = _row(**{wsr_review.COL_SEND_STATUS: wsr_review.STATUS_HELD})
    result = weekly_send.send_one_row(50)
    assert result.status == "skipped_held"
    stub["send_mail"].assert_not_called()


def test_row_not_found(stub):
    from shared.smartsheet_client import SmartsheetNotFoundError
    stub["get_row"].side_effect = SmartsheetNotFoundError("gone")
    assert weekly_send.send_one_row(50).status == "row_not_found"


# ---- HELD refusals (never send a half-formed packet) ----------------------


def test_unknown_job_is_held(stub):
    stub["get_job"].return_value = None
    result = weekly_send.send_one_row(50)
    assert result.status == "held_no_recipient"
    stub["send_mail"].assert_not_called()
    assert stub["update_rows"].call_args.args[1][0][wsr_review.COL_SEND_STATUS] == wsr_review.STATUS_HELD


def test_empty_to_contact_is_held(stub):
    stub["get_job"].return_value = _job(safety_reports_contact_email="")
    result = weekly_send.send_one_row(50)
    assert result.status == "held_no_recipient"
    stub["send_mail"].assert_not_called()


def test_missing_compiled_pdf_is_held(stub):
    stub["get_row"].return_value = _row(**{wsr_review.COL_COMPILED_PDF: ""})
    result = weekly_send.send_one_row(50)
    assert result.status == "held_missing_pdf"
    stub["send_mail"].assert_not_called()
    stub["download"].assert_not_called()


# ---- transient failures → FAILED + retry ----------------------------------


def test_box_download_failure_is_failed_not_held(stub):
    stub["download"].side_effect = weekly_send.box_client.BoxError("503")
    result = weekly_send.send_one_row(50)
    assert result.status == "send_failed"
    stub["send_mail"].assert_not_called()
    assert stub["update_rows"].call_args.args[1][0][wsr_review.COL_SEND_STATUS] == wsr_review.STATUS_FAILED


def test_graph_error_marks_failed_and_increments_retry(stub):
    stub["get_row"].return_value = _row(**{wsr_review.COL_NOTES: "[SEND_RETRY_COUNT: 1]"})
    stub["send_mail"].side_effect = GraphError("transient 500")
    result = weekly_send.send_one_row(50)
    assert result.status == "send_failed"
    notes = stub["update_rows"].call_args.args[1][0][wsr_review.COL_NOTES]
    assert "[SEND_RETRY_COUNT: 2]" in notes


def test_graph_error_at_max_retries_critical(stub):
    stub["get_row"].return_value = _row(**{wsr_review.COL_NOTES: f"[SEND_RETRY_COUNT: {weekly_send.MAX_SEND_RETRIES - 1}]"})
    stub["send_mail"].side_effect = GraphError("still failing")
    weekly_send.send_one_row(50)
    crits = [c for c in stub["log"].call_args_list if c.kwargs.get("error_code") == "weekly_send.retries_exhausted"]
    assert crits


def test_graph_auth_error_critical_and_failed(stub):
    stub["send_mail"].side_effect = GraphAuthError("401")
    result = weekly_send.send_one_row(50)
    assert result.status == "send_failed"
    crits = [c for c in stub["log"].call_args_list if c.kwargs.get("error_code") == "weekly_send.graph_auth_failed"]
    assert crits


# ---- post-send row-update failure → double-send guard ---------------------


def test_post_send_update_failure_returns_sent_with_critical(stub):
    from shared.smartsheet_client import SmartsheetError
    stub["update_rows"].side_effect = SmartsheetError("update failed after send")
    result = weekly_send.send_one_row(50)
    assert result.status == "sent"  # the send DID fire
    crits = [c for c in stub["log"].call_args_list if c.kwargs.get("error_code") == "weekly_send.post_send_row_update_failed"]
    assert crits


# ---- resolved recipients are logged --------------------------------------


def test_resolved_recipients_logged(stub):
    weekly_send.send_one_row(50)
    dispatch = [c for c in stub["log"].call_args_list if c.kwargs.get("error_code") == "weekly_send.dispatch"]
    assert dispatch and "pm@evergreenmirror.com" in dispatch[0].args[2]


# ---- _coerce_week --------------------------------------------------------

from datetime import date as _date  # noqa: E402


@pytest.mark.parametrize("raw,expected", [
    (None, ""),
    ("", ""),
    (_date(2026, 5, 30), "2026-05-30"),
    ("2026-05-30", "2026-05-30"),
    ("2026-05-30T00:00:00", "2026-05-30"),
])
def test_coerce_week(raw, expected):
    assert weekly_send._coerce_week(raw) == expected


# ---- HELD outcome statuses are explicit (not substring-sniffed) -----------


def test_held_outcomes_are_distinct(stub):
    # unknown job + empty TO → held_no_recipient; missing PDF → held_missing_pdf.
    stub["get_job"].return_value = None
    assert weekly_send.send_one_row(50).status == "held_no_recipient"
    stub["get_job"].return_value = _job()
    stub["get_row"].return_value = _row(**{wsr_review.COL_COMPILED_PDF: ""})
    assert weekly_send.send_one_row(50).status == "held_missing_pdf"
