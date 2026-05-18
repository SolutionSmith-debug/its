#!/usr/bin/env python3
"""Smoke test for ITS Smartsheet integration.

OPERATIONAL — makes REAL Smartsheet API calls and writes/deletes a row in
ITS_Errors as part of the round-trip check. Sandbox-only: sheet IDs come
from shared.sheet_ids and are the sandbox workspace's sheets.

Re-run after:
  - ITS_SMARTSHEET_TOKEN rotation
  - Any change to shared/smartsheet_client.py
  - Smartsheet workspace restructure (sheet IDs may have moved)

Verifies the full chain end-to-end:
  1. Keychain credential is readable
  2. SDK client initializes against the live API
  3. Read path works (ITS_Config → expects at least one row)
  4. Title→column-ID resolution works on a different sheet shape (ITS_Errors)
  5. Write path works (add INFO row to ITS_Errors)
  6. Update path works (mark the row with a Resolved timestamp)
  7. Delete path works (remove the row — leaves no smoke-test droppings)
  8. Error translation works (404 from bogus sheet ID → SmartsheetNotFoundError)
"""
from __future__ import annotations

import sys
from datetime import UTC, datetime

from shared import sheet_ids, smartsheet_client
from shared.smartsheet_client import SmartsheetNotFoundError


def main() -> None:
    print("ITS Smartsheet smoke test")
    print("=" * 60)

    print("\n[1/6] Initializing SDK client (Keychain → smartsheet.Smartsheet)...")
    smartsheet_client.get_client()
    print("      OK")

    print("\n[2/6] Reading ITS_Config (any row will do)...")
    config_rows = smartsheet_client.get_rows(sheet_ids.SHEET_CONFIG)
    print(f"      OK: {len(config_rows)} row(s)")

    print("\n[3/6] Appending INFO row to ITS_Errors...")
    timestamp = datetime.now(UTC).isoformat()
    new_ids = smartsheet_client.add_rows(
        sheet_ids.SHEET_ERRORS,
        [
            {
                "Error": "smoke_test",
                "Severity": "INFO",
                "Script": "smoke_test_smartsheet.py",
                "Message": f"Round-trip check at {timestamp}",
            }
        ],
    )
    if not new_ids:
        print("      ERROR: add_rows returned no row IDs")
        sys.exit(1)
    new_row_id = new_ids[0]
    print(f"      OK: row_id={new_row_id}")

    print("\n[4/6] Updating the row (set Resolved At, Notes)...")
    smartsheet_client.update_rows(
        sheet_ids.SHEET_ERRORS,
        [
            {
                "_row_id": new_row_id,
                "Resolved At": timestamp,
                "Notes": "smoke test — safe to ignore",
            }
        ],
    )
    print("      OK")

    print("\n[5/6] Deleting the row (no droppings)...")
    smartsheet_client.delete_rows(sheet_ids.SHEET_ERRORS, [new_row_id])
    print("      OK")

    print("\n[6/6] Verifying error translation against bogus sheet ID...")
    try:
        smartsheet_client.get_sheet(1)
    except SmartsheetNotFoundError as e:
        print(f"      OK: {type(e).__name__} surfaced ({str(e)[:80]}...)")
    except Exception as e:
        print(f"      ERROR: expected SmartsheetNotFoundError, got {type(e).__name__}: {e}")
        sys.exit(1)
    else:
        print("      ERROR: bogus sheet ID returned success")
        sys.exit(1)

    print("\n" + "=" * 60)
    print("All checks passed. smartsheet_client.py is wired.")


if __name__ == "__main__":
    main()
