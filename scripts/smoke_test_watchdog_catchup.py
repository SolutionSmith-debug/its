#!/usr/bin/env python3
"""Smoke test for watchdog Check I — weekly_generate catch-up recovery.

OPERATIONAL — invokes `watchdog._check_weekly_generate_catchup()` directly
against LIVE state: the `~/its/.watchdog/safety_weekly_generate.last_run`
marker (shared with the production weekly_generate daemon) and the LIVE
`WPR_Pending_Review` Smartsheet. Opt-in: the operator authorizes by running
this by hand. Two phases:

  Phase A (default, READ-ONLY, no side effects):
    Show Check I's live decision for the genuine current target week. With
    the real marker/rows in place this normally reports "no catch-up — ran"
    (the happy steady state) — proving detection works against live data.

  Phase B (`--apply`, LIVE FIRE, self-cleaning):
    1. Scan recent past weeks for one with NO WPR_Pending_Review rows (so we
       never touch real reviewer rows). Abort if none found in the scan range.
    2. Pin `_local_now` to the Saturday after that week's Friday (deterministic
       in-window target).
    3. Snapshot + remove the live marker (simulate a missed Friday run).
    4. Run Check I → REAL `weekly_generate._run_pipeline` for the empty week
       (an empty week yields ZERO_DATA placeholder rows — no Anthropic spend).
    5. Verify: catch-up FIRED (generation called once), marker refreshed, WPR
       rows now present for the week.
    6. Run Check I AGAIN → verify NO re-fire (generation NOT called a 2nd time).
    7. Teardown: delete the WPR rows this smoke created, restore the original
       marker snapshot.

Residual side effect of Phase B (documented, low-harm in the sandbox): the
per-project week-folder scaffolding `_run_pipeline` find-or-creates for the
target week is idempotent and left in place (the real Friday run would create
the same). Only the WPR rows + marker — the reviewer-facing/operator-facing
state — are cleaned up.

Requires (Phase B): ITS_ANTHROPIC_KEY (only if a scanned week has data — we
target an empty one to avoid it), Smartsheet token, a configured reviewer
chain for today (else `_run_pipeline` aborts on empty chain — reported, not
a smoke failure).

Run:  python scripts/smoke_test_watchdog_catchup.py            # Phase A only
      python scripts/smoke_test_watchdog_catchup.py --apply    # + Phase B
"""
from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, time, timedelta
from pathlib import Path

# Allow running from repo root without installing the package.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import watchdog  # noqa: E402  (resolves via the scripts/ sys.path insertion)

from safety_reports import weekly_generate  # noqa: E402
from shared import sheet_ids, smartsheet_client  # noqa: E402
from shared.kill_switch import SystemState, check_system_state  # noqa: E402

_MARKER = (
    watchdog.WATCHDOG_MARKER_DIR / f"{watchdog.WEEKLY_GENERATE_JOB_SLUG}.last_run"
)
_SCAN_WEEKS_BACK = 10  # how many past weeks to scan for an empty target


def _wpr_row_ids_for_week(week_monday: date) -> set[int]:
    rows = smartsheet_client.get_rows(
        sheet_ids.SHEET_WPR_PENDING_REVIEW,
        filters={"Week": week_monday.isoformat()},
    )
    return {r["_row_id"] for r in rows}


def _saturday_after(target_monday: date) -> datetime:
    """Saturday 07:00 (local) after target_monday's Friday — in-window."""
    friday = target_monday + timedelta(days=4)
    saturday = friday + timedelta(days=1)
    return datetime.combine(saturday, time(7, 0)).astimezone()


def _phase_a() -> None:
    print("\n--- Phase A: live decision for the current target week (READ-ONLY) ---")
    now = watchdog._local_now()
    last_trigger = watchdog._most_recent_friday_trigger(now)
    target = (last_trigger - timedelta(days=4)).date()
    print(f"  now (local)        = {now.isoformat()}")
    print(f"  most-recent trigger= {last_trigger.isoformat()} (Friday 14:00)")
    print(f"  target week (Mon)  = {target.isoformat()}")
    marker_dt = watchdog._read_marker_datetime(watchdog.WEEKLY_GENERATE_JOB_SLUG)
    print(f"  marker last_run    = {marker_dt.isoformat() if marker_dt else '(none)'}")
    try:
        rows = _wpr_row_ids_for_week(target)
        print(f"  WPR rows for week  = {len(rows)}")
    except Exception as exc:  # noqa: BLE001 — surface to operator
        print(f"  WPR read FAILED    = {exc!r}")

    # Read-only guarantee: stub _run_pipeline with a no-op (records would-fire,
    # writes NO marker, makes NO Smartsheet/Anthropic write) so calling the
    # real check exercises the genuine decision path WITHOUT side effects even
    # if the live state happens to satisfy the fire conditions.
    real_pipeline = weekly_generate._run_pipeline
    fired = {"would": False}

    def _noop_pipeline(**kwargs):  # type: ignore[no-untyped-def]
        fired["would"] = True
        return {
            "drafts_written": 0,
            "drafts_failed": 0,
            "aborted_empty_chain": False,
            "correlation_id": "(phase-a-stub)",
        }

    try:
        weekly_generate._run_pipeline = _noop_pipeline  # type: ignore[assignment]
        result = watchdog._check_weekly_generate_catchup()
    finally:
        weekly_generate._run_pipeline = real_pipeline  # type: ignore[assignment]

    decision = "WOULD FIRE catch-up" if fired["would"] else "no catch-up"
    print(f"  DECISION           = {decision} (generation stubbed — read-only)")
    print(f"  CheckResult        = {result.severity.value}: {result.summary}")
    print("  (steady state is usually 'no catch-up' — marker fresh or rows present.)")


def _find_empty_target_week(today: date) -> date | None:
    """First recent past week (Monday) with zero WPR rows, scanning back."""
    this_monday = today - timedelta(days=today.weekday())
    for back in range(1, _SCAN_WEEKS_BACK + 1):
        candidate = this_monday - timedelta(weeks=back)
        if not _wpr_row_ids_for_week(candidate):
            return candidate
    return None


def _phase_b() -> int:
    print("\n--- Phase B: live catch-up fire (--apply) ---")
    state = check_system_state()
    print(f"  system.state = {state.value}")
    if state == SystemState.PAUSED:
        print("  PAUSED — generation would be skipped at the daemon level; "
              "Phase B not meaningful. Exiting cleanly.")
        return 0

    target = _find_empty_target_week(date.today())
    if target is None:
        print(f"  No empty target week found in the last {_SCAN_WEEKS_BACK} weeks "
              "(all have WPR rows). Skipping Phase B to avoid touching real rows.")
        return 0
    print(f"  empty target week (Mon) = {target.isoformat()}")

    pinned_now = _saturday_after(target)
    print(f"  pinned now              = {pinned_now.isoformat()}")

    # Snapshot the live marker so we can restore the real system's view.
    marker_existed = _MARKER.exists()
    marker_backup = _MARKER.read_text() if marker_existed else None

    # Wrap _run_pipeline with a counter (still calls the real pipeline).
    real_pipeline = weekly_generate._run_pipeline
    original_local_now = watchdog._local_now
    calls = {"n": 0}

    def _counting_pipeline(**kwargs):  # type: ignore[no-untyped-def]
        calls["n"] += 1
        return real_pipeline(**kwargs)

    created_row_ids: set[int] = set()
    exit_code = 0
    try:
        watchdog._local_now = lambda: pinned_now  # type: ignore[assignment]
        weekly_generate._run_pipeline = _counting_pipeline  # type: ignore[assignment]

        # Simulate a missed run: remove the marker.
        if marker_existed:
            _MARKER.unlink()
        before_ids = _wpr_row_ids_for_week(target)
        print(f"  pre-run: marker removed; WPR rows for week = {len(before_ids)}")

        print("  [run 1] invoking Check I (real generation for the empty week)…")
        result1 = watchdog._check_weekly_generate_catchup()
        print(f"    -> {result1.severity.value}: {result1.summary}")
        print(f"    -> _run_pipeline calls so far: {calls['n']}")

        if result1.severity is watchdog.Severity.WARN and "empty reviewer chain" in result1.summary:
            print("    NOTE: generation aborted on empty reviewer chain (a real "
                  "config state today, not a catch-up bug). Marker stays stale by "
                  "design. Restoring and exiting.")
            return 0

        after_ids = _wpr_row_ids_for_week(target)
        created_row_ids = after_ids - before_ids
        marker_after = watchdog._read_marker_datetime(watchdog.WEEKLY_GENERATE_JOB_SLUG)

        ok = True
        if calls["n"] != 1:
            print(f"    FAIL: expected exactly 1 generation call, got {calls['n']}")
            ok = False
        if not created_row_ids:
            print("    FAIL: no WPR rows created by the catch-up")
            ok = False
        else:
            print(f"    OK: {len(created_row_ids)} WPR row(s) created for the week")
        if marker_after is None:
            print("    FAIL: marker not refreshed by generation")
            ok = False
        else:
            print(f"    OK: marker refreshed -> {marker_after.isoformat()}")

        print("  [run 2] invoking Check I again (must NOT re-fire)…")
        result2 = watchdog._check_weekly_generate_catchup()
        print(f"    -> {result2.severity.value}: {result2.summary}")
        if calls["n"] != 1:
            print(f"    FAIL: re-fired — generation called {calls['n']} times total")
            ok = False
        else:
            print("    OK: no re-fire (generation still called exactly once)")

        print(f"\n  Phase B: {'PASS' if ok else 'FAIL'}")
        exit_code = 0 if ok else 1
    finally:
        # Teardown: restore _local_now, _run_pipeline, the marker, delete rows.
        watchdog._local_now = original_local_now  # type: ignore[assignment]
        weekly_generate._run_pipeline = real_pipeline  # type: ignore[assignment]
        if created_row_ids:
            try:
                smartsheet_client.delete_rows(
                    sheet_ids.SHEET_WPR_PENDING_REVIEW, list(created_row_ids)
                )
                print(f"  teardown: deleted {len(created_row_ids)} smoke WPR row(s)")
            except Exception as exc:  # noqa: BLE001
                print(f"  teardown WARNING: failed to delete WPR rows "
                      f"{sorted(created_row_ids)}: {exc!r} — delete them manually")
        if marker_backup is not None:
            _MARKER.parent.mkdir(parents=True, exist_ok=True)
            _MARKER.write_text(marker_backup)
            print("  teardown: restored original marker")
        elif _MARKER.exists():
            _MARKER.unlink()
            print("  teardown: removed marker (was absent before the smoke)")
    return exit_code


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="run Phase B (live catch-up fire against the sandbox; self-cleaning)",
    )
    args = parser.parse_args()

    print("ITS Watchdog Catch-up Smoke (Check I)")
    print("=" * 60)
    print(f"Marker file: {_MARKER}")

    _phase_a()
    if args.apply:
        return _phase_b()
    print("\n(Phase A only. Re-run with --apply for the live catch-up fire.)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
