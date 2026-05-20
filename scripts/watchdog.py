"""ITS daily watchdog — runs every morning at 7:00 AM via launchd.

Verifies operational state via per-check probes. Silent if green; surfaces
WARN / CRITICAL through `shared.error_log.log()` (which fans out to the
local log file, ITS_Errors, and the Resend/Sentry legs on CRITICAL).

Kill-switch semantics (Op Stds v9 §2):
    ACTIVE      — run all checks normally.
    MAINTENANCE — run all checks; alerts suppressed (WARN/CRITICAL results
                  downgraded to INFO before routing, so the Smartsheet row
                  still lands but Resend/Sentry legs never fire).
    PAUSED      — skip all checks; single INFO line "PAUSED — skipping".

Failure isolation (Op Stds v9 §27):
    Each check runs inside `_run_check`, which catches `Exception` and emits
    a distinguishable marker line through `error_log.log` at ERROR severity.
    A failure in one check does NOT prevent later checks from running. The
    harness itself writes to no side channel — `error_log.log()` has its
    own three recursion guards (Smartsheet, Resend, Sentry), so the harness
    does NOT need a separate recursion guard.

Session 1 checks (this file):
    A. Stale ITS_Review_Queue items (PENDING past 2× SLA) — WARN
    B. Open CRITICAL ITS_Errors rows (Resolved At blank) — WARN

Session 2 checks (planned, NOT in this file):
    C. Scheduled-jobs last-run via marker files
    D. 14-day reviewer-chain forward scan (Op Stds v9 §18)
    E. Anthropic spend trend
    F. Mail.app rule silent-disable inbound-mail activity check
       (per docs/tech_debt.md Mail.app entry added 2026-05-19)

Trigger this script from a launchd plist. See `scripts/launchd/`.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from shared import review_queue, sheet_ids, smartsheet_client
from shared.error_log import Severity, its_error_log, log
from shared.kill_switch import SystemState, check_system_state

_SCRIPT = "scripts.watchdog"

# Caps prevent the WARN detail string from ballooning when something goes
# truly sideways (e.g., dozens of rows past SLA after a long PAUSED window).
# 5 is enough for an operator to triage; the full set is one Smartsheet
# query away anyway.
REVIEW_QUEUE_ITEM_CAP = 5
CRITICAL_ITEMS_CAP = 5


@dataclass(frozen=True)
class CheckResult:
    severity: Severity
    summary: str
    details: str = ""


# ---- Check A: stale review queue ----------------------------------------


def _check_stale_review_queue() -> CheckResult:
    """PENDING rows past 2× SLA → WARN with capped Item ID list."""
    rows = review_queue.get_pending()
    stale = [r for r in rows if review_queue.is_past_sla(r)]

    if not stale:
        return CheckResult(
            severity=Severity.INFO,
            summary="No stale items in ITS_Review_Queue (past 2× SLA).",
        )

    item_ids = [str(r["Item ID"]) for r in stale]
    capped = item_ids[:REVIEW_QUEUE_ITEM_CAP]
    details = f"Item IDs: {', '.join(capped)}"
    if len(item_ids) > REVIEW_QUEUE_ITEM_CAP:
        details += f" (showing first {REVIEW_QUEUE_ITEM_CAP} of {len(item_ids)})"

    return CheckResult(
        severity=Severity.WARN,
        summary=f"{len(stale)} item(s) past 2× SLA in ITS_Review_Queue.",
        details=details,
    )


# ---- Check B: open CRITICAL events --------------------------------------


def _check_open_criticals() -> CheckResult:
    """CRITICAL rows in ITS_Errors with Resolved At blank → WARN.

    The `Severity` filter is passed to `get_rows` so the filtering happens
    inside the SDK layer (currently client-side post-fetch per
    smartsheet_client.get_rows — at sandbox volume that's fine). The
    `Resolved At` blank-check is local because a missing-key row dict and
    a row with `Resolved At=None` both mean "open" per the schema's
    "presence implies resolved" design.
    """
    rows = smartsheet_client.get_rows(
        sheet_ids.SHEET_ERRORS,
        filters={"Severity": "CRITICAL"},
    )
    open_rows = [r for r in rows if not r.get("Resolved At")]

    if not open_rows:
        return CheckResult(
            severity=Severity.INFO,
            summary="No open CRITICAL events in ITS_Errors.",
        )

    codes: list[str] = []
    for r in open_rows:
        code = r.get("Error")
        if code in (None, ""):
            codes.append("<no-code>")
        else:
            codes.append(str(code))
    capped = codes[:CRITICAL_ITEMS_CAP]
    details = f"Error codes: {', '.join(capped)}"
    if len(codes) > CRITICAL_ITEMS_CAP:
        details += f" (showing first {CRITICAL_ITEMS_CAP} of {len(codes)})"

    return CheckResult(
        severity=Severity.WARN,
        summary=f"{len(open_rows)} open CRITICAL event(s) in ITS_Errors.",
        details=details,
    )


# ---- Failure-isolation harness ------------------------------------------


def _run_check(
    check_fn: Callable[[], CheckResult],
    *,
    alerts_suppressed: bool,
) -> None:
    """Run one check with own try/except + marker line on failure.

    Per Op Stds v9 §27. `alerts_suppressed=True` (MAINTENANCE) downgrades
    WARN/CRITICAL results to INFO before routing so the Smartsheet row
    still lands but Resend/Sentry legs (which trigger only on CRITICAL via
    `error_log._alert_critical`) never fire. ERROR (from harness catches)
    is NOT downgraded — a broken check must remain operator-visible
    regardless of operational state.
    """
    try:
        result = check_fn()
    except Exception as e:
        log(
            Severity.ERROR,
            _SCRIPT,
            f"[watchdog-check-failed:{check_fn.__name__}] {e!r}",
        )
        return

    severity = result.severity
    if alerts_suppressed and severity in (Severity.WARN, Severity.CRITICAL):
        severity = Severity.INFO

    message = result.summary
    if result.details:
        message = f"{result.summary} | {result.details}"

    log(severity, _SCRIPT, message)


# ---- Entrypoint ---------------------------------------------------------


CHECKS: list[Callable[[], CheckResult]] = [
    _check_stale_review_queue,
    _check_open_criticals,
]


@its_error_log(_SCRIPT)
def main() -> None:
    state = check_system_state()
    if state == SystemState.PAUSED:
        log(Severity.INFO, _SCRIPT, "PAUSED — skipping all checks")
        return
    alerts_suppressed = state == SystemState.MAINTENANCE
    if alerts_suppressed:
        log(
            Severity.INFO,
            _SCRIPT,
            "MAINTENANCE — checks will run but alerts suppressed",
        )
    for check in CHECKS:
        _run_check(check, alerts_suppressed=alerts_suppressed)


if __name__ == "__main__":
    main()
