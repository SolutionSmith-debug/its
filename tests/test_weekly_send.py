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
        wsr_review.COL_WORKSTREAM: "safety",  # present-matching tag → the guard passes
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
        "send_large": mocker.patch.object(weekly_send.graph_client, "send_mail_large_attachment"),
        "from_mailbox": mocker.patch.object(weekly_send, "_read_str_setting", return_value="safety@evergreenmirror.com"),
        "log": mocker.patch.object(weekly_send.error_log, "log"),
        "recipient_health": mocker.patch.object(weekly_send.recipient_health, "report_unhealthy_recipient"),
    }


# ---- happy send ----------------------------------------------------------


def test_send_resolves_recipients_from_active_jobs_and_attaches_pdf(stub):
    result = weekly_send.send_one_row(50, weekly_send.CONFIG)
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
    # Write-ahead marker: the FIRST update_rows call flipped the row to SENDING, the
    # LAST flipped it to SENT (the irreversible send sits between them).
    calls = stub["update_rows"].call_args_list
    assert calls[0].args[1][0][wsr_review.COL_SEND_STATUS] == wsr_review.STATUS_SENDING
    assert calls[-1].args[1][0][wsr_review.COL_SEND_STATUS] == wsr_review.STATUS_SENT


def test_sending_marker_is_written_before_the_send(stub):
    # At the moment send_mail fires, the row must ALREADY be marked SENDING — proving the
    # write-ahead ordering (so a post-send stamp failure can never leave it re-dispatchable).
    seen = {}

    def _capture(*a, **k):
        seen["status_at_send"] = stub["update_rows"].call_args.args[1][0][wsr_review.COL_SEND_STATUS]

    stub["send_mail"].side_effect = _capture
    weekly_send.send_one_row(50, weekly_send.CONFIG)
    assert seen["status_at_send"] == wsr_review.STATUS_SENDING


def test_download_uses_compiled_pdf_box_id(stub):
    weekly_send.send_one_row(50, weekly_send.CONFIG)
    stub["download"].assert_called_once_with("77")


# ---- idempotency / state gates -------------------------------------------


def test_already_sent_is_skipped(stub):
    stub["get_row"].return_value = _row(**{wsr_review.COL_SEND_STATUS: wsr_review.STATUS_SENT})
    result = weekly_send.send_one_row(50, weekly_send.CONFIG)
    assert result.status == "skipped_already_sent"
    stub["send_mail"].assert_not_called()


def test_held_row_is_skipped(stub):
    stub["get_row"].return_value = _row(**{wsr_review.COL_SEND_STATUS: wsr_review.STATUS_HELD})
    result = weekly_send.send_one_row(50, weekly_send.CONFIG)
    assert result.status == "skipped_held"
    stub["send_mail"].assert_not_called()


def test_row_not_found(stub):
    from shared.smartsheet_client import SmartsheetNotFoundError
    stub["get_row"].side_effect = SmartsheetNotFoundError("gone")
    assert weekly_send.send_one_row(50, weekly_send.CONFIG).status == "row_not_found"


# ---- HELD refusals (never send a half-formed packet) ----------------------


def test_unknown_job_is_held(stub):
    stub["get_job"].return_value = None
    result = weekly_send.send_one_row(50, weekly_send.CONFIG)
    assert result.status == "held_no_recipient"
    stub["send_mail"].assert_not_called()
    assert stub["update_rows"].call_args.args[1][0][wsr_review.COL_SEND_STATUS] == wsr_review.STATUS_HELD
    # Never-silent: the HELD also surfaces via recipient_health (Review-Queue + dedupe-gated alert).
    stub["recipient_health"].assert_called_once()
    assert stub["recipient_health"].call_args.kwargs["config_workstream"] == "safety_reports"


def test_empty_to_contact_is_held(stub):
    stub["get_job"].return_value = _job(safety_reports_contact_email="")
    result = weekly_send.send_one_row(50, weekly_send.CONFIG)
    assert result.status == "held_no_recipient"
    stub["send_mail"].assert_not_called()
    stub["recipient_health"].assert_called_once()


def test_missing_compiled_pdf_is_held(stub):
    stub["get_row"].return_value = _row(**{wsr_review.COL_COMPILED_PDF: ""})
    result = weekly_send.send_one_row(50, weekly_send.CONFIG)
    assert result.status == "held_missing_pdf"
    stub["send_mail"].assert_not_called()
    stub["download"].assert_not_called()


# ---- transient failures → FAILED + retry ----------------------------------


def test_box_download_failure_is_failed_not_held(stub):
    stub["download"].side_effect = weekly_send.box_client.BoxError("503")
    result = weekly_send.send_one_row(50, weekly_send.CONFIG)
    assert result.status == "send_failed"
    stub["send_mail"].assert_not_called()
    assert stub["update_rows"].call_args.args[1][0][wsr_review.COL_SEND_STATUS] == wsr_review.STATUS_FAILED


def test_graph_error_marks_failed_and_increments_retry(stub):
    stub["get_row"].return_value = _row(**{wsr_review.COL_NOTES: "[SEND_RETRY_COUNT: 1]"})
    stub["send_mail"].side_effect = GraphError("transient 500")
    result = weekly_send.send_one_row(50, weekly_send.CONFIG)
    assert result.status == "send_failed"
    notes = stub["update_rows"].call_args.args[1][0][wsr_review.COL_NOTES]
    assert "[SEND_RETRY_COUNT: 2]" in notes


def test_graph_error_at_max_retries_critical(stub):
    stub["get_row"].return_value = _row(**{wsr_review.COL_NOTES: f"[SEND_RETRY_COUNT: {weekly_send.MAX_SEND_RETRIES - 1}]"})
    stub["send_mail"].side_effect = GraphError("still failing")
    weekly_send.send_one_row(50, weekly_send.CONFIG)
    crits = [c for c in stub["log"].call_args_list if c.kwargs.get("error_code") == "weekly_send.retries_exhausted"]
    assert crits


def test_graph_auth_error_critical_and_failed(stub):
    stub["send_mail"].side_effect = GraphAuthError("401")
    result = weekly_send.send_one_row(50, weekly_send.CONFIG)
    assert result.status == "send_failed"
    crits = [c for c in stub["log"].call_args_list if c.kwargs.get("error_code") == "weekly_send.graph_auth_failed"]
    assert crits


# ---- write-ahead marker: post-send stamp failure must NOT double-send ------


def test_post_send_stamp_failure_leaves_row_sending_no_double_send(stub):
    from shared.smartsheet_client import SmartsheetError
    # SENDING marker write succeeds (call 1); the post-send SENT-stamp fails (call 2).
    stub["update_rows"].side_effect = [None, SmartsheetError("update failed after send")]
    result = weekly_send.send_one_row(50, weekly_send.CONFIG)
    assert result.status == "sent"  # the send DID fire
    stub["send_mail"].assert_called_once()
    crits = [c for c in stub["log"].call_args_list if c.kwargs.get("error_code") == "weekly_send.post_send_row_update_failed"]
    assert crits
    # The row is LEFT in SENDING (the write-ahead marker) — NOT a dispatch candidate, so
    # the poller never re-sends it. The first (successful) update_rows call set SENDING.
    calls = stub["update_rows"].call_args_list
    assert calls[0].args[1][0][wsr_review.COL_SEND_STATUS] == wsr_review.STATUS_SENDING


def test_pre_send_marker_failure_aborts_without_sending(stub):
    from shared.smartsheet_client import SmartsheetError
    # The SENDING-marker write (the FIRST update_rows call) fails → we must NOT send.
    stub["update_rows"].side_effect = SmartsheetError("smartsheet down before send")
    result = weekly_send.send_one_row(50, weekly_send.CONFIG)
    assert result.status == "send_failed"
    stub["send_mail"].assert_not_called()  # nothing irreversible happened — fail toward not-sending
    warns = [c for c in stub["log"].call_args_list if c.kwargs.get("error_code") == "weekly_send.pre_send_marker_failed"]
    assert warns


# ---- resolved recipients are logged --------------------------------------


def test_resolved_recipients_logged(stub):
    weekly_send.send_one_row(50, weekly_send.CONFIG)
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
    assert weekly_send.send_one_row(50, weekly_send.CONFIG).status == "held_no_recipient"
    stub["get_job"].return_value = _job()
    stub["get_row"].return_value = _row(**{wsr_review.COL_COMPILED_PDF: ""})
    assert weekly_send.send_one_row(50, weekly_send.CONFIG).status == "held_missing_pdf"


# ---- PR-3: inline ≤2.5 MB / upload-session >2.5 MB transport switch --------


def test_small_packet_sends_inline_not_upload_session(stub):
    # A small (default fixture) packet → inline send_mail; the upload-session path
    # is NOT taken.
    result = weekly_send.send_one_row(50, weekly_send.CONFIG)
    assert result.status == "sent"
    stub["send_mail"].assert_called_once()
    stub["send_large"].assert_not_called()


def test_large_packet_uses_upload_session_not_inline(stub):
    # A packet over the 2.5 MB threshold → upload-session path; inline NOT taken.
    big = b"\x00" * (weekly_send.UPLOAD_SESSION_THRESHOLD_BYTES + 1)
    stub["download"].return_value = big
    result = weekly_send.send_one_row(50, weekly_send.CONFIG)
    assert result.status == "sent"
    stub["send_large"].assert_called_once()
    stub["send_mail"].assert_not_called()
    kw = stub["send_large"].call_args.kwargs
    # Recipients resolved from active_jobs, same as the inline path; raw bytes passed.
    assert kw["to"] == ["pm@evergreenmirror.com"]
    assert kw["cc"] == ["cc1@x.com", "cc2@x.com"]
    assert kw["attachment_bytes"] == big
    assert kw["attachment_content_type"] == "application/pdf"
    assert kw["body"] == "Good morning Dana — packet attached."
    # The write-ahead SENDING marker still precedes the send; SENT follows.
    calls = stub["update_rows"].call_args_list
    assert calls[0].args[1][0][wsr_review.COL_SEND_STATUS] == wsr_review.STATUS_SENDING
    assert calls[-1].args[1][0][wsr_review.COL_SEND_STATUS] == wsr_review.STATUS_SENT


def test_threshold_boundary_inclusive_is_inline(stub):
    # Exactly at the threshold (not strictly greater) → still inline.
    stub["download"].return_value = b"\x00" * weekly_send.UPLOAD_SESSION_THRESHOLD_BYTES
    weekly_send.send_one_row(50, weekly_send.CONFIG)
    stub["send_mail"].assert_called_once()
    stub["send_large"].assert_not_called()


def test_oversized_packet_is_held_without_sending(stub, mocker):
    # A packet over Graph's 150 MB upload-session ceiling → HELD; neither send fires,
    # and the row is never flipped to SENDING. Constants monkeypatched small to avoid
    # allocating 150 MB of fixture bytes.
    mocker.patch.object(weekly_send.graph_client, "UPLOAD_SESSION_MAX_BYTES", 100)
    mocker.patch.object(weekly_send, "UPLOAD_SESSION_THRESHOLD_BYTES", 10)
    stub["download"].return_value = b"\x00" * 200  # > 100
    result = weekly_send.send_one_row(50, weekly_send.CONFIG)
    assert result.status == "held_oversized_packet"
    stub["send_mail"].assert_not_called()
    stub["send_large"].assert_not_called()
    # The single status write is HELD — never SENDING (refused before the marker).
    statuses = [c.args[1][0].get(wsr_review.COL_SEND_STATUS) for c in stub["update_rows"].call_args_list]
    assert wsr_review.STATUS_SENDING not in statuses
    assert stub["update_rows"].call_args.args[1][0][wsr_review.COL_SEND_STATUS] == wsr_review.STATUS_HELD


def test_upload_session_too_large_error_is_held(stub, mocker):
    # Belt-and-suspenders: if the layer below raises GraphAttachmentTooLargeError
    # (constants drifted), weekly_send HELDs rather than retrying forever.
    from shared.graph_client import GraphAttachmentTooLargeError
    big = b"\x00" * (weekly_send.UPLOAD_SESSION_THRESHOLD_BYTES + 1)
    stub["download"].return_value = big
    stub["send_large"].side_effect = GraphAttachmentTooLargeError("too big at the wire")
    result = weekly_send.send_one_row(50, weekly_send.CONFIG)
    assert result.status == "held_oversized_packet"
    assert stub["update_rows"].call_args.args[1][0][wsr_review.COL_SEND_STATUS] == wsr_review.STATUS_HELD


def test_large_packet_graph_error_retries(stub):
    # The upload-session path shares the FAILED/retry fence with the inline path.
    big = b"\x00" * (weekly_send.UPLOAD_SESSION_THRESHOLD_BYTES + 1)
    stub["download"].return_value = big
    stub["send_large"].side_effect = GraphError("transient 503 mid-upload")
    result = weekly_send.send_one_row(50, weekly_send.CONFIG)
    assert result.status == "send_failed"
    assert stub["update_rows"].call_args.args[1][0][wsr_review.COL_SEND_STATUS] == wsr_review.STATUS_FAILED


# ---- P1b: cross-workstream contamination guard (prove-the-control-bites) ----


def test_present_matching_workstream_sends(stub):
    # Default fixture is tagged `safety`; the safety CONFIG matches → normal send.
    result = weekly_send.send_one_row(50, weekly_send.CONFIG)
    assert result.status == "sent"
    stub["send_mail"].assert_called_once()


def test_present_mismatch_hard_held_critical(stub):
    # A row tagged for a DIFFERENT workstream → HELD, a CRITICAL Send-Gate event, NO send.
    stub["get_row"].return_value = _row(**{wsr_review.COL_WORKSTREAM: "progress"})
    result = weekly_send.send_one_row(50, weekly_send.CONFIG)
    assert result.status == "held_workstream_mismatch"
    stub["send_mail"].assert_not_called()
    stub["send_large"].assert_not_called()
    crits = [c for c in stub["log"].call_args_list if c.kwargs.get("error_code") == "weekly_send.workstream_mismatch"]
    assert crits and crits[0].args[0] == weekly_send.Severity.CRITICAL
    assert stub["update_rows"].call_args.args[1][0][wsr_review.COL_SEND_STATUS] == wsr_review.STATUS_HELD


def test_mismatch_never_writes_sending_marker(stub):
    # The guard sits BEFORE the write-ahead SENDING marker — a contaminated row never
    # enters the in-flight state. The ONLY status write is HELD; SENDING never appears.
    stub["get_row"].return_value = _row(**{wsr_review.COL_WORKSTREAM: "progress"})
    weekly_send.send_one_row(50, weekly_send.CONFIG)
    statuses = [c.args[1][0].get(wsr_review.COL_SEND_STATUS) for c in stub["update_rows"].call_args_list]
    assert wsr_review.STATUS_SENDING not in statuses
    assert statuses == [wsr_review.STATUS_HELD]


def test_absent_workstream_matches_with_warn(stub):
    # A pre-backfill row (blank Workstream) sends, with a WARN — never a CRITICAL.
    stub["get_row"].return_value = _row(**{wsr_review.COL_WORKSTREAM: ""})
    result = weekly_send.send_one_row(50, weekly_send.CONFIG)
    assert result.status == "sent"
    stub["send_mail"].assert_called_once()
    warns = [c for c in stub["log"].call_args_list if c.kwargs.get("error_code") == "weekly_send.workstream_absent"]
    assert warns
    crits = [c for c in stub["log"].call_args_list if c.kwargs.get("error_code") == "weekly_send.workstream_mismatch"]
    assert not crits


def test_sent_row_with_mismatch_is_still_skipped(stub):
    # The guard sits AFTER the SENT skip gate — a terminal SENT row is never rewritten,
    # even with a (stale) mismatched tag. No CRITICAL, no write.
    stub["get_row"].return_value = _row(**{
        wsr_review.COL_SEND_STATUS: wsr_review.STATUS_SENT,
        wsr_review.COL_WORKSTREAM: "progress",
    })
    result = weekly_send.send_one_row(50, weekly_send.CONFIG)
    assert result.status == "skipped_already_sent"
    stub["send_mail"].assert_not_called()
    stub["update_rows"].assert_not_called()


def test_case_variant_workstream_is_mismatch(stub):
    # Exact-match, case-sensitive (PICKLIST-controlled vocabulary): "Safety" != "safety".
    stub["get_row"].return_value = _row(**{wsr_review.COL_WORKSTREAM: "Safety"})
    result = weekly_send.send_one_row(50, weekly_send.CONFIG)
    assert result.status == "held_workstream_mismatch"
    stub["send_mail"].assert_not_called()


def test_send_config_has_no_unset_fields():
    # The contamination property: every REQUIRED SendConfig field is explicitly bound (no
    # silent default that could let a new workstream inherit a safety value). The OPTIONAL
    # `extra_attachments` seam (RFQ R3) is intentionally None for the single-attachment
    # bindings — that is its correct, non-contaminating default, so it is excluded here.
    import dataclasses
    for f in dataclasses.fields(weekly_send.CONFIG):
        if f.name == "extra_attachments":
            assert weekly_send.CONFIG.extra_attachments is None  # single-attachment binding
            continue
        assert getattr(weekly_send.CONFIG, f.name) not in (None, ""), f"CONFIG.{f.name} is unset"


def test_workstream_vocabulary_is_pinned():
    # The single `safety` literal MUST be identical across CONFIG.workstream_tag, the
    # add_wsr_row seed default, and the REGISTRY set. A drift silently routes every row to
    # the absent-WARN path (or blocks the write) — the vocabulary-pin guard.
    import inspect

    from shared import picklist_validation, sheet_ids
    tag = weekly_send.CONFIG.workstream_tag
    assert tag == "safety"
    assert inspect.signature(wsr_review.add_wsr_row).parameters["workstream"].default == tag
    assert picklist_validation.REGISTRY[sheet_ids.SHEET_WSR_HUMAN_REVIEW]["Workstream"] == frozenset({tag})


def test_whitespace_only_workstream_is_malformed_held(stub):
    # A NON-empty cell that STRIPS to empty (U+00A0 no-break space) must NOT take the
    # absent→WARN→proceed path — it is MALFORMED → HARD-HELD + CRITICAL + NO send (closes
    # the str().strip()-collapses-to-absent evasion).
    stub["get_row"].return_value = _row(**{wsr_review.COL_WORKSTREAM: "\xa0"})
    result = weekly_send.send_one_row(50, weekly_send.CONFIG)
    assert result.status == "held_workstream_mismatch"
    stub["send_mail"].assert_not_called()
    stub["send_large"].assert_not_called()
    crits = [c for c in stub["log"].call_args_list if c.kwargs.get("error_code") == "weekly_send.workstream_mismatch"]
    assert crits and crits[0].args[0] == weekly_send.Severity.CRITICAL
    # It did NOT take the back-compat absent path.
    warns = [c for c in stub["log"].call_args_list if c.kwargs.get("error_code") == "weekly_send.workstream_absent"]
    assert not warns
    assert stub["update_rows"].call_args.args[1][0][wsr_review.COL_SEND_STATUS] == wsr_review.STATUS_HELD


# ---- Growth Slice 4b: packet-size early warning (~100 MB, pre-HELD) --------
#
# A packet over defaults.PACKET_SIZE_WARN_BYTES (but under Graph's 150 MB
# ceiling) still SENDS via the upload-session path, and additionally lands a
# WARN record (error_code weekly_send.packet_size_warn) pointing at the manual
# packet-split runbook — the 150 MB HELD wall is forecast, not discovered on
# Friday. HELD semantics above the ceiling are untouched. Constants are
# monkeypatched small to avoid 100 MB fixtures.


def _packet_warn_calls(stub):
    return [
        c for c in stub["log"].call_args_list
        if c.kwargs.get("error_code") == "weekly_send.packet_size_warn"
    ]


def test_packet_over_warn_threshold_warns_and_still_sends(stub, mocker):
    # (The inline/upload-session transport switch reads the threshold BOUND
    # into CONFIG at import time, so this 200-byte packet takes the inline
    # path — the transport choice is orthogonal to the early warning.)
    mocker.patch.object(weekly_send.defaults, "PACKET_SIZE_WARN_BYTES", 100)
    mocker.patch.object(weekly_send.graph_client, "UPLOAD_SESSION_MAX_BYTES", 1000)
    stub["download"].return_value = b"\x00" * 200  # warn < 200 ≤ ceiling

    result = weekly_send.send_one_row(50, weekly_send.CONFIG)

    assert result.status == "sent"  # ← the WARN never blocks the send
    stub["send_mail"].assert_called_once()
    warns = _packet_warn_calls(stub)
    assert len(warns) == 1
    assert warns[0].args[0] is weekly_send.Severity.WARN
    message = warns[0].args[2]
    assert "docs/runbooks/safety_weekly_send.md" in message
    assert "200 bytes" in message


def test_packet_under_warn_threshold_no_warning(stub, mocker):
    mocker.patch.object(weekly_send.defaults, "PACKET_SIZE_WARN_BYTES", 100)
    stub["download"].return_value = b"\x00" * 50

    result = weekly_send.send_one_row(50, weekly_send.CONFIG)

    assert result.status == "sent"
    assert _packet_warn_calls(stub) == []


def test_packet_over_ceiling_helds_without_the_extra_warn(stub, mocker):
    # HELD semantics identical to pre-slice: over 150 MB → held BEFORE the
    # SENDING marker; the early-warning branch is unreachable (already
    # returned), so exactly one loud signal fires — the HELD itself.
    mocker.patch.object(weekly_send.defaults, "PACKET_SIZE_WARN_BYTES", 100)
    mocker.patch.object(weekly_send.graph_client, "UPLOAD_SESSION_MAX_BYTES", 150)
    mocker.patch.object(weekly_send, "UPLOAD_SESSION_THRESHOLD_BYTES", 10)
    stub["download"].return_value = b"\x00" * 200  # > ceiling

    result = weekly_send.send_one_row(50, weekly_send.CONFIG)

    assert result.status == "held_oversized_packet"
    stub["send_mail"].assert_not_called()
    stub["send_large"].assert_not_called()
    assert _packet_warn_calls(stub) == []
    statuses = [
        c.args[1][0].get(wsr_review.COL_SEND_STATUS)
        for c in stub["update_rows"].call_args_list
    ]
    assert wsr_review.STATUS_SENDING not in statuses


def test_packet_warn_threshold_boundary_exact_size_no_warning(stub, mocker):
    # Strictly-greater comparison: exactly AT the threshold does not warn
    # (mirrors the upload-session threshold's strict `>` convention).
    mocker.patch.object(weekly_send.defaults, "PACKET_SIZE_WARN_BYTES", 100)
    stub["download"].return_value = b"\x00" * 100

    weekly_send.send_one_row(50, weekly_send.CONFIG)

    assert _packet_warn_calls(stub) == []


# ---- _attachment_content_type (SC-S4: filename-derived, was hardcoded application/pdf) ----
# The ONLY engine change SC-S4 needs. These pin that the three PDF workstreams are BYTE-
# IDENTICAL to the old hardcode (.pdf → application/pdf) while .zip → application/zip.


def test_attachment_content_type_pdf_unchanged_for_pdf_workstreams():
    from safety_reports import weekly_send

    # safety/progress/PO all attach a .pdf — must resolve exactly as the old hardcode.
    assert weekly_send._attachment_content_type("Weekly Safety Report — 2026-07-09.pdf") == "application/pdf"
    assert weekly_send._attachment_content_type("2026.001 — Job_PO_2026.001.2.0.0.pdf") == "application/pdf"
    assert weekly_send._attachment_content_type("X.PDF") == "application/pdf"  # case-insensitive


def test_attachment_content_type_zip_for_subcontract_package():
    from safety_reports import weekly_send

    assert weekly_send._attachment_content_type("Job_Subcontract Package_2026.001.OR.0.0.zip") == "application/zip"
    assert weekly_send._attachment_content_type("X.ZIP") == "application/zip"


def test_attachment_content_type_unknown_falls_back_to_octet_stream():
    from safety_reports import weekly_send

    assert weekly_send._attachment_content_type("mystery.qqq") == "application/octet-stream"
