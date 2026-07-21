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
"""
from __future__ import annotations

import json
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

      1. ``SmartsheetCircuitOpenError`` → halt, WARN, and do NOT count. Folding
         circuit-open into the counter would make ONE real outage fire the breaker's own
         prolonged-open CRITICAL *plus* a ``*_sustained`` CRITICAL from each of the 6-10
         enrolled daemons, on separate ``alert_dedupe`` keys — a page storm for one root
         cause. The breaker surface owns that page.
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
        """Log one transient-halt occurrence. ``count=False`` skips the counter entirely
        (the circuit-open case — see the class docstring)."""
        if not count:
            error_log.log(
                Severity.WARN, self._script,
                f"Smartsheet circuit breaker OPEN — cycle skipped, no work attempted "
                f"({detail}). Not counted toward {self._sustained_error_code}: the "
                "breaker's own prolonged-open CRITICAL owns this page.",
                error_code=self._transient_error_code,
            )
            return
        n = self._counter.record()
        where = f" See {self._runbook}." if self._runbook else ""
        if n >= self._threshold:
            error_log.log(
                Severity.CRITICAL, self._script,
                f"Smartsheet read failing for {n} consecutive cycles — SUSTAINED outage, "
                f"not a blip (cycle halted each time, no work done).{where} {detail}",
                error_code=self._sustained_error_code,
            )
        else:
            error_log.log(
                Severity.ERROR, self._script,
                f"transient Smartsheet failure — cycle halted, retrying next cycle "
                f"({n} consecutive, CRITICAL at {self._threshold}).{where} {detail}",
                error_code=self._transient_error_code,
            )

    def reset(self) -> None:
        """Clear the consecutive count after a successful pre-work read.

        Short-circuits when no state file exists, so a healthy daemon does ZERO state I/O
        per cycle (and never creates the sidecar lock) — the breaker's same posture.
        """
        if not self._state_path.exists():
            return
        self._counter.reset()

    def flush_retry_recovery(self) -> None:
        """Drain `smartsheet_client.drain_retry_recovery()` → ONE summarized WARN row.

        Operator decision D3: a retry that SUCCEEDS is otherwise invisible on the
        dashboard, so a chronically flaky sheet would be silently absorbed. Best-effort —
        a failure here must never disturb an otherwise-successful pass.
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
                Severity.WARN, self._script,
                f"Smartsheet transient failures RECOVERED on retry this cycle: {detail}. "
                "The cycle succeeded; a repeating pattern here means the backend is "
                "chronically flaky.",
                error_code="smartsheet_retry_recovered",
            )
        except Exception:  # noqa: BLE001 — visibility extra; never disturb a good pass
            pass
