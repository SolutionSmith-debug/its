"""Safety Reports intake polling daemon — RETIRED 2026-06-05.

The safety email intake is superseded by the Safety Portal PULL model
(`safety_reports/portal_poll.py`, PLANNED; see `decision_phase5-portal-transport`).
The Cloudflare Worker queues each submission send-free in D1; portal_poll.py pulls
over HTTPS, HMAC-verifies via `shared/portal_hmac.py`, and hands the structured
submission to `safety_reports.intake`. The Microsoft-Graph email-polling engine that
read the `safety@` mailbox and called `intake.process_message` per unread message is
REMOVED.

PRESERVED, untouched: the shared Graph plumbing this daemon used
(`shared/graph_client.py` — list_inbox / mark_read / GraphError / MSAL — and the other
`shared/` primitives). These are workstream-agnostic; the committed future **Email
Triage** workstream reuses them. The CLEAN BREAK is the safety email-intake path only,
not an email-infrastructure teardown.

This module stays in-tree as a tombstone so any orphan-loaded launchd job surfaces the
retirement instead of a missing-file crash. `main()` (the launchd + CLI entry, invoked by
`python -m safety_reports.intake_poll`) RAISES `NotImplementedError` — failing visibly
(non-zero exit + stderr traceback in the launchd log) so an orphan-loaded job is noticed.
It is deliberately NOT wrapped in `@its_error_log`: that decorator's CRITICAL triple-fire
on every raise would, at the 60 s launchd cadence, become alert-spam (email + Sentry). So
the failure is loud-in-the-log but quiet-on-the-alert-channel until the operator unloads.

OPERATOR (operator-manual, never from code): unload the launchd job —
`scripts/uninstall_safety_intake_daemon.sh` (or `launchctl bootout`). Delete this stub +
the plist in a follow-on cleanup PR once no orphan plist remains on the production Mac.
"""
from __future__ import annotations

_RETIRED = (
    "safety_reports.intake_poll is RETIRED (2026-06-05) — the safety email intake is "
    "superseded by the Safety Portal PULL model (safety_reports/portal_poll.py, PLANNED; "
    "decision_phase5-portal-transport). Unload the launchd job via "
    "scripts/uninstall_safety_intake_daemon.sh."
)


def main() -> None:
    """RETIRED entry (launchd + CLI). Raises visibly; NOT @its_error_log-wrapped, so an
    orphan-loaded 60 s launchd job fails in the log without CRITICAL alert-spam."""
    raise NotImplementedError(_RETIRED)


if __name__ == "__main__":
    main()
