"""Smartsheet per-workspace sheet-count headroom guard (A1 / forensic scaling eval B1).

The eval's #1 liability is per-job-per-week Smartsheet sheet proliferation
(~1,040 sheets/yr at 20 jobs) marching toward an unverified per-workspace/plan
sheet cap, after which a create silently fails. Sheets stay WEEKLY (the 2026-06-28
"monthly" proposal was reverted 2026-06-29 — the sheet IS the week); the operator
confirmed Evergreen is on a Business/Enterprise plan (2026-06-29), so capacity is
NOT limiting. This module is the runtime backstop / runaway tripwire so a
find-or-create NEVER silently creates a sheet past the cap — when headroom is thin
it routes a breach signal to the Review Queue (an operator signal).

WIRED (growth Slice 3, 2026-07): `safety_reports.week_sheet.ensure_week_sheet` —
the ONE parameterized find-or-create engine both safety and progress compile paths
use (via `generate_core` / `intake` / `compile_now_poll`) — calls
`check_create_headroom` on its CREATE branch (`_warn_on_thin_headroom`). The
posture is ADVISORY: on a margin breach the caller WARNs + enqueues
`route_breach_to_review_queue`, and the create STILL PROCEEDS — the tripwire is
an operator signal (archive / period-split / raise the tier), never a compile
blocker. The ceiling is a conservative soft mark well before any plausible plan
cap, so blocking a filing path on it would trade a hypothetical cap breach for a
real outage.

FAIL-OPEN by design (matches `shared.kill_switch` / the `defaults` fallback
philosophy): a transient sheet-count read failure must NEVER block a create —
false negatives (a create proceeds without the guard) are recoverable; blocking a
legitimate create on a flaky Smartsheet read is not. The caller logs the WARN.

The REAL cap is not API-exposed; the ceiling/margin come from ITS_Config
(`smartsheet.sheet_count_ceiling` / `…margin`, workstream="global") with the
`shared.defaults` fallbacks. See `scripts/verify_sheet_cap.py` + the operator
follow-up to confirm the cap + the plan tier.
"""
from __future__ import annotations

from dataclasses import dataclass

from . import defaults, review_queue, smartsheet_client
from .error_log import Severity

CFG_CEILING = "smartsheet.sheet_count_ceiling"
CFG_MARGIN = "smartsheet.sheet_count_margin"


@dataclass(frozen=True)
class Headroom:
    """Result of a pre-create sheet-count headroom check.

    `ok=True`  → safe to create one more sheet in this workspace.
    `ok=False` → creating one more would cross `ceiling - margin`; the caller
                 routes the breach to the Review Queue
                 (`route_breach_to_review_queue`) + WARNs, then proceeds with
                 the create anyway — advisory, never silent, never blocking.

    On a fail-open read error `ok=True`, `current=-1`, and `note` carries the
    reason (the caller logs the WARN; the create proceeds unguarded — never blocked).
    """

    ok: bool
    current: int
    ceiling: int
    margin: int
    note: str = ""


def _read_int_setting(key: str, fallback: int) -> int:
    """Read a positive-int ITS_Config setting; fall back on miss/blank/invalid."""
    try:
        raw = smartsheet_client.get_setting(key, workstream="global")
    except smartsheet_client.SmartsheetError:
        return fallback
    if raw is None:
        return fallback
    try:
        value = int(raw)
    except (ValueError, TypeError):
        return fallback
    return value if value > 0 else fallback


def check_create_headroom(workspace_id: int, *, now_count: int | None = None) -> Headroom:
    """Headroom for creating ONE more sheet in `workspace_id`.

    `now_count` injects the count (testability / caller already has it); otherwise
    the live workspace count is read. FAIL-OPEN: a read failure returns
    `Headroom(ok=True, current=-1, …)` so a create is never blocked on a transient
    Smartsheet error.
    """
    ceiling = _read_int_setting(CFG_CEILING, defaults.SHEET_COUNT_CEILING)
    margin = _read_int_setting(CFG_MARGIN, defaults.SHEET_COUNT_MARGIN)

    if now_count is not None:
        current = now_count
    else:
        try:
            current = smartsheet_client.count_workspace_sheets(workspace_id)
        except Exception as exc:  # noqa: BLE001 — fail-open: a count read error must never block a create
            return Headroom(
                ok=True,
                current=-1,
                ceiling=ceiling,
                margin=margin,
                note=f"sheet-count read failed (fail-open, create allowed): {exc!r}",
            )

    ok = (current + 1) <= (ceiling - margin)
    return Headroom(ok=ok, current=current, ceiling=ceiling, margin=margin)


def route_breach_to_review_queue(
    workspace_id: int, headroom: Headroom, *, workstream: str
) -> None:
    """Enqueue a Review-Queue item for a would-breach create — never silent.

    Called by a find-or-create site when `check_create_headroom().ok` is False, so
    the operator sees "approaching the sheet cap — archive/period-split or raise the
    plan tier" instead of a later silent create failure.
    """
    review_queue.add(
        workstream=workstream,
        summary=(
            f"Smartsheet sheet-count near cap in workspace {workspace_id}: "
            f"{headroom.current}/{headroom.ceiling} (margin {headroom.margin}). "
            f"Creates still proceed (advisory tripwire) — archive-on-closure / "
            f"period-split, or raise the plan tier before the real cap bites."
        ),
        payload={
            "workspace_id": workspace_id,
            "current": headroom.current,
            "ceiling": headroom.ceiling,
            "margin": headroom.margin,
        },
        sla_tier=review_queue.SlaTier.SAFETY_INTAKE,
        reason=review_queue.ReviewReason.OTHER,
        severity=Severity.WARN,
    )
