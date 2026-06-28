"""Smartsheet per-workspace sheet-count headroom guard (A1 / forensic scaling eval B1).

The eval's #1 liability is per-job-per-week Smartsheet sheet proliferation
(~1,040 sheets/yr at 20 jobs) marching toward an unverified per-workspace/plan
sheet cap, after which a create silently fails. We've adopted MONTHLY sheets to
cut proliferation ~4-5x; this module is the runtime backstop so a find-or-create
NEVER silently creates a sheet past the cap — when headroom is thin it routes to
the Review Queue (an operator signal) instead.

Wired into the find-or-create call sites by P1a/P2/P7 (`ensure_week_sheet` etc.);
this module supplies the check + the enqueue helper only.

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
                 should route to the Review Queue (`route_breach_to_review_queue`)
                 instead of silently creating past the cap.

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
            f"Sheet create deferred — archive-on-closure / period-split, or raise the plan tier."
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
