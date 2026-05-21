#!/usr/bin/env python3
"""Smoke test for watchdog Check G — alert-dedupe summary sweep.

OPERATIONAL — invokes `_check_alert_dedupe_summaries()` directly against
the LIVE `~/its/state/alert_dedupe.json`. May send real Resend summary
emails and mutate state file entries (mark / delete). Opt-in: operator
authorizes by running this script by hand.

MAINTENANCE-aware: reads `shared.kill_switch.check_system_state()` and
threads `alerts_suppressed` into the Check G call. Mirrors the
production `watchdog.main → _run_check → signature-inspection` path so
the smoke harness honors Op Stds v10 §2 instead of always running with
the safety default `alerts_suppressed=False` (which would re-introduce
V1 in a different file). State = MAINTENANCE → phase-1 summaries
defer; state = ACTIVE → fire normally; state = PAUSED → script exits
without touching anything (matches production behavior where the
watchdog's CHECKS loop is skipped entirely under PAUSED).

Requires:
  1. ITS_RESEND_API_KEY in Keychain (only matters if there are summaries
     to fire — read-only-sweep mode never reaches Resend).
  2. `system.operator_email` in ITS_Config.
  3. At least one entry in `~/its/state/alert_dedupe.json` with
     `window_ends_at < now`. Run `scripts/smoke_test_alert_dedupe.py`,
     wait 60+ minutes (default dedupe window), then run this script.

What it does:
  1. Read current state file. Identify expired entries. If none → exit.
  2. Print pre-sweep state summary (which entries are expired,
     suppressed_count for each, summarized flag).
  3. Invoke `_check_alert_dedupe_summaries()` directly (NOT full watchdog;
     other checks are not exercised here).
  4. Print the check's CheckResult summary + the post-sweep state diff.
  5. Print operator-verifiable expectations:
       - Inbox: N summary emails matching the entries that needed firing.
       - State file: entries with suppressed_count >= 1 are now
         `summarized=true`; entries with `suppressed_count == 0` or
         already-summarized have been deleted.

The check itself is failure-isolated by the production `_run_check`
wrapper at watchdog main entry — invoking it directly here means any
raise here would surface in this script's stdout, which is what we
want for smoke verification.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

# Allow running from repo root without installing the package.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import watchdog  # noqa: E402  (resolves via the scripts/ sys.path insertion)

from shared import alert_dedupe  # noqa: E402
from shared.kill_switch import SystemState, check_system_state  # noqa: E402


def _read_state() -> dict:
    if alert_dedupe.STATE_FILE.exists():
        try:
            return json.loads(alert_dedupe.STATE_FILE.read_text())
        except json.JSONDecodeError:
            return {}
    return {}


def _print_state(state: dict, header: str) -> None:
    print(f"--- {header} ---")
    if not state:
        print("  (empty)")
        return
    for k, v in state.items():
        ends = v.get("window_ends_at", "?")
        count = v.get("suppressed_count", 0)
        summarized = v.get("summarized", False)
        print(
            f"  {k}\n"
            f"      window_ends_at={ends}  suppressed_count={count}  summarized={summarized}"
        )


def main() -> None:
    print("ITS Watchdog Summary-Sweep Smoke (Check G)")
    print("=" * 60)
    print(f"State file: {alert_dedupe.STATE_FILE}")

    # Mirror production: read kill switch, derive alerts_suppressed,
    # exit on PAUSED. MAINTENANCE flows through to the check as
    # `alerts_suppressed=True` so phase-1 summaries defer.
    state = check_system_state()
    alerts_suppressed = state == SystemState.MAINTENANCE
    print(f"system.state={state.value}  alerts_suppressed={alerts_suppressed}")
    if state == SystemState.PAUSED:
        print("PAUSED — exiting without touching anything (matches production).")
        return
    print()

    pre_state = _read_state()
    _print_state(pre_state, "Pre-sweep state")
    print()

    expired = alert_dedupe.list_expired_summaries()
    if not expired:
        print("No expired windows in state file.")
        print(
            "Run `scripts/smoke_test_alert_dedupe.py`, wait 60+ minutes,\n"
            "then re-run this smoke. The dedupe window default is 60 min\n"
            "(per ITS_Config alerting.dedupe_window_minutes)."
        )
        return

    print(f"Found {len(expired)} expired entr{'y' if len(expired) == 1 else 'ies'}:")
    will_fire: list[str] = []
    will_defer: list[str] = []
    will_delete: list[str] = []
    for e in expired:
        if e.suppressed_count >= 1 and not e.summarized:
            if alerts_suppressed:
                will_defer.append(e.key)
                print(
                    f"  [DEFER]     {e.key}  (suppressed={e.suppressed_count}, "
                    f"MAINTENANCE — no send, no mark; will fire next ACTIVE sweep)"
                )
            else:
                will_fire.append(e.key)
                print(
                    f"  [SUMMARY]   {e.key}  (suppressed={e.suppressed_count}, "
                    f"will fire 1 Resend email + mark_summarized)"
                )
        else:
            reason = (
                "already summarized" if e.summarized
                else "clean expiry (no suppressions)"
            )
            will_delete.append(e.key)
            print(
                f"  [DELETE]    {e.key}  ({reason}; "
                f"phase-2 proceeds regardless of MAINTENANCE)"
            )
    print()

    print(
        f"Invoking watchdog._check_alert_dedupe_summaries"
        f"(alerts_suppressed={alerts_suppressed})..."
    )
    print()
    result = watchdog._check_alert_dedupe_summaries(
        alerts_suppressed=alerts_suppressed
    )

    print("=" * 60)
    print(f"CheckResult: severity={result.severity.value}")
    print(f"  summary:  {result.summary}")
    if result.details:
        print(f"  details:  {result.details}")
    print()

    post_state = _read_state()
    _print_state(post_state, "Post-sweep state")
    print()

    print("Operator verification steps:")
    step = 1
    if will_fire:
        print(
            f"  {step}. Inbox — expect {len(will_fire)} new "
            f"[ITS CRITICAL SUMMARY] email(s):"
        )
        for k in will_fire:
            script = k.split("::", 1)[0]
            print(f"        - subject begins '[ITS CRITICAL SUMMARY] {script}: …'")
        step += 1
    if will_defer:
        print(
            f"  {step}. Inbox — expect 0 new emails for {len(will_defer)} "
            f"deferred entr{'y' if len(will_defer) == 1 else 'ies'} (MAINTENANCE):"
        )
        for k in will_defer:
            print(f"        - {k} (stays summarized=False; fires next ACTIVE sweep)")
        step += 1
    if not will_fire and not will_defer:
        print(f"  {step}. Inbox — no new emails expected (no summarizable entries).")
        step += 1
    if will_delete:
        print(f"  {step}. State file — {len(will_delete)} entry/entries deleted:")
        for k in will_delete:
            print(f"        - {k}")
        step += 1
    if will_fire:
        print(
            f"  {step}. State file — any 'fired' entries are now `summarized=true`\n"
            f"     and will be deleted on the next sweep (two-phase delete)."
        )


if __name__ == "__main__":
    main()
