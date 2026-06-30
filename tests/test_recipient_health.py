"""Tests for shared/recipient_health.py — the §3.1 record-leg for an unhealthy send recipient.

Pins: the Review-Queue row is a RECORD (always attempted, never push-deduped) tagged with the
caller's workstream; it is idempotent on OPEN-row state (a flapping re-HELD doesn't duplicate the
row, but a genuinely new incident is never swallowed); and every leg is fail-soft (a get_pending
/ add failure never propagates — the caller has already HELD the row).
"""
from __future__ import annotations

import json

import pytest

from shared import recipient_health


@pytest.fixture
def stub(mocker):
    return {
        # Default: no existing open rows → not idempotent-suppressed → the add fires.
        "get_pending": mocker.patch.object(recipient_health.review_queue, "get_pending", return_value=[]),
        "add": mocker.patch.object(recipient_health.review_queue, "add", return_value=123),
    }


def _call(**kw):
    base = dict(
        config_workstream="progress_reports",
        script_name="progress_reports.progress_send",
        row_id=70,
        job_id="JOB-9",
        project_name="Solar Ridge",
        reason_detail="empty/invalid contact (TO) for job JOB-9",
    )
    base.update(kw)
    recipient_health.report_unhealthy_recipient(**base)


def _open_row(*, row_id, source="recipient_health"):
    return {"Item ID": "x", "Payload": json.dumps({"row_id": row_id, "source": source})}


# ---- the record leg (always-write, workstream-tagged) --------------------


def test_files_review_queue_row_tagged_with_caller_workstream(stub):
    _call(config_workstream="progress_reports")
    stub["add"].assert_called_once()
    assert stub["add"].call_args.kwargs["workstream"] == "progress_reports"
    payload = stub["add"].call_args.kwargs["payload"]
    assert payload["job_id"] == "JOB-9" and payload["row_id"] == 70
    assert payload["source"] == "recipient_health"  # so the idempotency check recognises it


def test_safety_workstream_tag_passthrough(stub):
    _call(config_workstream="safety_reports")
    assert stub["add"].call_args.kwargs["workstream"] == "safety_reports"


# ---- record idempotency (open-row state, NOT push-dedup) ------------------


def test_skips_add_when_open_row_already_tracks_this_incident(stub):
    stub["get_pending"].return_value = [_open_row(row_id=70)]
    _call(row_id=70)
    stub["add"].assert_not_called()  # an open record already tracks it — no duplicate


def test_adds_when_open_row_is_for_a_different_incident(stub):
    # An open row for a DIFFERENT row_id, or one not from recipient_health, must not suppress.
    stub["get_pending"].return_value = [
        _open_row(row_id=999),
        _open_row(row_id=70, source="some_other_source"),
    ]
    _call(row_id=70)
    stub["add"].assert_called_once()


def test_malformed_or_missing_payload_row_does_not_crash_idempotency(stub):
    # A pending row with a non-JSON / empty Payload must be skipped by the parse (not crash),
    # so the new incident still gets its record. Fail-soft parse robustness.
    stub["get_pending"].return_value = [
        {"Item ID": "a", "Payload": "{not valid json"},
        {"Item ID": "b", "Payload": ""},
        {"Item ID": "c"},  # no Payload key at all
    ]
    _call(row_id=70)
    stub["add"].assert_called_once()


# ---- fail-soft (never raises) --------------------------------------------


def test_get_pending_failure_falls_through_to_add(stub):
    # A read failure must fail TOWARD surfacing (a possible duplicate beats a swallowed record).
    stub["get_pending"].side_effect = RuntimeError("smartsheet down")
    _call()  # must not raise
    stub["add"].assert_called_once()


def test_add_failure_never_raises(stub):
    stub["add"].side_effect = RuntimeError("smartsheet down")
    _call()  # must not raise (the caller has already HELD)


def test_total_failure_never_raises(stub):
    stub["get_pending"].side_effect = RuntimeError("read boom")
    stub["add"].side_effect = RuntimeError("write boom")
    _call()  # the send-path HELD already happened; this surface must never crash the caller
