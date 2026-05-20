#!/usr/bin/env python3
"""Smoke test for shared/scheduling._live_fetcher against ITS_Time_Off.

OPERATIONAL — makes REAL Smartsheet API calls; creates and deletes 3
deliberately-tagged rows in ITS_Time_Off (sheet ID from
`shared.sheet_ids.SHEET_TIME_OFF`). Sandbox-only.

Re-run after:
  - ITS_SMARTSHEET_TOKEN rotation
  - Any change to shared/scheduling.py:_live_fetcher
  - ITS_Time_Off schema changes (column rename, picklist drift)

Verifies the full chain end-to-end:
  1. Pre-smoke cleanup — delete any leftover `ITS-SMOKE-*` rows from
     prior runs so each smoke starts clean.
  2. Baseline row count.
  3. Create 3 tagged rows: one past PTO, one current-day PTO, one
     near-future PTO range.
  4. Fresh `TimeOffClient()` instance fetches via `_live_fetcher` and
     parses the rows.
  5. Assertions:
       a. ≥3 entries for the smoke email
       b. is_out(today) is True for the current-day row
       c. is_out(far-past) is False — past row doesn't leak into nearby
          dates
  6. Cleanup runs in `finally` so a failed assertion still removes the
     smoke rows. Pattern matches scripts/smoke_test_review_queue.py
     "leave no droppings" discipline.
"""
from __future__ import annotations

import sys
from datetime import date, timedelta

from shared import sheet_ids, smartsheet_client
from shared.scheduling import TimeOffClient

SMOKE_EMAIL = "seths@evergreenmirror.com"
SMOKE_PREFIX = f"ITS-SMOKE-{date.today().isoformat()}"


def _delete_existing_smoke_rows() -> int:
    """Find and delete any leftover ITS-SMOKE-* rows. Returns delete count."""
    rows = smartsheet_client.get_rows(sheet_ids.SHEET_TIME_OFF)
    leftover_ids = [
        r["_row_id"]
        for r in rows
        if isinstance(r.get("Entry"), str) and r["Entry"].startswith("ITS-SMOKE-")
    ]
    if leftover_ids:
        smartsheet_client.delete_rows(sheet_ids.SHEET_TIME_OFF, leftover_ids)
    return len(leftover_ids)


def main() -> None:  # noqa: C901 — single linear smoke script; complexity is fine
    print("ITS_Time_Off / scheduling._live_fetcher smoke test")
    print("=" * 60)

    print("\n[1/6] Pre-smoke cleanup of any leftover ITS-SMOKE-* rows...")
    deleted = _delete_existing_smoke_rows()
    print(f"      OK: deleted {deleted} leftover row(s)")

    print("\n[2/6] Baseline row count...")
    baseline = len(smartsheet_client.get_rows(sheet_ids.SHEET_TIME_OFF))
    print(f"      OK: {baseline} existing row(s)")

    today = date.today()
    past_start = today - timedelta(days=30)
    past_end = today - timedelta(days=28)
    future_start = today + timedelta(days=7)
    future_end = today + timedelta(days=9)

    rows_to_create = [
        {
            "Entry": f"{SMOKE_PREFIX}-past",
            "Person": SMOKE_EMAIL,
            "Start Date": past_start.isoformat(),
            "End Date": past_end.isoformat(),
            "Reason": "PTO",
            "Notes": "smoke-test: safe to delete",
        },
        {
            "Entry": f"{SMOKE_PREFIX}-current",
            "Person": SMOKE_EMAIL,
            "Start Date": today.isoformat(),
            "End Date": today.isoformat(),
            "Reason": "PTO",
            "Notes": "smoke-test: safe to delete",
        },
        {
            "Entry": f"{SMOKE_PREFIX}-future",
            "Person": SMOKE_EMAIL,
            "Start Date": future_start.isoformat(),
            "End Date": future_end.isoformat(),
            "Reason": "PTO",
            "Notes": "smoke-test: safe to delete",
        },
    ]

    print("\n[3/6] Creating 3 deliberately-tagged smoke rows...")
    created_row_ids: list[int] = []
    exit_code = 0
    try:
        created_row_ids = smartsheet_client.add_rows(
            sheet_ids.SHEET_TIME_OFF, rows_to_create
        )
        if len(created_row_ids) != 3:
            print(f"      ERROR: expected 3 row IDs, got {len(created_row_ids)}")
            sys.exit(1)
        print(f"      OK: created row IDs {created_row_ids}")

        print("\n[4/6] Live fetcher reads via fresh TimeOffClient()...")
        client = TimeOffClient()
        smoke_entries = [
            e for e in client._entries() if e.person_email == SMOKE_EMAIL  # noqa: SLF001
        ]
        if len(smoke_entries) < 3:
            print(
                f"      ERROR: expected ≥3 smoke entries for {SMOKE_EMAIL}, "
                f"got {len(smoke_entries)}"
            )
            for e in smoke_entries:
                print(f"             {e}")
            sys.exit(1)
        print(f"      OK: {len(smoke_entries)} entry(ies) for {SMOKE_EMAIL}")

        print("\n[5/6] is_out(today) True; is_out(far-future) False (past doesn't leak)...")
        far_future = today + timedelta(days=180)
        if not client.is_out(SMOKE_EMAIL, today):
            print(f"      ERROR: is_out({SMOKE_EMAIL}, today={today}) should be True")
            sys.exit(1)
        if client.is_out(SMOKE_EMAIL, far_future):
            print(
                f"      ERROR: is_out({SMOKE_EMAIL}, {far_future}) should be False "
                "(past PTO leaking into far-future)"
            )
            sys.exit(1)
        print(f"      OK: today={today} → out; far_future={far_future} → not out")

        print("\nPTO fetcher smoke: PASS")
    except SystemExit:
        # Re-raise to preserve exit code after finally cleanup runs.
        exit_code = 1
        raise
    except Exception as e:
        print(f"\n      ERROR: unexpected exception {type(e).__name__}: {e}")
        exit_code = 1
        raise
    finally:
        print("\n[6/6] Cleanup (always runs)...")
        if created_row_ids:
            smartsheet_client.delete_rows(sheet_ids.SHEET_TIME_OFF, created_row_ids)
            print(f"      OK: deleted {len(created_row_ids)} smoke row(s)")
        else:
            print("      OK: nothing to clean up")

        post = len(smartsheet_client.get_rows(sheet_ids.SHEET_TIME_OFF))
        if post != baseline:
            print(
                f"      WARN: baseline={baseline} post={post} (drift!); "
                "inspect ITS_Time_Off manually"
            )
            if exit_code == 0:
                sys.exit(1)
        else:
            print(f"      OK: {post} row(s), no droppings")


if __name__ == "__main__":
    main()
