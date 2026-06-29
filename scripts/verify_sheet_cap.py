#!/usr/bin/env python3
"""Read-only Smartsheet sheet-count headroom investigation (Tier-A A1).

The forensic scaling eval (2026-06-28) ranks per-job-per-week Smartsheet sheet
proliferation as the #1 liability + the only hard deployment gate: at 20 active
jobs the per-job-per-week model creates ~1,040 sheets/yr against an UNVERIFIED
per-workspace/plan sheet cap, after which writes silently fail. Sheets stay WEEKLY
(the "monthly" proposal was reverted 2026-06-29); the operator confirmed Evergreen
is on a Business/Enterprise plan (2026-06-29), so capacity is NOT limiting and the
margin-check is a runaway tripwire. The monthly projection below is kept only as a
fallback reference (the config-flip if proliferation ever bites).

This script reports the CURRENT sheet count in the Safety Portal workspace and the
weekly-vs-monthly projection at 20 jobs so the operator can sanity-check headroom.
It is READ-ONLY: no writes, no AI, no send.

HONEST LIMIT: the Smartsheet API does NOT expose the per-plan/per-workspace hard
sheet cap, and so this script does NOT invent one. It supplies the counts +
projection; confirming the real cap + the $600 (Pro) vs $2,400 (Business) tier is
an operator follow-up with Smartsheet plan docs/support. The runtime backstop is
`shared.sheet_capacity.check_create_headroom` (gates find-or-create against the
ITS_Config ceiling/margin, set once the cap is known).

Usage:  .venv/bin/python scripts/verify_sheet_cap.py
"""
from __future__ import annotations

from shared import defaults, sheet_ids, smartsheet_client

JOBS = 20
WEEKLY_PER_JOB_YR = 52
MONTHLY_PER_JOB_YR = 12
# Standing per-job structured sheets in the progress workspace before period-split
# (Materials/Equipment/Hours Status + Material List + Incidents) — order of magnitude.
STANDING_PER_JOB = 5


def main() -> int:
    print("ITS Smartsheet sheet-cap verification (Tier-A A1) — READ-ONLY")
    print("=" * 64)

    ws_id = sheet_ids.WORKSPACE_SAFETY_PORTAL
    current: int | None
    try:
        current = smartsheet_client.count_workspace_sheets(ws_id)
    except Exception as exc:  # noqa: BLE001 — investigation CLI: report + continue, never raise
        print(f"\n[live read skipped] could not read workspace {ws_id}: {exc!r}")
        print("  (Keychain ITS_SMARTSHEET_TOKEN or API access — the projection below is independent.)")
        current = None

    if current is not None:
        print(f"\nCurrent sheets in WORKSPACE_SAFETY_PORTAL ({ws_id}): {current}")

    print(f"\nProjection at {JOBS} active jobs (sheets created per year):")
    weekly = JOBS * WEEKLY_PER_JOB_YR
    monthly = JOBS * MONTHLY_PER_JOB_YR
    print(f"  WEEKLY  (chosen):   {weekly:>5}/yr  ({WEEKLY_PER_JOB_YR}/job)   <- sheet=week, report cadence")
    print(f"  MONTHLY (fallback): {monthly:>5}/yr  ({MONTHLY_PER_JOB_YR}/job)   ~{weekly / monthly:.1f}x fewer (config-flip if the cap ever bites)")
    print(f"  + standing per-job structured sheets: ~{JOBS * STANDING_PER_JOB} "
          f"(period-split + archive-on-closure bounds the live count)")

    print("\nHARD CAP + PLAN TIER  (operator follow-up — NOT exposed by the Smartsheet API):")
    print("  - Confirm the real per-workspace/account sheet cap with Smartsheet plan docs/support.")
    print("  - Operator confirmed Business/Enterprise (2026-06-29) → capacity non-limiting; weekly retained.")
    print("  - Set ITS_Config smartsheet.sheet_count_ceiling / smartsheet.sheet_count_margin once known")
    print(f"    (fallbacks: ceiling={defaults.SHEET_COUNT_CEILING}, "
          f"margin={defaults.SHEET_COUNT_MARGIN}).")
    print("\nRuntime backstop: shared.sheet_capacity.check_create_headroom gates find-or-create")
    print("  (routes a would-breach create to the Review Queue — never a silent cap failure).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
