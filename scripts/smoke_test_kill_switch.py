#!/usr/bin/env python3
"""Smoke test for shared/kill_switch.py against the sandbox ITS_Config.

OPERATIONAL — makes REAL Smartsheet API calls. Read-only.

Run twice: once before seeding ITS_Config (exercises the row-missing
fail-open branch) and once after (exercises the happy path).

Re-run after:
  - Any change to shared/kill_switch.py or shared/smartsheet_client.py
  - ITS_Config schema changes
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

from shared import kill_switch, smartsheet_client
from shared.error_log import LOG_DIR
from shared.smartsheet_client import SmartsheetNotFoundError


def _tail_today_log(lines: int = 10) -> None:
    log_path = LOG_DIR / f"{datetime.now():%Y-%m-%d}.log"
    print(f"\nLog file: {log_path}")
    if not log_path.exists():
        print("  (no log file written yet today)")
        return
    text = Path(log_path).read_text().splitlines()
    print(f"Last {min(lines, len(text))} line(s):")
    for line in text[-lines:]:
        print(f"  {line}")


def main() -> None:
    print("ITS kill_switch smoke test")
    print("=" * 60)

    print("\n[1/2] Calling kill_switch.check_system_state()...")
    state = kill_switch.check_system_state()
    print(f"      Result: {state.value}")

    print("\n[2/2] Verifying SmartsheetNotFoundError on bogus Setting key...")
    try:
        smartsheet_client.get_setting("smoke_test.does_not_exist", workstream="global")
    except SmartsheetNotFoundError as e:
        print(f"      OK: {type(e).__name__} surfaced ({str(e)[:80]}...)")
    except Exception as e:
        print(f"      ERROR: expected SmartsheetNotFoundError, got {type(e).__name__}: {e}")
        sys.exit(1)
    else:
        print("      ERROR: bogus key returned success")
        sys.exit(1)

    _tail_today_log()

    print("\n" + "=" * 60)
    print("Done. If state=ACTIVE and the log tail shows no WARN, ITS_Config is seeded.")
    print("If state=ACTIVE and the log tail shows a WARN with 'row missing' or 'read failed',")
    print("the fail-open path tripped — that's expected before seeding.")


if __name__ == "__main__":
    main()
