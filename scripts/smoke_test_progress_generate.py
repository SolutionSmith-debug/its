#!/usr/bin/env python3
"""Smoke test for progress_reports/progress_weekly_generate.py environment prereqs (P4).

OPERATIONAL — makes REAL Smartsheet API calls (no writes). The progress weekly compile is
the PROGRESS twin of safety's weekly_generate: it instantiates the shared `generate_core`
with `PROGRESS_GENERATE_CONFIG`, iterates the PROGRESS Active-Jobs sheet, and dual-writes a
Rollup row + a `WPR_human_review` row. This verifies the progress SURFACES are reachable and
the binding routes to them (never safety) — the operator's "can't get mixed up" check.

Re-run after:
  - ITS_SMARTSHEET_TOKEN rotation
  - Any change to progress_weekly_generate.py / generate_core.py module-level setup
  - WPR_human_review or ITS_Active_Jobs_Progress schema changes

Exit code 0 on full green; 1 on any stage failure.
"""
from __future__ import annotations

import sys

from progress_reports import progress_weekly_generate as pwg
from progress_reports import wpr_review
from safety_reports import generate_core, wsr_review
from shared import active_jobs, smartsheet_client
from shared.kill_switch import SystemState, check_system_state


def stage(n: int, label: str) -> None:
    print(f"\n[stage {n}] {label}")


def main() -> int:
    print("progress_reports.progress_weekly_generate smoke test (P4 deterministic compile)")
    print("==============================================================================")
    cfg = pwg.PROGRESS_GENERATE_CONFIG

    # ---- Stage 1: kill switch reads ACTIVE -------------------------------
    stage(1, "kill switch system.state")
    try:
        state = check_system_state()
    except Exception as exc:  # noqa: BLE001 — surface to operator
        print(f"  FAIL — check_system_state raised: {exc!r}")
        return 1
    print(f"  OK — system.state = {state.value!r}")
    if state is not SystemState.ACTIVE:
        print(f"  WARN — system.state is {state.value}; progress_weekly_generate would short-circuit via @require_active.")

    # ---- Stage 2: ITS_Config reachable + Evergreen-contact key read ------
    stage(2, "ITS_Config reachable + evergreen-contact key read")
    contact = generate_core._read_str_setting(
        cfg, cfg.cfg_evergreen_contact, cfg.default_evergreen_contact
    )
    print(f"  OK — evergreen contact = {contact!r} (default {cfg.default_evergreen_contact!r} when row absent)")

    # ---- Stage 3: ITS_Active_Jobs_Progress reachable + Active set --------
    stage(3, "ITS_Active_Jobs_Progress reachable (the PROGRESS sheet, never safety)")
    assert cfg.active_jobs_config is active_jobs.PROGRESS_ACTIVE_JOBS_CONFIG
    try:
        jobs = active_jobs.list_active_jobs(cfg.active_jobs_config)
    except Exception as exc:  # noqa: BLE001
        print(f"  FAIL — list_active_jobs(PROGRESS) raised: {exc!r}")
        return 1
    print(f"  OK — sheet {cfg.active_jobs_config.sheet_id}: {len(jobs)} Active progress job(s):")
    for j in jobs:
        print(f"    - {j.project_name} (job_id={j.job_id}, TO={j.reports_contact_email or '<none>'})")

    # ---- Stage 4: WPR_human_review reachable + expected columns ----------
    stage(4, "WPR_human_review sheet reachable + expected columns present")
    assert cfg.review_sheet_id == wpr_review.SHEET_ID
    try:
        rows = smartsheet_client.get_rows(cfg.review_sheet_id)
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
    marker_dir = cfg.watchdog_marker_dir
    try:
        marker_dir.mkdir(parents=True, exist_ok=True)
        probe = marker_dir / ".smoke_probe_progress"
        probe.write_text("probe")
        probe.unlink()
    except OSError as exc:
        print(f"  FAIL — marker dir not writeable: {exc!r}")
        return 1
    print(f"  OK — {marker_dir} writeable")

    print("\nAll stages green.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
