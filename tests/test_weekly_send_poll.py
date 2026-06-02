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
from shared.approval_verification import ApprovalVerdict, VerdictReason
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
        # F22: default to a VERIFIED approval so the existing dispatch tests
        # exercise the send path unchanged. Tests that assert the block path
        # override this return_value / side_effect.
        "verify_approval": mocker.patch(
            "safety_reports.weekly_send_poll.approval_verification.verify_approval",
            return_value=ApprovalVerdict(
                verified=True,
                reason=VerdictReason.AUTHORIZED,
                actor="seths@evergreenmirror.com",
            ),
        ),
        "alert_critical": mocker.patch(
            "safety_reports.weekly_send_poll.error_log._alert_critical",
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


# ---- F22 approval-attestation gate (poller wiring) ----------------------


def test_poll_blocks_unverified_approval(_patch_all):
    """A row whose approval fails attestation is NOT dispatched (fail-closed)."""
    _patch_all["get_rows"].return_value = [_row(row_id=70)]
    _patch_all["verify_approval"].return_value = ApprovalVerdict(
        verified=False,
        reason=VerdictReason.UNAUTHORIZED_ACTOR,
        actor="attacker@evil.com",
        detail="approval set by 'attacker@evil.com', who is not authorized",
    )
    result = _poll_inside_lock()
    _patch_all["send_one_row"].assert_not_called()
    assert result.blocked == 1
    assert result.dispatched == 0
    # Security reason → operator wake-up (triple-fire) fired with the dedupe
    # key intact (Op Stds §3.1: the (script, error_code) pair is the Resend
    # dedupe key; a dropped error_code would collide with the default bucket).
    _patch_all["alert_critical"].assert_called_once()
    alert_call = _patch_all["alert_critical"].call_args
    assert alert_call.args[0] == weekly_send_poll.SCRIPT_NAME
    assert alert_call.kwargs["error_code"] == "approval_unverified"


def test_poll_unauthorized_writes_critical_forensic_row(_patch_all):
    """The block path emits an approval_unverified row at CRITICAL severity."""
    _patch_all["get_rows"].return_value = [_row(row_id=71)]
    _patch_all["verify_approval"].return_value = ApprovalVerdict(
        verified=False, reason=VerdictReason.UNAUTHORIZED_ACTOR, actor="x@y.com"
    )
    _poll_inside_lock()
    blocked_calls = [
        c
        for c in _patch_all["error_log"].call_args_list
        if c.kwargs.get("error_code") == "approval_unverified"
    ]
    assert blocked_calls, "expected an approval_unverified forensic log line"
    # Severity is positional arg 0.
    from shared.error_log import Severity

    assert blocked_calls[0].args[0] == Severity.CRITICAL


def test_poll_empty_allowlist_blocks_and_pages(_patch_all):
    """An empty authorized-approver set blocks the send and wakes the operator."""
    _patch_all["get_rows"].return_value = [_row(row_id=72)]
    _patch_all["verify_approval"].return_value = ApprovalVerdict(
        verified=False, reason=VerdictReason.EMPTY_ALLOWLIST
    )
    result = _poll_inside_lock()
    assert result.blocked == 1
    _patch_all["send_one_row"].assert_not_called()
    _patch_all["alert_critical"].assert_called_once()


def test_poll_not_currently_approved_blocks_without_paging(_patch_all):
    """A benign race (cell un-approved) blocks but does NOT page the operator."""
    _patch_all["get_rows"].return_value = [_row(row_id=73)]
    _patch_all["verify_approval"].return_value = ApprovalVerdict(
        verified=False,
        reason=VerdictReason.NOT_CURRENTLY_APPROVED,
        actor="seths@evergreenmirror.com",
    )
    result = _poll_inside_lock()
    assert result.blocked == 1
    _patch_all["send_one_row"].assert_not_called()
    _patch_all["alert_critical"].assert_not_called()
    # The benign-race forensic row is logged at WARN (not CRITICAL/ERROR).
    from shared.error_log import Severity

    blocked_calls = [
        c
        for c in _patch_all["error_log"].call_args_list
        if c.kwargs.get("error_code") == "approval_unverified"
    ]
    assert blocked_calls and blocked_calls[0].args[0] == Severity.WARN


def test_poll_blocks_one_dispatches_other(_patch_all):
    """Per-row gate: one row's failed attestation does not stop the others."""
    rows = [_row(row_id=80), _row(row_id=81)]
    _patch_all["get_rows"].return_value = rows

    def _verify(sheet_id, row_id, column, *, authorized_actors):
        if row_id == 80:
            return ApprovalVerdict(
                verified=False,
                reason=VerdictReason.UNAUTHORIZED_ACTOR,
                actor="x@y.com",
            )
        return ApprovalVerdict(
            verified=True,
            reason=VerdictReason.AUTHORIZED,
            actor="seths@evergreenmirror.com",
        )

    _patch_all["verify_approval"].side_effect = _verify
    result = _poll_inside_lock()
    assert result.blocked == 1
    assert result.dispatched == 1
    assert _patch_all["send_one_row"].call_count == 1
    assert _patch_all["send_one_row"].call_args.args[0] == 81


def test_poll_blocked_sets_warn_heartbeat(_patch_all):
    """A blocked send pushes the cycle status to at least WARN."""
    _patch_all["get_rows"].return_value = [_row(row_id=90)]
    _patch_all["verify_approval"].return_value = ApprovalVerdict(
        verified=False, reason=VerdictReason.UNAUTHORIZED_ACTOR, actor="x@y.com"
    )
    _poll_inside_lock()
    kwargs = _patch_all["write_heartbeat_row"].call_args.kwargs
    assert kwargs["status"] == "WARN"


def test_poll_passes_approval_column_and_sheet_to_verifier(_patch_all):
    """The poller hands the verifier the WPR sheet id, row id, and approval column."""
    _patch_all["get_rows"].return_value = [_row(row_id=95)]
    _poll_inside_lock()
    call = _patch_all["verify_approval"].call_args
    assert call.args[0] == weekly_send_poll.sheet_ids.SHEET_WPR_PENDING_REVIEW
    assert call.args[1] == 95
    assert call.args[2] == weekly_send_poll.APPROVAL_COLUMN


@pytest.mark.parametrize(
    "reason",
    [VerdictReason.HISTORY_READ_FAILED, VerdictReason.NO_HISTORY],
)
def test_poll_error_branch_blocks_without_paging(_patch_all, reason):
    """Fail-closed infra/edge reasons block the row at ERROR severity, no page.

    Covers the `else` branch of _handle_unverified (HISTORY_READ_FAILED and
    NO_HISTORY) — a regression mis-routing these into _WAKE_REASONS (paging on
    every transient Smartsheet 500) would be caught here.
    """
    _patch_all["get_rows"].return_value = [_row(row_id=96)]
    _patch_all["verify_approval"].return_value = ApprovalVerdict(
        verified=False, reason=reason, detail="transient/edge"
    )
    result = _poll_inside_lock()
    assert result.blocked == 1
    _patch_all["send_one_row"].assert_not_called()
    _patch_all["alert_critical"].assert_not_called()  # no operator wake-up
    from shared.error_log import Severity

    blocked_calls = [
        c
        for c in _patch_all["error_log"].call_args_list
        if c.kwargs.get("error_code") == "approval_unverified"
    ]
    assert blocked_calls and blocked_calls[0].args[0] == Severity.ERROR


# ---- F08: config-read resilience to an OPEN breaker ----------------------


def test_read_str_setting_fails_open_on_open_breaker(mocker):
    """REGRESSION (live smoke B3, intake_poll's twin): an OPEN breaker
    short-circuits the daemon's `polling_enabled` config read with
    `SmartsheetCircuitOpenError`; `_read_str_setting` must fail open to the
    fallback so the cycle survives to surface CIRCUIT_OPEN instead of crashing
    at the config read.
    """
    mocker.patch(
        "safety_reports.weekly_send_poll.smartsheet_client.get_setting",
        side_effect=weekly_send_poll.smartsheet_client.SmartsheetCircuitOpenError("open"),
    )
    assert weekly_send_poll._read_str_setting("any.key", "fb") == "fb"
    assert weekly_send_poll._polling_enabled() is weekly_send_poll.DEFAULT_POLLING_ENABLED


def test_poll_once_read_short_circuit_surfaces_circuit_open(_patch_all, mocker):
    """REGRESSION (F08): when the WPR_Pending_Review scan short-circuits because
    the breaker is OPEN, the heartbeat must surface CIRCUIT_OPEN (not a generic
    ERROR). The scan-failure early-return path bypasses the normal-path status
    determination, so it applies the CIRCUIT_OPEN override itself.
    """
    _patch_all["get_rows"].side_effect = (
        weekly_send_poll.smartsheet_client.SmartsheetCircuitOpenError("breaker open")
    )
    mocker.patch(
        "safety_reports.weekly_send_poll.circuit_breaker.is_open", return_value=True
    )

    poll_once()

    kwargs = _patch_all["write_heartbeat_row"].call_args.kwargs
    assert kwargs["status"] == "CIRCUIT_OPEN"
    assert kwargs["error_summary"] is None


# ---- A1: ITS_Daemon_Health row self-provision (the 2026-06-02 dark gap) --


@pytest.fixture
def heartbeat_state_in_tmp(monkeypatch, tmp_path):
    """Redirect HEARTBEAT_ROW_STATE_PATH into tmp_path so the live shared
    state file (~/its/state/heartbeat_row_ids.json) is untouched."""
    state = tmp_path / "heartbeat_row_ids.json"
    monkeypatch.setattr(weekly_send_poll, "HEARTBEAT_ROW_STATE_PATH", state)
    return state


def test_resolve_row_id_self_provisions_weekly_send_poll(heartbeat_state_in_tmp, mocker):
    """REGRESSION (the 2026-06-02 dark-daemon gap): weekly_send_poll had no
    ITS_Daemon_Health row, so every heartbeat logged 'seeder needed' and the
    daemon was invisible. A missing row now self-provisions, with this daemon's
    own cadence (900s) — not intake's 60s — proving the per-file registration
    constants are wired correctly into the otherwise-identical helper.

    The create-fail / race-adopt / write-then-update / no-raise paths are
    exhaustively unit-tested on the intake_poll side (tests/test_intake_poll.py);
    this side relies on those bodies being byte-identical, which
    tests/test_heartbeat_helper_parity.py enforces. This test covers the one
    real behavioral difference: the per-daemon cadence constant.
    """
    mocker.patch(
        "safety_reports.weekly_send_poll.smartsheet_client.find_row_by_primary",
        return_value=None,
    )
    add = mocker.patch(
        "safety_reports.weekly_send_poll.smartsheet_client.add_row_by_id",
        return_value=3344,
    )
    row_id = weekly_send_poll._resolve_heartbeat_row_id(DAEMON_NAME)
    assert row_id == 3344
    add.assert_called_once()
    from shared.sheet_ids import DAEMON_HEALTH_COLUMNS, SHEET_DAEMON_HEALTH
    sheet_id_arg, payload = add.call_args.args
    assert sheet_id_arg == SHEET_DAEMON_HEALTH
    assert payload[DAEMON_HEALTH_COLUMNS["daemon_name"]] == DAEMON_NAME
    assert payload[DAEMON_HEALTH_COLUMNS["workstream"]] == "safety_reports"
    assert (
        payload[DAEMON_HEALTH_COLUMNS["interval_seconds"]]
        == weekly_send_poll.DEFAULT_POLL_INTERVAL
    )
    # Per-cycle columns are filled by the immediately-following update, not here.
    assert DAEMON_HEALTH_COLUMNS["last_cycle_status"] not in payload
