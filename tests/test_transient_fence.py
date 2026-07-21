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


def _fence(tmp_path, *, threshold=3) -> sustained_failure.TransientFence:
    return sustained_failure.TransientFence(
        "test_daemon",
        state_path=tmp_path / "fence.json",
        transient_error_code="test_daemon.read_transient",
        sustained_error_code="test_daemon.read_sustained",
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
    assert json.loads((tmp_path / "fence.json").read_text()) == {"count": 1}

    fence.reset()
    assert json.loads((tmp_path / "fence.json").read_text()) == {"count": 0}

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
    assert not (tmp_path / "fence.json").exists()


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
    assert not (tmp_path / "fence.json").exists()


def test_circuit_open_does_not_disturb_an_in_flight_transient_count(tmp_path, logged):
    fence = _fence(tmp_path, threshold=3)
    fence.handle(smartsheet_client.SmartsheetTransientError("HTTP 500"))
    fence.handle(smartsheet_client.SmartsheetCircuitOpenError("open"))
    fence.handle(smartsheet_client.SmartsheetTransientError("HTTP 500"))

    assert json.loads((tmp_path / "fence.json").read_text()) == {"count": 2}


# ---- state-glitch posture ------------------------------------------------


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
