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

    Also redirects `watchdog.WATCHDOG_MARKER_DIR` to tmp_path so
    `write_last_run_marker("watchdog")` at the end of main() doesn't
    write into the operator's real ~/its/.watchdog/.
    """
    monkeypatch.setattr("shared.error_log.LOG_DIR", tmp_path)
    monkeypatch.setattr("watchdog.WATCHDOG_MARKER_DIR", tmp_path / ".watchdog")
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


def test_checks_list_has_all_session_1_2_3_checks():
    """CHECKS registry in priority order. Check E is deferred to its
    shipping PR (Admin API key prerequisite) and intentionally absent
    here. Check G (alert-dedupe summary sweep) shipped in PR β
    (Session 3) and is registered last."""
    assert watchdog.CHECKS == [
        watchdog._check_stale_review_queue,
        watchdog._check_open_criticals,
        watchdog._check_scheduled_jobs,
        watchdog._check_reviewer_chain_forward,
        watchdog._check_mail_intake_silent_disable,
        watchdog._check_alert_dedupe_summaries,
    ]


def test_tracked_jobs_empty_by_design():
    """Planning decision C1: TRACKED_JOBS stays empty until a second
    scheduled job ships and starts calling write_last_run_marker."""
    assert watchdog.TRACKED_JOBS == []


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

    # All registered checks (Session 1 + Session 2 minus Check E) ran
    # with alerts_suppressed=True.
    assert run_check_spy.call_count == len(watchdog.CHECKS)
    for call in run_check_spy.call_args_list:
        assert call.kwargs == {"alerts_suppressed": True}
    # Preamble line present.
    messages = [c.args[2] for c in mock_log.call_args_list]
    assert any("MAINTENANCE" in m for m in messages)


def test_active_runs_normally(mock_check_state, mock_log, mocker):
    mock_check_state.return_value = SystemState.ACTIVE
    run_check_spy = mocker.patch("watchdog._run_check")

    watchdog.main()

    assert run_check_spy.call_count == len(watchdog.CHECKS)
    for call in run_check_spy.call_args_list:
        assert call.kwargs == {"alerts_suppressed": False}
    # No PAUSED/MAINTENANCE preamble line.
    messages = [c.args[2] for c in mock_log.call_args_list]
    assert not any("PAUSED" in m or "MAINTENANCE" in m for m in messages)


def test_main_writes_watchdog_marker_at_end(mock_check_state, mocker):
    """`main()` calls write_last_run_marker('watchdog') after the check loop.

    PAUSED short-circuits before checks AND before the marker, so the
    marker is only written when state was ACTIVE or MAINTENANCE.
    """
    mock_check_state.return_value = SystemState.ACTIVE
    mocker.patch("watchdog._run_check")

    watchdog.main()

    marker = watchdog.WATCHDOG_MARKER_DIR / "watchdog.last_run"
    assert marker.exists()
    # ISO 8601 with tz info.
    contents = marker.read_text()
    assert "T" in contents and ("+00:00" in contents or contents.endswith("Z"))


def test_main_paused_does_not_write_marker(mock_check_state, mocker):
    mock_check_state.return_value = SystemState.PAUSED
    mocker.patch("watchdog._run_check")

    watchdog.main()

    marker = watchdog.WATCHDOG_MARKER_DIR / "watchdog.last_run"
    assert not marker.exists()


# ---- Group F: Check C — scheduled jobs scaffold + marker writes ---------


def test_write_last_run_marker_writes_iso_timestamp():
    watchdog.write_last_run_marker("smoke_test_job")
    marker = watchdog.WATCHDOG_MARKER_DIR / "smoke_test_job.last_run"
    assert marker.exists()
    from datetime import datetime as dt
    parsed = dt.fromisoformat(marker.read_text())
    assert parsed.tzinfo is not None


def test_write_last_run_marker_creates_dir_on_demand():
    """Marker dir is created if missing — first scheduled job after fresh
    install should not crash because ~/its/.watchdog/ doesn't exist."""
    # The autouse isolate fixture redirects WATCHDOG_MARKER_DIR to a path
    # under tmp_path that doesn't exist yet.
    assert not watchdog.WATCHDOG_MARKER_DIR.exists()
    watchdog.write_last_run_marker("fresh_install_test")
    assert watchdog.WATCHDOG_MARKER_DIR.is_dir()


def test_write_last_run_marker_fail_soft_on_oserror(mocker, mock_log):
    """Marker write failures WARN, do not raise. A successful job must not
    fail just because the marker write hit an OS error (full disk, etc.)."""
    mocker.patch(
        "watchdog.Path.write_text",
        side_effect=OSError("No space left on device"),
    )
    # Must NOT raise.
    watchdog.write_last_run_marker("disk_full_job")
    # Exactly one WARN was logged with the failure detail.
    severities = [c.args[0] for c in mock_log.call_args_list]
    assert Severity.WARN in severities
    warn_msg = next(c.args[2] for c in mock_log.call_args_list if c.args[0] is Severity.WARN)
    assert "disk_full_job" in warn_msg
    assert "No space left" in warn_msg


def test_check_scheduled_jobs_returns_info_when_tracked_jobs_empty():
    """TRACKED_JOBS is empty by design — Check C is a no-op."""
    result = watchdog._check_scheduled_jobs()
    assert result.severity is Severity.INFO
    assert "empty by design" in result.summary or "No scheduled jobs" in result.summary


def test_check_scheduled_jobs_warns_on_missing_marker(monkeypatch):
    monkeypatch.setattr("watchdog.TRACKED_JOBS", ["nonexistent_job"])
    result = watchdog._check_scheduled_jobs()
    assert result.severity is Severity.WARN
    assert "nonexistent_job" in result.details
    assert "no marker" in result.details


def test_check_scheduled_jobs_warns_on_stale_marker(monkeypatch):
    """A marker older than 24 hours is stale."""
    from datetime import datetime as dt
    from datetime import timedelta as td
    monkeypatch.setattr("watchdog.TRACKED_JOBS", ["stale_job"])
    watchdog.WATCHDOG_MARKER_DIR.mkdir(parents=True, exist_ok=True)
    stale_marker = watchdog.WATCHDOG_MARKER_DIR / "stale_job.last_run"
    stale_ts = (dt.now(watchdog.UTC) - td(hours=25)).isoformat()
    stale_marker.write_text(stale_ts)

    result = watchdog._check_scheduled_jobs()

    assert result.severity is Severity.WARN
    assert "stale_job" in result.details
    assert stale_ts in result.details


def test_check_scheduled_jobs_ok_when_marker_fresh(monkeypatch):
    monkeypatch.setattr("watchdog.TRACKED_JOBS", ["fresh_job"])
    watchdog.write_last_run_marker("fresh_job")
    result = watchdog._check_scheduled_jobs()
    assert result.severity is Severity.INFO
    assert "fresh" in result.summary.lower()


# ---- Group G: Check D — reviewer-chain forward scan ---------------------


@pytest.fixture
def mock_review_queue_add(mocker):
    """review_queue.add as imported into watchdog — captures the row write."""
    return mocker.patch("watchdog.review_queue.add", return_value=12345)


@pytest.fixture
def mock_resolve_chain(mocker):
    return mocker.patch("watchdog.resolve_chain")


@pytest.fixture
def mock_is_federal_holiday(mocker):
    return mocker.patch("watchdog.is_federal_holiday", return_value=False)


@pytest.fixture
def mock_time_off_client(mocker):
    """Replace TimeOffClient with a no-op factory so Check D doesn't try
    to hit Smartsheet through _live_fetcher."""
    instance = mocker.MagicMock()
    return mocker.patch("watchdog.TimeOffClient", return_value=instance)


def _chain(*emails) -> object:
    """Build a minimal ReviewerChain-shaped object for Check D's needs."""
    from types import SimpleNamespace
    return SimpleNamespace(slots=tuple(emails), is_empty=not emails)


def test_check_reviewer_chain_no_gaps(
    mock_resolve_chain, mock_is_federal_holiday, mock_time_off_client,
    mock_review_queue_add,
):
    """All 14 days have a non-empty chain → no anomaly rows written."""
    mock_resolve_chain.return_value = _chain("p@x", "s@x", "t@x")

    result = watchdog._check_reviewer_chain_forward()

    assert result.severity is Severity.INFO
    assert "No reviewer-chain gaps" in result.summary
    mock_review_queue_add.assert_not_called()


def test_check_reviewer_chain_single_gap(
    mock_resolve_chain, mock_is_federal_holiday, mock_time_off_client,
    mock_review_queue_add,
):
    """One day has an empty chain → one anomaly row per workstream."""
    # First call → empty (gap), rest → full chain.
    mock_resolve_chain.side_effect = [_chain(), *[_chain("p", "s", "t")] * 13]

    result = watchdog._check_reviewer_chain_forward()

    assert result.severity is Severity.INFO
    assert "Logged 1 reviewer-chain anomaly row" in result.summary
    mock_review_queue_add.assert_called_once()
    kwargs = mock_review_queue_add.call_args.kwargs
    assert kwargs["workstream"] == "global"  # not "watchdog" — see preflight resolution
    assert kwargs["reason"] is watchdog.ReviewReason.OTHER
    assert kwargs["sla_tier"] is watchdog.SlaTier.SUBCONTRACT_DRAFT
    assert kwargs["severity"] is Severity.INFO
    assert "reviewer-chain gap" in kwargs["summary"]
    # The actual workstream name lives in the payload, not the row's workstream cell.
    assert kwargs["payload"]["workstream"] == "safety_reports"
    assert len(kwargs["payload"]["gap_dates"]) == 1


def test_check_reviewer_chain_multiple_gaps_collapse_into_one_row(
    mock_resolve_chain, mock_is_federal_holiday, mock_time_off_client,
    mock_review_queue_add,
):
    """3 gap days → 1 anomaly row with 3 dates in payload."""
    mock_resolve_chain.side_effect = (
        [_chain()] * 3 + [_chain("p", "s", "t")] * 11
    )

    watchdog._check_reviewer_chain_forward()

    mock_review_queue_add.assert_called_once()
    assert len(mock_review_queue_add.call_args.kwargs["payload"]["gap_dates"]) == 3


def test_check_reviewer_chain_federal_holiday_skipped(
    mock_resolve_chain, mock_is_federal_holiday, mock_time_off_client,
    mock_review_queue_add,
):
    """Federal holidays don't count as reviewer gaps."""
    mock_is_federal_holiday.return_value = True  # every day is a holiday
    # resolve_chain would return empty if called — but Check D should skip it.
    mock_resolve_chain.return_value = _chain()

    result = watchdog._check_reviewer_chain_forward()

    assert result.severity is Severity.INFO
    assert "No reviewer-chain gaps" in result.summary
    # resolve_chain never called because every day was a holiday.
    mock_resolve_chain.assert_not_called()
    mock_review_queue_add.assert_not_called()


def test_check_reviewer_chain_constructs_one_time_off_client(
    mock_resolve_chain, mock_is_federal_holiday, mock_time_off_client,
    mock_review_queue_add,
):
    """Per-instance caching → one TimeOffClient() per check run, which
    means a single Smartsheet read regardless of how many days scanned."""
    mock_resolve_chain.return_value = _chain("p", "s", "t")

    watchdog._check_reviewer_chain_forward()

    # One TimeOffClient construction per check run (one workstream × one client).
    assert mock_time_off_client.call_count == 1


def test_check_reviewer_chain_payload_dates_iso_formatted(
    mock_resolve_chain, mock_is_federal_holiday, mock_time_off_client,
    mock_review_queue_add,
):
    """Gap dates are serialized as ISO 8601 strings (date.isoformat)."""
    from datetime import date
    mock_resolve_chain.side_effect = [_chain(), *[_chain("p", "s", "t")] * 13]

    watchdog._check_reviewer_chain_forward()

    payload = mock_review_queue_add.call_args.kwargs["payload"]
    [gap_str] = payload["gap_dates"]
    # Round-trips through date.fromisoformat without error.
    date.fromisoformat(gap_str)


# ---- Group H: Check F — mail intake silent-disable ----------------------


@pytest.fixture
def mock_get_settings_with_prefix(mocker):
    return mocker.patch("watchdog.smartsheet_client.get_settings_with_prefix")


@pytest.fixture
def mock_fetch_latest_inbound(mocker):
    return mocker.patch("watchdog.graph_client.fetch_latest_inbound_timestamp")


def test_check_mail_intake_no_rows(mock_get_settings_with_prefix):
    mock_get_settings_with_prefix.return_value = {}
    result = watchdog._check_mail_intake_silent_disable()
    assert result.severity is Severity.INFO
    assert "Nothing to check" in result.summary or "No mail_intake" in result.summary


def test_check_mail_intake_under_threshold(
    mock_get_settings_with_prefix, mock_fetch_latest_inbound,
):
    from datetime import datetime as dt
    mock_get_settings_with_prefix.return_value = {
        "mail_intake.safety.max_idle_hours": "96",
    }
    # Last inbound 24h ago — well under 96h threshold.
    mock_fetch_latest_inbound.return_value = dt.now(watchdog.UTC) - watchdog.timedelta(hours=24)

    result = watchdog._check_mail_intake_silent_disable()

    assert result.severity is Severity.INFO
    assert "fresh" in result.summary.lower()


def test_check_mail_intake_over_threshold_warns(
    mock_get_settings_with_prefix, mock_fetch_latest_inbound,
):
    from datetime import datetime as dt
    mock_get_settings_with_prefix.return_value = {
        "mail_intake.safety.max_idle_hours": "96",
    }
    # Last inbound 200h ago — well past 96h threshold.
    mock_fetch_latest_inbound.return_value = dt.now(watchdog.UTC) - watchdog.timedelta(hours=200)

    result = watchdog._check_mail_intake_silent_disable()

    assert result.severity is Severity.WARN
    assert "safety@evergreenmirror.com" in result.details
    assert "200" in result.details
    assert "96" in result.details


def test_check_mail_intake_no_inbound_history_does_not_warn(
    mock_get_settings_with_prefix, mock_fetch_latest_inbound,
):
    """Empty mailbox (None from fetch_latest) is informational, not silent-disable."""
    mock_get_settings_with_prefix.return_value = {
        "mail_intake.safety.max_idle_hours": "96",
    }
    mock_fetch_latest_inbound.return_value = None

    result = watchdog._check_mail_intake_silent_disable()

    assert result.severity is Severity.INFO


def test_check_mail_intake_missing_mailbox_config_warns(
    mock_get_settings_with_prefix, mock_log, mock_fetch_latest_inbound,
):
    """Unmapped workstream → WARN about config gap, don't crash."""
    mock_get_settings_with_prefix.return_value = {
        "mail_intake.unicorn.max_idle_hours": "96",
    }
    result = watchdog._check_mail_intake_silent_disable()

    severities = [c.args[0] for c in mock_log.call_args_list]
    assert Severity.WARN in severities
    msg = next(c.args[2] for c in mock_log.call_args_list if c.args[0] is Severity.WARN)
    assert "unicorn" in msg
    assert "WORKSTREAM_TO_MAILBOX" in msg or "no mailbox" in msg.lower()
    # Overall result remains INFO since no actual silent mailbox detected.
    assert result.severity is Severity.INFO
    mock_fetch_latest_inbound.assert_not_called()


def test_check_mail_intake_graph_failure_warns_and_continues(
    mock_get_settings_with_prefix, mock_fetch_latest_inbound, mock_log,
):
    """Per-mailbox Graph failure WARNs but the check overall still completes."""
    from shared.graph_client import GraphPermissionError
    mock_get_settings_with_prefix.return_value = {
        "mail_intake.safety.max_idle_hours": "96",
    }
    mock_fetch_latest_inbound.side_effect = GraphPermissionError(
        "HTTP 403: ApplicationAccessPolicy denied"
    )

    result = watchdog._check_mail_intake_silent_disable()

    # Per-mailbox WARN was emitted.
    severities = [c.args[0] for c in mock_log.call_args_list]
    assert Severity.WARN in severities
    msg = next(c.args[2] for c in mock_log.call_args_list if c.args[0] is Severity.WARN)
    assert "safety@evergreenmirror.com" in msg
    # Overall check returns INFO (no silent mailbox identified).
    assert result.severity is Severity.INFO


def test_check_mail_intake_non_int_threshold_warns(
    mock_get_settings_with_prefix, mock_log, mock_fetch_latest_inbound,
):
    """Garbage threshold value → WARN about the row, skip the mailbox."""
    mock_get_settings_with_prefix.return_value = {
        "mail_intake.safety.max_idle_hours": "not-a-number",
    }
    watchdog._check_mail_intake_silent_disable()

    severities = [c.args[0] for c in mock_log.call_args_list]
    assert Severity.WARN in severities
    msg = next(c.args[2] for c in mock_log.call_args_list if c.args[0] is Severity.WARN)
    assert "not-a-number" in msg
    mock_fetch_latest_inbound.assert_not_called()


def test_check_mail_intake_ignores_non_threshold_rows(
    mock_get_settings_with_prefix, mock_fetch_latest_inbound,
):
    """Rows like mail_intake.foo.other_setting are skipped (not max_idle_hours)."""
    mock_get_settings_with_prefix.return_value = {
        "mail_intake.safety.notes": "ignore me",
    }
    result = watchdog._check_mail_intake_silent_disable()
    assert result.severity is Severity.INFO
    mock_fetch_latest_inbound.assert_not_called()


# ---- Group I: Check G — alert-dedupe summary sweep ---------------------


@pytest.fixture
def dedupe_state(tmp_path, monkeypatch):
    """Redirect alert_dedupe state file to tmp_path for hermetic tests.

    Mirrors the pattern in tests/test_alert_dedupe.py::state_in_tmp. The
    watchdog imports `alert_dedupe` from `shared`, so monkeypatching the
    module attributes is sufficient — the check function reads
    `alert_dedupe.STATE_FILE` indirectly through the public helpers.
    """
    import shared.alert_dedupe as ad
    state_dir = tmp_path / "state"
    monkeypatch.setattr(ad, "STATE_DIR", state_dir)
    monkeypatch.setattr(ad, "STATE_FILE", state_dir / "alert_dedupe.json")
    return state_dir


@pytest.fixture
def frozen_clock(monkeypatch):
    """Pin alert_dedupe._now() to a known UTC moment with mutation support."""
    from datetime import UTC, datetime, timedelta

    import shared.alert_dedupe as ad

    class _Clock:
        def __init__(self):
            self.now = datetime(2026, 5, 20, 15, 0, 0, tzinfo=UTC)

        def advance(self, **kwargs):
            self.now = self.now + timedelta(**kwargs)

    clock = _Clock()
    monkeypatch.setattr(ad, "_now", lambda: clock.now)
    return clock


@pytest.fixture(autouse=True)
def settings_mock_for_dedupe(mocker):
    """Default ITS_Config window = 60 min so record_fire works in tests."""
    return mocker.patch(
        "shared.smartsheet_client.get_setting", return_value="60"
    )


@pytest.fixture
def mock_send_alert(mocker):
    return mocker.patch("watchdog.resend_client.send_alert")


def _seed_expired_entry(
    key: str, suppressed_count: int = 0, summarized: bool = False
):
    """Write one entry to the redirected state file with window already expired."""
    import json as _json

    import shared.alert_dedupe as ad
    ad.STATE_DIR.mkdir(parents=True, exist_ok=True)
    state = (
        _json.loads(ad.STATE_FILE.read_text())
        if ad.STATE_FILE.exists() and ad.STATE_FILE.stat().st_size > 0
        else {}
    )
    state[key] = {
        "first_fired_at": "2026-05-20T14:00:00+00:00",
        "last_fired_at":  "2026-05-20T14:05:00+00:00",
        "suppressed_count": suppressed_count,
        "window_ends_at": "2026-05-20T14:30:00+00:00",  # before frozen_clock = 15:00
        "summarized": summarized,
    }
    ad.STATE_FILE.write_text(_json.dumps(state))


def test_summary_sweep_no_expired_returns_info(dedupe_state, frozen_clock, mock_send_alert):
    result = watchdog._check_alert_dedupe_summaries()

    assert result.severity is Severity.INFO
    assert "No expired" in result.summary
    mock_send_alert.assert_not_called()


def test_summary_sweep_fires_resend_for_suppressed_expired_entry(
    dedupe_state, frozen_clock, mock_send_alert
):
    _seed_expired_entry("safety.intake::uncaught_exception", suppressed_count=4)

    result = watchdog._check_alert_dedupe_summaries()

    mock_send_alert.assert_called_once()
    subject, body = mock_send_alert.call_args.args
    assert subject.startswith("[ITS CRITICAL SUMMARY]")
    assert "safety.intake" in subject
    assert "4 suppressed" in subject
    assert "Script:           safety.intake" in body
    assert "Error code:       uncaught_exception" in body
    assert "Suppressed count: 4" in body
    assert "Filter ITS_Errors by:" in body
    assert result.severity is Severity.INFO
    assert "fired 1 summary" in result.summary


def test_summary_sweep_marks_summarized_after_fire(
    dedupe_state, frozen_clock, mock_send_alert
):
    import json as _json

    import shared.alert_dedupe as ad

    _seed_expired_entry("script::uncaught_exception", suppressed_count=2)

    watchdog._check_alert_dedupe_summaries()

    state = _json.loads(ad.STATE_FILE.read_text())
    assert state["script::uncaught_exception"]["summarized"] is True


def test_summary_sweep_does_not_delete_freshly_summarized(
    dedupe_state, frozen_clock, mock_send_alert
):
    """Two-phase deletion: a just-summarized entry stays for next sweep."""
    import json as _json

    import shared.alert_dedupe as ad

    _seed_expired_entry("script::uncaught_exception", suppressed_count=2)
    watchdog._check_alert_dedupe_summaries()

    state = _json.loads(ad.STATE_FILE.read_text())
    assert "script::uncaught_exception" in state


def test_summary_sweep_deletes_already_summarized_entry(
    dedupe_state, frozen_clock, mock_send_alert
):
    """Phase 2 of two-phase delete: summarized entries get removed."""
    import json as _json

    import shared.alert_dedupe as ad

    _seed_expired_entry("script::uncaught_exception", suppressed_count=2, summarized=True)

    result = watchdog._check_alert_dedupe_summaries()

    state = _json.loads(ad.STATE_FILE.read_text())
    assert "script::uncaught_exception" not in state
    mock_send_alert.assert_not_called()
    assert "deleted 1 entry" in result.summary


def test_summary_sweep_deletes_clean_expired_entry(
    dedupe_state, frozen_clock, mock_send_alert
):
    """suppressed_count == 0 means no flapping happened; delete on first sweep
    without firing a summary."""
    import json as _json

    import shared.alert_dedupe as ad

    _seed_expired_entry("script::uncaught_exception", suppressed_count=0)

    result = watchdog._check_alert_dedupe_summaries()

    state = _json.loads(ad.STATE_FILE.read_text())
    assert "script::uncaught_exception" not in state
    mock_send_alert.assert_not_called()
    assert "deleted 1 entry" in result.summary


def test_summary_sweep_skips_open_windows(
    dedupe_state, frozen_clock, mock_send_alert
):
    """Open-window entries are excluded from the sweep entirely (Phase 0).

    list_expired_summaries filters on window_ends_at < now; open windows
    never reach the check function's loop.
    """
    import json as _json

    import shared.alert_dedupe as ad

    # Seed a state entry with a future window_ends_at.
    ad.STATE_DIR.mkdir(parents=True, exist_ok=True)
    ad.STATE_FILE.write_text(_json.dumps({
        "script::uncaught_exception": {
            "first_fired_at": "2026-05-20T14:55:00+00:00",
            "last_fired_at":  "2026-05-20T14:55:30+00:00",
            "suppressed_count": 3,
            "window_ends_at": "2026-05-20T15:55:00+00:00",  # 55 min after frozen
            "summarized": False,
        }
    }))

    result = watchdog._check_alert_dedupe_summaries()

    mock_send_alert.assert_not_called()
    state = _json.loads(ad.STATE_FILE.read_text())
    # Still present — sweep didn't touch it.
    assert "script::uncaught_exception" in state
    assert "No expired" in result.summary


def test_summary_sweep_resend_failure_leaves_entry_unmarked(
    dedupe_state, frozen_clock, mock_send_alert, mock_log
):
    """If Resend raises, the entry stays unmarked so the next sweep retries."""
    import json as _json

    import shared.alert_dedupe as ad

    _seed_expired_entry("script::uncaught_exception", suppressed_count=2)
    mock_send_alert.side_effect = RuntimeError("resend down")

    watchdog._check_alert_dedupe_summaries()

    state = _json.loads(ad.STATE_FILE.read_text())
    assert state["script::uncaught_exception"]["summarized"] is False
    # WARN line via watchdog.log captures the send failure.
    severities = [c.args[0] for c in mock_log.call_args_list]
    assert Severity.WARN in severities


def test_summary_sweep_state_read_failure_logs_marker_no_crash(
    dedupe_state, frozen_clock, mock_send_alert, monkeypatch
):
    """If list_expired_summaries returns empty (e.g., due to internal failure),
    the check still completes cleanly with the no-work message."""
    import shared.alert_dedupe as ad
    monkeypatch.setattr(ad, "list_expired_summaries", lambda: [])

    result = watchdog._check_alert_dedupe_summaries()

    assert result.severity is Severity.INFO
    mock_send_alert.assert_not_called()


def test_summary_sweep_mixed_expired_entries(
    dedupe_state, frozen_clock, mock_send_alert
):
    """One needs summarizing, one needs deleting (clean expiry), one is
    summarized-and-pending-delete. All three handled in one sweep."""
    import json as _json

    import shared.alert_dedupe as ad

    _seed_expired_entry("a::uncaught_exception", suppressed_count=3)
    _seed_expired_entry("b::uncaught_exception", suppressed_count=0)
    _seed_expired_entry(
        "c::uncaught_exception", suppressed_count=5, summarized=True
    )

    result = watchdog._check_alert_dedupe_summaries()

    # `a` got a summary fired + marked; `b` and `c` deleted.
    mock_send_alert.assert_called_once()
    subject, _ = mock_send_alert.call_args.args
    assert "a::" not in subject  # subject uses just the script half
    assert subject.startswith("[ITS CRITICAL SUMMARY] a:")

    state = _json.loads(ad.STATE_FILE.read_text())
    assert "a::uncaught_exception" in state  # stays this sweep (phase 1)
    assert state["a::uncaught_exception"]["summarized"] is True
    assert "b::uncaught_exception" not in state  # deleted
    assert "c::uncaught_exception" not in state  # deleted

    assert "fired 1 summary" in result.summary
    assert "deleted 2 entries" in result.summary


def test_summary_subject_format_matches_spec(
    dedupe_state, frozen_clock, mock_send_alert
):
    _seed_expired_entry("safety_reports.intake::uncaught_exception", suppressed_count=7)
    watchdog._check_alert_dedupe_summaries()

    subject, _ = mock_send_alert.call_args.args
    assert subject == "[ITS CRITICAL SUMMARY] safety_reports.intake: 7 suppressed occurrences"


def test_summary_body_includes_filter_criteria(
    dedupe_state, frozen_clock, mock_send_alert
):
    _seed_expired_entry("safety_reports.intake::uncaught_exception", suppressed_count=2)
    watchdog._check_alert_dedupe_summaries()

    _, body = mock_send_alert.call_args.args
    # Filter criteria pointing operator at ITS_Errors.
    assert "Filter ITS_Errors by:" in body
    assert "Script = safety_reports.intake" in body
    assert "Surfaced At BETWEEN" in body
    # Sheet ID reference (so operator can copy/paste).
    from shared import sheet_ids
    assert str(sheet_ids.SHEET_ERRORS) in body


def test_summary_sweep_check_registered_in_checks_list():
    """The new check is registered in CHECKS so main() runs it."""
    assert watchdog._check_alert_dedupe_summaries in watchdog.CHECKS


def test_summary_sweep_check_failure_isolated_by_run_check(
    dedupe_state, frozen_clock, mock_send_alert, mock_log, monkeypatch
):
    """A raise inside the check is caught by _run_check, marker logged,
    other checks would still run (here we only assert no propagation)."""
    def _boom():
        raise RuntimeError("unexpected sweep failure")

    monkeypatch.setattr(watchdog, "_check_alert_dedupe_summaries", _boom)
    # Patch the CHECKS list to only include the broken one for clarity.
    monkeypatch.setattr(watchdog, "CHECKS", [_boom])

    watchdog._run_check(_boom, alerts_suppressed=False)

    severities = [c.args[0] for c in mock_log.call_args_list]
    assert Severity.ERROR in severities
    err_line = next(
        c.args[2] for c in mock_log.call_args_list if c.args[0] is Severity.ERROR
    )
    assert "[watchdog-check-failed:_boom]" in err_line
