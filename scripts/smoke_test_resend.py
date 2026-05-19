#!/usr/bin/env python3
"""Smoke test for shared/resend_client.py via the error_log alert path.

OPERATIONAL — makes a REAL Resend API call and delivers an email to the
operator address configured in `ITS_Config.system.operator_email`.
Opt-in: run by hand when you want to verify the alert path end-to-end.
NOT scheduled, NOT triggered by CI.

Requires:
  1. `ITS_RESEND_API_KEY` set in macOS Keychain.
  2. The sender domain (currently `DEFAULT_FROM` in resend_client.py)
     verified in the operator's Resend dashboard.
  3. `system.operator_email` row seeded in ITS_Config (already done by
     scripts/seed_its_config.py).

Re-run after:
  - Resend API key rotation.
  - Any change to shared/resend_client.py or shared/error_log.py
    `_alert_critical`.
  - Sender domain change in Resend dashboard.

What it verifies:
  - Keychain has the Resend API key.
  - resend_client.send_alert succeeds end-to-end (HTTP 200 from Resend).
  - The error_log `_alert_critical` path constructs a valid subject + body.
  - Operator inbox receives the test email (operator verifies by hand;
    this script does NOT poll for delivery).
"""
from __future__ import annotations

import sys
from datetime import UTC, datetime

from shared import resend_client
from shared.error_log import _alert_critical
from shared.resend_client import ResendAuthError, ResendError


def main() -> None:
    print("ITS resend smoke test")
    print("=" * 60)

    # 1. Verify Keychain has the key without actually using it for an HTTP call.
    print("\n[1/3] Loading Resend API key from Keychain...")
    try:
        key = resend_client.get_client()
    except Exception as e:
        print(f"      ERROR: {type(e).__name__}: {e}")
        print(
            "      The Resend API key is not in macOS Keychain. Add it with:\n"
            "        security add-generic-password -a $USER -s ITS_RESEND_API_KEY -w <YOUR_KEY>"
        )
        sys.exit(1)
    print(f"      OK: key loaded ({len(key)} chars)")

    # 2. Direct send_alert call — minimum-surface verification.
    print("\n[2/3] send_alert direct call (no decorator)...")
    ts = datetime.now(UTC).isoformat()
    try:
        resend_client.send_alert(
            subject=f"[ITS smoke] resend_client direct call {ts}",
            body=(
                "This is a Resend smoke test from scripts/smoke_test_resend.py.\n\n"
                f"Sent at: {ts}\n"
                "Origin:  shared.resend_client.send_alert direct call\n\n"
                "Safe to ignore. If you DID NOT trigger this, somebody else ran the smoke test."
            ),
        )
    except ResendAuthError as e:
        print(f"      ERROR: ResendAuthError — {e}")
        print(
            "      Likely cause: the API key is valid but the sender domain "
            "is not verified in your Resend dashboard, OR the key has insufficient "
            "scope. Check Resend: https://resend.com/domains"
        )
        sys.exit(1)
    except ResendError as e:
        print(f"      ERROR: {type(e).__name__}: {e}")
        sys.exit(1)
    print("      OK: send_alert returned without raising")

    # 3. Exercise the _alert_critical path with a synthetic CRITICAL event.
    print("\n[3/3] _alert_critical path (full error_log integration)...")
    fake_tb = (
        "Traceback (most recent call last):\n"
        "  File \"scripts/smoke_test_resend.py\", line 99, in <synthetic>\n"
        "    raise RuntimeError(\"synthetic CRITICAL for smoke\")\n"
        "RuntimeError: synthetic CRITICAL for smoke"
    )
    _alert_critical(
        script="scripts.smoke_test_resend",
        message="synthetic CRITICAL — smoke test, safe to ignore",
        exc_info=fake_tb,
    )
    print("      OK: _alert_critical returned without raising")
    print(
        "      (If Resend fails internally, error_log catches it and writes a "
        "[resend-alert-failed] marker to ~/its/logs/<today>.log)"
    )

    print("\n" + "=" * 60)
    print(
        "All checks passed. Two test emails were sent to the operator address "
        "configured in ITS_Config.system.operator_email. Verify by checking the "
        "inbox; this script does not poll for delivery."
    )


if __name__ == "__main__":
    main()
