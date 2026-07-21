"""Orchestration tests for the on-demand Compile-Now poller (Part B), now cross-workstream.

The compile itself (generate_core._compile_job_week) + all Smartsheet I/O are mocked; this verifies
the poll loop: per-workstream gate, single-flight lock, trigger-honored, selection narrowing,
fail-loud, and the safety+progress iteration (§14 parameterize-not-clone — ONE daemon, one lock, one
heartbeat).

Default fixture posture: only the SAFETY workstream is enabled, so the single-workstream assertions
below read exactly as they did pre-generalization; the cross-workstream tests flip progress on.
"""
from __future__ import annotations

import json
import re
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
        # Scan summarization + escalation state. Mocked so no test touches live
        # ~/its/state (the conftest live-state guard raises on that) and so each test
        # drives the consecutive counts it wants.
        "jobs_failed": mocker.patch.object(
            cnp.active_jobs, "last_read_failed", return_value=False
        ),
        "scan_record": mocker.patch.object(cnp._SCAN_FAILS, "record", return_value=1),
        "scan_reset": mocker.patch.object(cnp._SCAN_FAILS, "reset"),
        "ledger": mocker.patch.object(cnp._JOB_LEDGER, "apply", return_value={}),
    }


def _rows(log_mock) -> list[tuple[object, str | None, str]]:
    """(severity, error_code, message) for every ITS_Errors row a cycle wrote."""
    return [
        (c.args[0], c.kwargs.get("error_code"), c.args[2]) for c in log_mock.call_args_list
    ]


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


# ---- Scan-failure summarization + sustained escalation --------------------

ROLLUP_OK: list[dict[str, int]] = [{"_row_id": 1}]


def _coded(log_mock, code: str) -> list[tuple[object, str | None, str]]:
    return [r for r in _rows(log_mock) if r[1] == code]


def test_scan_failures_summarize_to_one_row_naming_every_failing_job(stub):
    """WAS one ERROR row PER JOB per failing cycle (31 rows in a day). Now: one row."""
    stub["jobs"].return_value = [_job("A", "JOB-A"), _job("B", "JOB-B"), _job("C", "JOB-C")]
    stub["rollup"].side_effect = SmartsheetError("HTTP 500 errorCode 4000")

    out = cnp.poll_once()

    assert out.errors == 3 and out.scan_failures == 3 and out.jobs_scanned == 3
    rows = _coded(stub["log"], "compile_now_poll.scan_failed")
    assert len(rows) == 1
    severity, _, message = rows[0]
    assert severity == cnp.Severity.ERROR
    assert "3/3 scanned jobs" in message
    for label in ("[safety_reports] A (JOB-A)",
                  "[safety_reports] B (JOB-B)",
                  "[safety_reports] C (JOB-C)"):
        assert label in message


def test_summary_row_carries_the_underlying_error(stub):
    """REGRESSION (review MF1): the summary row MUST carry the cause. The scan is fenced with
    a bare `except Exception`, so without it a genuine code regression reads exactly like a
    Smartsheet 500 — 'N/N scanned jobs' and nothing else — and escalates onto the runbook
    branch that tells the operator the condition self-heals."""
    stub["jobs"].return_value = [_job("A", "JOB-A"), _job("B", "JOB-B")]
    stub["rollup"].side_effect = SmartsheetError("HTTP 500 errorCode 4000")

    cnp.poll_once()

    message = _coded(stub["log"], "compile_now_poll.scan_failed")[0][2]
    assert "SmartsheetError" in message           # the exception TYPE
    assert "HTTP 500 errorCode 4000" in message   # and its message


def test_summary_row_distinguishes_a_code_regression_from_an_outage(stub):
    """The whole point of the cause clause: a TypeError from a schema change must not be
    reported in the same words as a transient Smartsheet incident."""
    stub["jobs"].return_value = [_job("A", "JOB-A")]
    stub["rollup"].side_effect = TypeError("'NoneType' object is not subscriptable")

    cnp.poll_once()

    message = _coded(stub["log"], "compile_now_poll.scan_failed")[0][2]
    assert "TypeError" in message
    assert "'NoneType' object is not subscriptable" in message


def test_sustained_critical_also_carries_the_cause(stub):
    """The CRITICAL is the row that pages and lands on the 'wait, it self-heals' runbook
    branch — it is the one that most needs the cause."""
    stub["jobs"].return_value = [_job("A", "JOB-A")]
    stub["rollup"].side_effect = AttributeError("'dict' object has no attribute 'cells'")
    stub["scan_record"].return_value = cnp.sustained_failure.DEFAULT_CRITICAL_THRESHOLD

    cnp.poll_once()

    message = _coded(stub["log"], "compile_now_scan_sustained")[0][2]
    assert "AttributeError" in message
    assert "'dict' object has no attribute 'cells'" in message


def test_cause_clause_lists_every_distinct_type_and_bounds_the_messages(stub):
    """Types are short and diagnostic → all of them. Full details are long → a bounded
    sample plus a count of the rest."""
    n = cnp.SCAN_CAUSE_SAMPLE + 2
    stub["jobs"].return_value = [_job(f"J{i}", f"JOB-{i}") for i in range(n)]
    stub["rollup"].side_effect = [SmartsheetError(f"distinct {i}") for i in range(n - 1)] + [
        TypeError("wrong shape")
    ]

    cnp.poll_once()

    message = _coded(stub["log"], "compile_now_poll.scan_failed")[0][2]
    assert "SmartsheetError, TypeError" in message  # sorted, every distinct type
    assert f"(+{n - cnp.SCAN_CAUSE_SAMPLE} more distinct message(s))" in message


def test_cause_sample_quotes_the_dominant_cause_not_the_alphabetical_first(stub):
    """REGRESSION (re-review new_defect 3). The sample used to be `sorted(details)[:2]`, and
    every detail is `trigger scan failed: {exc!r}` — so alphabetical-by-message is really
    alphabetical-by-TYPE-NAME, and the cause behind 3 of the 5 failures gets dropped entirely
    in favour of two singleton types that happen to sort earlier. Selection is now one
    representative per distinct type, most-common type FIRST."""
    stub["jobs"].return_value = [_job(f"J{i}", f"JOB-{i}") for i in range(5)]
    stub["rollup"].side_effect = [
        SmartsheetError("zz-dominant-500-a"),   # 3 of 5 failures, but sorts LAST by type name
        SmartsheetError("zz-dominant-500-b"),
        SmartsheetError("zz-dominant-500-c"),
        AttributeError("aa-minor-attr"),
        KeyError("kk-minor-key"),
    ]

    cnp.poll_once()

    message = _coded(stub["log"], "compile_now_poll.scan_failed")[0][2]
    assert "AttributeError, KeyError, SmartsheetError" in message   # all types, always
    assert "zz-dominant-500-a" in message      # the majority cause — DROPPED when alphabetical
    assert "aa-minor-attr" in message
    assert message.index("zz-dominant-500-a") < message.index("aa-minor-attr")
    assert "kk-minor-key" not in message       # crowded out by the dominant cause, correctly
    assert "(+3 more distinct message(s))" in message


def test_cause_sample_never_spends_both_slots_on_one_type(stub):
    """The other half: two spellings of the SAME cause must not crowd out the only quote of a
    different type — under alphabetical-by-message they did, whenever the crowding type's
    name sorted first."""
    stub["jobs"].return_value = [_job(f"J{i}", f"JOB-{i}") for i in range(3)]
    stub["rollup"].side_effect = [
        AttributeError("aa-crowder-one"),   # same type, 2 distinct messages, sorts FIRST
        AttributeError("aa-crowder-two"),
        SmartsheetError("ss-other-type"),
    ]

    cnp.poll_once()

    message = _coded(stub["log"], "compile_now_poll.scan_failed")[0][2]
    assert "ss-other-type" in message                    # the other type survived
    assert message.count("aa-crowder") == 1              # only ONE AttributeError quote
    assert "(+1 more distinct message(s))" in message


def test_a_long_cause_is_truncated(stub):
    stub["jobs"].return_value = [_job("A", "JOB-A")]
    stub["rollup"].side_effect = SmartsheetError("x" * 4000)

    cnp.poll_once()

    message = _coded(stub["log"], "compile_now_poll.scan_failed")[0][2]
    assert "SmartsheetError" in message
    assert len(message) < 4000
    assert "…" in message


def test_summary_row_truncates_the_job_list_past_the_sample(stub):
    n = cnp.SCAN_SUMMARY_SAMPLE + 2
    stub["jobs"].return_value = [_job(f"J{i}", f"JOB-{i}") for i in range(n)]
    stub["rollup"].side_effect = SmartsheetError("blip")

    cnp.poll_once()

    message = _coded(stub["log"], "compile_now_poll.scan_failed")[0][2]
    assert "…and 2 more" in message
    assert f"{n}/{n} scanned jobs" in message


def test_below_fraction_cycle_does_not_increment_but_still_ledgers_and_reports(stub):
    """A 1-in-4 flake must NOT walk toward a CRITICAL — but the failing job is still
    counted per-job (that ledger is what catches ONE chronically-dead sheet)."""
    stub["jobs"].return_value = [_job(f"J{i}", f"JOB-{i}") for i in range(4)]
    stub["rollup"].side_effect = [SmartsheetError("blip"), ROLLUP_OK, ROLLUP_OK, ROLLUP_OK]

    out = cnp.poll_once()

    assert out.scan_failures == 1 and out.jobs_scanned == 4
    stub["scan_record"].assert_not_called()
    stub["scan_reset"].assert_called_once()
    stub["ledger"].assert_called_once_with({"safety_reports:JOB-0"})
    assert len(_coded(stub["log"], "compile_now_poll.scan_failed")) == 1  # still reported


def test_at_the_fraction_the_cycle_counts_as_failing(stub):
    stub["jobs"].return_value = [_job(f"J{i}", f"JOB-{i}") for i in range(4)]
    stub["rollup"].side_effect = [
        SmartsheetError("blip"), SmartsheetError("blip"), ROLLUP_OK, ROLLUP_OK,
    ]

    out = cnp.poll_once()

    assert out.scan_failures / out.jobs_scanned == cnp.SCAN_FAILURE_CYCLE_FRACTION
    stub["scan_record"].assert_called_once()
    stub["scan_reset"].assert_not_called()


def test_sustained_failing_cycles_escalate_to_one_critical_row(stub):
    """AT the threshold the SAME failure becomes CRITICAL — the only severity the
    triple-fire push path and the dashboard fire surfaces key on."""
    stub["jobs"].return_value = [_job("A", "JOB-A"), _job("B", "JOB-B")]
    stub["rollup"].side_effect = SmartsheetError("HTTP 503")
    stub["scan_record"].return_value = cnp.sustained_failure.DEFAULT_CRITICAL_THRESHOLD

    cnp.poll_once()

    crit = _coded(stub["log"], "compile_now_scan_sustained")
    assert len(crit) == 1  # still ONE row for the whole pass
    assert crit[0][0] == cnp.Severity.CRITICAL
    assert "docs/runbooks/compile_now_poll.md" in crit[0][2]
    assert "[safety_reports] A (JOB-A)" in crit[0][2]
    # the ERROR summary is REPLACED, not doubled up
    assert _coded(stub["log"], "compile_now_poll.scan_failed") == []


def test_one_cycle_below_threshold_stays_an_error(stub):
    stub["jobs"].return_value = [_job("A", "JOB-A")]
    stub["rollup"].side_effect = SmartsheetError("HTTP 503")
    stub["scan_record"].return_value = cnp.sustained_failure.DEFAULT_CRITICAL_THRESHOLD - 1

    cnp.poll_once()

    assert len(_coded(stub["log"], "compile_now_poll.scan_failed")) == 1
    assert _coded(stub["log"], "compile_now_scan_sustained") == []


def test_a_clean_cycle_resets_the_counter_and_writes_no_scan_row(stub):
    stub["jobs"].return_value = [_job("A", "JOB-A"), _job("B", "JOB-B")]

    cnp.poll_once()

    stub["scan_reset"].assert_called_once()
    stub["scan_record"].assert_not_called()
    assert _coded(stub["log"], "compile_now_poll.scan_failed") == []
    assert _coded(stub["log"], "compile_now_scan_sustained") == []
    stub["ledger"].assert_called_once_with(set())  # nothing failing → ledger empties


def test_a_single_job_failing_past_the_per_job_threshold_fires_its_own_critical(stub):
    """The case the cycle fraction structurally cannot see: ONE job's week sheet dead for
    half an hour while every other job scans fine."""
    stub["jobs"].return_value = [_job("Dead", "JOB-D")] + [
        _job(f"J{i}", f"JOB-{i}") for i in range(3)
    ]
    stub["rollup"].side_effect = [SmartsheetError("500"), ROLLUP_OK, ROLLUP_OK, ROLLUP_OK]
    stub["ledger"].return_value = {"safety_reports:JOB-D": cnp.JOB_SCAN_CRITICAL_THRESHOLD}

    cnp.poll_once()

    crit = _coded(stub["log"], "compile_now_job_scan_sustained")
    assert len(crit) == 1
    assert crit[0][0] == cnp.Severity.CRITICAL
    assert "[safety_reports] Dead (JOB-D)" in crit[0][2]
    assert f"{cnp.JOB_SCAN_CRITICAL_THRESHOLD} consecutive" in crit[0][2]
    stub["scan_record"].assert_not_called()  # 1 of 4 — the CYCLE is not failing


def test_per_job_critical_stays_quiet_below_its_threshold(stub):
    stub["jobs"].return_value = [_job("Dead", "JOB-D")] + [
        _job(f"J{i}", f"JOB-{i}") for i in range(3)
    ]
    stub["rollup"].side_effect = [SmartsheetError("500"), ROLLUP_OK, ROLLUP_OK, ROLLUP_OK]
    stub["ledger"].return_value = {"safety_reports:JOB-D": cnp.JOB_SCAN_CRITICAL_THRESHOLD - 1}

    cnp.poll_once()

    assert _coded(stub["log"], "compile_now_job_scan_sustained") == []


def test_per_job_row_is_per_occurrence_but_terminal_between_ladder_rungs(stub):
    """§3.1's per-occurrence RECORD mandate is met at EVERY cycle past the threshold — the
    ladder only decides the row's SEVERITY. At THRESHOLD+1 (not a rung) the row is an ERROR,
    so `errors_rotation` can reclaim it; an open CRITICAL is never terminal at any floor."""
    stub["jobs"].return_value = [_job("Dead", "JOB-D")] + [
        _job(f"J{i}", f"JOB-{i}") for i in range(3)
    ]
    stub["rollup"].side_effect = [SmartsheetError("500"), ROLLUP_OK, ROLLUP_OK, ROLLUP_OK]
    stub["ledger"].return_value = {
        "safety_reports:JOB-D": cnp.JOB_SCAN_CRITICAL_THRESHOLD + 1
    }

    cnp.poll_once()

    assert _coded(stub["log"], "compile_now_job_scan_sustained") == []   # not a rung
    rows = _coded(stub["log"], "compile_now_poll.job_scan_failed")
    assert len(rows) == 1                                                # …but a row DID land
    assert rows[0][0] == cnp.Severity.ERROR
    assert f"{cnp.JOB_SCAN_CRITICAL_THRESHOLD + 1} consecutive" in rows[0][2]
    assert f"next CRITICAL at {cnp.JOB_SCAN_CRITICAL_THRESHOLD * 2} consecutive" in rows[0][2]


def _streak_counts(rows: list[tuple[object, str | None, str]], suffix: str) -> list[int]:
    """The `N consecutive …` count each row reports, in the order the rows were written."""
    out = []
    for row in rows:
        found = re.search(rf"(\d+) consecutive {suffix}", str(row[2]))
        assert found is not None, row[2]
        out.append(int(found.group(1)))
    return out


def test_per_job_ladder_bounds_the_criticals_over_a_long_outage(stub):
    """THE ROW-VOLUME BOUND. A per-cycle CRITICAL on a 90 s daemon is ~960 unreclaimable rows
    a day per failing job — `errors_rotation` never treats an open CRITICAL as terminal, so
    neither watchdog Check O nor the dashboard clear verb can reclaim them at ANY floor, and
    ITS_Errors hit 19,975 of its 20,000-row hard cap on 2026-07-13 and locked out twice.
    Across 50 consecutive failing cycles the job pages on the CROSSING cycle and then only at
    2×/4× — every other cycle still records its row, at ERROR."""
    stub["jobs"].return_value = [_job("Dead", "JOB-D")] + [
        _job(f"J{i}", f"JOB-{i}") for i in range(3)
    ]
    stub["rollup"].side_effect = (
        [SmartsheetError("500"), ROLLUP_OK, ROLLUP_OK, ROLLUP_OK] * 50
    )
    stub["ledger"].side_effect = [{"safety_reports:JOB-D": n} for n in range(1, 51)]

    for _ in range(50):
        cnp.poll_once()

    crit = _coded(stub["log"], "compile_now_job_scan_sustained")
    err = _coded(stub["log"], "compile_now_poll.job_scan_failed")
    assert _streak_counts(crit, "cycles") == [20, 40]      # crossing, then 2× — nothing else
    assert all(r[0] == cnp.Severity.CRITICAL for r in crit)
    # every past-threshold cycle still wrote exactly one row (§3.1), the rest at ERROR
    assert len(crit) + len(err) == 50 - cnp.JOB_SCAN_CRITICAL_THRESHOLD + 1
    assert all(r[0] == cnp.Severity.ERROR for r in err)
    assert _streak_counts(err, "cycles") == [
        n for n in range(cnp.JOB_SCAN_CRITICAL_THRESHOLD, 51) if n not in (20, 40)
    ]


def test_cycle_ladder_bounds_the_criticals_over_a_long_outage(stub):
    """Same bound on the CYCLE escalation: 50 straight majority-failing cycles page at 5
    (the crossing), 10, 20, 40 — four CRITICALs, not fifty — and record ERROR in between."""
    stub["jobs"].return_value = [_job("A", "JOB-A"), _job("B", "JOB-B")]
    stub["rollup"].side_effect = SmartsheetError("HTTP 503")
    stub["scan_record"].side_effect = list(range(1, 51))

    for _ in range(50):
        cnp.poll_once()

    crit = _coded(stub["log"], "compile_now_scan_sustained")
    err = _coded(stub["log"], "compile_now_poll.scan_failed")
    assert _streak_counts(crit, r"failing cycle\(s\)") == [5, 10, 20, 40]
    assert all(r[0] == cnp.Severity.CRITICAL for r in crit)
    assert len(crit) + len(err) == 50            # every cycle still records (§3.1)
    assert all(r[0] == cnp.Severity.ERROR for r in err)
    # the intermediate past-threshold rows say so, and say when it pages again
    past = [r[2] for r in err if "ALREADY escalated" in r[2]]
    assert len(past) == 50 - cnp.sustained_failure.DEFAULT_CRITICAL_THRESHOLD + 1 - len(crit)
    assert "next CRITICAL at 10 consecutive failing cycle(s)" in past[0]


def test_escalation_ladder_rungs():
    """Unit-level pin on the rung predicate itself."""
    assert [n for n in range(1, 101) if cnp._is_escalation_cycle(n, 5)] == [5, 10, 20, 40, 80]
    assert [n for n in range(1, 101) if cnp._is_escalation_cycle(n, 20)] == [20, 40, 80]
    assert cnp._is_escalation_cycle(4, 5) is False        # below the threshold never fires
    assert cnp._next_escalation_cycle(5, 5) == 10
    assert cnp._next_escalation_cycle(6, 5) == 10
    assert cnp._next_escalation_cycle(0, 5) == 5


def test_per_job_message_does_not_claim_other_jobs_are_fine_during_a_broad_outage(stub):
    """REGRESSION (re-review new_defect 1). Narrowing the suppression made this row reachable
    when EVERY scanned job is failing — a broad Smartsheet outage on a small deployment. The
    'while other jobs scan fine' premise is FALSE there, and it is what the operator routes
    on: it sends them to Fault E's rename-back repair for a platform incident."""
    stub["jobs"].return_value = [_job("A", "JOB-A"), _job("B", "JOB-B"), _job("C", "JOB-C")]
    stub["rollup"].side_effect = SmartsheetError("HTTP 500")   # EVERY job fails
    stub["scan_record"].return_value = cnp.sustained_failure.DEFAULT_CRITICAL_THRESHOLD
    stub["ledger"].return_value = {
        f"safety_reports:JOB-{j}": cnp.JOB_SCAN_CRITICAL_THRESHOLD for j in "ABC"
    }

    cnp.poll_once()

    crit = _coded(stub["log"], "compile_now_job_scan_sustained")
    assert len(crit) == 3
    for row in crit:
        assert "while other jobs scan fine" not in row[2]
        assert "EVERY other scanned job (3/3)" in row[2]
        assert "BROAD scan outage, NOT a per-job fault" in row[2]
        assert "do not rename anything" in row[2]


def test_per_job_message_keeps_the_isolated_premise_when_others_do_scan_fine(stub):
    """The other half of the same branch: with a live job alongside it the isolated-fault
    premise is TRUE and must survive — that is the row's whole diagnostic value."""
    stub["jobs"].return_value = [_job("Dead", "JOB-D"), _job("Fine", "JOB-F")]
    stub["rollup"].side_effect = [SmartsheetError("500"), ROLLUP_OK]
    stub["ledger"].return_value = {
        "safety_reports:JOB-D": cnp.JOB_SCAN_CRITICAL_THRESHOLD
    }

    cnp.poll_once()

    crit = _coded(stub["log"], "compile_now_job_scan_sustained")
    assert len(crit) == 1
    assert "while other jobs scan fine" in crit[0][2]
    assert "BROAD scan outage" not in crit[0][2]


def test_per_job_criticals_are_suppressed_only_when_more_jobs_fail_than_can_be_named(stub):
    """Bound on the per-job rows: when the pass row could not even NAME the failing jobs
    (more than SCAN_SUMMARY_SAMPLE of them), a per-job CRITICAL for each is a duplicate storm
    on top of a CRITICAL that already says 'most jobs'."""
    n = cnp.SCAN_SUMMARY_SAMPLE + 1
    stub["jobs"].return_value = [_job(f"J{i}", f"JOB-{i}") for i in range(n)]
    stub["rollup"].side_effect = SmartsheetError("500")
    stub["scan_record"].return_value = cnp.sustained_failure.DEFAULT_CRITICAL_THRESHOLD
    stub["ledger"].return_value = {
        f"safety_reports:JOB-{i}": cnp.JOB_SCAN_CRITICAL_THRESHOLD for i in range(n)
    }

    cnp.poll_once()

    assert _coded(stub["log"], "compile_now_job_scan_sustained") == []
    assert len(_coded(stub["log"], "compile_now_scan_sustained")) == 1


def test_a_single_dead_job_on_a_small_deployment_still_gets_its_own_critical(stub):
    """REGRESSION (review SF2): suppressing on `sustained` ALONE permanently hid the per-job
    CRITICAL on a small deployment — 1 of 2 jobs failing IS >= the 50% fraction, so ONE
    renamed job folder fired the 'wait, this self-heals' cycle CRITICAL forever and NEVER the
    'rename it back' one. Both rows must appear."""
    stub["jobs"].return_value = [_job("Dead", "JOB-D"), _job("Fine", "JOB-F")]
    stub["rollup"].side_effect = [SmartsheetError("500"), ROLLUP_OK]
    stub["scan_record"].return_value = cnp.sustained_failure.DEFAULT_CRITICAL_THRESHOLD
    stub["ledger"].return_value = {
        "safety_reports:JOB-D": cnp.JOB_SCAN_CRITICAL_THRESHOLD
    }

    cnp.poll_once()

    assert len(_coded(stub["log"], "compile_now_scan_sustained")) == 1   # the cycle row
    per_job = _coded(stub["log"], "compile_now_job_scan_sustained")
    assert len(per_job) == 1                                            # AND the actionable one
    assert "[safety_reports] Dead (JOB-D)" in per_job[0][2]


def test_a_blind_cycle_leaves_the_per_job_ledger_untouched(stub):
    """REGRESSION (review SF1): an ITS_Active_Jobs read failure scans NO jobs, so an
    unconditional ledger write would persist an empty failing set and DROP every per-job
    count. With a blip every N cycles a permanently-dead week sheet would then never reach
    JOB_SCAN_CRITICAL_THRESHOLD — the exact escalation this ledger exists to produce."""
    stub["jobs"].return_value = []
    stub["jobs_failed"].return_value = True

    out = cnp.poll_once()

    assert out.active_jobs_read_failures == 1
    stub["ledger"].assert_not_called()
    # …and the cycle is still reported + counted (the cycle counter covers the outage).
    stub["scan_record"].assert_called_once()
    assert len(_coded(stub["log"], "compile_now_poll.scan_failed")) == 1


def test_active_jobs_read_failure_is_a_failing_cycle_and_named_in_the_summary(stub):
    """Regression for the silent hole: `_load_jobs` returns [] on a read failure, so the
    cycle used to scan zero jobs and report a clean OK heartbeat through an outage."""
    stub["jobs"].return_value = []          # what a failed read yields
    stub["jobs_failed"].return_value = True

    out = cnp.poll_once()

    assert out.jobs_scanned == 0
    assert out.active_jobs_read_failures == 1 and out.errors == 1
    stub["scan_record"].assert_called_once()  # counts as failing despite zero scan failures
    rows = _coded(stub["log"], "compile_now_poll.scan_failed")
    assert len(rows) == 1
    assert "ITS_Active_Jobs read FAILED for 1 served workstream(s)" in rows[0][2]
    assert stub["hb_row"].call_args.kwargs["status"] == "DEGRADED"
    # …and it does NOT open with a meaningless "0/0 scanned jobs" fraction — that clause is
    # the FIRST thing the runbook tells the operator to read off this row.
    assert "0/0 scanned jobs" not in rows[0][2]
    assert rows[0][2].startswith("compile-now trigger scan scanned NO jobs")


def test_active_jobs_read_failed_is_asked_per_workstream_config(stub):
    """The flag is per-SHEET; asking it without the workstream's own ActiveJobsConfig would
    read safety's outcome while iterating progress's jobs (and vice versa)."""
    stub["enabled_ws"]["progress_reports"] = True

    cnp.poll_once()

    asked = [c.args[0] for c in stub["jobs_failed"].call_args_list]
    assert asked == [
        SAFETY_CFG.active_jobs_config,
        PROGRESS_CFG.active_jobs_config,
    ]
    # the job LIST is read under the same per-workstream config
    assert [c.args[0] for c in stub["jobs"].call_args_list] == asked


def test_active_jobs_read_failure_escalates_when_sustained(stub):
    stub["jobs"].return_value = []
    stub["jobs_failed"].return_value = True
    stub["scan_record"].return_value = cnp.sustained_failure.DEFAULT_CRITICAL_THRESHOLD

    cnp.poll_once()

    crit = _coded(stub["log"], "compile_now_scan_sustained")
    assert len(crit) == 1 and crit[0][0] == cnp.Severity.CRITICAL


def test_liveness_surfaces_land_before_the_escalation_egress(stub, mocker):
    """REGRESSION (review SF6): `_record_scan_outcome` is the only step that performs
    CRITICAL triple-fire egress. Running it FIRST meant an alerting failure cost the cycle
    its ITS_Daemon_Health heartbeat AND its Check-C watchdog marker — an alerting outage
    masquerading as a dead daemon."""
    mocker.patch.object(
        cnp, "_record_scan_outcome", side_effect=RuntimeError("Resend exploded")
    )
    stub["jobs"].return_value = [_job("A", "JOB-A")]

    with pytest.raises(RuntimeError):
        cnp._poll_inside_lock((SAFETY_CFG,))  # inside the lock — no @its_error_log swallow

    stub["hb"].assert_called_once()      # liveness file
    stub["hb_row"].assert_called_once()  # ITS_Daemon_Health row
    stub["marker"].assert_called_once()  # watchdog Check C marker


# ---- The per-job ledger itself --------------------------------------------


def test_job_ledger_round_trips_resets_on_success_and_sweeps_departed(tmp_path):
    led = cnp._JobScanLedger(tmp_path / "l.json", "test.script", "test_ledger_failed")
    assert led.apply({"safety_reports:A", "safety_reports:B"}) == {
        "safety_reports:A": 1, "safety_reports:B": 1,
    }
    assert led.apply({"safety_reports:A"}) == {"safety_reports:A": 2}  # B succeeded
    assert led.apply({"safety_reports:A"}) == {"safety_reports:A": 3}
    assert json.loads((tmp_path / "l.json").read_text()) == {
        "counts": {"safety_reports:A": 3}
    }
    assert led.apply(set()) == {}                                     # A departed / clean
    assert led.apply({"safety_reports:A"}) == {"safety_reports:A": 1}  # the count really went


@pytest.mark.parametrize("persisted", [True, -5, "3", 2.0, None])
def test_job_ledger_rejects_non_count_values(tmp_path, persisted):
    """`isinstance(True, int)` is True and a negative count is nonsense — either would
    round-trip into a real consecutive-cycle count (True + 1 == 2)."""
    path = tmp_path / "l.json"
    path.write_text(json.dumps({"counts": {"safety_reports:A": persisted}}))
    led = cnp._JobScanLedger(path, "test.script", "test_ledger_failed")
    assert led.apply({"safety_reports:A"}) == {"safety_reports:A": 1}  # started over, not 2


def test_job_ledger_corrupt_state_reads_as_empty(tmp_path):
    path = tmp_path / "l.json"
    path.write_text("{not json")
    led = cnp._JobScanLedger(path, "test.script", "test_ledger_failed")
    assert led.apply({"safety_reports:A"}) == {"safety_reports:A": 1}


def test_job_ledger_state_error_degrades_to_one_with_warn(tmp_path, mocker):
    log = mocker.patch.object(cnp.error_log, "log")
    mocker.patch.object(cnp.state_io, "with_path_lock", side_effect=RuntimeError("lock boom"))
    led = cnp._JobScanLedger(tmp_path / "l.json", "test.script", "test_ledger_failed")

    assert led.apply({"safety_reports:A", "safety_reports:B"}) == {
        "safety_reports:A": 1, "safety_reports:B": 1,
    }  # never page off a state glitch
    assert log.call_args.kwargs["error_code"] == "test_ledger_failed"


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
