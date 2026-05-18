#!/usr/bin/env python3
"""Smoke test for shared/error_log.py against the sandbox ITS_Errors sheet.

OPERATIONAL — makes REAL Smartsheet API calls and writes/deletes rows in
ITS_Errors as part of the round-trip check. Sandbox-only: sheet IDs come
from shared.sheet_ids.

Run with:
    ITS_ERROR_LOG_INFO=1 python scripts/smoke_test_error_log.py

The env var forces step 1's INFO write to land in Smartsheet (default is
local-only, so the round-trip check wouldn't be exercisable).

Re-run after:
  - ITS_SMARTSHEET_TOKEN rotation
  - Any change to shared/error_log.py or shared/smartsheet_client.py
  - ITS_Errors schema changes
"""
from __future__ import annotations

import io
import sys

from shared import sheet_ids, smartsheet_client
from shared.error_log import Severity, log


def _find_marker_row(marker_value: str) -> int | None:
    """Return the _row_id of the most recent ITS_Errors row whose Message
    contains the marker, else None.
    """
    rows = smartsheet_client.get_rows(sheet_ids.SHEET_ERRORS)
    candidates = [r for r in rows if marker_value in (r.get("Message") or "")]
    if not candidates:
        return None
    # If multiple matches exist (rare), pick the highest row_id — most recent.
    return max(int(r["_row_id"]) for r in candidates)


def _check_round_trip(severity: Severity, marker: str, error_code: str) -> None:
    print(f"\n[{severity.value}] Writing row with marker={marker!r}...")
    log(severity, "smoke_test_error_log", marker, error_code=error_code)

    row_id = _find_marker_row(marker)
    if row_id is None:
        print(f"      ERROR: no row found matching marker {marker!r}")
        sys.exit(1)
    print(f"      OK: row_id={row_id}")

    print(f"      Cleaning up row {row_id}...")
    smartsheet_client.delete_rows(sheet_ids.SHEET_ERRORS, [row_id])
    print("      OK: row deleted")


def _check_404_filter() -> None:
    print("\n[404 filter] Triggering a bogus-sheet 404 and capturing stdout/stderr...")
    stdout_cap, stderr_cap = io.StringIO(), io.StringIO()
    real_stdout, real_stderr = sys.stdout, sys.stderr
    sys.stdout, sys.stderr = stdout_cap, stderr_cap
    try:
        try:
            smartsheet_client.get_sheet(1)
        except smartsheet_client.SmartsheetNotFoundError:
            pass
    finally:
        sys.stdout, sys.stderr = real_stdout, real_stderr

    stdout_text = stdout_cap.getvalue()
    stderr_text = stderr_cap.getvalue()

    # The raw 404 JSON body is the noise we filter; the typed exception's
    # text is fine and may surface elsewhere (we don't print it here).
    if '"statusCode": 404' in stdout_text or '"statusCode": 404' in stderr_text:
        print("      ERROR: raw 404 JSON body still leaking")
        print(f"      stdout: {stdout_text[:200]!r}")
        print(f"      stderr: {stderr_text[:200]!r}")
        sys.exit(1)
    print("      OK: stdout/stderr clean of raw 404 JSON")
    if stderr_text:
        print(f"      (stderr was non-empty but did not contain 404 body: {stderr_text[:200]!r})")


def main() -> None:
    print("ITS error_log smoke test")
    print("=" * 60)

    marker_info = "smoke_test_INFO_round_trip"
    marker_warn = "smoke_test_WARN_round_trip"

    _check_round_trip(Severity.INFO, marker_info, error_code="smoke_info")
    _check_round_trip(Severity.WARN, marker_warn, error_code="smoke_warn")
    _check_404_filter()

    print("\n" + "=" * 60)
    print("All checks passed. error_log.py is wired and 404 filter is live.")


if __name__ == "__main__":
    main()
