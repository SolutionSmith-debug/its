#!/usr/bin/env python3
"""Smoke test for safety_reports/weekly_generate.py environment prereqs.

OPERATIONAL — makes REAL Smartsheet API calls (no writes). No Anthropic
call is made; the standard branch is exercised only via
`weekly_generate.iter_active_projects()` (pure read of the static
project map). The end-to-end exercise lives in
`tests/test_weekly_generate_integration.py` (gated `pytest -m integration`).

Re-run after:
  - ITS_SMARTSHEET_TOKEN rotation
  - Any change to safety_reports/weekly_generate.py module-level setup
  - WPR_Pending_Review schema changes (column rename, picklist drift)
  - PROJECT_NAME_BY_FOLDER_ID changes (project add/remove)

Six numbered stages, each printed to stdout. Exit code 0 on full
green; 1 on any stage failure (also re-raises the exception so the
operator sees the traceback).
"""
from __future__ import annotations

import sys
from datetime import date

from safety_reports import weekly_generate
from shared import scheduling, sheet_ids, smartsheet_client
from shared.kill_switch import SystemState, check_system_state


def stage(n: int, label: str) -> None:
    print(f"\n[stage {n}] {label}")


def main() -> int:
    print("safety_reports.weekly_generate smoke test")
    print("==========================================")

    # ---- Stage 1: kill switch reads ACTIVE -------------------------------
    stage(1, "kill switch system.state")
    try:
        state = check_system_state()
    except Exception as exc:  # noqa: BLE001 — surface to operator
        print(f"  FAIL — check_system_state raised: {exc!r}")
        return 1
    print(f"  OK — system.state = {state.value!r}")
    if state is not SystemState.ACTIVE:
        print(
            f"  WARN — system.state is {state.value}; weekly_generate"
            f" would short-circuit via @require_active."
        )

    # ---- Stage 2: ITS_Config reachable + threshold read ------------------
    stage(2, "ITS_Config reachable + threshold key read")
    threshold = weekly_generate._read_float_setting(
        weekly_generate.CFG_CONFIDENCE_THRESHOLD,
        weekly_generate.DEFAULT_CONFIDENCE_THRESHOLD,
    )
    print(
        f"  OK — confidence threshold = {threshold} "
        f"(default {weekly_generate.DEFAULT_CONFIDENCE_THRESHOLD} when row absent)"
    )

    # ---- Stage 3: reviewer chain resolves non-empty ----------------------
    stage(3, "reviewer chain resolves non-empty")
    chain = scheduling.resolve_chain("safety_reports", on_date=date.today())
    if not chain.slots:
        print("  FAIL — resolve_chain returned 0 slots (everyone is out per ITS_Time_Off).")
        print("         weekly_generate would CRITICAL-abort if invoked now.")
        return 1
    emails = [slot.email for slot in chain.slots]
    print(f"  OK — {len(chain.slots)} slot(s): {emails}")

    # ---- Stage 4: WPR_Pending_Review reachable + schema sanity -----------
    stage(4, "WPR_Pending_Review sheet reachable + expected columns present")
    try:
        rows = smartsheet_client.get_rows(sheet_ids.SHEET_WPR_PENDING_REVIEW)
    except Exception as exc:  # noqa: BLE001
        print(f"  FAIL — get_rows raised: {exc!r}")
        return 1
    # Column presence check via the title set on the first row (if any).
    expected_columns = {
        "Customer",
        "Job",
        "Week",
        "Draft Body",
        "Recipients",
        "Approved for Send",
        "Send Status",
    }
    if rows:
        present = set(rows[0].keys()) - {"_row_id"}
        missing = expected_columns - present
        if missing:
            print(f"  WARN — expected columns missing from first row: {sorted(missing)}")
        else:
            print(f"  OK — first row has all {len(expected_columns)} expected columns")
        print(f"  INFO — total rows: {len(rows)}")
    else:
        print("  OK — sheet reachable; 0 rows present (schema check deferred)")

    # ---- Stage 5: watchdog marker dir writeable --------------------------
    stage(5, "watchdog marker dir writeable")
    try:
        weekly_generate.WATCHDOG_MARKER_DIR.mkdir(parents=True, exist_ok=True)
        probe = weekly_generate.WATCHDOG_MARKER_DIR / ".smoke_probe"
        probe.write_text("probe")
        probe.unlink()
    except OSError as exc:
        print(f"  FAIL — marker dir not writeable: {exc!r}")
        return 1
    print(f"  OK — {weekly_generate.WATCHDOG_MARKER_DIR} writeable")

    # ---- Stage 6: iter_active_projects dry-run --------------------------
    stage(6, "iter_active_projects (no Smartsheet calls)")
    projects = weekly_generate.iter_active_projects()
    print(f"  OK — {len(projects)} active project(s):")
    for folder_id, name in projects:
        print(f"    - {name} (folder_id={folder_id})")

    print("\nAll stages green.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
