"""Tests for shared/recipient_health.py — the loud-surface for an unhealthy send recipient.

Pins: the Review-Queue row is tagged with the caller's workstream; the alert + row are
dedupe-gated (one per window, not one per poll cycle); and every leg is fail-soft (a
Review-Queue / alert / dedupe failure never propagates — the caller has already HELD the row).
"""
from __future__ import annotations

import pytest

from shared import recipient_health


@pytest.fixture
def stub(mocker):
    return {
        "should_fire": mocker.patch.object(recipient_health.alert_dedupe, "should_fire", return_value=True),
        "record_fire": mocker.patch.object(recipient_health.alert_dedupe, "record_fire"),
        "add": mocker.patch.object(recipient_health.review_queue, "add", return_value=123),
        "log": mocker.patch.object(recipient_health.error_log, "log"),
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


# ---- happy surface -------------------------------------------------------


def test_files_review_queue_row_tagged_with_caller_workstream(stub):
    _call(config_workstream="progress_reports")
    stub["add"].assert_called_once()
    assert stub["add"].call_args.kwargs["workstream"] == "progress_reports"
    # The payload carries the routing keys the operator needs.
    payload = stub["add"].call_args.kwargs["payload"]
    assert payload["job_id"] == "JOB-9" and payload["row_id"] == 70


def test_fires_a_dedupe_gated_alert_then_opens_the_window(stub):
    _call()
    assert any(
        c.kwargs.get("error_code") == "recipient_health.unhealthy_recipient"
        for c in stub["log"].call_args_list
    )
    stub["record_fire"].assert_called_once()


def test_safety_workstream_tag_passthrough(stub):
    _call(config_workstream="safety_reports")
    assert stub["add"].call_args.kwargs["workstream"] == "safety_reports"


# ---- dedupe gate ---------------------------------------------------------


def test_suppressed_within_window_files_nothing(stub):
    stub["should_fire"].return_value = False
    _call()
    stub["add"].assert_not_called()
    stub["log"].assert_not_called()
    stub["record_fire"].assert_not_called()


# ---- fail-soft (never raises) --------------------------------------------


def test_review_queue_failure_does_not_block_the_alert(stub):
    stub["add"].side_effect = RuntimeError("smartsheet down")
    _call()  # must not raise
    # The alert leg still fired despite the Review-Queue write failing.
    assert any(
        c.kwargs.get("error_code") == "recipient_health.unhealthy_recipient"
        for c in stub["log"].call_args_list
    )
    stub["record_fire"].assert_called_once()


def test_total_failure_never_raises(stub):
    stub["should_fire"].side_effect = RuntimeError("dedupe state corrupt")
    stub["add"].side_effect = RuntimeError("smartsheet down")
    stub["log"].side_effect = RuntimeError("log down")
    stub["record_fire"].side_effect = RuntimeError("record down")
    # The send-path HELD has already happened; this surface must never crash the caller.
    _call()
