"""Tests for scripts/watchdog.py.

`scripts/` is not a Python package (per pyproject.toml comment); we use
the same sys.path-insert pattern as tests/test_migration_import_hygiene.py
so `import watchdog` resolves the script as a top-level module.

All Smartsheet, Resend, and Sentry boundaries are mocked. LOG_DIR is
redirected to a per-test tmp_path so the decorator's started/completed
INFO lines don't pollute ~/its/logs/.

Run with: pytest -q tests/test_watchdog.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from shared.error_log import Severity
from shared.kill_switch import SystemState
from shared.smartsheet_client import SmartsheetError

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import watchdog  # noqa: E402  — must come after sys.path insertion above


@pytest.fixture(autouse=True)
def isolate_error_log(tmp_path, monkeypatch, mocker):
    """Autouse: route error_log filesystem + side-channel writes to mocks.

    The watchdog's `main()` is wrapped in `@its_error_log`, which emits
    INFO started/completed lines through `shared.error_log.log`. Those go
    to a local file (redirected here) and skip Smartsheet/Resend/Sentry
    by default (INFO is env-gated and triple-fire is CRITICAL-only). On
    a CRITICAL path (e.g., main raises), the side channels fire — mock
    those so tests are hermetic.
    """
    monkeypatch.setattr("shared.error_log.LOG_DIR", tmp_path)
    mocker.patch("shared.error_log.smartsheet_client.add_rows")
    mocker.patch("shared.resend_client.send_alert")
    mocker.patch("shared.sentry_client.capture_exception")
    import shared.error_log as el
    el._in_smartsheet_write = False
    el._in_resend_alert = False
    el._in_sentry_capture = False
    yield
    el._in_smartsheet_write = False
    el._in_resend_alert = False
    el._in_sentry_capture = False


@pytest.fixture
def mock_log(mocker):
    """Mock `watchdog.log` — captures main's preamble + _run_check routing.

    Does NOT capture the @its_error_log decorator's started/completed lines
    (those bind to `shared.error_log.log`, not `watchdog.log`).
    """
    return mocker.patch("watchdog.log")


@pytest.fixture
def mock_check_state(mocker):
    return mocker.patch("watchdog.check_system_state")


@pytest.fixture
def mock_get_pending(mocker):
    return mocker.patch("watchdog.review_queue.get_pending")


@pytest.fixture
def mock_is_past_sla(mocker):
    return mocker.patch("watchdog.review_queue.is_past_sla")


@pytest.fixture
def mock_get_rows(mocker):
    return mocker.patch("watchdog.smartsheet_client.get_rows")


# ---- Group A: module shape ----------------------------------------------


def test_checks_list_has_session_1_checks():
    assert watchdog.CHECKS == [
        watchdog._check_stale_review_queue,
        watchdog._check_open_criticals,
    ]


# ---- Group B: _check_stale_review_queue ---------------------------------


def test_no_pending_items(mock_get_pending, mock_is_past_sla):
    mock_get_pending.return_value = []

    result = watchdog._check_stale_review_queue()

    assert result.severity is Severity.INFO
    assert "No stale items" in result.summary
    mock_is_past_sla.assert_not_called()


def test_pending_but_none_stale(mock_get_pending, mock_is_past_sla):
    mock_get_pending.return_value = [
        {"Item ID": "safety_reports-20260519-100000", "Created At": "2026-05-19",
         "SLA Tier": "4h"},
        {"Item ID": "po_materials-20260519-110000", "Created At": "2026-05-19",
         "SLA Tier": "24h"},
    ]
    mock_is_past_sla.return_value = False

    result = watchdog._check_stale_review_queue()

    assert result.severity is Severity.INFO
    assert result.details == ""
    assert mock_is_past_sla.call_count == 2


def test_pending_and_stale_under_cap(mock_get_pending, mock_is_past_sla):
    mock_get_pending.return_value = [
        {"Item ID": "safety_reports-20260515-100000", "Created At": "2026-05-15",
         "SLA Tier": "4h"},
        {"Item ID": "po_materials-20260515-110000", "Created At": "2026-05-15",
         "SLA Tier": "4h"},
    ]
    mock_is_past_sla.return_value = True

    result = watchdog._check_stale_review_queue()

    assert result.severity is Severity.WARN
    assert "2 item(s)" in result.summary
    assert "safety_reports-20260515-100000" in result.details
    assert "po_materials-20260515-110000" in result.details
    assert "showing first" not in result.details


def test_pending_and_stale_over_cap(mock_get_pending, mock_is_past_sla):
    over_cap = watchdog.REVIEW_QUEUE_ITEM_CAP + 1
    rows = [
        {"Item ID": f"safety_reports-2026051{i}-100000", "Created At": "2026-05-15",
         "SLA Tier": "4h"}
        for i in range(over_cap)
    ]
    mock_get_pending.return_value = rows
    mock_is_past_sla.return_value = True

    result = watchdog._check_stale_review_queue()

    assert result.severity is Severity.WARN
    assert f"{over_cap} item(s)" in result.summary
    # Cap-many IDs shown; "showing first N of M" suffix appended.
    capped_ids = [r["Item ID"] for r in rows[:watchdog.REVIEW_QUEUE_ITEM_CAP]]
    for cid in capped_ids:
        assert cid in result.details
    # The (cap+1)-th ID must NOT appear.
    assert rows[watchdog.REVIEW_QUEUE_ITEM_CAP]["Item ID"] not in result.details
    assert f"first {watchdog.REVIEW_QUEUE_ITEM_CAP} of {over_cap}" in result.details


def test_stale_check_smartsheet_failure_propagates(mock_get_pending):
    # The check itself raises; the harness (Group D) catches separately.
    mock_get_pending.side_effect = SmartsheetError("HTTP 500: server error")
    with pytest.raises(SmartsheetError, match="500"):
        watchdog._check_stale_review_queue()


# ---- Group C: _check_open_criticals -------------------------------------


def test_no_criticals_in_errors(mock_get_rows):
    mock_get_rows.return_value = []

    result = watchdog._check_open_criticals()

    assert result.severity is Severity.INFO
    assert "No open CRITICAL" in result.summary


def test_all_criticals_resolved(mock_get_rows):
    mock_get_rows.return_value = [
        {"Error": "uncaught_exception", "Severity": "CRITICAL",
         "Resolved At": "2026-05-18"},
        {"Error": "smartsheet-write-failed", "Severity": "CRITICAL",
         "Resolved At": "2026-05-19"},
    ]

    result = watchdog._check_open_criticals()

    assert result.severity is Severity.INFO


def test_open_critical_under_cap(mock_get_rows):
    mock_get_rows.return_value = [
        {"Error": "uncaught_exception", "Severity": "CRITICAL",
         "Resolved At": None},
        {"Error": "smartsheet-write-failed", "Severity": "CRITICAL"},  # missing key
    ]

    result = watchdog._check_open_criticals()

    assert result.severity is Severity.WARN
    assert "2 open CRITICAL" in result.summary
    assert "uncaught_exception" in result.details
    assert "smartsheet-write-failed" in result.details
    assert "showing first" not in result.details


def test_open_critical_over_cap(mock_get_rows):
    over_cap = watchdog.CRITICAL_ITEMS_CAP + 1
    mock_get_rows.return_value = [
        {"Error": f"code-{i}", "Severity": "CRITICAL", "Resolved At": ""}
        for i in range(over_cap)
    ]

    result = watchdog._check_open_criticals()

    assert result.severity is Severity.WARN
    assert f"{over_cap} open CRITICAL" in result.summary
    for i in range(watchdog.CRITICAL_ITEMS_CAP):
        assert f"code-{i}" in result.details
    assert f"code-{watchdog.CRITICAL_ITEMS_CAP}" not in result.details
    assert f"first {watchdog.CRITICAL_ITEMS_CAP} of {over_cap}" in result.details


def test_critical_missing_error_code_renders_placeholder(mock_get_rows):
    mock_get_rows.return_value = [
        {"Error": None, "Severity": "CRITICAL", "Resolved At": None},
        {"Error": "", "Severity": "CRITICAL", "Resolved At": None},
        {"Severity": "CRITICAL", "Resolved At": None},  # Error key missing
    ]

    result = watchdog._check_open_criticals()

    assert result.severity is Severity.WARN
    # All three rows render <no-code>; details should contain three of them.
    assert result.details.count("<no-code>") == 3


def test_critical_filter_applied(mock_get_rows):
    # The Severity filter must be passed to get_rows — we don't fetch
    # everything and filter client-side here.
    mock_get_rows.return_value = []
    watchdog._check_open_criticals()
    mock_get_rows.assert_called_once_with(
        watchdog.sheet_ids.SHEET_ERRORS,
        filters={"Severity": "CRITICAL"},
    )


# ---- Group D: _run_check harness ----------------------------------------


def _result(severity: Severity, summary: str = "ok", details: str = "") -> watchdog.CheckResult:
    return watchdog.CheckResult(severity=severity, summary=summary, details=details)


def test_info_result_logs_info(mock_log):
    def info_check() -> watchdog.CheckResult:
        return _result(Severity.INFO, "all clear")
    watchdog._run_check(info_check, alerts_suppressed=False)
    severity, _script, _msg = mock_log.call_args.args
    assert severity is Severity.INFO


def test_warn_result_logs_warn(mock_log):
    def warn_check() -> watchdog.CheckResult:
        return _result(Severity.WARN, "stale items", details="X, Y")
    watchdog._run_check(warn_check, alerts_suppressed=False)
    severity, _script, msg = mock_log.call_args.args
    assert severity is Severity.WARN
    assert "stale items" in msg
    assert "X, Y" in msg


def test_critical_result_logs_critical(mock_log):
    def crit_check() -> watchdog.CheckResult:
        return _result(Severity.CRITICAL, "house is on fire")
    watchdog._run_check(crit_check, alerts_suppressed=False)
    severity, _script, _msg = mock_log.call_args.args
    assert severity is Severity.CRITICAL


def test_alerts_suppressed_downgrades_warn_to_info(mock_log):
    def warn_check() -> watchdog.CheckResult:
        return _result(Severity.WARN, "stale items")
    watchdog._run_check(warn_check, alerts_suppressed=True)
    severity, _script, _msg = mock_log.call_args.args
    assert severity is Severity.INFO


def test_alerts_suppressed_downgrades_critical_to_info(mock_log):
    def crit_check() -> watchdog.CheckResult:
        return _result(Severity.CRITICAL, "house is on fire")
    watchdog._run_check(crit_check, alerts_suppressed=True)
    severity, _script, _msg = mock_log.call_args.args
    assert severity is Severity.INFO


def test_alerts_suppressed_passes_info_through(mock_log):
    def info_check() -> watchdog.CheckResult:
        return _result(Severity.INFO, "all clear")
    watchdog._run_check(info_check, alerts_suppressed=True)
    severity, _script, _msg = mock_log.call_args.args
    assert severity is Severity.INFO


def test_check_raising_emits_marker_line(mock_log):
    def boom_check() -> watchdog.CheckResult:
        raise SmartsheetError("HTTP 500: server error")
    # Must NOT propagate.
    watchdog._run_check(boom_check, alerts_suppressed=False)
    severity, _script, msg = mock_log.call_args.args
    assert severity is Severity.ERROR
    assert "[watchdog-check-failed:boom_check]" in msg
    assert "HTTP 500" in msg


def test_check_failure_does_not_block_next_check(mock_log):
    calls: list[str] = []

    def failing_check() -> watchdog.CheckResult:
        calls.append("failing")
        raise RuntimeError("oops")

    def working_check() -> watchdog.CheckResult:
        calls.append("working")
        return _result(Severity.INFO, "ok")

    for check in (failing_check, working_check):
        watchdog._run_check(check, alerts_suppressed=False)

    # Both checks ran; the second wasn't blocked by the first's failure.
    assert calls == ["failing", "working"]
    # Two log calls: 1 ERROR marker line, 1 INFO from working_check.
    severities = [c.args[0] for c in mock_log.call_args_list]
    assert severities == [Severity.ERROR, Severity.INFO]


# ---- Group E: main() integration ----------------------------------------


def test_paused_skips_all_checks(mock_check_state, mock_log, mocker):
    mock_check_state.return_value = SystemState.PAUSED
    # Spy on the checks via the run_check entry point.
    run_check_spy = mocker.patch("watchdog._run_check")

    watchdog.main()

    run_check_spy.assert_not_called()
    # The single PAUSED preamble line was logged.
    messages = [c.args[2] for c in mock_log.call_args_list]
    assert any("PAUSED" in m for m in messages)


def test_maintenance_runs_with_alerts_suppressed(mock_check_state, mock_log, mocker):
    mock_check_state.return_value = SystemState.MAINTENANCE
    run_check_spy = mocker.patch("watchdog._run_check")

    watchdog.main()

    # Both checks ran with alerts_suppressed=True.
    assert run_check_spy.call_count == 2
    for call in run_check_spy.call_args_list:
        assert call.kwargs == {"alerts_suppressed": True}
    # Preamble line present.
    messages = [c.args[2] for c in mock_log.call_args_list]
    assert any("MAINTENANCE" in m for m in messages)


def test_active_runs_normally(mock_check_state, mock_log, mocker):
    mock_check_state.return_value = SystemState.ACTIVE
    run_check_spy = mocker.patch("watchdog._run_check")

    watchdog.main()

    assert run_check_spy.call_count == 2
    for call in run_check_spy.call_args_list:
        assert call.kwargs == {"alerts_suppressed": False}
    # No PAUSED/MAINTENANCE preamble line.
    messages = [c.args[2] for c in mock_log.call_args_list]
    assert not any("PAUSED" in m or "MAINTENANCE" in m for m in messages)
