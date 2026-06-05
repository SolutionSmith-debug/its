#!/usr/bin/env python3
"""Smoke test for safety_reports/weekly_generate.py environment prereqs (Phase 5).

OPERATIONAL — makes REAL Smartsheet API calls (no writes). weekly_generate is now
the DETERMINISTIC compile (no Anthropic, no reviewer-chain abort): it iterates Active
jobs, merges per-submission PDFs, and dual-writes a Rollup row + a WSR_human_review
row. The end-to-end exercise lives in tests/test_weekly_generate_integration.py
(gated `pytest -m integration`).

Re-run after:
  - ITS_SMARTSHEET_TOKEN rotation
  - Any change to weekly_generate.py module-level setup
  - WSR_human_review schema changes (column rename, picklist drift)
  - ITS_Active_Jobs changes (job add/remove/activate)

Exit code 0 on full green; 1 on any stage failure.
"""
from __future__ import annotations

import sys

from safety_reports import weekly_generate, wsr_review
from shared import active_jobs, smartsheet_client
from shared.kill_switch import SystemState, check_system_state


def stage(n: int, label: str) -> None:
    print(f"\n[stage {n}] {label}")


def main() -> int:
    print("safety_reports.weekly_generate smoke test (Phase 5 deterministic compile)")
    print("=========================================================================")

    # ---- Stage 1: kill switch reads ACTIVE -------------------------------
    stage(1, "kill switch system.state")
    try:
        state = check_system_state()
    except Exception as exc:  # noqa: BLE001 — surface to operator
        print(f"  FAIL — check_system_state raised: {exc!r}")
        return 1
    print(f"  OK — system.state = {state.value!r}")
    if state is not SystemState.ACTIVE:
        print(f"  WARN — system.state is {state.value}; weekly_generate would short-circuit via @require_active.")

    # ---- Stage 2: ITS_Config reachable + Evergreen-contact key read ------
    stage(2, "ITS_Config reachable + evergreen-contact key read")
    contact = weekly_generate._read_str_setting(
        weekly_generate.CFG_EVERGREEN_CONTACT, weekly_generate.DEFAULT_EVERGREEN_CONTACT
    )
    print(f"  OK — evergreen contact = {contact!r} (default {weekly_generate.DEFAULT_EVERGREEN_CONTACT!r} when row absent)")

    # ---- Stage 3: ITS_Active_Jobs reachable + Active set -----------------
    stage(3, "ITS_Active_Jobs reachable")
    try:
        jobs = active_jobs.list_active_jobs()
    except Exception as exc:  # noqa: BLE001
        print(f"  FAIL — list_active_jobs raised: {exc!r}")
        return 1
    print(f"  OK — {len(jobs)} Active job(s):")
    for j in jobs:
        print(f"    - {j.project_name} (job_id={j.job_id}, TO={j.safety_reports_contact_email or '<none>'})")

    # ---- Stage 4: WSR_human_review reachable + expected columns ----------
    stage(4, "WSR_human_review sheet reachable + expected columns present")
    try:
        rows = smartsheet_client.get_rows(wsr_review.SHEET_ID)
    except Exception as exc:  # noqa: BLE001
        print(f"  FAIL — get_rows raised: {exc!r}")
        return 1
    expected = {
        wsr_review.COL_JOB_PROJECT, wsr_review.COL_JOB_ID, wsr_review.COL_WEEK_OF,
        wsr_review.COL_COMPILED_PDF, wsr_review.COL_EMAIL_BODY, wsr_review.COL_SEND_STATUS,
    }
    if rows:
        present = set(rows[0].keys()) - {"_row_id"}
        missing = expected - present
        if missing:
            print(f"  WARN — expected columns missing from first row: {sorted(missing)}")
        else:
            print(f"  OK — first row has all {len(expected)} expected columns")
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

    print("\nAll stages green.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
