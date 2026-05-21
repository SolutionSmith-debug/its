#!/usr/bin/env python3
"""Smoke test for shared/alert_dedupe.py + triple-fire dedupe gating.

OPERATIONAL — fires 5 REAL CRITICAL events through the full triple-fire
path (Smartsheet ITS_Errors + Resend operator email + Sentry). With
dedupe operational, the operator's inbox should receive exactly ONE
email; ITS_Errors should grow by 5 rows; Sentry should record 5 events.

Opt-in: run by hand after PR α lands. NOT scheduled, NOT triggered by
CI. Re-run after:
  - Any change to shared/alert_dedupe.py.
  - Any change to shared/error_log.py `_fire_resend_leg` dedupe gate.

Requires:
  1. ITS_SMARTSHEET_TOKEN in Keychain.
  2. ITS_RESEND_API_KEY in Keychain.
  3. ITS_SENTRY_DSN in Keychain.
  4. Correlation_ID column present in ITS_Errors (PR α migration).
  5. alerting.dedupe_window_minutes row present in ITS_Config (PR α migration).
  6. system.operator_email row present in ITS_Config (already seeded).

What it verifies:
  - 5 CRITICALs with same (script, error_code) trigger exactly 1 Resend
    send (subsequent 4 suppressed by the dedupe gate).
  - All 5 share the same script + error_code dedupe key but each carries
    a DISTINCT correlation_id (one UUID per CRITICAL event).
  - Smartsheet ITS_Errors records 5 rows. Sentry records 5 events.

What it does NOT verify automatically:
  - Resend delivery to the operator inbox (operator confirms by counting
    emails received).
  - Smartsheet row count (operator confirms via sheet inspection).
  - Sentry event arrival (operator confirms via Sentry dashboard).

Note on state-file hygiene: the dedupe state file at
`~/its/state/alert_dedupe.json` is cleared at script start so each run
starts from a clean window. If you want to test that the window persists
across runs, comment out the clear step.
"""
from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path

# Allow running from repo root without installing the package.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shared import alert_dedupe  # noqa: E402
from shared.error_log import _alert_critical  # noqa: E402

SCRIPT_NAME = "scripts.smoke_test_alert_dedupe"
ERROR_CODE = "smoke_dedupe"  # explicit non-uncaught_exception key
FIRE_COUNT = 5


def _clear_state() -> None:
    """Reset dedupe state so this run starts fresh."""
    if alert_dedupe.STATE_FILE.exists():
        alert_dedupe.STATE_FILE.unlink()
        print(f"[setup] Cleared {alert_dedupe.STATE_FILE}")
    else:
        print(f"[setup] No prior state file at {alert_dedupe.STATE_FILE}")


def main() -> None:
    print("ITS alert-dedupe smoke test")
    print("=" * 60)
    _clear_state()
    print()

    ts = datetime.now(UTC).isoformat()
    correlation_ids: list[str] = []

    for i in range(1, FIRE_COUNT + 1):
        message = f"synthetic CRITICAL #{i} at {ts} — safe to ignore"
        fake_tb = (
            "Traceback (most recent call last):\n"
            "  File \"scripts/smoke_test_alert_dedupe.py\", line 99, in <synthetic>\n"
            f"    raise RuntimeError(\"synthetic #{i}\")\n"
            f"RuntimeError: synthetic #{i}"
        )
        # _alert_critical generates a correlation_id internally when not
        # passed; for the smoke test we want to surface the ID, so we
        # pre-generate here and pass through. Mirrors the decorator
        # pattern for visibility.
        import uuid
        correlation_id = str(uuid.uuid4())
        correlation_ids.append(correlation_id)
        print(f"[fire {i}/{FIRE_COUNT}] correlation_id={correlation_id}")
        _alert_critical(
            script=SCRIPT_NAME,
            message=message,
            exc_info=fake_tb,
            correlation_id=correlation_id,
            error_code=ERROR_CODE,
        )

    print()
    print("=" * 60)
    print("All 5 CRITICALs fired. Expected results:")
    print(f"  Resend inbox:        1 email  (one '[corr: {correlation_ids[0][:8]}]')")
    print(f"  ITS_Errors:          {FIRE_COUNT} rows (one per correlation_id below)")
    print(f"  Sentry events:       {FIRE_COUNT} events tagged with the IDs below")
    print()
    print("Correlation IDs (full UUIDs, in fire order):")
    for i, cid in enumerate(correlation_ids, start=1):
        print(f"  {i}. {cid}")
    print()
    print("Operator verification steps:")
    print(
        f"  1. Check operator inbox — exactly 1 email with subject suffix\n"
        f"     [corr: {correlation_ids[0][:8]}].\n"
        f"  2. Open ITS_Errors in Smartsheet — 5 new rows, Severity=CRITICAL,\n"
        f"     Script={SCRIPT_NAME!r}, Error={ERROR_CODE!r},\n"
        f"     each with a distinct Correlation_ID value.\n"
        f"  3. Sentry dashboard — 5 new events tagged correlation_id=<above>."
    )
    print()
    print("State file after run:")
    if alert_dedupe.STATE_FILE.exists():
        print(f"  {alert_dedupe.STATE_FILE} (will contain one window entry)")
    else:
        print("  (no state file written — investigate)")


if __name__ == "__main__":
    main()
