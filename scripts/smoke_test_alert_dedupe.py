#!/usr/bin/env python3
"""Smoke test for alert-routing dedupe + correlation-ID threading.

OPERATIONAL — exercises the FULL triple-fire path through the
`@its_error_log` decorator:
  - Smartsheet ITS_Errors row write (via `log()`)
  - Resend operator email (via `_alert_critical`, gated by `alert_dedupe`)
  - Sentry event (via `_alert_critical`)

Fires 5 CRITICALs with the same `(script, error_code)` key in a tight
loop. Resend dedupe will suppress all-but-the-first within the window.

Opt-in: run by hand. NOT scheduled, NOT triggered by CI. Re-run after:
  - Any change to `shared/alert_dedupe.py`.
  - Any change to `shared/error_log.py` (decorator, `log()`,
    `_alert_critical`, `_fire_resend_leg`).
  - Schema changes to ITS_Errors `Correlation_ID` column.

Requires (per PR α landing state):
  1. ITS_SMARTSHEET_TOKEN in Keychain.
  2. ITS_RESEND_API_KEY in Keychain.
  3. ITS_SENTRY_DSN in Keychain.
  4. `Correlation_ID` column on ITS_Errors (sheet 27291433258884).
  5. `alerting.dedupe_window_minutes` row in ITS_Config (workstream `global`).
  6. `system.operator_email` row in ITS_Config (already seeded).

What it verifies (all three triple-fire legs):
  - 5 CRITICALs through `@its_error_log` decorator land:
      • Smartsheet ITS_Errors: **5 new rows**, each with a distinct
        `Correlation_ID` value (decorator generates one UUID per CRITICAL).
      • Resend operator inbox: **1 new email** if the
        `<script>::uncaught_exception` dedupe window was previously empty
        OR **0 new emails** if a prior smoke (or production CRITICAL on
        the same key) has the window already open. The first fire's
        correlation ID lands in the Resend subject's `[corr: ...]` suffix.
      • Sentry: **5 new events**, each tagged with one of the 5 distinct
        correlation IDs. Sentry's fingerprint may group them into one
        issue (identical stacktrace) or split by exception message
        (`#1` … `#5`) — both outcomes demonstrate the leg fired.

State-file note:
  This harness does NOT clear `~/its/state/alert_dedupe.json` at start.
  Persisting state across invocations is part of the test surface — a
  prior open window on the same dedupe key suppresses every fire of
  this run. The smoke's job is to exercise the legs; the operator
  inspects state before + after to see what dedupe did with this batch.

Smoke harness divergence:
  `smoke_test_sentry.py` and `smoke_test_resend.py` call `_alert_critical`
  directly, which skips the Smartsheet leg (that path is in `log()`,
  upstream of `_alert_critical`). This smoke uses the full decorator
  path specifically to exercise all three legs. See
  `docs/tech_debt.md` "Smoke harness pattern divergence" entry.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

# Allow running from repo root without installing the package.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shared import alert_dedupe  # noqa: E402
from shared.error_log import its_error_log  # noqa: E402

SCRIPT_NAME = "scripts.smoke_test_alert_dedupe"
FIRE_COUNT = 5


@its_error_log(SCRIPT_NAME)
def _fire_one(iteration: int) -> None:
    """Decorator-wrapped CRITICAL trigger.

    The decorator catches the raised exception, writes a CRITICAL row to
    ITS_Errors via `log()`, then fires `_alert_critical` (Resend gated by
    dedupe + Sentry). The raised exception is re-raised after the
    side-channel writes complete; the loop in `main` swallows it so the
    next iteration runs.
    """
    raise RuntimeError(f"smoke-dedupe synthetic CRITICAL #{iteration}")


def _read_state() -> dict:
    if alert_dedupe.STATE_FILE.exists():
        try:
            return json.loads(alert_dedupe.STATE_FILE.read_text())
        except json.JSONDecodeError:
            return {}
    return {}


def main() -> None:
    print("ITS Alert Dedupe Smoke — Triple-Fire Verification")
    print("=" * 60)

    pre_state = _read_state()
    pre_keys = set(pre_state.keys())
    print(f"[setup] State file: {alert_dedupe.STATE_FILE}")
    if pre_keys:
        print(f"[setup] Pre-existing dedupe entries: {len(pre_keys)}")
        for k, v in pre_state.items():
            ends = v.get("window_ends_at", "(unknown)")
            count = v.get("suppressed_count", 0)
            print(f"          {k}  (window_ends_at={ends}, suppressed_count={count})")
    else:
        print("[setup] State file empty / missing — first fire will open a fresh window.")
    print()

    expected_key = f"{SCRIPT_NAME}::uncaught_exception"
    print(f"[fire] Triggering {FIRE_COUNT} CRITICALs via @its_error_log decorator.")
    print(f"[fire] Dedupe key:  {expected_key}")
    print()

    for i in range(1, FIRE_COUNT + 1):
        try:
            _fire_one(i)
        except RuntimeError:
            # Decorator re-raises. Swallow so loop continues.
            pass

    print()
    print("=" * 60)
    post_state = _read_state()
    new_window = post_state.get(expected_key)
    pre_window_open = expected_key in pre_keys

    print(f"All {FIRE_COUNT} CRITICALs fired through the decorator path.")
    print()
    print("Expected results:")
    print(f"  ITS_Errors:        {FIRE_COUNT} new rows, each with a DISTINCT Correlation_ID")
    print(f"  Sentry:            {FIRE_COUNT} new events, each tagged with one of those IDs")
    if pre_window_open:
        print(
            f"  Resend inbox:      0 NEW emails (prior {expected_key!s} window was open;"
            f"\n                     all {FIRE_COUNT} suppressed)"
        )
    else:
        print(
            f"  Resend inbox:      1 NEW [ITS CRITICAL] email\n"
            f"                     (fire #1 opened the window; #2-{FIRE_COUNT} suppressed)"
        )
    print()

    if new_window:
        suppressed = new_window.get("suppressed_count", 0)
        first = new_window.get("first_fired_at", "?")
        ends = new_window.get("window_ends_at", "?")
        print(f"State file after run — entry for {expected_key!s}:")
        print(f"  first_fired_at:    {first}")
        print(f"  window_ends_at:    {ends}")
        print(f"  suppressed_count:  {suppressed}  (expected: {FIRE_COUNT - 1 if not pre_window_open else FIRE_COUNT})")
    else:
        print(f"WARNING: no state entry written for {expected_key!s} — investigate.")
    print()

    print("Operator verification steps:")
    print(
        f"  1. Inbox — check for [ITS CRITICAL] emails. Expected count depends on\n"
        f"     pre-existing window state (see above). The email subject carries a\n"
        f"     [corr: xxxxxxxx] suffix matching one of the row Correlation_IDs.\n"
        f"  2. ITS_Errors (sheet 27291433258884) — filter Surfaced At to the last\n"
        f"     10 minutes; expect 5 rows with Severity=CRITICAL,\n"
        f"     Script={SCRIPT_NAME!r}, Error='uncaught_exception',\n"
        f"     and 5 distinct Correlation_ID values.\n"
        f"  3. Sentry dashboard — expect 5 new events, all tagged\n"
        f"     source=its-error-log + correlation_id=<distinct uuid>. Grouping\n"
        f"     under one issue OR five issues are both acceptable outcomes."
    )


if __name__ == "__main__":
    main()
