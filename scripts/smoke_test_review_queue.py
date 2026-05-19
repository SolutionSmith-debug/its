#!/usr/bin/env python3
"""Smoke test for shared/review_queue.py against the sandbox ITS_Review_Queue.

OPERATIONAL — makes REAL Smartsheet API calls and writes/deletes a row.
Sandbox-only: sheet ID comes from `shared.sheet_ids`.

Re-run after:
  - ITS_SMARTSHEET_TOKEN rotation
  - Any change to shared/review_queue.py or shared/smartsheet_client.py
  - ITS_Review_Queue schema changes

Verifies the full chain end-to-end:
  1. Baseline row count
  2. add() writes a row, returns row ID
  3. get_status() reads the row back and parses Status correctly
  4. Synthetic row is deleted (no droppings)
  5. Post-smoke row count matches baseline
"""
from __future__ import annotations

import sys

from shared import review_queue, sheet_ids, smartsheet_client
from shared.error_log import Severity
from shared.review_queue import ReviewReason, ReviewStatus, SlaTier


def main() -> None:
    print("ITS_Review_Queue smoke test")
    print("=" * 60)

    print("\n[1/5] Baseline row count...")
    baseline = smartsheet_client.get_rows(sheet_ids.SHEET_REVIEW_QUEUE)
    print(f"      OK: {len(baseline)} existing row(s)")

    print("\n[2/5] add() — write a synthetic review-queue item...")
    row_id = review_queue.add(
        workstream="global",
        summary="smoke_test_review_queue — safe to ignore",
        payload={"smoke": True, "ts": "2026-05-19"},
        sla_tier=SlaTier.SAFETY_INTAKE,
        reason=ReviewReason.MANUAL,
        severity=Severity.INFO,
        source_file="scripts/smoke_test_review_queue.py",
        security_flag=False,
    )
    print(f"      OK: row_id={row_id}")

    # Locate the row we just wrote so we can grab its Item ID for get_status.
    print("\n[3/5] get_status() — read the row back...")
    rows = smartsheet_client.get_rows(sheet_ids.SHEET_REVIEW_QUEUE)
    matching = [r for r in rows if r.get("_row_id") == row_id]
    if not matching:
        print(f"      ERROR: wrote row_id={row_id} but it isn't in get_rows output")
        sys.exit(1)
    item_id = matching[0].get("Item ID")
    if not item_id:
        print("      ERROR: row has no Item ID cell")
        sys.exit(1)

    status = review_queue.get_status(item_id)
    if status is not ReviewStatus.PENDING:
        print(f"      ERROR: expected PENDING, got {status}")
        sys.exit(1)
    print(f"      OK: Item ID={item_id} Status=PENDING")

    print("\n[4/5] Delete the synthetic row...")
    smartsheet_client.delete_rows(sheet_ids.SHEET_REVIEW_QUEUE, [row_id])
    print("      OK")

    print("\n[5/5] Post-smoke row count matches baseline...")
    final = smartsheet_client.get_rows(sheet_ids.SHEET_REVIEW_QUEUE)
    if len(final) != len(baseline):
        print(f"      ERROR: baseline={len(baseline)} final={len(final)} (drift!)")
        sys.exit(1)
    print(f"      OK: {len(final)} row(s), no droppings")

    print("\n" + "=" * 60)
    print("All checks passed. review_queue.py is wired.")


if __name__ == "__main__":
    main()
