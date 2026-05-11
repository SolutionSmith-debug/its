"""ITS daily watchdog — runs every morning at 7:00 AM via launchd.

Verifies:
- Critical scheduled jobs ran in the last 24 hours.
- No items past 2x SLA in ITS_Review_Queue.
- Anthropic spend is trending within budget cap.
- Inbound mail processed in last 24h (once Email Triage is live).

Silent if green. Emails + SMS maintainer if anything is off.

Trigger this script from a launchd plist. Example plist lives in `scripts/launchd/`
(TODO once we have the production MacBook).
"""
from __future__ import annotations

from shared.error_log import Severity, its_error_log, log
from shared.kill_switch import SystemState, check_system_state


@its_error_log("scripts.watchdog")
def main() -> None:
    state = check_system_state()
    if state == SystemState.MAINTENANCE:
        # Maintenance mode: watchdog runs but does not alert.
        log(Severity.INFO, "scripts.watchdog", "MAINTENANCE — alerts suppressed")
        return

    # TODO: real checks once dependencies are in place.
    log(Severity.INFO, "scripts.watchdog", "watchdog stub — no checks implemented yet")


if __name__ == "__main__":
    main()
