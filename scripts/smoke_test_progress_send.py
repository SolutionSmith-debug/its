#!/usr/bin/env python3
"""Smoke test for progress_reports/progress_send.py environment prereqs.

OPERATIONAL — makes REAL Smartsheet API calls (read-only) and a probe into the Graph
client (cached credentials check; no message send). The progress twin of
``scripts/smoke_test_weekly_send.py``.

End-to-end send is exercised by the operator's live e2e (seed a progress job → submit a
progress form → compile → approve the WPR row → watch progress_send transmit); the smoke
here only checks env prereqs + the two cross-workstream-contamination guards a config
typo would trip:

  - the from-mailbox key resolves under the PROGRESS workstream (not safety);
  - recipients would resolve from ITS_Active_Jobs_Progress (the S5a
    CONFIG.recipient_lookup.active_jobs_config binding), NOT ITS_Active_Jobs — the
    P4-Slice-1 trap;
  - the F22 approver authority is the Progress Reporting workspace's membership
    (§46 re-share) — an EMPTY approver set fails CLOSED (no progress send can dispatch).

Re-run after:
  - ITS_SMARTSHEET_TOKEN rotation
  - Graph credential rotation
  - Changes to progress_send.py / progress_send_poll.py module-level setup
  - WPR_human_review schema changes
  - re-sharing (or un-sharing) an approver into the Progress Reporting workspace

Eight numbered stages, each printed to stdout. Exit code 0 on full green; 1 on any
stage failure. WARN (not FAIL) on the §46 empty-approver-set case — it is an expected
pre-cutover state that the operator closes by re-sharing approvers.
"""
from __future__ import annotations

import sys

from progress_reports import progress_send, progress_send_poll
from shared import active_jobs, sheet_ids, smartsheet_client
from shared.kill_switch import SystemState, check_system_state


def stage(n: int, label: str) -> None:
    print(f"\n[stage {n}] {label}")


def main() -> int:
    print("progress_reports.progress_send smoke test")
    print("=========================================")

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
            f"  WARN — state is {state.value}; progress_send_poll would short-circuit"
            f" via @require_active."
        )

    # ---- Stage 2: progress config binding sanity -----------------------
    stage(2, "progress CONFIG binds the PROGRESS sheets/workspace (not safety)")
    cfg = progress_send.CONFIG
    problems: list[str] = []
    if cfg.workstream_tag != "progress":
        problems.append(f"workstream_tag={cfg.workstream_tag!r}, expected 'progress'")
    lookup = cfg.recipient_lookup
    bound_ajc = getattr(lookup, "active_jobs_config", None)
    if bound_ajc is not active_jobs.PROGRESS_ACTIVE_JOBS_CONFIG:
        problems.append(
            "recipient_lookup.active_jobs_config is NOT PROGRESS_ACTIVE_JOBS_CONFIG "
            "(would resolve recipients from the SAFETY sheet — the P4-Slice-1 trap)"
        )
    if bound_ajc is None or bound_ajc.sheet_id != sheet_ids.SHEET_ACTIVE_JOBS_PROGRESS:
        problems.append(
            f"recipient_lookup.active_jobs_config.sheet_id="
            f"{getattr(bound_ajc, 'sheet_id', None)}, "
            f"expected SHEET_ACTIVE_JOBS_PROGRESS={sheet_ids.SHEET_ACTIVE_JOBS_PROGRESS}"
        )
    if progress_send_poll.CONFIG.f22_workspace_id != sheet_ids.WORKSPACE_PROGRESS_REPORTING:
        problems.append("poll f22_workspace_id is NOT the Progress Reporting workspace")
    if problems:
        for problem in problems:
            print(f"  FAIL — {problem}")
        return 1
    print("  OK — workstream_tag='progress'; recipients ← ITS_Active_Jobs_Progress; "
          "F22 ← Progress Reporting workspace")

    # ---- Stage 3: ITS_Config readable (progress-scoped) ----------------
    stage(3, "ITS_Config keys (from_mailbox + poll_interval + scheduled_send_local)")
    from_mailbox = progress_send.weekly_send._read_str_setting(
        progress_send.CFG_FROM_MAILBOX, progress_send.DEFAULT_FROM_MAILBOX,
        workstream=progress_send.WORKSTREAM,
    )
    print(f"  OK — from_mailbox = {from_mailbox!r}")
    scheduled_spec = progress_send_poll._read_str_setting(
        progress_send_poll.CFG_SCHEDULED_SEND_LOCAL,
        progress_send_poll.DEFAULT_SCHEDULED_SEND_LOCAL,
    )
    print(f"  OK — scheduled_send_local = {scheduled_spec!r}")
    poll_interval = progress_send_poll._read_str_setting(
        progress_send_poll.CFG_POLL_INTERVAL,
        str(progress_send_poll.DEFAULT_POLL_INTERVAL),
    )
    print(f"  OK — poll_interval_seconds = {poll_interval!r}")

    # ---- Stage 4: Graph credentials reachable --------------------------
    stage(4, "Graph credentials reachable (token acquisition probe)")
    try:
        from shared import graph_client

        token = graph_client._get_token()
        assert token  # truthy on success
    except Exception as exc:  # noqa: BLE001
        print(f"  FAIL — _get_token raised: {exc!r}")
        return 1
    print("  OK — Graph token acquired (creds in keychain + Entra reachable)")

    # ---- Stage 5: WPR_human_review schema check ------------------------
    stage(5, "WPR_human_review sheet reachable + expected columns present")
    try:
        from progress_reports import wpr_review
        rows = smartsheet_client.get_rows(wpr_review.SHEET_ID)
    except Exception as exc:  # noqa: BLE001
        print(f"  FAIL — get_rows raised: {exc!r}")
        return 1
    expected = {
        wpr_review.COL_JOB_PROJECT,
        wpr_review.COL_JOB_ID,
        wpr_review.COL_WEEK_OF,
        wpr_review.COL_COMPILED_PDF,
        wpr_review.COL_EMAIL_BODY,
        wpr_review.COL_APPROVE_SCHEDULED,
        wpr_review.COL_SEND_NOW,
        wpr_review.COL_SEND_STATUS,
        wpr_review.COL_SENT_AT,
        wpr_review.COL_NOTES,
        wpr_review.COL_WORKSTREAM,
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

    # ---- Stage 6: F22 approver authority (Progress workspace, §46) -----
    stage(6, "F22 approver set = Progress Reporting workspace membership (§46 re-share)")
    try:
        approvers = smartsheet_client.list_workspace_share_emails(
            sheet_ids.WORKSPACE_PROGRESS_REPORTING
        )
    except Exception as exc:  # noqa: BLE001
        print(f"  FAIL — list_workspace_share_emails raised: {exc!r}")
        return 1
    if approvers:
        print(f"  OK — {len(approvers)} approver(s) shared into the Progress workspace")
    else:
        print(
            "  WARN — EMPTY approver set: no individual shares on the Progress Reporting "
            "workspace. F22 fails CLOSED → every progress send is blocked (EMPTY_ALLOWLIST). "
            "Re-share each safety approver into the new workspace before the live send (§46)."
        )

    # ---- Stage 7: ITS_Daemon_Health writable ---------------------------
    stage(7, "ITS_Daemon_Health reachable")
    try:
        smartsheet_client.get_rows(sheet_ids.SHEET_DAEMON_HEALTH)
    except Exception as exc:  # noqa: BLE001
        print(f"  FAIL — ITS_Daemon_Health get_rows raised: {exc!r}")
        return 1
    print("  OK — ITS_Daemon_Health reachable")

    # ---- Stage 8: dry-run poll filter on empty state -------------------
    stage(8, "progress_send_poll filter on known-empty state")
    candidates = progress_send_poll._filter_dispatch_candidates([])
    if candidates != []:
        print(f"  FAIL — filter on empty list returned {candidates!r}")
        return 1
    print("  OK — filter on empty list returns empty list")

    print("\nAll stages green.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
