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

import inspect
import sys
from datetime import UTC, date, datetime, timedelta, timezone
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


@pytest.fixture
def mock_get_setting(mocker):
    """Mock the ITS_Config read main() uses for the F16 heartbeat URL."""
    return mocker.patch("watchdog.smartsheet_client.get_setting")


@pytest.fixture
def mock_ping(mocker):
    """Mock the outbound heartbeat beacon so main() makes no real GET."""
    return mocker.patch("watchdog.heartbeat_client.ping")


# ---- Group A: module shape ----------------------------------------------


def test_checks_list_has_all_session_1_2_3_checks():
    """CHECKS registry in priority order. Check E is deferred to its
    shipping PR (Admin API key prerequisite) and intentionally absent
    here. Check G (alert-dedupe summary sweep) shipped in PR β
    (Session 3). Check I (weekly_generate catch-up) is registered last and
    runs after Check C (it recovers what Check C only detects). There is no
    Check H — the marker-file Check C is the staleness floor doctrine once
    named "Check H" (2026-06-01 doctrine correction). Checks J (circuit-breaker
    prolonged-open page) and K (guaranteed F09 cap-window summary sweep) shipped
    in F08/F09 PR 2. Check L (token write-capability probe) shipped in B2.
    Check N (WSR rows stuck in SENDING) is the weekly_send write-ahead-marker
    safety net."""
    assert watchdog.CHECKS == [
        watchdog._check_stale_review_queue,
        watchdog._check_open_criticals,
        watchdog._check_scheduled_jobs,
        watchdog._check_reviewer_chain_forward,
        # _check_mail_intake_silent_disable RETIRED 2026-06-05 (safety email intake retired).
        watchdog._check_alert_dedupe_summaries,
        watchdog._check_weekly_generate_catchup,
        watchdog._check_circuit_breaker_prolonged_open,
        watchdog._check_alert_rate_cap_window,
        watchdog._check_token_write_capability,  # Check L (B2)
        watchdog._check_blueprint_guard_symlinks,  # Check M (C3)
        watchdog._check_stuck_wsr_send,  # Check N (WSR write-ahead-marker safety net)
    ]


# ---- Check L: token write-capability probe (B2) --------------------------


def test_token_write_capability_ok(mocker):
    mocker.patch("watchdog.smartsheet_client.verify_write_capability", return_value=55)
    delete = mocker.patch("watchdog.smartsheet_client.delete_sheet_settling")
    result = watchdog._check_token_write_capability()
    assert result.severity is Severity.INFO
    delete.assert_called_once_with(55)  # throwaway probe sheet cleaned up (with retry)


def test_token_write_capability_critical_on_write_error(mocker):
    mocker.patch(
        "watchdog.smartsheet_client.verify_write_capability",
        side_effect=watchdog.smartsheet_client.SmartsheetWriteCapabilityError(
            "read-only token"
        ),
    )
    result = watchdog._check_token_write_capability()
    assert result.severity is Severity.CRITICAL  # _run_check pages this (post-A3)
    assert "cannot write" in result.summary


def test_token_write_capability_skips_on_breaker_open(mocker):
    # A Smartsheet OUTAGE is not a token verdict — INFO-skip, never CRITICAL.
    mocker.patch(
        "watchdog.smartsheet_client.verify_write_capability",
        side_effect=watchdog.smartsheet_client.SmartsheetCircuitOpenError("open"),
    )
    result = watchdog._check_token_write_capability()
    assert result.severity is Severity.INFO
    assert "breaker OPEN" in result.summary


def test_token_write_capability_warn_on_delete_failure(mocker):
    # Create proved write capability; a cleanup-delete failure is WARN, not CRITICAL.
    mocker.patch("watchdog.smartsheet_client.verify_write_capability", return_value=77)
    mocker.patch(
        "watchdog.smartsheet_client.delete_sheet_settling",
        side_effect=SmartsheetError("delete boom"),  # all settle retries exhausted
    )
    result = watchdog._check_token_write_capability()
    assert result.severity is Severity.WARN
    assert "77" in result.summary


def test_tracked_jobs_contains_safety_weekly_generate():
    """R3 Session 2 registered safety_weekly_generate as the first
    non-watchdog tracked job. Per-job freshness window is 8 days (it runs
    Friday 14:00 — 1-day-late survives, missed-week surfaces)."""
    assert "safety_weekly_generate" in watchdog.TRACKED_JOBS
    assert watchdog.TRACKED_JOB_WINDOWS["safety_weekly_generate"].days == 8


# test_tracked_jobs_contains_safety_intake + test_safety_intake_slug_matches_intake_poll_module
# REMOVED 2026-06-05: the safety email-intake poller (intake_poll) is RETIRED to a
# tombstone (Safety Portal PULL model supersedes it), so safety_intake is no longer a
# Check-C TRACKED_JOBS entry and the tombstone writes no marker / has no WATCHDOG_JOB_SLUG.


def test_picklist_marker_slugs_match_their_writers():
    """C4 consistency guard (same class as the intake one above): every tracked
    picklist slug must be written by an actual scheduled job, or the watchdog
    tracks a marker nothing writes → permanent false WARN.

    - safety_picklist_audit ← scripts/audit_picklist_drift (weekly plist, C4).
    - safety_picklist_sync  ← scripts/run_picklist_sync (hourly plist, C4 adds
      the marker so the previously-untracked job's death becomes visible).
    """
    import audit_picklist_drift
    import run_picklist_sync

    assert audit_picklist_drift.WATCHDOG_JOB_NAME == "safety_picklist_audit"
    assert audit_picklist_drift.WATCHDOG_JOB_NAME in watchdog.TRACKED_JOBS
    assert run_picklist_sync.WATCHDOG_JOB_NAME == "safety_picklist_sync"
    assert run_picklist_sync.WATCHDOG_JOB_NAME in watchdog.TRACKED_JOBS


def test_portal_poll_marker_slug_matches_writer_and_window():
    """Same Check-C consistency guard for the Safety Portal pull daemon: the
    slug portal_poll writes (safety_portal_poll.last_run) must match the slug
    the watchdog tracks, or Check C either watches a marker nothing writes
    (permanent false WARN) or — worse — a dead puller goes unnoticed.
    Registered at the 2026-06-06 deploy session (previously a deferred
    "future addition"). 5-min window == ~5 poll cycles at the 60s default."""
    from safety_reports import portal_poll

    assert portal_poll.WATCHDOG_JOB_SLUG == "safety_portal_poll"
    assert portal_poll.WATCHDOG_JOB_SLUG in watchdog.TRACKED_JOBS
    assert watchdog.TRACKED_JOB_WINDOWS[portal_poll.WATCHDOG_JOB_SLUG] == timedelta(
        minutes=5
    )


def test_run_picklist_sync_write_marker_round_trips(monkeypatch, tmp_path):
    """C4: run_picklist_sync writes a parseable ISO timestamp to its marker
    (fail-soft path mirrors audit_picklist_drift's, proven elsewhere)."""
    import run_picklist_sync

    marker = tmp_path / "safety_picklist_sync.last_run"
    monkeypatch.setattr(run_picklist_sync, "_watchdog_marker_path", lambda: marker)
    run_picklist_sync._write_marker()
    assert marker.exists()
    datetime.fromisoformat(marker.read_text().strip())  # raises if not valid ISO


# ---- Check M: blueprint guard-symlink resolution (C3) --------------------


def test_blueprint_guard_symlinks_ok(monkeypatch, tmp_path):
    bp = tmp_path / "its-blueprint"
    (bp / ".claude" / "agents").mkdir(parents=True)
    (bp / ".claude" / "hooks").mkdir(parents=True)
    monkeypatch.setattr("watchdog._BLUEPRINT_ROOT", bp)
    result = watchdog._check_blueprint_guard_symlinks()
    assert result.severity is Severity.INFO
    assert "resolve OK" in result.summary


def test_blueprint_guard_symlinks_dangling_warns(monkeypatch, tmp_path):
    # Blueprint root exists, but the .claude guard symlinks dangle (target gone).
    bp = tmp_path / "its-blueprint"
    (bp / ".claude").mkdir(parents=True)
    (bp / ".claude" / "agents").symlink_to(tmp_path / "missing-agents")  # dangling
    (bp / ".claude" / "hooks").symlink_to(tmp_path / "missing-hooks")    # dangling
    monkeypatch.setattr("watchdog._BLUEPRINT_ROOT", bp)
    result = watchdog._check_blueprint_guard_symlinks()
    assert result.severity is Severity.WARN
    assert "fail-open" in result.summary
    assert ".claude/agents" in result.summary


def test_blueprint_guard_symlinks_skipped_when_absent(monkeypatch, tmp_path):
    monkeypatch.setattr("watchdog._BLUEPRINT_ROOT", tmp_path / "no-blueprint-here")
    result = watchdog._check_blueprint_guard_symlinks()
    assert result.severity is Severity.INFO
    assert "not on this host" in result.summary


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


# ---- Group N: _check_stuck_wsr_send (write-ahead-marker safety net) -------


def test_no_wsr_rows_stuck_in_sending(mock_get_rows):
    mock_get_rows.return_value = []

    result = watchdog._check_stuck_wsr_send()

    assert result.severity is Severity.INFO
    assert "No WSR rows stuck in SENDING" in result.summary


def test_wsr_rows_stuck_in_sending_warn(mock_get_rows):
    mock_get_rows.return_value = [{"_row_id": 50}, {"_row_id": 51}]

    result = watchdog._check_stuck_wsr_send()

    assert result.severity is Severity.WARN
    assert "2 WSR row(s) stuck in SENDING" in result.summary
    assert "50" in result.details and "51" in result.details


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


def test_maintenance_runs_with_alerts_suppressed(
    mock_check_state, mock_log, mock_get_setting, mock_ping, mocker
):
    mock_check_state.return_value = SystemState.MAINTENANCE
    mock_get_setting.return_value = "https://hc-ping.com/test-uuid"
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


def test_active_runs_normally(
    mock_check_state, mock_log, mock_get_setting, mock_ping, mocker
):
    mock_check_state.return_value = SystemState.ACTIVE
    mock_get_setting.return_value = "https://hc-ping.com/test-uuid"
    run_check_spy = mocker.patch("watchdog._run_check")

    watchdog.main()

    assert run_check_spy.call_count == len(watchdog.CHECKS)
    for call in run_check_spy.call_args_list:
        assert call.kwargs == {"alerts_suppressed": False}
    # No PAUSED/MAINTENANCE preamble line.
    messages = [c.args[2] for c in mock_log.call_args_list]
    assert not any("PAUSED" in m or "MAINTENANCE" in m for m in messages)


def test_main_writes_watchdog_marker_at_end(
    mock_check_state, mock_get_setting, mock_ping, mocker
):
    """`main()` calls write_last_run_marker('watchdog') after the check loop.

    PAUSED short-circuits before checks AND before the marker, so the
    marker is only written when state was ACTIVE or MAINTENANCE.
    """
    mock_check_state.return_value = SystemState.ACTIVE
    mock_get_setting.return_value = "https://hc-ping.com/test-uuid"
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


# ---- Group E2: main() F16 heartbeat beacon ------------------------------


def test_main_pings_heartbeat_with_configured_url(
    mock_check_state, mock_get_setting, mock_ping, mocker
):
    """ACTIVE run reads system.heartbeat_url and pings exactly that URL."""
    mock_check_state.return_value = SystemState.ACTIVE
    mocker.patch("watchdog._run_check")
    mock_get_setting.return_value = "https://hc-ping.com/real-uuid"

    watchdog.main()

    mock_get_setting.assert_called_once_with(
        "system.heartbeat_url", workstream="global"
    )
    mock_ping.assert_called_once_with("https://hc-ping.com/real-uuid")


def test_main_pings_heartbeat_during_maintenance(
    mock_check_state, mock_get_setting, mock_ping, mocker
):
    """MAINTENANCE still pings — the host is alive during a maintenance
    window, so suppressing the beacon would trip a false 'host dead' alert.
    Alert suppression applies to the checks' own alerts, not the beacon."""
    mock_check_state.return_value = SystemState.MAINTENANCE
    mocker.patch("watchdog._run_check")
    mock_get_setting.return_value = "https://hc-ping.com/real-uuid"

    watchdog.main()

    mock_ping.assert_called_once_with("https://hc-ping.com/real-uuid")


@pytest.mark.parametrize(
    "value",
    [None, "", "PLACEHOLDER_uptimerobot_heartbeat_url"],
)
def test_main_skips_ping_when_url_missing_or_placeholder(
    value, mock_check_state, mock_get_setting, mock_ping, mock_log, mocker
):
    """Unconfigured (missing/blank/seed-placeholder) URL → no ping, one INFO
    'not configured' line. The placeholder token MUST match the seed Value."""
    mock_check_state.return_value = SystemState.ACTIVE
    mocker.patch("watchdog._run_check")
    mock_get_setting.return_value = value

    watchdog.main()

    mock_ping.assert_not_called()
    messages = [c.args[2] for c in mock_log.call_args_list]
    assert any("not configured" in m for m in messages)


def test_main_paused_does_not_ping(
    mock_check_state, mock_get_setting, mock_ping, mocker
):
    """PAUSED returns before the marker AND the heartbeat block — no config
    read, no ping (a deliberately-paused system does not claim liveness)."""
    mock_check_state.return_value = SystemState.PAUSED
    mocker.patch("watchdog._run_check")

    watchdog.main()

    mock_ping.assert_not_called()
    mock_get_setting.assert_not_called()


def test_main_heartbeat_read_failure_is_swallowed(
    mock_check_state, mock_get_setting, mock_ping, mock_log, mocker
):
    """A SmartsheetError reading the URL is caught (WARN), the ping is
    skipped, and main() completes without raising — fail-soft per §3.1."""
    mock_check_state.return_value = SystemState.ACTIVE
    mocker.patch("watchdog._run_check")
    mock_get_setting.side_effect = SmartsheetError("config unreachable")

    # Must NOT raise.
    watchdog.main()

    mock_ping.assert_not_called()
    severities = [c.args[0] for c in mock_log.call_args_list]
    assert Severity.WARN in severities
    warn_msg = next(
        c.args[2] for c in mock_log.call_args_list if c.args[0] is Severity.WARN
    )
    assert "heartbeat_url read failed" in warn_msg


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


def test_check_scheduled_jobs_returns_info_when_tracked_jobs_empty(monkeypatch):
    """The empty-TRACKED_JOBS branch still exists and returns INFO.

    R3 Session 2 added safety_weekly_generate to TRACKED_JOBS, so this
    test temporarily empties the list to exercise the original no-op
    branch — useful coverage for the brief period before the first
    tracked job ships in any future forked customer repo.
    """
    monkeypatch.setattr("watchdog.TRACKED_JOBS", [])
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


# ---- Group H: Check F — RETIRED 2026-06-05 (safety mail-intake silent-disable removed) ----


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


# ---- Group J: Check G — MAINTENANCE defer (V1 fix) ----------------------


def test_check_g_skips_summary_fire_during_maintenance(
    dedupe_state, frozen_clock, mock_send_alert
):
    """MAINTENANCE: phase-1 entry must not fire Resend; summarized stays False;
    entry persists in state for the post-MAINTENANCE sweep."""
    import json as _json

    import shared.alert_dedupe as ad
    _seed_expired_entry("safety.intake::uncaught_exception", suppressed_count=4)

    result = watchdog._check_alert_dedupe_summaries(alerts_suppressed=True)

    mock_send_alert.assert_not_called()
    state = _json.loads(ad.STATE_FILE.read_text())
    # Entry persists, still unsummarized.
    assert "safety.intake::uncaught_exception" in state
    assert state["safety.intake::uncaught_exception"]["summarized"] is False
    # Result summary names the deferred count.
    assert "deferred 1 summar" in result.summary
    assert "MAINTENANCE" in result.summary
    assert "safety.intake::uncaught_exception" in result.details


def test_check_g_processes_phase2_delete_during_maintenance(
    dedupe_state, frozen_clock, mock_send_alert
):
    """Phase-2 deletion has no push side-effect, so it proceeds during
    MAINTENANCE — otherwise the state file would grow unboundedly
    while the kill switch is engaged."""
    import json as _json

    import shared.alert_dedupe as ad
    _seed_expired_entry(
        "safety.intake::uncaught_exception", suppressed_count=4, summarized=True
    )

    result = watchdog._check_alert_dedupe_summaries(alerts_suppressed=True)

    state = _json.loads(ad.STATE_FILE.read_text())
    assert "safety.intake::uncaught_exception" not in state
    mock_send_alert.assert_not_called()
    assert "deleted 1 entry" in result.summary


def test_check_g_processes_clean_expiry_delete_during_maintenance(
    dedupe_state, frozen_clock, mock_send_alert
):
    """A clean-expiry entry (suppressed_count==0) also lands in phase 2
    and deletes during MAINTENANCE — same no-push-side-effect rationale."""
    import json as _json

    import shared.alert_dedupe as ad
    _seed_expired_entry("safety.intake::uncaught_exception", suppressed_count=0)

    watchdog._check_alert_dedupe_summaries(alerts_suppressed=True)

    state = _json.loads(ad.STATE_FILE.read_text())
    assert "safety.intake::uncaught_exception" not in state
    mock_send_alert.assert_not_called()


def test_check_g_fires_normally_after_maintenance_ends(
    dedupe_state, frozen_clock, mock_send_alert
):
    """Sequence: first sweep during MAINTENANCE defers; second sweep
    after MAINTENANCE clears fires the deferred digest."""
    import json as _json

    import shared.alert_dedupe as ad
    _seed_expired_entry("safety.intake::uncaught_exception", suppressed_count=4)

    # First sweep — MAINTENANCE on.
    watchdog._check_alert_dedupe_summaries(alerts_suppressed=True)
    mock_send_alert.assert_not_called()
    state = _json.loads(ad.STATE_FILE.read_text())
    assert state["safety.intake::uncaught_exception"]["summarized"] is False

    # Second sweep — MAINTENANCE off.
    result = watchdog._check_alert_dedupe_summaries(alerts_suppressed=False)
    mock_send_alert.assert_called_once()
    subject, _ = mock_send_alert.call_args.args
    assert "safety.intake" in subject
    assert "4 suppressed" in subject
    state = _json.loads(ad.STATE_FILE.read_text())
    assert state["safety.intake::uncaught_exception"]["summarized"] is True
    assert "fired 1 summary" in result.summary


def test_check_g_default_arg_is_alerts_suppressed_false(
    dedupe_state, frozen_clock, mock_send_alert
):
    """Calling without the kwarg must NOT defer — safety default per spec."""
    _seed_expired_entry("safety.intake::uncaught_exception", suppressed_count=4)

    # No alerts_suppressed kwarg.
    watchdog._check_alert_dedupe_summaries()

    mock_send_alert.assert_called_once()


def test_check_g_mixed_entries_during_maintenance(
    dedupe_state, frozen_clock, mock_send_alert
):
    """Phase-1 entries defer; phase-2 entries delete; both happen in one
    MAINTENANCE sweep."""
    import json as _json

    import shared.alert_dedupe as ad

    _seed_expired_entry("phase1::uncaught_exception", suppressed_count=4)
    _seed_expired_entry("phase2_clean::uncaught_exception", suppressed_count=0)
    _seed_expired_entry(
        "phase2_summarized::uncaught_exception", suppressed_count=2, summarized=True
    )

    result = watchdog._check_alert_dedupe_summaries(alerts_suppressed=True)

    mock_send_alert.assert_not_called()
    state = _json.loads(ad.STATE_FILE.read_text())
    # Phase 1 deferred → still present, still unsummarized.
    assert "phase1::uncaught_exception" in state
    assert state["phase1::uncaught_exception"]["summarized"] is False
    # Both phase-2 entries deleted.
    assert "phase2_clean::uncaught_exception" not in state
    assert "phase2_summarized::uncaught_exception" not in state

    assert "fired 0 summary" in result.summary
    assert "deleted 2 entries" in result.summary
    assert "deferred 1 summary during MAINTENANCE" in result.summary


def test_run_check_threads_alerts_suppressed_to_check_g(
    dedupe_state, frozen_clock, mock_send_alert
):
    """_run_check must detect Check G's alerts_suppressed kwarg and pass it.

    Regression guard against the signature-inspection fork in _run_check
    (V1 fix). If the threading regresses, Check G would always run as if
    alerts_suppressed=False (default), re-introducing the V1 bug.
    """
    _seed_expired_entry("safety.intake::uncaught_exception", suppressed_count=4)

    watchdog._run_check(
        watchdog._check_alert_dedupe_summaries, alerts_suppressed=True
    )
    # Send NOT called — proves alerts_suppressed=True was threaded in.
    mock_send_alert.assert_not_called()


def test_run_check_does_not_pass_alerts_suppressed_to_legacy_checks(mocker):
    """_run_check inspects signature; legacy checks that take no args
    must still be called with no args (no TypeError from unexpected kwarg).
    """
    legacy_check = mocker.MagicMock(
        return_value=watchdog.CheckResult(
            severity=Severity.INFO, summary="ok"
        )
    )
    legacy_check.__name__ = "legacy_check"
    # Make signature inspection return zero params (mirrors the real
    # legacy checks).
    legacy_check.__signature__ = inspect.Signature(parameters=[])

    watchdog._run_check(legacy_check, alerts_suppressed=True)

    legacy_check.assert_called_once_with()  # zero args, NOT alerts_suppressed kwarg


# ---- Group I: Check I — weekly_generate catch-up recovery ----------------
#
# weekly_generate is the lone tracked daemon on a calendar schedule
# (StartCalendarInterval Friday 14:00), so a crashed Friday cycle is not
# re-invoked by launchd until the next Friday. Check I re-fires the missed
# generation on a subsequent daily watchdog run while inside a short window.

# Fixed offset standing in for the local timezone (the trigger fires in
# local time). Anchor scenario: the Friday 2026-06-05 14:00 trigger, whose
# ISO week's Monday is 2026-06-01.
_LOCAL = timezone(timedelta(hours=-7))
_LAST_TRIGGER = datetime(2026, 6, 5, 14, 0, tzinfo=_LOCAL)
_TARGET_MONDAY = date(2026, 6, 1)
# Saturday morning after the trigger — squarely inside the catch-up window.
_SAT_AFTER_TRIGGER = datetime(2026, 6, 6, 7, 0, tzinfo=_LOCAL)
# Tuesday morning — past the 3-day (through-Monday) catch-up window.
_TUE_OUT_OF_WINDOW = datetime(2026, 6, 9, 7, 0, tzinfo=_LOCAL)


@pytest.fixture
def catchup_now(monkeypatch):
    """Pin Check I's clock (_local_now). Returns a setter for each test."""
    def _set(now: datetime) -> None:
        monkeypatch.setattr("watchdog._local_now", lambda: now)
    return _set


@pytest.fixture
def mock_run_pipeline(mocker):
    """Mock weekly_generate._run_pipeline at the watchdog import path.

    Default return = a healthy run (5 drafts). Tests override return_value
    (empty-chain) or side_effect (failure).
    """
    return mocker.patch(
        "watchdog.weekly_generate._run_pipeline",
        return_value={
            "drafts_written": 5,
            "drafts_failed": 0,
            "aborted_empty_chain": False,
            "correlation_id": "abc123def456",
            "week_start": _TARGET_MONDAY.isoformat(),
        },
    )


@pytest.fixture
def mock_alert_critical(mocker):
    """Mock the triple-fire push leg so no real Resend/Sentry page fires."""
    return mocker.patch("watchdog._alert_critical")


def _write_wg_marker(ts: datetime) -> None:
    """Write the safety_weekly_generate marker with an explicit timestamp."""
    watchdog.WATCHDOG_MARKER_DIR.mkdir(parents=True, exist_ok=True)
    (watchdog.WATCHDOG_MARKER_DIR / "safety_weekly_generate.last_run").write_text(
        ts.isoformat()
    )


@pytest.mark.parametrize(
    "now, expect_trigger, expect_target_monday",
    [
        # Friday before 14:00 → most recent trigger is LAST Friday (this
        # week's run hasn't fired yet; last week is the most recent trigger).
        (
            datetime(2026, 6, 5, 7, 0, tzinfo=_LOCAL),
            datetime(2026, 5, 29, 14, 0, tzinfo=_LOCAL),
            "2026-05-25",
        ),
        # Friday after 14:00 → this Friday.
        (
            datetime(2026, 6, 5, 15, 0, tzinfo=_LOCAL),
            datetime(2026, 6, 5, 14, 0, tzinfo=_LOCAL),
            "2026-06-01",
        ),
        # Saturday → yesterday's Friday.
        (
            datetime(2026, 6, 6, 7, 0, tzinfo=_LOCAL),
            datetime(2026, 6, 5, 14, 0, tzinfo=_LOCAL),
            "2026-06-01",
        ),
        # Monday → the previous Friday.
        (
            datetime(2026, 6, 8, 7, 0, tzinfo=_LOCAL),
            datetime(2026, 6, 5, 14, 0, tzinfo=_LOCAL),
            "2026-06-01",
        ),
    ],
)
def test_most_recent_friday_trigger(now, expect_trigger, expect_target_monday):
    trigger = watchdog._most_recent_friday_trigger(now)
    assert trigger == expect_trigger
    # Target week = the Monday of the trigger Friday's ISO week (Friday - 4d).
    assert (trigger - timedelta(days=4)).date().isoformat() == expect_target_monday


def test_check_i_registered_in_checks_after_check_c():
    assert watchdog._check_weekly_generate_catchup in watchdog.CHECKS
    # Check I recovers what Check C only detects, so it must run after it.
    assert watchdog.CHECKS.index(
        watchdog._check_weekly_generate_catchup
    ) > watchdog.CHECKS.index(watchdog._check_scheduled_jobs)


def test_catchup_no_fire_when_marker_fresh(catchup_now, mock_run_pipeline):
    """(a) Generation ran this week (marker fresh) → no catch-up.

    Marker written in UTC (as weekly_generate actually does) AFTER the local
    Friday 14:00 trigger — exercises the cross-timezone marker comparison.
    """
    catchup_now(_SAT_AFTER_TRIGGER)
    # 2026-06-05 22:00 UTC == 15:00 -07:00, i.e. after the 14:00 local trigger.
    _write_wg_marker(datetime(2026, 6, 5, 22, 0, tzinfo=UTC))

    result = watchdog._check_weekly_generate_catchup()

    assert result.severity is Severity.INFO
    assert "ran for week" in result.summary
    mock_run_pipeline.assert_not_called()


def test_catchup_fires_when_missing_in_window(
    catchup_now, mock_run_pipeline, mock_get_rows
):
    """(b) No marker + no rows + in window → catch-up fires exactly once."""
    catchup_now(_SAT_AFTER_TRIGGER)
    mock_get_rows.return_value = []  # no WPR rows for the target week

    result = watchdog._check_weekly_generate_catchup()

    mock_run_pipeline.assert_called_once_with(week_start_override=_TARGET_MONDAY)
    assert result.severity is Severity.INFO
    assert "catch-up fired" in result.summary
    assert "5 draft" in result.summary


def test_catchup_no_fire_outside_window(
    catchup_now, mock_run_pipeline, mock_get_rows
):
    """(c) Missed, but now is past the catch-up window → no catch-up."""
    catchup_now(_TUE_OUT_OF_WINDOW)
    mock_get_rows.return_value = []

    result = watchdog._check_weekly_generate_catchup()

    assert result.severity is Severity.INFO
    assert "past the catch-up window" in result.summary
    mock_run_pipeline.assert_not_called()


def test_catchup_no_fire_when_rows_present_marker_stale(
    catchup_now, mock_run_pipeline, mock_get_rows
):
    """Robustness: marker stale/missing BUT WPR rows exist (fail-soft marker
    write left a stale marker on an otherwise-successful run) → no re-fire."""
    catchup_now(_SAT_AFTER_TRIGGER)
    mock_get_rows.return_value = [{"_row_id": 1, "Week": _TARGET_MONDAY.isoformat()}]

    result = watchdog._check_weekly_generate_catchup()

    assert result.severity is Severity.INFO
    assert "rows present" in result.summary
    mock_run_pipeline.assert_not_called()


def test_catchup_failure_triple_fires_critical_no_loop(
    catchup_now, mock_run_pipeline, mock_get_rows, mock_alert_critical, mock_log
):
    """(d) Catch-up generation raises → CRITICAL triple-fire, fired once."""
    catchup_now(_SAT_AFTER_TRIGGER)
    mock_get_rows.return_value = []
    mock_run_pipeline.side_effect = RuntimeError("generation boom")

    result = watchdog._check_weekly_generate_catchup()

    # No loop — generation attempted exactly once.
    mock_run_pipeline.assert_called_once()
    # Operator paged via the triple-fire push legs.
    mock_alert_critical.assert_called_once()
    assert (
        mock_alert_critical.call_args.kwargs["error_code"]
        == "weekly_generate_catchup_failed"
    )
    # CRITICAL record row written with a matching distinct error_code.
    crit_calls = [c for c in mock_log.call_args_list if c.args[0] is Severity.CRITICAL]
    assert len(crit_calls) == 1
    assert crit_calls[0].kwargs["error_code"] == "weekly_generate_catchup_failed"
    # A3: the record log MUST opt out of auto-paging (alert=False) so the
    # explicit, MAINTENANCE-deferrable page below is the ONLY page. Dropping
    # this kwarg would double-fire and page during MAINTENANCE (live, not in
    # these mocked tests) — this assertion is the regression lock.
    assert crit_calls[0].kwargs["alert"] is False
    # Row + page share one correlation_id (so a single grep recovers all legs).
    assert (
        crit_calls[0].kwargs["correlation_id"]
        == mock_alert_critical.call_args.kwargs["correlation_id"]
    )
    assert result.severity is Severity.INFO  # alerting already fired explicitly
    assert "FAILED" in result.summary


def test_catchup_paused_skips_via_main(mock_check_state, mock_run_pipeline):
    """(e) PAUSED → main() returns before the checks loop; no catch-up."""
    mock_check_state.return_value = SystemState.PAUSED

    watchdog.main()

    mock_run_pipeline.assert_not_called()


def test_catchup_maintenance_runs_but_defers_page(
    catchup_now, mock_run_pipeline, mock_get_rows, mock_alert_critical, mock_log
):
    """(f) MAINTENANCE: generation RUNS but a failure's page is DEFERRED;
    the CRITICAL record row is still written (push-vs-record, Op Stds §3.1)."""
    catchup_now(_SAT_AFTER_TRIGGER)
    mock_get_rows.return_value = []
    mock_run_pipeline.side_effect = RuntimeError("boom")

    result = watchdog._check_weekly_generate_catchup(alerts_suppressed=True)

    mock_run_pipeline.assert_called_once()  # generation RAN during MAINTENANCE
    mock_alert_critical.assert_not_called()  # operator page DEFERRED
    # Record row still written at CRITICAL (forensic trail preserved).
    severities = [c.args[0] for c in mock_log.call_args_list]
    assert Severity.CRITICAL in severities
    assert "page deferred" in result.summary


def test_catchup_maintenance_success_runs(
    catchup_now, mock_run_pipeline, mock_get_rows
):
    """(f) MAINTENANCE happy path: generation still runs (not @require_active
    blocked) and succeeds."""
    catchup_now(_SAT_AFTER_TRIGGER)
    mock_get_rows.return_value = []

    result = watchdog._check_weekly_generate_catchup(alerts_suppressed=True)

    mock_run_pipeline.assert_called_once_with(week_start_override=_TARGET_MONDAY)
    assert result.severity is Severity.INFO
    assert "catch-up fired" in result.summary


def test_run_check_threads_alerts_suppressed_to_check_i(
    catchup_now, mock_run_pipeline, mock_get_rows, mock_alert_critical
):
    """_run_check must detect Check I's alerts_suppressed kwarg and thread it
    (signature-inspection fork). If it regresses, the MAINTENANCE page would
    fire."""
    catchup_now(_SAT_AFTER_TRIGGER)
    mock_get_rows.return_value = []
    mock_run_pipeline.side_effect = RuntimeError("boom")

    watchdog._run_check(
        watchdog._check_weekly_generate_catchup, alerts_suppressed=True
    )

    mock_run_pipeline.assert_called_once()  # generation ran
    mock_alert_critical.assert_not_called()  # threaded → page deferred


def test_check_i_default_arg_alerts_suppressed_false(
    catchup_now, mock_run_pipeline, mock_get_rows, mock_alert_critical
):
    """Calling Check I without the kwarg must NOT defer — safety default."""
    catchup_now(_SAT_AFTER_TRIGGER)
    mock_get_rows.return_value = []
    mock_run_pipeline.side_effect = RuntimeError("boom")

    watchdog._check_weekly_generate_catchup()  # no kwarg

    mock_alert_critical.assert_called_once()  # NOT deferred


def test_catchup_empty_chain_returns_warn(
    catchup_now, mock_run_pipeline, mock_get_rows, mock_alert_critical
):
    """Empty reviewer chain: weekly_generate already recorded its own
    CRITICAL → Check I surfaces WARN and does NOT double-escalate."""
    catchup_now(_SAT_AFTER_TRIGGER)
    mock_get_rows.return_value = []
    mock_run_pipeline.return_value = {
        "aborted_empty_chain": True,
        "drafts_written": 0,
        "drafts_failed": 0,
        "correlation_id": "x",
    }

    result = watchdog._check_weekly_generate_catchup()

    assert result.severity is Severity.WARN
    assert "empty reviewer chain" in result.summary
    mock_alert_critical.assert_not_called()


def test_wsr_rows_exist_failsoft_returns_false(mock_get_rows, mock_log):
    """A Smartsheet read failure during evaluation fails soft → False (the
    decision falls back to the marker signal) and logs a WARN."""
    mock_get_rows.side_effect = SmartsheetError("boom")

    assert watchdog._wsr_rows_exist_for_week(_TARGET_MONDAY) is False

    severities = [c.args[0] for c in mock_log.call_args_list]
    assert Severity.WARN in severities


# ---- Check J: circuit-breaker prolonged-open (F08/F09 PR 2) --------------


def test_prolonged_open_pages_past_threshold(mocker):
    mocker.patch("watchdog.circuit_breaker.seconds_open", return_value=700.0)
    mocker.patch("watchdog.smartsheet_client.get_setting", return_value="600")
    alert = mocker.patch("watchdog._alert_critical")
    log_mock = mocker.patch("watchdog.log")

    result = watchdog._check_circuit_breaker_prolonged_open()

    alert.assert_called_once()
    assert alert.call_args.kwargs["error_code"] == "circuit_breaker_prolonged_open"
    assert result.severity is Severity.INFO
    # A3: the record log MUST opt out of auto-paging (alert=False); the page
    # fires explicitly under bypass below. Regression lock (the live double-fire
    # / page-in-MAINTENANCE would otherwise be invisible to these mocked tests).
    crit = [c for c in log_mock.call_args_list if c.args[0] is Severity.CRITICAL]
    assert crit and crit[0].kwargs["alert"] is False


def test_prolonged_open_silent_under_threshold(mocker):
    mocker.patch("watchdog.circuit_breaker.seconds_open", return_value=120.0)
    mocker.patch("watchdog.smartsheet_client.get_setting", return_value="600")
    alert = mocker.patch("watchdog._alert_critical")

    result = watchdog._check_circuit_breaker_prolonged_open()

    alert.assert_not_called()
    assert result.severity is Severity.WARN


def test_prolonged_open_not_open_no_alert(mocker):
    mocker.patch("watchdog.circuit_breaker.seconds_open", return_value=None)
    alert = mocker.patch("watchdog._alert_critical")

    result = watchdog._check_circuit_breaker_prolonged_open()

    alert.assert_not_called()
    assert result.severity is Severity.INFO


def test_prolonged_open_threshold_read_fails_open_to_default(mocker):
    """The threshold read short-circuits during the outage; the check must fail
    open to the default (600), so a 700s outage still pages."""
    mocker.patch("watchdog.circuit_breaker.seconds_open", return_value=700.0)
    mocker.patch(
        "watchdog.smartsheet_client.get_setting",
        side_effect=watchdog.smartsheet_client.SmartsheetCircuitOpenError("open"),
    )
    alert = mocker.patch("watchdog._alert_critical")
    mocker.patch("watchdog.log")

    watchdog._check_circuit_breaker_prolonged_open()

    alert.assert_called_once()  # 700 > default 600 → page


def test_prolonged_open_page_runs_under_bypass(mocker):
    """The page MUST fire inside circuit_breaker.bypass() — otherwise the Resend
    leg's operator_email read short-circuits on the very-OPEN breaker."""
    import shared.circuit_breaker as cb

    mocker.patch("watchdog.circuit_breaker.seconds_open", return_value=700.0)
    mocker.patch("watchdog.smartsheet_client.get_setting", return_value="600")
    mocker.patch("watchdog.log")
    seen: dict[str, int] = {}
    mocker.patch(
        "watchdog._alert_critical",
        side_effect=lambda *a, **k: seen.update(depth=cb._bypass_depth),
    )

    watchdog._check_circuit_breaker_prolonged_open()

    assert seen.get("depth", 0) > 0


def test_prolonged_open_maintenance_defers_page(mocker):
    mocker.patch("watchdog.circuit_breaker.seconds_open", return_value=700.0)
    mocker.patch("watchdog.smartsheet_client.get_setting", return_value="600")
    alert = mocker.patch("watchdog._alert_critical")
    mocker.patch("watchdog.log")

    result = watchdog._check_circuit_breaker_prolonged_open(alerts_suppressed=True)

    alert.assert_not_called()  # page deferred during MAINTENANCE
    assert "deferred" in result.summary.lower()


# ---- Check K: guaranteed F09 cap-window-summary sweep (F08/F09 PR 2) -----


def test_cap_window_sweep_calls_maybe_fire(mocker):
    fire = mocker.patch("watchdog._maybe_fire_window_summary")

    result = watchdog._check_alert_rate_cap_window()

    fire.assert_called_once()
    assert result.severity is Severity.INFO


def test_cap_window_sweep_deferred_in_maintenance(mocker):
    fire = mocker.patch("watchdog._maybe_fire_window_summary")

    result = watchdog._check_alert_rate_cap_window(alerts_suppressed=True)

    fire.assert_not_called()
    assert "defer" in result.summary.lower()
