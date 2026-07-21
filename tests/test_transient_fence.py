"""TransientFence — the pass-boundary severity fence for a pre-work Smartsheet read.

THE forensic this locks (2026-07-21): a single 30 s ReadTimeout inside a daemon's
pre-work read escaped the pass, and `@its_error_log` stamps ANY unhandled exception
CRITICAL `uncaught_exception` unconditionally — so two healthy daemons paged the operator
for blips that had self-healed one cycle later (`progress_send_poll` 05:36Z inside
`list_workspace_share_emails`; `publish_daemon` 14:37Z inside `get_setting`).

Every test here is prove-the-control-bites in one direction or the other: below the
threshold a transient stays an ERROR; AT the threshold the SAME failure escalates to
CRITICAL; and a NON-transient never gets softened at all.

Run with: pytest -q tests/test_transient_fence.py
"""
from __future__ import annotations

import json

import pytest

from shared import smartsheet_client, sustained_failure
from shared.error_log import Severity


@pytest.fixture
def logged(mocker):
    """Capture (severity, script, message, error_code) for every error_log.log call."""
    calls: list[dict] = []

    def _capture(severity, script, message, **kwargs):
        calls.append({
            "severity": severity, "script": script, "message": message,
            "error_code": kwargs.get("error_code"),
        })

    mocker.patch("shared.error_log.log", side_effect=_capture)
    return calls


def _fence(tmp_path, *, threshold=3, script="test_daemon") -> sustained_failure.TransientFence:
    return sustained_failure.TransientFence(
        script,
        state_path=tmp_path / f"{script}_fence.json",
        transient_error_code=f"{script}.read_transient",
        sustained_error_code=f"{script}.read_sustained",
        threshold=threshold,
        runbook="docs/runbooks/example.md",
    )


def _severities(calls, code):
    return [c["severity"] for c in calls if c["error_code"] == code]


# ---- the escalation ladder -----------------------------------------------


def test_sustained_transients_escalate_to_critical_exactly_at_the_threshold(tmp_path, logged):
    fence = _fence(tmp_path, threshold=3)
    exc = smartsheet_client.SmartsheetTransientError("HTTP 500 (code 4000)")

    for _ in range(2):
        assert fence.handle(exc) is True

    assert _severities(logged, "test_daemon.read_transient") == [Severity.ERROR, Severity.ERROR]
    assert _severities(logged, "test_daemon.read_sustained") == []

    assert fence.handle(exc) is True  # the 3rd consecutive cycle

    assert _severities(logged, "test_daemon.read_sustained") == [Severity.CRITICAL]
    critical = next(c for c in logged if c["error_code"] == "test_daemon.read_sustained")
    assert "3 consecutive cycles" in critical["message"]
    assert "docs/runbooks/example.md" in critical["message"]


def test_critical_fires_every_cycle_past_the_threshold(tmp_path, logged):
    """Op Stds §3.1: the ITS_Errors record leg is PER-OCCURRENCE. Suppressing repeats
    belongs to the push legs (alert_dedupe), never to the record."""
    fence = _fence(tmp_path, threshold=2)
    exc = smartsheet_client.SmartsheetTransientError("HTTP 503")

    for _ in range(5):
        fence.handle(exc)

    assert _severities(logged, "test_daemon.read_sustained") == [Severity.CRITICAL] * 4


def test_one_off_transient_never_criticals_and_reset_zeroes_the_counter(tmp_path, logged):
    fence = _fence(tmp_path, threshold=3)
    exc = smartsheet_client.SmartsheetTransientError("HTTP 500")

    assert fence.handle(exc) is True
    assert _severities(logged, "test_daemon.read_sustained") == []
    assert json.loads((tmp_path / "test_daemon_fence.json").read_text()) == {"count": 1}

    fence.reset()
    assert json.loads((tmp_path / "test_daemon_fence.json").read_text()) == {"count": 0}

    # And the next blip starts the ladder over rather than resuming near the threshold.
    fence.handle(exc)
    assert _severities(logged, "test_daemon.read_sustained") == []


# ---- the three outcomes --------------------------------------------------


@pytest.mark.parametrize(
    "exc",
    [
        smartsheet_client.SmartsheetAuthError("401"),
        smartsheet_client.SmartsheetPermissionError("403"),
        smartsheet_client.SmartsheetNotFoundError("404"),
        smartsheet_client.SmartsheetValidationError("400"),
        smartsheet_client.SmartsheetRateLimitError("429"),
        smartsheet_client.SmartsheetError("untyped"),
        ValueError("a genuine bug, nothing to do with Smartsheet"),
    ],
)
def test_non_transient_is_not_softened_and_never_increments(tmp_path, logged, exc):
    """The doctrine line: only the precisely-typed transient class is softened. A real
    bug (or a revoked token, or exhausted 429 pressure) keeps its immediate CRITICAL."""
    fence = _fence(tmp_path)

    assert fence.handle(exc) is False
    assert logged == []
    assert not (tmp_path / "test_daemon_fence.json").exists()


def test_circuit_open_halts_without_counting(tmp_path, logged):
    """Counting circuit-open would turn ONE outage into the breaker's prolonged-open
    CRITICAL *plus* a *_sustained CRITICAL from every enrolled daemon, on separate
    alert_dedupe keys — a page storm for one root cause."""
    fence = _fence(tmp_path, threshold=2)
    exc = smartsheet_client.SmartsheetCircuitOpenError("breaker OPEN")

    for _ in range(6):
        assert fence.handle(exc) is True

    assert _severities(logged, "test_daemon.read_transient") == [Severity.WARN] * 6
    assert _severities(logged, "test_daemon.read_sustained") == []
    assert not (tmp_path / "test_daemon_fence.json").exists()


# ---- fleet-level circuit-open escalation ---------------------------------
#
# The constraint these lock: "a REAL sustained outage must still escalate to CRITICAL."
# Keeping circuit-open out of the PER-DAEMON counter (above) is right — it stops one
# outage becoming 6-10 pages — but on its own it also stopped the outage escalating at
# ALL, leaving watchdog Check J (07:00 DAILY) as the only remaining page. These prove
# the shared fleet counter restores a sub-hour CRITICAL without restoring the storm.


@pytest.fixture
def fleet_clock(mocker):
    """Drive `sustained_failure`'s wall clock so the window is deterministic."""
    def _at(*offsets: float):
        base = 1_000_000.0
        mocker.patch.object(
            sustained_failure.time, "time", side_effect=[base + o for o in offsets]
        )
    return _at


def _fleet_pages(calls):
    return [c for c in calls if c["error_code"] == sustained_failure.CIRCUIT_OPEN_ERROR_CODE]


def test_sustained_circuit_open_still_pages_critical(tmp_path, logged, fleet_clock):
    """THE regression this closes: with circuit-open uncounted everywhere, a real outage
    produced no CRITICAL until the 07:00 watchdog — up to ~24 h of frozen sends."""
    window = sustained_failure.CIRCUIT_OPEN_SUSTAINED_SECONDS
    fleet_clock(0, 60, window + 1)
    fence = _fence(tmp_path, threshold=3)
    exc = smartsheet_client.SmartsheetCircuitOpenError("breaker OPEN")

    for _ in range(3):
        assert fence.handle(exc) is True

    pages = _fleet_pages(logged)
    assert [p["severity"] for p in pages] == [Severity.CRITICAL]
    assert pages[0]["script"] == sustained_failure.CIRCUIT_OPEN_SCRIPT
    assert "approved sends are FROZEN" in pages[0]["message"]
    # The per-daemon ladder stayed OUT of it — that is what stops the storm.
    assert _severities(logged, "test_daemon.read_sustained") == []


def test_circuit_open_inside_the_window_does_not_page_yet(tmp_path, logged, fleet_clock):
    """A breaker that trips on a burst and recovers inside its cooldown is not an outage."""
    fleet_clock(0, 60, 120)
    fence = _fence(tmp_path)

    for _ in range(3):
        fence.handle(smartsheet_client.SmartsheetCircuitOpenError("breaker OPEN"))

    assert _fleet_pages(logged) == []


def test_the_whole_fleet_pages_under_one_alert_dedupe_key(tmp_path, logged, fleet_clock):
    """`alert_dedupe` keys the push legs on `(script, error_code)`. Every daemon records
    under the SAME fixed pair, so five observers collapse into one operator wake-up —
    the storm concern the per-daemon exclusion was protecting against."""
    window = sustained_failure.CIRCUIT_OPEN_SUSTAINED_SECONDS
    scripts = [
        "safety_reports.weekly_send_poll",
        "progress_reports.progress_send_poll",
        "po_materials.po_send_poll",
        "po_materials.rfq_send_poll",
        "subcontracts.subcontract_send_poll",
    ]
    fleet_clock(*[0.0] + [window + 1.0 + i for i in range(len(scripts))])
    exc = smartsheet_client.SmartsheetCircuitOpenError("breaker OPEN")

    # One priming observation opens the window, then every daemon in the fleet observes.
    _fence(tmp_path, script="primer").handle(exc)
    for script in scripts:
        _fence(tmp_path, script=script).handle(exc)

    pages = _fleet_pages(logged)
    assert len(pages) == len(scripts)  # per-occurrence ITS_Errors rows (Op Stds §3.1)
    assert {(p["script"], p["error_code"]) for p in pages} == {
        (sustained_failure.CIRCUIT_OPEN_SCRIPT, sustained_failure.CIRCUIT_OPEN_ERROR_CODE)
    }
    # And NO per-daemon *_sustained code fired — one root cause, one dedupe key.
    per_daemon = {
        c["error_code"] for c in logged
        if (c["error_code"] or "").endswith(".read_sustained")
    }
    assert per_daemon == set()


def test_a_successful_read_closes_the_fleet_window(tmp_path, logged, fleet_clock):
    """Any daemon's successful read proves the backend is reachable, so the next outage
    starts its own window rather than inheriting a stale one and paging instantly."""
    window = sustained_failure.CIRCUIT_OPEN_SUSTAINED_SECONDS
    fleet_clock(0, window + 1)
    fence = _fence(tmp_path)
    exc = smartsheet_client.SmartsheetCircuitOpenError("breaker OPEN")

    fence.handle(exc)
    assert sustained_failure.CIRCUIT_OPEN_STATE_PATH.exists()

    fence.reset()
    assert not sustained_failure.CIRCUIT_OPEN_STATE_PATH.exists()

    fence.handle(exc)  # a fresh window opens; the old elapsed time is gone
    assert _fleet_pages(logged) == []


def test_fleet_counter_state_glitch_warns_instead_of_paging(tmp_path, logged, mocker):
    mocker.patch(
        "shared.state_io.with_path_lock", side_effect=OSError("lock surface broken")
    )
    _fence(tmp_path).handle(smartsheet_client.SmartsheetCircuitOpenError("breaker OPEN"))

    assert _fleet_pages(logged) == []
    assert _severities(
        logged, f"{sustained_failure.CIRCUIT_OPEN_ERROR_CODE}_counter_failed"
    ) == [Severity.WARN]


def test_circuit_open_does_not_disturb_an_in_flight_transient_count(tmp_path, logged):
    fence = _fence(tmp_path, threshold=3)
    fence.handle(smartsheet_client.SmartsheetTransientError("HTTP 500"))
    fence.handle(smartsheet_client.SmartsheetCircuitOpenError("open"))
    fence.handle(smartsheet_client.SmartsheetTransientError("HTTP 500"))

    assert json.loads((tmp_path / "test_daemon_fence.json").read_text()) == {"count": 2}


# ---- state-glitch posture ------------------------------------------------


def test_record_does_not_swallow_an_assertion_control(tmp_path, logged, mocker):
    """An AssertionError is a CONTROL firing, not a state glitch — it must PROPAGATE.

    This is the exact mechanism that hid nine live `~/its/state` writes on 2026-07-21: the
    suite's `_forbid_live_state_writes` guard raises AssertionError, `record()`'s broad
    `except Exception` caught it, answered "count = 1", and the run went green. Two bugs in
    one — the guard was defeated, AND the escalation ladder silently flattened to a
    constant 1 in every daemon-entry test, so a `*_sustained` CRITICAL could never fire
    under test even if the code were broken.
    """
    mocker.patch(
        "shared.state_io.atomic_write_json", side_effect=AssertionError("state guard fired")
    )
    counter = sustained_failure.SustainedFailureCounter(
        tmp_path / "counter.json", "test_daemon", "test_daemon.counter_failed"
    )

    with pytest.raises(AssertionError, match="state guard fired"):
        counter.record()

    # And it is not degraded into a WARN either — nothing was logged at all.
    assert logged == []


def test_reset_does_not_swallow_an_assertion_control(tmp_path, mocker):
    """Same reasoning for the best-effort reset, whose `except Exception: pass` is even
    quieter than record()'s (no WARN at all)."""
    path = tmp_path / "counter.json"
    path.write_text('{"count": 2}')
    mocker.patch(
        "shared.state_io.atomic_write_json", side_effect=AssertionError("state guard fired")
    )
    counter = sustained_failure.SustainedFailureCounter(
        path, "test_daemon", "test_daemon.counter_failed"
    )

    with pytest.raises(AssertionError, match="state guard fired"):
        counter.reset()


def test_counter_state_glitch_degrades_to_one_rather_than_paging(tmp_path, logged, mocker):
    """Never page off a state glitch (the SustainedFailureCounter contract)."""
    mocker.patch(
        "shared.state_io.with_path_lock", side_effect=OSError("lock surface broken")
    )
    fence = _fence(tmp_path, threshold=2)

    fence.handle(smartsheet_client.SmartsheetTransientError("HTTP 500"))
    fence.handle(smartsheet_client.SmartsheetTransientError("HTTP 500"))

    assert _severities(logged, "test_daemon.read_sustained") == []
    assert _severities(logged, "test_daemon.read_transient") == [Severity.ERROR] * 2


# ---- recovery flush (D3) -------------------------------------------------


def test_flush_writes_one_summarized_warn_when_a_retry_recovered(tmp_path, logged, mocker):
    mocker.patch.object(
        smartsheet_client, "drain_retry_recovery",
        return_value={
            "get_rows": {"sequences": 2, "attempts": 3},
            "list_workspace_share_emails": {"sequences": 1, "attempts": 1},
        },
    )
    _fence(tmp_path).flush_retry_recovery()

    rows = [c for c in logged if c["error_code"] == "smartsheet_retry_recovered"]
    assert len(rows) == 1
    assert rows[0]["severity"] == Severity.WARN
    assert "get_rows×2" in rows[0]["message"]
    assert "list_workspace_share_emails×1" in rows[0]["message"]


def test_flush_is_silent_when_nothing_recovered(tmp_path, logged, mocker):
    mocker.patch.object(smartsheet_client, "drain_retry_recovery", return_value={})
    _fence(tmp_path).flush_retry_recovery()
    assert logged == []


def test_flush_never_disturbs_an_otherwise_successful_pass(tmp_path, logged, mocker):
    mocker.patch.object(
        smartsheet_client, "drain_retry_recovery", side_effect=RuntimeError("boom")
    )
    _fence(tmp_path).flush_retry_recovery()  # must not raise
