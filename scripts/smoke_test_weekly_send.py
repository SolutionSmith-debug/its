#!/usr/bin/env python3
"""Smoke test for safety_reports/weekly_send.py environment prereqs.

OPERATIONAL — makes REAL Smartsheet API calls (read-only) and a probe
into the Graph client (cached credentials check; no message send).

End-to-end send is exercised by tests/test_weekly_send_integration.py
(gated `pytest -m integration`); the smoke here only checks env prereqs.

Re-run after:
  - ITS_SMARTSHEET_TOKEN rotation
  - Graph credential rotation
  - Changes to safety_reports/weekly_send.py or weekly_send_poll.py
    module-level setup
  - WPR_Pending_Review schema changes
  - ITS_Daemon_Health schema changes

Six numbered stages, each printed to stdout. Exit code 0 on full green;
1 on any stage failure.
"""
from __future__ import annotations

import sys

from safety_reports import weekly_send, weekly_send_poll
from shared import sheet_ids, smartsheet_client
from shared.kill_switch import SystemState, check_system_state


def stage(n: int, label: str) -> None:
    print(f"\n[stage {n}] {label}")


def main() -> int:
    print("safety_reports.weekly_send smoke test")
    print("=====================================")

    # ---- Stage 1: kill switch ACTIVE -----------------------------------
    stage(1, "kill switch system.state")
    try:
        state = check_system_state()
    except Exception as exc:  # noqa: BLE001
        print(f"  FAIL — check_system_state raised: {exc!r}")
        return 1
    print(f"  OK — system.state = {state.value!r}")
    if state is not SystemState.ACTIVE:
        print(
            f"  WARN — state is {state.value}; weekly_send_poll would short-circuit"
            f" via @require_active."
        )

    # ---- Stage 2: ITS_Config readable ----------------------------------
    stage(2, "ITS_Config keys (from_mailbox + poll_interval + scheduled_send_local)")
    from_mailbox = weekly_send._read_str_setting(
        weekly_send.CFG_FROM_MAILBOX, weekly_send.DEFAULT_FROM_MAILBOX,
        workstream=weekly_send.WORKSTREAM,
    )
    print(f"  OK — from_mailbox = {from_mailbox!r}")
    scheduled_spec = weekly_send_poll._read_str_setting(
        weekly_send_poll.CFG_SCHEDULED_SEND_LOCAL,
        weekly_send_poll.DEFAULT_SCHEDULED_SEND_LOCAL,
    )
    print(f"  OK — scheduled_send_local = {scheduled_spec!r}")
    poll_interval = weekly_send_poll._read_str_setting(
        weekly_send_poll.CFG_POLL_INTERVAL,
        str(weekly_send_poll.DEFAULT_POLL_INTERVAL),
    )
    print(f"  OK — poll_interval_seconds = {poll_interval!r}")

    # ---- Stage 3: Graph credentials reachable --------------------------
    stage(3, "Graph credentials reachable (token acquisition probe)")
    try:
        from shared import graph_client

        token = graph_client._get_token()
        assert token  # truthy on success
    except Exception as exc:  # noqa: BLE001
        print(f"  FAIL — _get_token raised: {exc!r}")
        return 1
    print("  OK — Graph token acquired (creds in keychain + Entra reachable)")

    # ---- Stage 4: WSR_human_review schema check (Phase-5) ---------------
    stage(4, "WSR_human_review sheet reachable + expected columns present")
    try:
        from safety_reports import wsr_review
        rows = smartsheet_client.get_rows(wsr_review.SHEET_ID)
    except Exception as exc:  # noqa: BLE001
        print(f"  FAIL — get_rows raised: {exc!r}")
        return 1
    expected = {
        wsr_review.COL_JOB_PROJECT,
        wsr_review.COL_JOB_ID,
        wsr_review.COL_WEEK_OF,
        wsr_review.COL_COMPILED_PDF,
        wsr_review.COL_EMAIL_BODY,
        wsr_review.COL_APPROVE_SCHEDULED,
        wsr_review.COL_SEND_NOW,
        wsr_review.COL_SEND_STATUS,
        wsr_review.COL_SENT_AT,
        wsr_review.COL_NOTES,
        wsr_review.COL_WORKSTREAM,
    }
    if rows:
        present = set(rows[0].keys()) - {"_row_id"}
        missing = expected - present
        if missing:
            print(f"  WARN — expected columns missing: {sorted(missing)}")
        else:
            print(f"  OK — all {len(expected)} expected columns present")
        print(f"  INFO — total rows: {len(rows)}")
    else:
        print("  OK — sheet reachable; 0 rows present (schema check deferred)")

    # ---- Stage 5: ITS_Daemon_Health writable ---------------------------
    stage(5, "ITS_Daemon_Health reachable")
    try:
        smartsheet_client.get_rows(sheet_ids.SHEET_DAEMON_HEALTH)
    except Exception as exc:  # noqa: BLE001
        print(f"  FAIL — ITS_Daemon_Health get_rows raised: {exc!r}")
        return 1
    print("  OK — ITS_Daemon_Health reachable")

    # ---- Stage 6: dry-run poll_once on empty filter --------------------
    stage(6, "weekly_send_poll filter on known-empty state")
    # Just probe the filter function with a synthetic empty row list.
    candidates = weekly_send_poll._filter_dispatch_candidates([])
    if candidates != []:
        print(f"  FAIL — filter on empty list returned {candidates!r}")
        return 1
    print("  OK — filter on empty list returns empty list")

    print("\nAll stages green.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
