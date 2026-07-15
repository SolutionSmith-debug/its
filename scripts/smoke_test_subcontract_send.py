#!/usr/bin/env python3
"""Smoke test for subcontracts/subcontract_send.py + subcontract_send_poll.py env prereqs (SC-S4).

OPERATIONAL — makes REAL Smartsheet API calls (read-only) and a Graph credential probe
(no message send). The subcontract twin of ``scripts/smoke_test_po_send.py``.

End-to-end send is exercised by the operator's live e2e (draft a subcontract → generate →
approve the Subcontract_Pending_Review row → watch subcontract_send transmit the
Subcontract Package.zip to a subcontractor-stand-in mailbox). This smoke checks env prereqs +
the cross-workstream-contamination guards a config typo would trip:

  - the subcontract CONFIG binds the Subcontract_Pending_Review sheet + the ITS — Subcontracts
    workspace (never safety's/progress's/PO's — the recipient/approver cross-wiring trap);
  - the from-mailbox key resolves under the subcontracts workstream;
  - the F22 approver authority is the ITS — Subcontracts workspace membership (§46) — an EMPTY
    approver set fails CLOSED (no subcontract send can dispatch, EMPTY_ALLOWLIST).

DEPLOY-GATED items the operator confirms separately at go-live (NOT this smoke): the
procurement@ mailbox exists + is in the app's Application Access Policy scope; the subcontract
approvers are shared into the workspace.

Re-run after:
  - ITS_SMARTSHEET_TOKEN rotation
  - Graph credential rotation
  - Changes to subcontract_send.py / subcontract_send_poll.py module-level setup
  - Subcontract_Pending_Review schema changes
  - re-sharing (or un-sharing) an approver into the ITS — Subcontracts workspace

Eight numbered stages, each printed to stdout. Exit 0 on full green; 1 on any stage failure.
WARN (not FAIL) on the §46 empty-approver-set case — an expected pre-go-live state the operator
closes by re-sharing approvers.
"""
from __future__ import annotations

import sys

from shared import sheet_ids, smartsheet_client
from shared.kill_switch import SystemState, check_system_state
from subcontracts import subcontract_review, subcontract_send, subcontract_send_poll


def stage(n: int, label: str) -> None:
    print(f"\n[stage {n}] {label}")


def main() -> int:
    print("subcontracts.subcontract_send smoke test")
    print("========================================")

    # ---- Stage 1: kill switch ACTIVE -----------------------------------
    stage(1, "kill switch system.state")
    try:
        state = check_system_state()
    except Exception as exc:  # noqa: BLE001
        print(f"  FAIL — check_system_state raised: {exc!r}")
        return 1
    print(f"  OK — system.state = {state.value!r}")
    if state is not SystemState.ACTIVE:
        print(f"  WARN — state is {state.value}; subcontract_send_poll would short-circuit via @require_active.")

    # ---- Stage 2: subcontract config binding sanity (cross-wiring guards) --------
    stage(2, "subcontract CONFIG binds the subcontract sheet/workspace (not safety/progress/PO)")
    cfg = subcontract_send.CONFIG
    poll_cfg = subcontract_send_poll.CONFIG
    problems: list[str] = []
    if cfg.workstream_tag != "subcontracts":
        problems.append(f"workstream_tag={cfg.workstream_tag!r}, expected 'subcontracts'")
    if not isinstance(cfg.recipient_lookup, subcontract_send._SubcontractorRecipientLookup):
        problems.append("recipient_lookup is NOT the ITS_Subcontractors _SubcontractorRecipientLookup binding")
    if poll_cfg.poll_sheet_id != sheet_ids.SHEET_SUBCONTRACT_PENDING_REVIEW:
        problems.append(
            f"poll_sheet_id={poll_cfg.poll_sheet_id}, expected "
            f"SHEET_SUBCONTRACT_PENDING_REVIEW={sheet_ids.SHEET_SUBCONTRACT_PENDING_REVIEW}"
        )
    if poll_cfg.f22_workspace_id != sheet_ids.WORKSPACE_SUBCONTRACTS:
        problems.append(
            "poll f22_workspace_id is NOT the ITS — Subcontracts workspace "
            f"(got {poll_cfg.f22_workspace_id}, expected {sheet_ids.WORKSPACE_SUBCONTRACTS})"
        )
    if subcontract_review.STATUS_SENDING in poll_cfg.dispatch_statuses:
        problems.append("SENDING is a dispatch candidate — the no-double-send exclusion is broken")
    if problems:
        for problem in problems:
            print(f"  FAIL — {problem}")
        return 1
    print("  OK — workstream_tag='subcontracts'; recipients ← ITS_Subcontractors; "
          "F22 ← ITS — Subcontracts workspace; SENDING excluded")

    # ---- Stage 3: ITS_Config readable (subcontract-scoped) --------------
    stage(3, "ITS_Config keys (from_mailbox + poll_interval + scheduled_send_local)")
    from_mailbox = subcontract_send.weekly_send._read_str_setting(
        subcontract_send.CFG_FROM_MAILBOX, subcontract_send.DEFAULT_FROM_MAILBOX,
        workstream=subcontract_send.WORKSTREAM,
    )
    print(f"  OK — from_mailbox = {from_mailbox!r}")
    scheduled_spec = subcontract_send_poll._read_str_setting(
        subcontract_send_poll.CFG_SCHEDULED_SEND_LOCAL, subcontract_send_poll.DEFAULT_SCHEDULED_SEND_LOCAL,
    )
    print(f"  OK — scheduled_send_local = {scheduled_spec!r}")
    poll_interval = subcontract_send_poll._read_str_setting(
        subcontract_send_poll.CFG_POLL_INTERVAL, str(subcontract_send_poll.DEFAULT_POLL_INTERVAL),
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

    # ---- Stage 5: Subcontract_Pending_Review schema check --------------
    stage(5, "Subcontract_Pending_Review sheet reachable + expected columns present")
    try:
        rows = smartsheet_client.get_rows(subcontract_review.SHEET_ID)
    except Exception as exc:  # noqa: BLE001
        print(f"  FAIL — get_rows raised: {exc!r}")
        return 1
    expected = {
        subcontract_review.COL_JOB_PROJECT, subcontract_review.COL_JOB_ID, subcontract_review.COL_WEEK_OF,
        subcontract_review.COL_COMPILED_PDF, subcontract_review.COL_EMAIL_BODY,
        subcontract_review.COL_APPROVE_SCHEDULED, subcontract_review.COL_SEND_NOW,
        subcontract_review.COL_SEND_STATUS, subcontract_review.COL_SENT_AT,
        subcontract_review.COL_NOTES, subcontract_review.COL_WORKSTREAM,
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

    # ---- Stage 6: F22 approver authority (subcontract workspace, §46) --
    stage(6, "F22 approver set = ITS — Subcontracts workspace membership (§46)")
    try:
        approvers = smartsheet_client.list_workspace_share_emails(
            sheet_ids.WORKSPACE_SUBCONTRACTS
        )
    except Exception as exc:  # noqa: BLE001
        print(f"  FAIL — list_workspace_share_emails raised: {exc!r}")
        return 1
    if approvers:
        print(f"  OK — {len(approvers)} approver(s) shared into the ITS — Subcontracts workspace")
    else:
        print(
            "  WARN — EMPTY approver set: no individual shares on the ITS — Subcontracts "
            "workspace. F22 fails CLOSED → every subcontract send is blocked (EMPTY_ALLOWLIST). "
            "Share each subcontract approver into the workspace before the live send (§46)."
        )

    # ---- Stage 7: ITS_Daemon_Health reachable --------------------------
    stage(7, "ITS_Daemon_Health reachable")
    try:
        smartsheet_client.get_rows(sheet_ids.SHEET_DAEMON_HEALTH)
    except Exception as exc:  # noqa: BLE001
        print(f"  FAIL — ITS_Daemon_Health get_rows raised: {exc!r}")
        return 1
    print("  OK — ITS_Daemon_Health reachable")

    # ---- Stage 8: dry-run poll filter on empty state -------------------
    stage(8, "subcontract_send_poll filter on known-empty state")
    candidates = subcontract_send_poll._filter_dispatch_candidates([])
    if candidates != []:
        print(f"  FAIL — filter on empty list returned {candidates!r}")
        return 1
    print("  OK — filter on empty list returns empty list")

    print("\nAll stages green.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
