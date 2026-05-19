#!/usr/bin/env python3
"""Smoke test for shared/sentry_client.py via the error_log alert path.

OPERATIONAL — sends REAL events to the Sentry project whose DSN is in
Keychain under `ITS_SENTRY_DSN`. Opt-in: run by hand when you want to
verify the Sentry leg end-to-end. NOT scheduled, NOT triggered by CI.

Requires:
  1. `ITS_SENTRY_DSN` set in macOS Keychain (the full DSN URL from
     your Sentry project's "Client Keys (DSN)" settings page).
  2. The Sentry project must exist and accept events.

Re-run after:
  - DSN rotation in the Sentry dashboard.
  - Any change to shared/sentry_client.py or shared/error_log.py
    `_alert_critical`.

What it verifies:
  - Keychain has the Sentry DSN.
  - sentry_client.capture_exception succeeds end-to-end (no exception
    from the SDK).
  - The error_log `_alert_critical` path fires BOTH Resend and Sentry
    (this script doesn't reach into Sentry's UI to verify event
    arrival — operator checks the dashboard).

What it does NOT verify:
  - Event arrival in the Sentry dashboard. Operator must check
    sentry.io → Issues → most-recent event.
  - Resend delivery (covered by scripts/smoke_test_resend.py).
"""
from __future__ import annotations

import sys
from datetime import UTC, datetime

from shared import sentry_client
from shared.error_log import _alert_critical
from shared.sentry_client import SentryCaptureError, SentryError, SentryInitError


def main() -> None:
    print("ITS sentry smoke test")
    print("=" * 60)

    # 1. Verify Keychain has the DSN without fully initializing the SDK.
    #    The init happens at get_client() call below.
    print("\n[1/3] Initializing Sentry SDK (loads DSN from Keychain)...")
    try:
        sentry_client.get_client()
    except SentryInitError as e:
        print(f"      ERROR: SentryInitError — {e}")
        print(
            "      Likely cause: DSN is malformed or unreachable. Verify with:\n"
            "        security find-generic-password -a $USER -s ITS_SENTRY_DSN -w\n"
            "      The value should be a URL like https://...@sentry.io/..."
        )
        sys.exit(1)
    except Exception as e:
        print(f"      ERROR: {type(e).__name__}: {e}")
        print(
            "      If the DSN is not in Keychain, add it with:\n"
            "        security add-generic-password -a $USER -s ITS_SENTRY_DSN -w '<DSN>'"
        )
        sys.exit(1)
    print("      OK: SDK initialized")

    # 2. Direct capture_exception call.
    print("\n[2/3] capture_exception direct call (no decorator)...")
    ts = datetime.now(UTC).isoformat()
    try:
        sentry_client.capture_exception(
            script="scripts.smoke_test_sentry",
            message=f"smoke test direct call at {ts}",
            exc_info=(
                "Traceback (most recent call last):\n"
                "  File \"scripts/smoke_test_sentry.py\", line 99, in <synthetic>\n"
                "    raise RuntimeError(\"synthetic for sentry smoke\")\n"
                "RuntimeError: synthetic for sentry smoke"
            ),
        )
    except SentryCaptureError as e:
        print(f"      ERROR: SentryCaptureError — {e}")
        sys.exit(1)
    except SentryError as e:
        print(f"      ERROR: {type(e).__name__}: {e}")
        sys.exit(1)
    print("      OK: capture_exception returned without raising")

    # 3. Full _alert_critical path — fires both Resend AND Sentry legs.
    print("\n[3/3] _alert_critical path (Resend + Sentry both fire)...")
    fake_tb = (
        "Traceback (most recent call last):\n"
        "  File \"scripts/smoke_test_sentry.py\", line 110, in <synthetic>\n"
        "    raise RuntimeError(\"synthetic CRITICAL for triple-fire smoke\")\n"
        "RuntimeError: synthetic CRITICAL for triple-fire smoke"
    )
    _alert_critical(
        script="scripts.smoke_test_sentry",
        message="synthetic CRITICAL — triple-fire smoke, safe to ignore",
        exc_info=fake_tb,
    )
    print("      OK: _alert_critical returned without raising")
    print(
        "      Note: this fired BOTH Resend (operator email) AND Sentry\n"
        "      (dashboard event). Marker lines in ~/its/logs/<today>.log\n"
        "      would surface any failures from either leg."
    )

    print("\n" + "=" * 60)
    print(
        "All checks passed. Verify in your Sentry dashboard:\n"
        "  https://sentry.io → your project → Issues\n"
        "Three test events should be present (two from steps 2-3 in this\n"
        "script, one from your earlier Resend smoke if you re-ran it)."
    )


if __name__ == "__main__":
    main()
