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
        "rollup": mocker.patch.object(cnp.week_sheet, "get_rollup_row", return_value={"_row_id": 1}),
        "subs": mocker.patch.object(cnp.week_sheet, "list_submission_rows", return_value=[]),
        "requested": mocker.patch.object(cnp.week_sheet, "compile_now_requested", return_value=False),
        "selected": mocker.patch.object(cnp.week_sheet, "selected_submission_row_ids", return_value=set()),
        "clear": mocker.patch.object(cnp.week_sheet, "clear_compile_now"),
        "compile": mocker.patch.object(cnp.weekly_generate, "_compile_job_week"),
        "rq": mocker.patch.object(cnp.weekly_generate, "_safe_review_queue"),
        "marker": mocker.patch.object(cnp, "_write_watchdog_marker"),
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
