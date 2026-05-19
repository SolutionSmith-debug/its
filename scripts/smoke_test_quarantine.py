#!/usr/bin/env python3
"""Smoke test for shared/quarantine.log_quarantined_message against the
sandbox ITS_Quarantine sheet.

OPERATIONAL — makes REAL Smartsheet API calls and writes/deletes a row.
Sandbox-only: sheet ID comes from `shared.sheet_ids`.

Re-run after:
  - ITS_SMARTSHEET_TOKEN rotation
  - Any change to shared/quarantine.py or shared/smartsheet_client.py
  - ITS_Quarantine schema changes

Verifies the full chain end-to-end:
  1. Baseline row count
  2. log_quarantined_message() writes a row, returns row ID
  3. Row appears in get_rows output with expected cell values
  4. Synthetic row is deleted (no droppings)
  5. Post-smoke row count matches baseline
"""
from __future__ import annotations

import sys
from datetime import UTC, datetime

from shared import quarantine, sheet_ids, smartsheet_client


def main() -> None:
    print("ITS_Quarantine smoke test")
    print("=" * 60)

    print("\n[1/5] Baseline row count...")
    baseline = smartsheet_client.get_rows(sheet_ids.SHEET_QUARANTINE)
    print(f"      OK: {len(baseline)} existing row(s)")

    print("\n[2/5] log_quarantined_message() — write a synthetic quarantine row...")
    timestamp = datetime.now(UTC).isoformat()
    row_id = quarantine.log_quarantined_message(
        sender="smoke_test@example.invalid",
        subject="smoke_test_quarantine — safe to ignore",
        timestamp=timestamp,
        summary=(
            "Smoke test from scripts/smoke_test_quarantine.py. Safe to ignore. "
            "This row will be deleted by the smoke runner before exit."
        ),
        workstream="other",
    )
    print(f"      OK: row_id={row_id}")

    print("\n[3/5] Verify row appears with expected cell values...")
    rows = smartsheet_client.get_rows(sheet_ids.SHEET_QUARANTINE)
    matching = [r for r in rows if r.get("_row_id") == row_id]
    if not matching:
        print(f"      ERROR: wrote row_id={row_id} but it isn't in get_rows output")
        sys.exit(1)
    row = matching[0]
    expected = {
        "Sender": "smoke_test@example.invalid",
        "Subject": "smoke_test_quarantine — safe to ignore",
        "Workstream": "other",
    }
    for k, v in expected.items():
        if row.get(k) != v:
            print(f"      ERROR: {k} cell mismatch — expected {v!r}, got {row.get(k)!r}")
            sys.exit(1)
    print(f"      OK: row data matches (Quarantined Message={row.get('Quarantined Message')!r})")

    print("\n[4/5] Delete the synthetic row...")
    smartsheet_client.delete_rows(sheet_ids.SHEET_QUARANTINE, [row_id])
    print("      OK")

    print("\n[5/5] Post-smoke row count matches baseline...")
    final = smartsheet_client.get_rows(sheet_ids.SHEET_QUARANTINE)
    if len(final) != len(baseline):
        print(f"      ERROR: baseline={len(baseline)} final={len(final)} (drift!)")
        sys.exit(1)
    print(f"      OK: {len(final)} row(s), no droppings")

    print("\n" + "=" * 60)
    print("All checks passed. quarantine.log_quarantined_message is wired.")


if __name__ == "__main__":
    main()
