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

import ast
import json
from pathlib import Path

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


# ---- the escalation CADENCE (geometric ladder) ---------------------------
#
# WHY A LADDER AND NOT `n >= threshold` (2026-07-21 re-review). An open CRITICAL is NEVER
# terminal per `shared/errors_rotation.errors_row_is_terminal`, so a CRITICAL row is
# UNROTATABLE at any floor, including the storm floor. Firing CRITICAL on EVERY cycle past
# the threshold means a 21 h outage on a 120 s daemon mints ~626 permanent rows.
# ITS_Errors hit 19,975 of its 20,000 hard cap on 2026-07-13 and fired a "NOTHING is
# deletable" lockout twice; this path re-opens that by a route rotation cannot rescue, and
# buries the genuinely-open CRITICALs that watchdog Check B and the dashboard panel exist
# to surface. The ladder keeps BOTH doctrine requirements — a real sustained outage
# escalates to CRITICAL, and every cycle still writes a per-occurrence row — while leaving
# all but a handful of those rows TERMINAL and reclaimable.


def _ladder_rows(logged):
    return [c for c in logged if c["error_code"] in
            ("test_daemon.read_sustained", "test_daemon.read_transient")]


def test_the_ladder_fires_on_the_crossing_cycle_then_geometrically(tmp_path, logged):
    fence = _fence(tmp_path, threshold=5)
    exc = smartsheet_client.SmartsheetTransientError("HTTP 503")

    for _ in range(50):
        fence.handle(exc)

    criticals = [i + 1 for i, c in enumerate(_ladder_rows(logged))
                 if c["error_code"] == "test_daemon.read_sustained"]
    # threshold, 2×, 4×, 8× — and the 8× step is the cap, so it repeats every 40 from there.
    assert criticals == [5, 10, 20, 40]
    assert criticals[0] == 5, "the FIRST CRITICAL must land exactly on the crossing cycle"


def test_every_cycle_past_the_threshold_still_writes_a_row(tmp_path, logged):
    """Op Stds §3.1: the ITS_Errors record leg is PER-OCCURRENCE. The ladder changes the
    SEVERITY of the repeats, never their existence — nothing is silent."""
    fence = _fence(tmp_path, threshold=5)
    exc = smartsheet_client.SmartsheetTransientError("HTTP 503")

    for _ in range(50):
        fence.handle(exc)

    rows = _ladder_rows(logged)
    assert len(rows) == 50
    assert len([c for c in rows if c["severity"] == Severity.CRITICAL]) == 4
    assert len([c for c in rows if c["severity"] == Severity.ERROR]) == 46
    assert {c["severity"] for c in rows} == {Severity.CRITICAL, Severity.ERROR}


def test_a_post_threshold_error_row_is_terminal_and_therefore_rotatable(tmp_path, logged):
    """The property the ladder exists to preserve, asserted against the REAL predicate
    rather than by inspection: the 46 repeats must be reclaimable by row-cap rotation."""
    from shared import errors_rotation

    fence = _fence(tmp_path, threshold=5)
    for _ in range(50):
        fence.handle(smartsheet_client.SmartsheetTransientError("HTTP 503"))

    terminal = [
        r for r in _ladder_rows(logged)
        if errors_rotation.errors_row_is_terminal(
            {"Severity": r["severity"].value, "Resolved At": None}
        )
    ]
    assert len(terminal) == 46, (
        "an open CRITICAL is never terminal — firing one every cycle mints unrotatable "
        "rows and re-opens the 2026-07-13 ITS_Errors hard-cap lockout"
    )


@pytest.mark.parametrize(
    "threshold, expected",
    [
        (1, [1, 2, 4, 8, 16, 24, 32, 40, 48]),   # cap = 1×8 = 8, then every 8
        (3, [3, 6, 12, 24, 48]),                 # cap = 24, then every 24
        (5, [5, 10, 20, 40]),                    # cap = 40
    ],
)
def test_is_escalation_cycle_is_the_one_place_the_cadence_is_decided(threshold, expected):
    """ONE helper, so the compare cannot drift between the fence and the six daemons that
    hand-rolled `n >= threshold`."""
    fired = [n for n in range(1, 51)
             if sustained_failure.is_escalation_cycle(n, threshold)]
    assert fired == expected
    assert not any(sustained_failure.is_escalation_cycle(n, threshold)
                   for n in range(1, threshold))


def test_is_escalation_cycle_keeps_re_notifying_forever():
    """Capped, not purely geometric: an outage lasting days must not go silent because the
    next rung is 10 h away."""
    cap = 5 * sustained_failure.LADDER_MAX_MULTIPLIER
    tail = [n for n in range(200, 2001) if sustained_failure.is_escalation_cycle(n, 5)]
    assert tail  # it never stops
    assert all(b - a == cap for a, b in zip(tail, tail[1:], strict=False))


def test_is_escalation_cycle_is_total_on_a_nonsense_threshold():
    """A misconfigured threshold must not wedge the escalation decision (never silent, but
    also never a crash inside an error-handling path)."""
    assert sustained_failure.is_escalation_cycle(5, 0) is True
    assert sustained_failure.is_escalation_cycle(5, -3) is True
    assert sustained_failure.is_escalation_cycle(0, 5) is False


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


def test_a_fence_reset_must_not_close_the_fleet_window(tmp_path, logged, fleet_clock):
    """Fleet-scoped state is not any one daemon's to clear.

    `reset()` is reached on a daemon's own BELIEF that its read succeeded, and readers
    that fail OPEN reach it having proved nothing. Wiring the clear here let the 120 s
    publish daemon wipe the window every 2 minutes during an outage, so it could never
    mature to the 600 s threshold — a fleet-wide alert suppressor that every unit test
    reported green."""
    fleet_clock(0)
    fence = _fence(tmp_path)

    fence.handle(smartsheet_client.SmartsheetCircuitOpenError("breaker OPEN"))
    assert sustained_failure.CIRCUIT_OPEN_STATE_PATH.exists()

    fence.reset()

    assert sustained_failure.CIRCUIT_OPEN_STATE_PATH.exists()


def test_the_breaker_recovery_transition_closes_the_fleet_window(tmp_path, logged, mocker):
    """The ONE genuine proof of reachability: a real Smartsheet call answered while the
    breaker was in a non-healthy state. Driven through the REAL `circuit_breaker` state
    machine, not by calling `clear_circuit_open` directly — the wiring is the thing that
    broke, so the wiring is what is asserted."""
    from shared import circuit_breaker

    breaker_state = tmp_path / "breaker.json"
    mocker.patch.object(circuit_breaker, "STATE_FILE", breaker_state)
    cfg = circuit_breaker.CircuitConfig(
        enabled=True, failure_threshold=2, cooldown_seconds=0
    )

    _fence(tmp_path).handle(smartsheet_client.SmartsheetCircuitOpenError("breaker OPEN"))
    assert sustained_failure.CIRCUIT_OPEN_STATE_PATH.exists()

    # Two failures trip the breaker, then a real success recovers it.
    circuit_breaker._record_failure(breaker_state, cfg)
    circuit_breaker._record_failure(breaker_state, cfg)
    circuit_breaker._record_success(breaker_state)

    assert not sustained_failure.CIRCUIT_OPEN_STATE_PATH.exists()
    # …and the sidecar goes with it, rather than orphaning in ~/its/state after every
    # outage (the litter class the state-write discipline exists to prevent).
    from shared import state_io
    assert not state_io.lock_path_for(sustained_failure.CIRCUIT_OPEN_STATE_PATH).exists()


def test_a_healthy_breaker_never_pays_for_the_recovery_hook(tmp_path, mocker):
    """The hook sits PAST `_record_success`'s hot-path short-circuit, so a healthy system
    does not so much as `stat` the fleet window on every Smartsheet call."""
    from shared import circuit_breaker

    breaker_state = tmp_path / "breaker.json"
    hook = mocker.patch.object(circuit_breaker, "_registered_recovery_hook")

    circuit_breaker._record_success(breaker_state)   # never failed → already CLOSED/0

    hook.assert_not_called()


def test_a_stale_window_is_abandoned_rather_than_paging_instantly(tmp_path, logged, mocker):
    """Closing the window is best-effort, so the counter must self-heal if a close is
    missed: the FIRST observation of a NEW outage must not inherit a six-hour-old
    `first_seen_epoch` and page as though the fleet had been down all along."""
    base = 1_000_000.0
    stale = sustained_failure.CIRCUIT_OPEN_WINDOW_STALE_SECONDS
    mocker.patch.object(
        sustained_failure.time, "time", side_effect=[base, base + stale + 60]
    )
    fence = _fence(tmp_path)
    exc = smartsheet_client.SmartsheetCircuitOpenError("breaker OPEN")

    fence.handle(exc)              # opens a window nobody ever closes
    fence.handle(exc)              # a NEW outage, long after

    assert _fleet_pages(logged) == []
    state = json.loads(sustained_failure.CIRCUIT_OPEN_STATE_PATH.read_text())
    assert state["observations"] == 1  # a fresh window, not an inherited one


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


# ---- the cadence cannot drift back (parity guard) -------------------------


REPO_ROOT = Path(__file__).resolve().parent.parent

#: Every first-party module that escalates a PER-CYCLE consecutive-failure counter to
#: CRITICAL. Each must route the decision through `is_escalation_cycle`, or a 21 h outage
#: mints unrotatable rows again. Derived from disk below, so this list is the CLAIM and
#: the walk is the check — adding a seventh escalation site RED-lights until enrolled.
LADDER_CONSUMERS = frozenset({
    "shared/sustained_failure.py",        # TransientFence (publish_daemon + 5 send pollers)
    "po_materials/po_poll.py",
    "po_materials/rfq_poll.py",
    "po_materials/estimate_poll.py",
    "subcontracts/subcontract_poll.py",
    "field_ops/fieldops_sync.py",
    "safety_reports/portal_poll.py",
})

#: `scripts/watchdog.py` Check Q compares the SAME counter with `>=`, correctly: it is the
#: DAILY backstop reading the persisted count once per run, not a per-cycle escalation, so
#: it can fire at most once a day and mints at most one row. Allowlisted with that reason
#: rather than silently skipped.
LADDER_EXEMPT = frozenset({"scripts/watchdog.py"})

_FIRST_PARTY = (
    "shared", "safety_reports", "progress_reports", "po_materials",
    "subcontracts", "field_ops", "operator_dashboard", "scripts",
)


def _first_party_sources() -> list[Path]:
    out: list[Path] = []
    for pkg in _FIRST_PARTY:
        out.extend(sorted((REPO_ROOT / pkg).rglob("*.py")))
    return out


def _threshold_ge_compares(tree: ast.Module) -> list[int]:
    """Line numbers of every `<x> >= <…CRITICAL_THRESHOLD>` comparison in the module."""
    hits: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare) or not node.ops:
            continue
        if not isinstance(node.ops[0], ast.GtE):
            continue
        for comparator in node.comparators:
            name = (
                comparator.attr if isinstance(comparator, ast.Attribute)
                else comparator.id if isinstance(comparator, ast.Name)
                else ""
            )
            if name.endswith("CRITICAL_THRESHOLD"):
                hits.append(node.lineno)
    return hits


def test_no_daemon_hand_rolls_the_threshold_compare_again():
    """PROVE-IT-BITES anchor: reverting any daemon to `if n >= …CRITICAL_THRESHOLD:` for
    its per-cycle escalation RED-lights here, so the ladder cannot silently regress in one
    lane while the other six stay correct."""
    offenders: dict[str, list[int]] = {}
    for path in _first_party_sources():
        rel = str(path.relative_to(REPO_ROOT))
        if rel in LADDER_EXEMPT:
            continue
        lines = _threshold_ge_compares(
            ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        )
        if lines:
            offenders[rel] = lines
    assert not offenders, (
        "per-cycle CRITICAL escalation must go through "
        "`sustained_failure.is_escalation_cycle`, not a raw `n >= threshold` compare — an "
        f"open CRITICAL is never terminal, so `>=` mints unrotatable rows: {offenders}"
    )


def _calls_the_helper(tree: ast.Module) -> bool:
    """A real CALL to `is_escalation_cycle`, not a mention of it.

    Deliberately AST, not a substring scan: every one of these modules NAMES the helper in
    the comment explaining why it uses it, so a substring scan stays green when a daemon
    is reverted to `n >= threshold` with the comment left behind — which is exactly the
    shape a careless revert takes. Prose is not reach (the `_reachable_names` lesson in
    tests/test_smartsheet_retry.py)."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        name = (
            fn.attr if isinstance(fn, ast.Attribute)
            else fn.id if isinstance(fn, ast.Name)
            else ""
        )
        if name == "is_escalation_cycle":
            return True
    return False


def test_every_escalation_site_routes_through_the_shared_helper():
    """The other half: the helper exists AND every escalating module actually calls it."""
    callers = set()
    for path in _first_party_sources():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        if _calls_the_helper(tree) or (
            # the DEFINING module counts as a consumer — `TransientFence` calls it
            path.name == "sustained_failure.py"
        ):
            callers.add(str(path.relative_to(REPO_ROOT)))
    assert callers == LADDER_CONSUMERS, (
        "the set of modules routing through `is_escalation_cycle` drifted from the "
        f"declared list. missing={LADDER_CONSUMERS - callers} unexpected={callers - LADDER_CONSUMERS}"
    )
