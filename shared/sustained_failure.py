"""Sustained consecutive-failure counter — the ERROR→CRITICAL escalation primitive.

THE GAP THIS CLOSES (2026-07-20 forensic): `estimate_poll`'s pending fetch failed every
120s cycle for ~21 hours — 629 ITS_Errors rows — and the operator never saw it, because
each cycle logged Severity **ERROR** ("transient, rows left for next cycle") and the
dashboard's fire surfaces (the Open-CRITICALs panel, the /system map badges) and the
triple-fire push path all key on **CRITICAL**. A *persistent* transient IS an outage;
without escalation it is structurally invisible.

`fieldops_sync` and `portal_poll` (Check Q) already carry a per-daemon copy of the
persisted consecutive-failure counter that closes this. This module is that pattern
extracted once (§14: 4 immediate live consumers — estimate_poll / rfq_poll / po_poll /
subcontract_poll — plus the 2 existing per-daemon copies as future convergence), so a
new intake daemon gets escalation by construction instead of by remembering.

Posture (mirrors `fieldops_sync._record_pending_fetch_failure` exactly):
- state under ``~/its/state/`` via `state_io` (atomic write + sidecar lock — the
  house write discipline);
- `record()` returns the new consecutive count; ANY state error degrades to
  ``1`` with a WARN (never page off a state glitch);
- `reset()` zeroes after a success, best-effort (a reset failure risks one spurious
  CRITICAL next cycle, never a missed outage);
- the CALLER owns the threshold compare + both log lines, so each daemon's error
  codes and remediation copy stay lane-specific (`<lane>_pending_fetch_sustained`).

`TransientFence` (added 2026-07-21) is the counter plus the severity decision, for the
adjacent gap at the OTHER end of the scale: a pre-work Smartsheet read that fails ONCE
and escapes the pass, which `@its_error_log` then stamps CRITICAL `uncaught_exception`.
See its class docstring.

`record_circuit_open` / `clear_circuit_open` are the THIRD scale: a whole-fleet outage.
Once the breaker trips, every fenced read raises circuit-open, which each daemon
deliberately leaves out of its own counter — so without a shared counter nothing escalated
at all and the only page left was the 07:00 watchdog. This is one window, one fixed
`(script, error_code)` pair, one page for the fleet — and, since the 2026-07-21 re-review,
on the SAME `is_escalation_cycle` ladder as everything else here rather than firing CRITICAL
on every observation. See the section comment above it.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from shared import error_log, smartsheet_client, state_io
from shared.error_log import Severity

#: Consecutive failing cycles before the caller escalates ERROR → CRITICAL. Shared
#: default (5 × 120s ≈ 10 min of sustained outage); callers may override.
DEFAULT_CRITICAL_THRESHOLD = 5

#: Threshold for the 15-minute send pollers. 5 cycles there would be ~75 min of silence
#: before the page; 3 keeps the same ~45 min ceiling as 5×120s on the fast daemons while
#: still absorbing an isolated blip (operator decision D2, 2026-07-21).
SLOW_CADENCE_CRITICAL_THRESHOLD = 3

#: The geometric ladder stops doubling here and repeats at a FIXED interval of
#: ``threshold × LADDER_MAX_MULTIPLIER`` cycles. Purely geometric would take a
#: multi-day outage from a 10 h gap to a 21 h gap to a 42 h gap — eventually the
#: escalation stops being a notification at all. 8 keeps the steady-state re-notify at
#: 40 cycles (~80 min) for the 120 s intake daemons and 24 cycles (~6 h) for the
#: 15-minute send pollers, both of which have ALREADY paged at the crossing cycle.
LADDER_MAX_MULTIPLIER = 8


def is_escalation_cycle(
    count: int, threshold: int, *, max_multiplier: int = LADDER_MAX_MULTIPLIER
) -> bool:
    """Should THIS consecutive-failure cycle be logged CRITICAL rather than ERROR?

    THE ONE PLACE the cadence is decided (§14: eight live callers — this module's
    `TransientFence` and `record_circuit_open`, plus the six daemons that each hand-rolled
    ``n >= threshold``), so the compare cannot drift lane by lane.

    ``max_multiplier`` exists ONLY so the fleet circuit-open ladder can pick a
    steady-state interval appropriate to ITS clock (it counts OBSERVATIONS arriving from
    several daemons at once, not one daemon's cycles — see `record_circuit_open`). Same
    function, same arithmetic, one declared knob: the alternative was a second ladder
    implementation, which is precisely the drift this helper exists to prevent. Every
    per-cycle caller takes the default, so their cadence is unchanged.

    WHY NOT ``n >= threshold`` (the shape this replaces, 2026-07-21 re-review). An open
    CRITICAL is NEVER terminal — `shared/errors_rotation.errors_row_is_terminal` returns
    False for a CRITICAL without a "Resolved At" stamp — so a CRITICAL row is UNROTATABLE
    at every floor, including watchdog Check O's storm floor. Firing one on every cycle
    past the threshold turns a 21 h outage on a 120 s daemon into ~626 PERMANENT rows.
    ITS_Errors reached 19,975 of its 20,000 hard cap on 2026-07-13 and twice fired a
    "NOTHING is deletable" lockout; this path re-opens that lockout by a route rotation
    cannot rescue, and buries the genuinely-open CRITICALs that watchdog Check B and the
    dashboard's Open-CRITICALs panel exist to surface. The path had never yet run in
    production (zero ``*_sustained`` rows live), so it was fixed while still latent.

    THE LADDER: fire on the threshold-CROSSING cycle, then at 2×, 4×, 8× … the threshold,
    capping the step at ``threshold × max_multiplier`` so a long outage keeps
    re-notifying on a fixed interval instead of going quiet. Every other cycle past the
    threshold still writes its per-occurrence row — at Severity.ERROR, which IS terminal
    and therefore reclaimable. Both doctrine requirements hold: a real sustained outage
    escalates to CRITICAL (§3.1 push legs wake the operator), and nothing is silent.

    TOTAL by construction: this runs inside an error-handling path, so a nonsense
    threshold (0 / negative, e.g. a typo'd override) escalates rather than raising.
    """
    if count < 1:
        return False
    if threshold < 1:
        return True  # degenerate config: escalate rather than crash inside error handling
    if count < threshold:
        return False
    cap = threshold * max(1, max_multiplier)
    if count >= cap:
        return count % cap == 0
    quotient, remainder = divmod(count, threshold)
    # A power of two → the count sits exactly on a rung of the doubling ladder.
    return remainder == 0 and quotient & (quotient - 1) == 0


# ---- Fleet-level circuit-open escalation ---------------------------------
#
# THE GAP THIS CLOSES (2026-07-21 review finding). `TransientFence` deliberately keeps
# SmartsheetCircuitOpenError OUT of each daemon's own counter, because one outage would
# otherwise fire a `*_sustained` CRITICAL from every enrolled daemon on a SEPARATE
# `alert_dedupe` key — 6-10 pages for one root cause. But during a REAL outage the
# breaker trips within a cycle or two, after which every fenced read raises exactly
# SmartsheetCircuitOpenError — so with circuit-open uncounted, nothing escalated at all
# and the only remaining CRITICAL was watchdog **Check J**, which runs 07:00 DAILY. Net:
# "Smartsheet is down and approved sends are frozen" could sit unpaged for up to ~24 h.
#
# The fix is a SHARED, fleet-level counter instead of a per-daemon one. It is coherent
# because the breaker's own state is already cross-process: whichever daemon fires next
# observes the same OPEN breaker. Every observation and every page is keyed on the FIXED
# pair below, so `shared/alert_dedupe.py` (which dedupes the push legs on
# `(script, error_code)`) collapses the entire fleet into ONE operator page per window —
# the storm concern is answered without surrendering detection to a daily job.
#
# TIME-BASED, NOT COUNT-BASED, deliberately: a count threshold means a different
# wall-clock guarantee for a 60 s daemon than for a 15-minute one. The window opens on
# the first observation; the page fires on the first observation at or after
# CIRCUIT_OPEN_SUSTAINED_SECONDS.
#
# WHO ACTUALLY OBSERVES (verified against live code, 2026-07-21 — an earlier revision of
# this comment overstated it, and a false operator-facing claim is an Op Stds §55
# violation, not a rounding error). Only a daemon that REACHES a fenced site during the
# outage contributes an observation. There are exactly two fenced call sites —
# `publish_daemon` (its polling-gate + base-URL reads) and `send_poll_core`'s
# review-sheet + approver reads, which the five send pollers share. Of those five,
# `po_send_poll`, `rfq_send_poll` and `subcontract_send_poll` carry
# DEFAULT_POLLING_ENABLED=False, so `send_poll_core._polling_enabled` fail-opens to False
# during circuit-open and `poll_inside_lock` — where both fences live — is never entered.
# They never observe. The honest live observer set is therefore THREE:
#   * publish_daemon        — 120 s cadence (StartInterval 120)
#   * weekly_send_poll      — 15 min
#   * progress_send_poll    — 15 min
# publish_daemon is only in that set because its gate read PROPAGATES circuit-open to the
# fence instead of collapsing it into a fail-open "false" (see `_read_str_setting`). If
# that ever regresses, the observer set silently drops to the two 15-minute pollers and
# this comment is wrong again — `tests/test_publish_daemon.py` pins it.
#
# RESULTING WALL-CLOCK TIME-TO-PAGE, given that set:
#   * with publish_daemon observing → the window matures on its first 120 s cycle at or
#     after 600 s, i.e. ~10-12 min after the breaker opens.
#   * publish_daemon down / gated off, only the two 15-minute pollers observing →
#     ~10-25 min (the first observation at or after 600 s lands on whichever poller's
#     cycle falls next; worst case ~25 min if a single poller's first observation lands
#     just as the breaker opens).
# Both are sub-hour and both are dramatically better than the up-to-24 h that watchdog
# Check J alone provided.
# The alerting path does not depend on Smartsheet being up: `error_log.log(CRITICAL)`
# triple-fires, and the Resend + Sentry legs are independent of the failing backend.
#: Fixed script/code pair — the whole POINT is that every daemon pages under ONE
#: alert_dedupe key. Do NOT parameterize these per daemon.
CIRCUIT_OPEN_SCRIPT = "shared.smartsheet_client"
CIRCUIT_OPEN_ERROR_CODE = "smartsheet_circuit_open_sustained"

#: Seconds of continuously-observed circuit-open before the fleet pages. 600 s mirrors
#: `defaults.CIRCUIT_BREAKER_PROLONGED_OPEN_ALERT_SECONDS` — the SAME definition of
#: "prolonged" the watchdog uses, just delivered sub-daily instead of at 07:00.
CIRCUIT_OPEN_SUSTAINED_SECONDS = 600

#: THE FLEET LADDER (2026-07-21 re-review — the one escalation that was NOT on the ladder).
#: This path used to fire CRITICAL on EVERY observation once the window matured, which is
#: the exact unbounded-growth shape `is_escalation_cycle` was introduced to remove: an open
#: CRITICAL is never terminal (`errors_rotation.errors_row_is_terminal`), so those rows are
#: unrotatable at every floor, and ITS_Errors reached 19,975 of its 20,000 hard cap on
#: 2026-07-13 and twice fired a "NOTHING is deletable" lockout. With the honest 3-observer
#: set (publish_daemon 120 s + weekly_send_poll 15 min + progress_send_poll 15 min ≈ 38
#: observations/h) publish_daemon ALONE minted ~30 permanent CRITICALs/h, ~720/day.
#:
#: KEYED ON OBSERVATIONS, NOT CYCLES. The counter below advances once per observation from
#: ANY daemon, so a "rung" is not a fixed wall-clock step the way a single daemon's cycle
#: count is — the interval shortens as more observers participate and lengthens as they
#: drop out. Two consequences are deliberate:
#:   * THRESHOLD 1 — the first rung IS the crossing observation, so the fleet still pages
#:     the moment the window matures (~10-12 min after the breaker opens, unchanged).
#:   * A LARGER CAP than the per-cycle ladders. `LADDER_MAX_MULTIPLIER` (8) is tuned to a
#:     daemon's own cadence: 40 cycles ≈ 80 min for a 120 s daemon. Applied to a threshold
#:     of 1 against a ~38/h observation stream it would re-notify every 8 observations
#:     ≈ every 13 min — ~110 unrotatable rows/day, still the growth problem. 48 puts the
#:     steady-state re-notify at ~76 min with the full observer set, i.e. the SAME ~80 min
#:     band the per-cycle ladders chose.
#: Resulting rungs (observations past the window) and their wall clock at ~38 obs/h, one
#: observation per ~95 s, measured from the crossing observation:
#:     1 (the crossing observation, ~10-12 min after the breaker opened) · 2 (+~1.5 min) ·
#:     4 (+~5 min) · 8 (+~11 min) · 16 (+~24 min) · 32 (+~49 min) · then every 48
#:     (~76 min apart) forever.
#: A 24 h total outage therefore mints ~25 CRITICAL rows instead of ~912, and every other
#: observation still writes its per-occurrence row at Severity.ERROR — terminal, and so
#: reclaimable by row-cap rotation. If publish_daemon is down and only the two 15-minute
#: pollers observe (8 obs/h) the same rungs stretch to ~6 h apart in steady state, which
#: is the correct direction: fewer observers means less evidence, not more paging.
CIRCUIT_OPEN_LADDER_THRESHOLD = 1
CIRCUIT_OPEN_LADDER_MAX_MULTIPLIER = 48

#: A window with no observation for this long is ABANDONED — the next observation opens a
#: fresh one instead of inheriting an ancient `first_seen_epoch` and paging instantly.
#: Closing the window (see `clear_circuit_open`) is best-effort by design, so the counter
#: must also be self-healing if a close is ever missed. MUST exceed the slowest observer's
#: cadence (900 s, the send pollers) or a slow observer could never accumulate; 3600 s
#: gives 4× headroom over that while still expiring a forgotten window within the hour.
CIRCUIT_OPEN_WINDOW_STALE_SECONDS = 3600

STATE_DIR = Path.home() / "its" / "state"
#: Shared across every process that observes circuit-open (module-level so the suite can
#: redirect it, exactly like `circuit_breaker.STATE_FILE`).
CIRCUIT_OPEN_STATE_PATH = STATE_DIR / "smartsheet_circuit_open.json"


def record_circuit_open(observer: str, detail: str) -> None:
    """Record one fleet-level circuit-open observation; CRITICAL on the LADDER once sustained.

    Called by every `TransientFence` on the circuit-open branch. All access goes through
    `state_io.with_path_lock` because MULTIPLE daemons genuinely contend here — that is
    the point of a shared counter, not an accident.

    THREE outcomes, mirroring `TransientFence.note_transient` so the fleet path is on the
    SAME ladder as every other escalation (see CIRCUIT_OPEN_LADDER_THRESHOLD above for the
    observation-vs-cycle reasoning and the wall clock of each rung):

      1. inside the window (< CIRCUIT_OPEN_SUSTAINED_SECONDS) → nothing logged here. The
         observing daemon has already written its own WARN row for this cycle, so the
         "never silent" invariant is satisfied without a second row per observation.
      2. a LADDER rung → Severity.CRITICAL under the fixed
         ``(shared.smartsheet_client, smartsheet_circuit_open_sustained)`` pair, so
         `alert_dedupe` still collapses the whole fleet into ONE operator push per window.
      3. any other matured observation → the SAME message and error code at
         Severity.ERROR. One error code deliberately, not a second "_repeat" code: the
         whole point of this path is ONE fixed pair for one root cause, and an ERROR is
         terminal (`errors_rotation.errors_row_is_terminal`) and therefore reclaimable, so
         the record leg is preserved without minting unrotatable rows.

    Best-effort like every other counter in this module: a state error logs a WARN and
    returns rather than paging off a filesystem glitch.
    """
    now = time.time()
    matured = 0
    try:
        with state_io.with_path_lock(CIRCUIT_OPEN_STATE_PATH):
            state: dict[str, object] = {}
            if CIRCUIT_OPEN_STATE_PATH.exists():
                try:
                    loaded = json.loads(CIRCUIT_OPEN_STATE_PATH.read_text())
                    if isinstance(loaded, dict):
                        state = loaded
                except (OSError, json.JSONDecodeError, ValueError):
                    state = {}
            raw_last = state.get("last_seen_epoch")
            # A window nobody has touched in CIRCUIT_OPEN_WINDOW_STALE_SECONDS is stale:
            # the outage ended and the close was missed (it is best-effort). Inheriting
            # its `first_seen_epoch` would page on the FIRST observation of the NEXT
            # outage, which reads to the operator as a 6-hour-old fault. Start over.
            last_seen = float(raw_last) if isinstance(raw_last, int | float) else None
            stale = last_seen is None or (now - last_seen) > CIRCUIT_OPEN_WINDOW_STALE_SECONDS
            if stale:
                state = {}
            raw_first = state.get("first_seen_epoch")
            first_seen = float(raw_first) if isinstance(raw_first, int | float) else now
            observations = state.get("observations")
            count = observations + 1 if isinstance(observations, int) else 1
            # The ladder counts only observations at or PAST the window, so rung 1 is the
            # crossing observation. Held in the shared file (not derived from `count`)
            # because the observation rate varies with how many daemons are alive —
            # deriving it would silently re-date the rungs when an observer drops out.
            # A pre-ladder state file has no key: absent → 0, so an in-flight window
            # simply starts its ladder at the next matured observation.
            escalations = state.get("escalations")
            matured = escalations if isinstance(escalations, int) and escalations > 0 else 0
            if (now - first_seen) >= CIRCUIT_OPEN_SUSTAINED_SECONDS:
                matured += 1
            state_io.atomic_write_json(
                CIRCUIT_OPEN_STATE_PATH,
                {
                    "first_seen_epoch": first_seen,
                    "last_seen_epoch": now,
                    "observations": count,
                    "escalations": matured,
                    "last_observer": observer,
                },
            )
    except AssertionError:  # a control firing, not a state glitch — see record()
        raise
    except Exception as exc:  # noqa: BLE001 — never page off a state glitch
        error_log.log(
            Severity.WARN, CIRCUIT_OPEN_SCRIPT,
            f"fleet circuit-open counter write failed (observation dropped): {exc!r}",
            error_code=f"{CIRCUIT_OPEN_ERROR_CODE}_counter_failed",
        )
        return

    open_for = now - first_seen
    if open_for < CIRCUIT_OPEN_SUSTAINED_SECONDS:
        return
    severity = (
        Severity.CRITICAL
        if is_escalation_cycle(
            matured,
            CIRCUIT_OPEN_LADDER_THRESHOLD,
            max_multiplier=CIRCUIT_OPEN_LADDER_MAX_MULTIPLIER,
        )
        else Severity.ERROR
    )
    error_log.log(
        severity, CIRCUIT_OPEN_SCRIPT,
        f"Smartsheet circuit breaker has been OPEN for {open_for / 60:.0f} min "
        f"({count} observations across the daemon fleet, most recently {observer}). "
        "Every Smartsheet-backed daemon is short-circuiting: approved sends are FROZEN "
        "and nothing is being filed. This is a SUSTAINED backend outage, not a blip. "
        f"See docs/runbooks/circuit_breaker.md. Last detail: {detail}",
        error_code=CIRCUIT_OPEN_ERROR_CODE,
    )


def clear_circuit_open() -> None:
    """Close the fleet circuit-open window — on PROOF the backend answered, nothing less.

    WHO MAY CALL THIS (the 2026-07-21 re-review lesson). Exactly one caller:
    `circuit_breaker`'s recovery transition, reached only when a real Smartsheet call
    SUCCEEDED after the breaker had left the closed/zero state. That is the single event
    in the system which actually proves reachability.

    It is emphatically NOT `TransientFence.reset()`. A daemon calls `reset()` at a site it
    BELIEVES follows a successful read, and that belief is unverifiable from here: several
    readers fail OPEN, returning a fallback value with no exception (publish_daemon's own
    `_read_str_setting` did exactly that with circuit-open before this change). Wiring the
    clear into `reset()` therefore let the 120 s publish daemon wipe a fleet-wide window
    every 2 minutes during an outage — the window could never mature to
    CIRCUIT_OPEN_SUSTAINED_SECONDS, and the escalation was unreachable in production while
    every unit test stayed green. A fail-open fallback is not evidence of anything, and
    fleet-scoped state must never be cleared on one process's local belief.

    Short-circuits when no state file exists, so a healthy fleet does ZERO state I/O per
    cycle (one `stat`) — the same posture as `TransientFence.reset`.
    """
    if not CIRCUIT_OPEN_STATE_PATH.exists():
        return
    try:
        with state_io.with_path_lock(CIRCUIT_OPEN_STATE_PATH):
            CIRCUIT_OPEN_STATE_PATH.unlink(missing_ok=True)
    except AssertionError:  # a control firing, not a state glitch — see record()
        raise
    except Exception:  # noqa: BLE001 — best-effort clear (risks one late page, never a missed one)
        return
    # Remove the sidecar too, OUTSIDE the lock (with_path_lock holds an flock on it, and
    # unlinking a file you hold open drops the inode the next waiter would contend on).
    # Left behind, it is a permanent orphan in ~/its/state after every outage — the exact
    # litter class the state-write discipline exists to prevent.
    try:
        state_io.lock_path_for(CIRCUIT_OPEN_STATE_PATH).unlink(missing_ok=True)
    except OSError:  # noqa: S110 — cosmetic cleanup; never worth disturbing a caller
        pass


class SustainedFailureCounter:
    """A persisted consecutive-failure counter for one recurring operation."""

    def __init__(self, state_path: Path, script_name: str, counter_error_code: str) -> None:
        self._path = state_path
        self._script = script_name
        self._counter_error_code = counter_error_code

    def record(self) -> int:
        """Increment + persist; return the new consecutive count (state error → 1 + WARN)."""
        try:
            with state_io.with_path_lock(self._path):
                count = 0
                if self._path.exists():
                    try:
                        count = int(json.loads(self._path.read_text()).get("count", 0))
                    except (OSError, json.JSONDecodeError, ValueError, TypeError, AttributeError):
                        count = 0
                count += 1
                state_io.atomic_write_json(self._path, {"count": count})
                return count
        except AssertionError:
            # NOT a state glitch — an assertion is a CONTROL firing, not an I/O failure.
            # The unit suite's `_forbid_live_state_writes` guard raises AssertionError,
            # and this broad handler used to swallow it and answer "count = 1": the guard
            # was defeated AND the escalation ladder silently degraded to a constant 1 in
            # every daemon-entry test. That is exactly how nine real `~/its/state/*.lock`
            # files got created on 2026-07-21 while the suite reported clean. Re-raised so
            # a control that fires is never absorbed into a quiet degraded path.
            raise
        except Exception as exc:  # noqa: BLE001 — counter is best-effort; never page off a state glitch
            error_log.log(
                Severity.WARN, self._script,
                f"consecutive-failure counter write failed (treating as #1): {exc!r}",
                error_code=self._counter_error_code,
            )
            return 1

    def reset(self) -> None:
        """Zero after a success. Best-effort — a failure only risks one spurious CRITICAL."""
        try:
            with state_io.with_path_lock(self._path):
                if self._path.exists():
                    state_io.atomic_write_json(self._path, {"count": 0})
        except AssertionError:  # a control firing, not a state glitch — see record()
            raise
        except Exception:  # noqa: BLE001 — best-effort reset
            pass


class TransientFence:
    """Pass-boundary severity fence for a Smartsheet read a daemon does BEFORE its work.

    THE GAP THIS CLOSES (2026-07-21 forensic). `@its_error_log` stamps ANY unhandled
    exception ``Severity.CRITICAL error_code="uncaught_exception"``, unconditionally. So a
    single 30 s ReadTimeout inside a pre-work config/approver read escaped the pass and
    paged the operator — twice in one day (`progress_send_poll` 05:36Z inside
    ``list_workspace_share_emails``; `publish_daemon` 14:37Z inside ``get_setting``). Both
    daemons recovered on their very next cycle. Bounded retry alone does not fix this: an
    EXHAUSTED retry sequence still raises, so it still lands as a CRITICAL.

    NOT a decorator, deliberately. A decorator would have to return a foreign sentinel on
    the halted path, which breaks the typed returns under blocking mypy (``poll_once`` →
    ``PollStats``, ``publish_once`` → ``PublishStats``) and forces every caller to learn a
    new return contract. As a site-local helper each daemon returns ITS OWN typed halted
    value::

        try:
            ...pre-work read...
        except Exception as exc:
            if fence.handle(exc):     # transient → already logged
                return <this daemon's own typed halted value>
            raise                     # non-transient → real bug → CRITICAL, unchanged
        fence.reset()

    THREE outcomes in ``handle``:

      1. ``SmartsheetCircuitOpenError`` → halt, WARN, and do NOT count in THIS daemon's
         counter. Folding circuit-open into the per-daemon counter would make ONE real
         outage fire a ``*_sustained`` CRITICAL from each of the 6-10 enrolled daemons on
         separate ``alert_dedupe`` keys — a page storm for one root cause. Instead the
         observation goes to the SHARED fleet counter (``record_circuit_open`` above),
         which pages ONCE per window under the fixed
         ``(shared.smartsheet_client, smartsheet_circuit_open_sustained)`` pair. So a real
         sustained outage still escalates sub-hour; it just does not multiply.
      2. Any other ``SmartsheetTransientError`` → ERROR row + counter; at
         ``n >= threshold`` a CRITICAL instead, fired EVERY cycle past the threshold
         (Op Stds §3.1 — the ITS_Errors record leg is per-occurrence; suppression is the
         push legs' job via ``alert_dedupe``).
      3. Everything else → ``False``, caller re-raises. Genuinely-unknown exceptions keep
         their immediate ``uncaught_exception`` CRITICAL — only the precisely-typed
         transient class is softened, so a real bug is never masked as a blip.

    ``SmartsheetRateLimitError`` is deliberately in bucket 3. A 429 reaching us means the
    SDK ALREADY exhausted its own 4003 retry window, i.e. sustained pressure rather than a
    blip, and treating it as non-transient preserves today's immediate page exactly.

    Fail-closed is untouched: the fence changes only the SEVERITY and the return path of a
    failed pre-work read. The cycle still aborts before any dispatch, so a fenced daemon
    performs ZERO sends on any approver-load failure.
    """

    def __init__(
        self,
        script_name: str,
        *,
        state_path: Path,
        transient_error_code: str,
        sustained_error_code: str,
        threshold: int = DEFAULT_CRITICAL_THRESHOLD,
        runbook: str = "",
    ) -> None:
        self._script = script_name
        self._transient_error_code = transient_error_code
        self._sustained_error_code = sustained_error_code
        self._threshold = threshold
        self._runbook = runbook
        self._state_path = state_path
        self._counter = SustainedFailureCounter(
            state_path, script_name, f"{transient_error_code}_counter_failed"
        )

    def handle(self, exc: BaseException) -> bool:
        """True ⇒ transient (already logged); the caller halts its cycle. False ⇒ re-raise."""
        if isinstance(exc, smartsheet_client.SmartsheetCircuitOpenError):
            self.note_transient(f"{type(exc).__name__}: {exc}", count=False)
            return True
        if smartsheet_client.is_transient_error(exc):
            self.note_transient(f"{type(exc).__name__}: {exc}", count=True)
            return True
        return False

    def note_transient(self, detail: str, *, count: bool = True) -> None:
        """Log one transient-halt occurrence. ``count=False`` routes the observation to the
        SHARED fleet circuit-open counter instead of this daemon's own (see the class
        docstring outcome 1) — it is never simply discarded."""
        if not count:
            error_log.log(
                Severity.WARN, self._script,
                f"Smartsheet circuit breaker OPEN — cycle skipped, no work attempted "
                f"({detail}). Not counted toward {self._sustained_error_code}: a fleet-wide "
                f"outage escalates ONCE via {CIRCUIT_OPEN_ERROR_CODE}, not once per daemon.",
                error_code=self._transient_error_code,
            )
            record_circuit_open(self._script, detail)
            return
        n = self._counter.record()
        where = f" See {self._runbook}." if self._runbook else ""
        if is_escalation_cycle(n, self._threshold):
            error_log.log(
                Severity.CRITICAL, self._script,
                f"Smartsheet read failing for {n} consecutive cycles — SUSTAINED outage, "
                f"not a blip (cycle halted each time, no work done).{where} {detail}",
                error_code=self._sustained_error_code,
            )
        else:
            still = "still " if n > self._threshold else ""
            error_log.log(
                Severity.ERROR, self._script,
                f"transient Smartsheet failure — cycle halted, {still}retrying next cycle "
                f"({n} consecutive, CRITICAL at {self._threshold}).{where} {detail}",
                error_code=self._transient_error_code,
            )

    def reset(self) -> None:
        """Clear THIS daemon's consecutive count after a successful pre-work read.

        Deliberately does NOT touch the shared fleet circuit-open window. A caller
        reaches `reset()` on the strength of its own belief that a read succeeded, and
        readers that fail OPEN reach it having proved nothing at all — see
        `clear_circuit_open`'s docstring for the regression that taught us this. The fleet
        window is closed only by the breaker's own recovery transition.

        Short-circuits when the state file is absent, so a healthy daemon does ZERO state
        I/O per cycle (one `stat`) — the breaker's same posture.
        """
        if not self._state_path.exists():
            return
        self._counter.reset()

    def flush_retry_recovery(self) -> None:
        """Drain the recovered-retry accumulator for THIS daemon (see the module function).

        Convenience only — a daemon that has no fence calls `flush_retry_recovery(script)`
        directly; the two must stay behaviourally identical, so this delegates rather than
        duplicating the body.
        """
        flush_retry_recovery(self._script)


def flush_retry_recovery(script_name: str) -> None:
    """Drain `smartsheet_client.drain_retry_recovery()` → ONE summarized WARN row.

    Operator decision D3: a retry that SUCCEEDS is invisible by construction — nothing
    raises, nothing is logged — so a chronically flaky sheet would be silently absorbed,
    which is the "never silent" invariant inverted.

    A MODULE FUNCTION, not only a `TransientFence` method, because most interval daemons
    have no fence: `portal_poll` / `po_poll` / `rfq_poll` / `estimate_poll` /
    `subcontract_poll` / `fieldops_sync` all issue enrolled Smartsheet reads but classify
    their own failures. Shipping D3 as a fence method alone gave the summary row to 2
    daemons out of ~12 — the recoveries of the other ten stayed local-log-only, i.e.
    invisible on exactly the dashboard surface D3 exists to feed.

    CALL IT AT A PASS EXIT, and only where the accumulator belongs to the pass that just
    ran: the accumulator is process-global, so an early return that skips the flush simply
    rolls its recoveries into the next cycle's row (a merge, never a loss).

    Best-effort: a failure here must never disturb an otherwise-successful pass.
    """
    try:
        recovered = smartsheet_client.drain_retry_recovery()
        if not recovered:
            return
        detail = ", ".join(
            f"{call}×{stats['sequences']} ({stats['attempts']} extra attempts)"
            for call, stats in sorted(recovered.items())
        )
        error_log.log(
            Severity.WARN, script_name,
            f"Smartsheet transient failures RECOVERED on retry this cycle: {detail}. "
            "The cycle succeeded; a repeating pattern here means the backend is "
            "chronically flaky.",
            error_code="smartsheet_retry_recovered",
        )
    except Exception:  # noqa: BLE001 — visibility extra; never disturb a good pass
        pass
