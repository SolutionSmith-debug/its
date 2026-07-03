"""Orchestration tests for the on-demand Compile-Now poller (Part B). The compile itself
(weekly_generate._compile_job_week) + all Smartsheet I/O are mocked; this verifies the poll
loop: gate, single-flight lock, trigger-honored, selection narrowing, and fail-loud."""
from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace

import pytest

import shared.kill_switch as ks
from safety_reports import compile_now_poll as cnp


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
    return {
        "enabled": mocker.patch.object(cnp, "_polling_enabled", return_value=True),
        "lock": mocker.patch.object(cnp, "_file_lock", side_effect=lambda *a, **k: _cm(True)),
        "jobs": mocker.patch.object(cnp.active_jobs, "list_active_jobs", return_value=[]),
        "ensure": mocker.patch.object(cnp.week_sheet, "ensure_week_sheet", return_value=111),
        "rollup": mocker.patch.object(cnp.week_sheet, "list_rollup_rows", return_value=[{"_row_id": 1}]),
        "subs": mocker.patch.object(cnp.week_sheet, "list_submission_rows", return_value=[]),
        "requested": mocker.patch.object(cnp.week_sheet, "any_compile_now_requested", return_value=False),
        "selected": mocker.patch.object(cnp.week_sheet, "selected_submission_row_ids", return_value=set()),
        "clear": mocker.patch.object(cnp.week_sheet, "clear_compile_now"),
        "compile": mocker.patch.object(cnp.weekly_generate, "_compile_job_week"),
        "rq": mocker.patch.object(cnp.weekly_generate, "_safe_review_queue"),
        "marker": mocker.patch.object(cnp, "_write_watchdog_marker"),
        # R4-F1: the daemon now writes an ITS_Daemon_Health heartbeat per cycle. Mock the
        # two thin-delegator seams so no test touches live state / Smartsheet.
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
    stub["enabled"].return_value = False
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
    assert any(c.args and c.args[0] == cnp.Severity.ERROR for c in stub["log"].call_args_list)


def test_per_job_fence_one_bad_job_does_not_block_others(stub):
    stub["jobs"].return_value = [_job("A", "JOB-A"), _job("B", "JOB-B")]
    stub["requested"].return_value = True
    stub["compile"].side_effect = [RuntimeError("boom"), None]  # job A fails, B compiles
    out = cnp.poll_once()
    assert out.errors == 1 and out.compiled == 1  # B still compiled


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
    stub["enabled"].return_value = False
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
