"""Unit tests for safety_reports/weekly_send_poll.py.

All external services mocked. Tests exercise the poller's filter +
dispatch + heartbeat + watchdog-marker behavior.

Structure mirrors tests/test_intake_poll.py and tests/test_weekly_send.py.
"""
from __future__ import annotations

import json
from typing import Any

import pytest

from safety_reports import weekly_send, weekly_send_poll
from safety_reports.weekly_send import SendResult
from safety_reports.weekly_send_poll import (
    DAEMON_NAME,
    _filter_dispatch_candidates,
    _poll_inside_lock,
    poll_once,
)
from shared.smartsheet_client import SmartsheetError, SmartsheetNotFoundError


def _row(
    *,
    row_id: int,
    job: str = "Bradley 1",
    approved: bool = True,
    send_status: str = weekly_send.STATUS_PENDING,
    notes: str = "",
) -> dict[str, Any]:
    return {
        "_row_id": row_id,
        "Job": job,
        "Week": "2026-05-18",
        "Approved for Send": approved,
        "Send Status": send_status,
        "Notes": notes,
        "Recipients": json.dumps(["seths@evergreenmirror.com"]),
        "Draft Body": "draft",
    }


@pytest.fixture
def _patch_all(mocker):
    """Default mock surface covering the entire poll cycle."""
    mocks = {
        "get_rows": mocker.patch(
            "safety_reports.weekly_send_poll.smartsheet_client.get_rows",
            return_value=[],
        ),
        "send_one_row": mocker.patch(
            "safety_reports.weekly_send_poll.weekly_send.send_one_row",
            return_value=SendResult(
                status="sent", row_id=0, project_name="Bradley 1"
            ),
        ),
        "get_setting": mocker.patch(
            "safety_reports.weekly_send_poll.smartsheet_client.get_setting",
            side_effect=SmartsheetNotFoundError("default test stub"),
        ),
        "write_heartbeat": mocker.patch(
            "safety_reports.weekly_send_poll._write_heartbeat",
            return_value=None,
        ),
        "write_heartbeat_row": mocker.patch(
            "safety_reports.weekly_send_poll._write_heartbeat_row",
            return_value=None,
        ),
        "write_watchdog_marker": mocker.patch(
            "safety_reports.weekly_send_poll._write_watchdog_marker",
            return_value=None,
        ),
        "error_log": mocker.patch(
            "safety_reports.weekly_send_poll.error_log.log",
            return_value=None,
        ),
    }
    return mocks


# ---- _filter_dispatch_candidates ----------------------------------------


def test_filter_only_approved_rows():
    rows = [
        _row(row_id=1, approved=True),
        _row(row_id=2, approved=False),
    ]
    out = _filter_dispatch_candidates(rows)
    assert [r["_row_id"] for r in out] == [1]


def test_filter_skips_sent_rows():
    rows = [
        _row(row_id=1, send_status=weekly_send.STATUS_SENT),
        _row(row_id=2, send_status=weekly_send.STATUS_PENDING),
    ]
    out = _filter_dispatch_candidates(rows)
    assert [r["_row_id"] for r in out] == [2]


def test_filter_includes_failed_under_retry_cap():
    rows = [
        _row(
            row_id=1,
            send_status=weekly_send.STATUS_FAILED,
            notes="[SEND_RETRY_COUNT: 1]",
        ),
    ]
    out = _filter_dispatch_candidates(rows)
    assert [r["_row_id"] for r in out] == [1]


def test_filter_skips_terminally_failed_rows():
    """Rows at MAX_SEND_RETRIES are filtered out."""
    rows = [
        _row(
            row_id=1,
            send_status=weekly_send.STATUS_FAILED,
            notes=f"[SEND_RETRY_COUNT: {weekly_send.MAX_SEND_RETRIES}]",
        ),
    ]
    out = _filter_dispatch_candidates(rows)
    assert out == []


# ---- _poll_inside_lock dispatch behavior -------------------------------


def test_poll_once_dispatches_each_approved_pending_row(_patch_all):
    """3 approved PENDING + 1 unapproved + 1 SENT → 3 dispatches."""
    rows = [
        _row(row_id=10),
        _row(row_id=11),
        _row(row_id=12, approved=False),
        _row(row_id=13, send_status=weekly_send.STATUS_SENT),
        _row(row_id=14),
    ]
    _patch_all["get_rows"].return_value = rows
    result = _poll_inside_lock()
    assert _patch_all["send_one_row"].call_count == 3
    dispatched_ids = sorted(
        call.args[0] for call in _patch_all["send_one_row"].call_args_list
    )
    assert dispatched_ids == [10, 11, 14]
    assert result.dispatched == 3
    assert result.sent == 3
    assert result.skipped == 0
    assert result.failed == 0


def test_poll_once_skips_terminally_failed_rows(_patch_all):
    rows = [
        _row(
            row_id=20,
            send_status=weekly_send.STATUS_FAILED,
            notes=f"[SEND_RETRY_COUNT: {weekly_send.MAX_SEND_RETRIES}]",
        ),
        _row(row_id=21),
    ]
    _patch_all["get_rows"].return_value = rows
    _poll_inside_lock()
    assert _patch_all["send_one_row"].call_count == 1
    dispatched_id = _patch_all["send_one_row"].call_args.args[0]
    assert dispatched_id == 21


def test_poll_once_per_row_fence_continues_after_error(_patch_all):
    """One row's SmartsheetError doesn't kill the cycle."""
    rows = [_row(row_id=30), _row(row_id=31), _row(row_id=32)]
    _patch_all["get_rows"].return_value = rows
    call_order = []

    def _side_effect(row_id):
        call_order.append(row_id)
        if row_id == 31:
            raise SmartsheetError("transient")
        return SendResult(status="sent", row_id=row_id)

    _patch_all["send_one_row"].side_effect = _side_effect
    result = _poll_inside_lock()
    assert call_order == [30, 31, 32]
    assert result.dispatched == 3
    assert result.errors == 1
    assert result.sent == 2


def test_poll_once_skip_counters_split_correctly(_patch_all):
    """Mix of sent + skipped + send_failed outcomes counted separately."""
    rows = [_row(row_id=40), _row(row_id=41), _row(row_id=42)]
    _patch_all["get_rows"].return_value = rows
    outcomes = [
        SendResult(status="sent", row_id=40),
        SendResult(status="skipped_no_recipients", row_id=41),
        SendResult(status="send_failed", row_id=42, error="GraphError"),
    ]
    _patch_all["send_one_row"].side_effect = outcomes
    result = _poll_inside_lock()
    assert result.sent == 1
    assert result.skipped == 1
    assert result.failed == 1


# ---- Heartbeat behavior --------------------------------------------------


def test_poll_once_writes_heartbeat_file(_patch_all):
    _poll_inside_lock()
    _patch_all["write_heartbeat"].assert_called_once()


def test_poll_once_writes_heartbeat_row_with_ok_status(_patch_all):
    _poll_inside_lock()
    _patch_all["write_heartbeat_row"].assert_called_once()
    kwargs = _patch_all["write_heartbeat_row"].call_args.kwargs
    assert kwargs["status"] == "OK"


def test_poll_once_writes_heartbeat_row_with_warn_on_failure(_patch_all):
    """Send failures (not exceptions) → WARN status."""
    _patch_all["get_rows"].return_value = [_row(row_id=50)]
    _patch_all["send_one_row"].return_value = SendResult(
        status="send_failed", row_id=50, error="GraphError"
    )
    _poll_inside_lock()
    kwargs = _patch_all["write_heartbeat_row"].call_args.kwargs
    assert kwargs["status"] == "WARN"


def test_poll_once_writes_heartbeat_row_with_degraded_on_exception(_patch_all):
    """Per-row exception → DEGRADED status (errors counter > 0)."""
    _patch_all["get_rows"].return_value = [_row(row_id=60)]
    _patch_all["send_one_row"].side_effect = SmartsheetError("boom")
    _poll_inside_lock()
    kwargs = _patch_all["write_heartbeat_row"].call_args.kwargs
    assert kwargs["status"] == "DEGRADED"


def test_poll_once_writes_watchdog_marker(_patch_all):
    _poll_inside_lock()
    _patch_all["write_watchdog_marker"].assert_called_once()


# ---- poll_once outer behavior (kill switch + lock) ---------------------


def test_poll_once_skipped_when_polling_disabled(_patch_all):
    """ITS_Config polling_enabled=false → skipped_disabled, no work done."""
    _patch_all["get_setting"].side_effect = None
    _patch_all["get_setting"].return_value = "false"
    result = poll_once()
    assert result.skipped_disabled is True
    _patch_all["get_rows"].assert_not_called()


def test_poll_once_returns_stats_on_empty_sheet(_patch_all):
    """Empty WPR_Pending_Review → no dispatches, OK heartbeat."""
    _patch_all["get_rows"].return_value = []
    result = poll_once()
    assert result.dispatched == 0
    assert result.rows_scanned == 0
    _patch_all["write_heartbeat"].assert_called_once()


# ---- Smartsheet read failure handling -----------------------------------


def test_poll_once_handles_get_rows_failure(_patch_all):
    """Smartsheet unreachable → ERROR heartbeat + watchdog marker still written."""
    _patch_all["get_rows"].side_effect = SmartsheetError("HTTP 500")
    result = poll_once()
    assert result.errors == 1
    _patch_all["write_heartbeat_row"].assert_called_once()
    kwargs = _patch_all["write_heartbeat_row"].call_args.kwargs
    assert kwargs["status"] == "ERROR"
    _patch_all["write_watchdog_marker"].assert_called_once()


# ---- Daemon name + module wiring ----------------------------------------


def test_daemon_name_is_stable():
    """Stable identifier; regression-protect against accidental rename."""
    assert DAEMON_NAME == "safety_reports.weekly_send_poll"


def test_watchdog_job_slug_matches_watchdog_tracked_jobs():
    """The marker slug must match what scripts/watchdog.py expects."""
    import sys
    from pathlib import Path

    scripts_dir = Path(__file__).resolve().parent.parent / "scripts"
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    import watchdog  # noqa: E402 — sys.path-driven import matching tests/test_watchdog.py

    assert weekly_send_poll.WATCHDOG_JOB_SLUG in watchdog.TRACKED_JOBS
