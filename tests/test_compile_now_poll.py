"""Orchestration tests for the on-demand Compile-Now poller (Part B), now cross-workstream.

The compile itself (generate_core._compile_job_week) + all Smartsheet I/O are mocked; this verifies
the poll loop: per-workstream gate, single-flight lock, trigger-honored, selection narrowing,
fail-loud, and the safety+progress iteration (§14 parameterize-not-clone — ONE daemon, one lock, one
heartbeat).

Default fixture posture: only the SAFETY workstream is enabled, so the single-workstream assertions
below read exactly as they did pre-generalization; the cross-workstream tests flip progress on.
"""
from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace

import pytest

import shared.kill_switch as ks
from safety_reports import compile_now_poll as cnp
from shared.smartsheet_client import SmartsheetError

SAFETY_CFG = cnp.weekly_generate.SAFETY_GENERATE_CONFIG
PROGRESS_CFG = cnp.progress_weekly_generate.PROGRESS_GENERATE_CONFIG


def _job(name: str = "ZZ Job", jid: str = "JOB-1") -> SimpleNamespace:
    # The daemon only reads .project_name / .job_id; _compile_job_week (which takes the real
    # ActiveJob) is mocked, so a namespace stub is sufficient here.
    return SimpleNamespace(project_name=name, job_id=jid)


@contextmanager
def _cm(value: bool):
    yield value


@pytest.fixture
def stub(mocker):
    mocker.patch.object(ks, "check_system_state", return_value=ks.SystemState.ACTIVE)
    # Default: SAFETY enabled, PROGRESS disabled → the legacy single-workstream behaviour. Tests
    # exercising progress mutate `enabled_ws` (the closure reads this exact dict object).
    enabled_ws = {"safety_reports": True, "progress_reports": False}

    def _enabled(config):
        return enabled_ws.get(config.workstream, False)

    return {
        "enabled_ws": enabled_ws,
        "enabled": mocker.patch.object(cnp, "_polling_enabled", side_effect=_enabled),
        "lock": mocker.patch.object(cnp, "_file_lock", side_effect=lambda *a, **k: _cm(True)),
        "jobs": mocker.patch.object(cnp.active_jobs, "list_active_jobs", return_value=[]),
        "ensure": mocker.patch.object(cnp.week_sheet, "ensure_week_sheet", return_value=111),
        "rollup": mocker.patch.object(cnp.week_sheet, "list_rollup_rows", return_value=[{"_row_id": 1}]),
        "subs": mocker.patch.object(cnp.week_sheet, "list_submission_rows", return_value=[]),
        "requested": mocker.patch.object(cnp.week_sheet, "any_compile_now_requested", return_value=False),
        "selected": mocker.patch.object(cnp.week_sheet, "selected_submission_row_ids", return_value=set()),
        "clear": mocker.patch.object(cnp.week_sheet, "clear_compile_now"),
        # The compile + review-queue fence are the SHARED generate_core primitives (config-bound).
        "compile": mocker.patch.object(cnp.generate_core, "_compile_job_week"),
        "rq": mocker.patch.object(cnp.generate_core, "_safe_review_queue"),
        "marker": mocker.patch.object(cnp, "_write_watchdog_marker"),
        # R4-F1: the daemon writes an ITS_Daemon_Health heartbeat per cycle. Mock the two
        # thin-delegator seams so no test touches live state / Smartsheet.
        "hb": mocker.patch.object(cnp, "_write_heartbeat"),
        "hb_row": mocker.patch.object(cnp, "_write_heartbeat_row"),
        "circuit": mocker.patch.object(cnp.circuit_breaker, "is_open", return_value=False),
        "log": mocker.patch.object(cnp.error_log, "log"),
    }


def test_no_trigger_skips_the_job(stub):
    stub["jobs"].return_value = [_job()]
    stub["requested"].return_value = False  # no Compile Now on the Rollup row
    out = cnp.poll_once()
    assert out.jobs_scanned == 1 and out.triggered == 0 and out.compiled == 0
    stub["compile"].assert_not_called()  # on-demand only — never auto-compiles


def test_trigger_compiles_and_clears(stub):
    stub["jobs"].return_value = [_job()]
    stub["requested"].return_value = True
    out = cnp.poll_once()
    assert out.triggered == 1 and out.compiled == 1 and out.errors == 0
    stub["compile"].assert_called_once()
    # the compile is bound to the SAFETY config (first positional arg)
    assert stub["compile"].call_args.args[0] is SAFETY_CFG
    stub["clear"].assert_called_once()  # the per-submission selection is cleared on success


def test_selection_narrows_the_packet(stub):
    stub["jobs"].return_value = [_job()]
    stub["requested"].return_value = True
    stub["selected"].return_value = {5, 6}
    cnp.poll_once()
    assert stub["compile"].call_args.kwargs["selection"] == {5, 6}
    stub["clear"].assert_called_once_with(111, {5, 6})


def test_default_all_when_no_submission_selected(stub):
    stub["jobs"].return_value = [_job()]
    stub["requested"].return_value = True
    stub["selected"].return_value = set()  # nothing checked → full week
    cnp.poll_once()
    assert stub["compile"].call_args.kwargs["selection"] is None  # default-all


def test_single_flight_when_locked(stub):
    stub["jobs"].return_value = [_job()]
    stub["lock"].side_effect = lambda *a, **k: _cm(False)  # another cycle holds the lock
    out = cnp.poll_once()
    assert out.halted == "locked"
    stub["jobs"].assert_not_called()
    stub["compile"].assert_not_called()


def test_polling_disabled_halts(stub):
    stub["enabled"].side_effect = lambda config: False  # ALL workstreams off
    out = cnp.poll_once()
    assert out.halted == "polling_disabled"
    stub["jobs"].assert_not_called()


def test_compile_failure_is_fail_loud(stub):
    stub["jobs"].return_value = [_job()]
    stub["requested"].return_value = True
    stub["compile"].side_effect = RuntimeError("box down")
    out = cnp.poll_once()
    assert out.errors == 1 and out.compiled == 0
    stub["clear"].assert_not_called()   # the trigger STAYS SET (never reached the clear)
    stub["rq"].assert_called_once()     # surfaced to the Review Queue
    # the fence routes to the SAFETY config's review queue (first positional arg)
    assert stub["rq"].call_args.args[0] is SAFETY_CFG
    assert any(c.args and c.args[0] == cnp.Severity.ERROR for c in stub["log"].call_args_list)


def test_per_job_fence_one_bad_job_does_not_block_others(stub):
    stub["jobs"].return_value = [_job("A", "JOB-A"), _job("B", "JOB-B")]
    stub["requested"].return_value = True
    stub["compile"].side_effect = [RuntimeError("boom"), None]  # job A fails, B compiles
    out = cnp.poll_once()
    assert out.errors == 1 and out.compiled == 1  # B still compiled


def test_scan_failure_on_untriggered_job_is_scan_failed_no_review_row(stub):
    """A transient Smartsheet error during the ROUTINE trigger scan (BEFORE the trigger is
    confirmed set) logs `scan_failed` and does NOT seed a Review-Queue row — mislabeling it
    compile_failed fed a multi-hundred-row review backlog during Smartsheet outages."""
    stub["jobs"].return_value = [_job()]
    stub["rollup"].side_effect = SmartsheetError("read timeout")  # scan-phase blip
    out = cnp.poll_once()
    assert out.errors == 1 and out.compiled == 0
    stub["compile"].assert_not_called()
    stub["rq"].assert_not_called()  # NO Review-Queue row for a scan blip
    codes = [kw.get("error_code") for _, kw in stub["log"].call_args_list]
    assert "compile_now_poll.scan_failed" in codes
    assert "compile_now_poll.compile_failed" not in codes


def test_compile_failure_after_trigger_confirmed_keeps_review_row(stub):
    """Phase contrast: the SAME transient during the actual compile of a TRIGGERED job keeps
    today's fail-loud behaviour exactly — compile_failed + Review-Queue row."""
    stub["jobs"].return_value = [_job()]
    stub["requested"].return_value = True
    stub["compile"].side_effect = SmartsheetError("read timeout")
    out = cnp.poll_once()
    assert out.errors == 1
    stub["rq"].assert_called_once()
    codes = [kw.get("error_code") for _, kw in stub["log"].call_args_list]
    assert "compile_now_poll.compile_failed" in codes
    assert "compile_now_poll.scan_failed" not in codes


# ---- Cross-workstream iteration (§14 parameterize-not-clone) ---------------


def test_both_workstreams_iterate_when_enabled(stub):
    stub["enabled_ws"]["progress_reports"] = True  # enable progress too
    safety_job = _job("Safety Job", "JOB-S")
    progress_job = _job("Progress Job", "JOB-P")

    def _jobs(config=cnp.active_jobs.SAFETY_ACTIVE_JOBS_CONFIG):
        # config here is an ActiveJobsConfig — distinguish by identity.
        if config is cnp.active_jobs.PROGRESS_ACTIVE_JOBS_CONFIG:
            return [progress_job]
        return [safety_job]

    stub["jobs"].side_effect = _jobs
    stub["requested"].return_value = True
    out = cnp.poll_once()
    assert out.jobs_scanned == 2 and out.compiled == 2 and out.errors == 0
    assert stub["compile"].call_count == 2
    compiled_ws = {c.args[0].workstream for c in stub["compile"].call_args_list}
    assert compiled_ws == {"safety_reports", "progress_reports"}
    # ONE aggregate heartbeat for the whole cycle (not one per workstream).
    stub["hb"].assert_called_once()
    assert stub["hb_row"].call_args.kwargs["items_processed"] == 2
    stub["marker"].assert_called_once()


def test_disabled_workstream_is_not_iterated(stub):
    # progress stays disabled (default); even with a triggered progress job it must be skipped.
    def _jobs(config=cnp.active_jobs.SAFETY_ACTIVE_JOBS_CONFIG):
        if config is cnp.active_jobs.PROGRESS_ACTIVE_JOBS_CONFIG:
            return [_job("Progress Job", "JOB-P")]
        return [_job("Safety Job", "JOB-S")]

    stub["jobs"].side_effect = _jobs
    stub["requested"].return_value = True
    out = cnp.poll_once()
    assert out.jobs_scanned == 1  # only safety iterated
    assert stub["compile"].call_count == 1
    assert stub["compile"].call_args.args[0] is SAFETY_CFG


def test_only_progress_enabled_compiles_progress(stub):
    stub["enabled_ws"]["safety_reports"] = False
    stub["enabled_ws"]["progress_reports"] = True
    stub["jobs"].return_value = [_job("Progress Job", "JOB-P")]
    stub["requested"].return_value = True
    out = cnp.poll_once()
    assert out.compiled == 1
    assert stub["compile"].call_args.args[0] is PROGRESS_CFG


def test_compile_configs_bind_both_workstreams():
    # Pin the served set (self-provision / no-mix-up): exactly safety + progress, in order.
    assert [c.workstream for c in cnp.COMPILE_CONFIGS] == ["safety_reports", "progress_reports"]


# ---- ITS_Daemon_Health heartbeat (R4-F1) ----------------------------------


def test_cycle_writes_ok_heartbeat_before_the_marker(stub):
    stub["jobs"].return_value = [_job()]
    stub["requested"].return_value = True
    cnp.poll_once()
    stub["hb"].assert_called_once()
    assert stub["hb_row"].call_args.kwargs["status"] == "OK"
    assert stub["hb_row"].call_args.kwargs["items_processed"] == 1
    assert stub["hb_row"].call_args.kwargs["error_summary"] is None
    stub["marker"].assert_called_once()


def test_per_job_error_writes_degraded_heartbeat(stub):
    stub["jobs"].return_value = [_job()]
    stub["requested"].return_value = True
    stub["compile"].side_effect = RuntimeError("boom")
    cnp.poll_once()
    assert stub["hb_row"].call_args.kwargs["status"] == "DEGRADED"
    assert stub["hb_row"].call_args.kwargs["error_summary"] == "errors=1"


def test_open_circuit_writes_circuit_open_heartbeat(stub):
    stub["circuit"].return_value = True
    cnp.poll_once()
    assert stub["hb_row"].call_args.kwargs["status"] == "CIRCUIT_OPEN"


def test_disabled_cycle_skips_heartbeat(stub):
    stub["enabled"].side_effect = lambda config: False
    cnp.poll_once()
    stub["hb"].assert_not_called()
    stub["hb_row"].assert_not_called()


def test_locked_cycle_skips_heartbeat(stub):
    stub["lock"].side_effect = lambda *a, **k: _cm(False)
    cnp.poll_once()
    stub["hb"].assert_not_called()
    stub["hb_row"].assert_not_called()


def test_heartbeat_row_failure_never_blocks_the_cycle(stub):
    # The reporter itself never raises, but the outer-catch fence must still hold if the
    # delegator does (heartbeat-never-blocks; mirrors fieldops_sync).
    stub["jobs"].return_value = [_job()]
    stub["requested"].return_value = True
    stub["hb_row"].side_effect = RuntimeError("sheet down")
    out = cnp.poll_once()
    assert out.compiled == 1  # primary work already done; the fence swallowed the raise
    stub["marker"].assert_called_once()  # cycle continued through to the marker
    assert any(
        c.kwargs.get("error_code") == "daemon_health_write_failed"
        for c in stub["log"].call_args_list
    )


def test_reporter_registration_metadata_is_self_provisioning_config(stub):
    # A1 self-provision rides constructor config — pin the registration identity so the
    # ITS_Daemon_Health row this daemon creates is stable (shared row-state file, ARCH-2).
    r = cnp._heartbeat_reporter
    assert r.daemon_name == "safety_reports.compile_now_poll"
    assert r.workstream == "safety_reports"
    assert r.interval_seconds == 90
    assert r.row_state_path.name == "heartbeat_row_ids.json"


# ---- config-read transient fence (DASH-14 port of PR #613) ----------------


def test_read_str_setting_transient_error_falls_open_with_warn(mocker):
    """A generic SmartsheetError from get_setting (read-timeout / 5xx) must NOT escape
    to @its_error_log as a spurious CRITICAL — WARN `config_read_error` + fallback,
    same disposition as the circuit-open branch."""
    mocker.patch(
        "safety_reports.compile_now_poll.smartsheet_client.get_setting",
        side_effect=SmartsheetError("read timeout"),
    )
    log = mocker.patch("safety_reports.compile_now_poll.error_log.log")

    result = cnp._read_str_setting(
        "safety_reports.some_key", "safety_reports", "fallback-val"
    )  # must not raise

    assert result == "fallback-val"
    codes = [kw.get("error_code") for _, kw in log.call_args_list]
    assert "config_read_error" in codes


def test_polling_gate_transient_error_resolves_to_default(mocker):
    """Cycle-entry proof: the per-workstream polling gate read survives a transient and
    resolves to the default (True) instead of crashing the cycle."""
    mocker.patch(
        "safety_reports.compile_now_poll.smartsheet_client.get_setting",
        side_effect=SmartsheetError("HTTP 502"),
    )
    mocker.patch("safety_reports.compile_now_poll.error_log.log")

    assert cnp._polling_enabled(SAFETY_CFG) is cnp.DEFAULT_POLLING_ENABLED
