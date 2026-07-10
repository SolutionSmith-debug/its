#!/usr/bin/env python3
"""Smoke test for po_materials/po_send.py + po_send_poll.py environment prereqs (S5b).

OPERATIONAL — makes REAL Smartsheet API calls (read-only) and a Graph credential probe
(no message send). The PO twin of ``scripts/smoke_test_progress_send.py``.

End-to-end send is exercised by the operator's live e2e (draft a PO → generate → approve
the PO_Pending_Review row → watch po_send transmit to a supplier-stand-in mailbox). This
smoke checks env prereqs + the cross-workstream-contamination guards a config typo would
trip:

  - the PO CONFIG binds the PO_Pending_Review sheet + the ITS — Purchase Orders workspace
    (never safety's/progress's — the recipient/approver cross-wiring trap);
  - the from-mailbox key resolves under the PO workstream;
  - the F22 approver authority is the ITS — Purchase Orders workspace membership (§46) —
    an EMPTY approver set fails CLOSED (no PO send can dispatch, EMPTY_ALLOWLIST).

DEPLOY-GATED items the operator confirms separately at cutover (NOT this smoke): the
procurement@ mailbox exists + is in the app's Application Access Policy scope; the PO
approvers are shared into the workspace.

Re-run after:
  - ITS_SMARTSHEET_TOKEN rotation
  - Graph credential rotation
  - Changes to po_send.py / po_send_poll.py module-level setup
  - PO_Pending_Review schema changes
  - re-sharing (or un-sharing) an approver into the ITS — Purchase Orders workspace

Eight numbered stages, each printed to stdout. Exit 0 on full green; 1 on any stage
failure. WARN (not FAIL) on the §46 empty-approver-set case — an expected pre-cutover
state the operator closes by re-sharing approvers.
"""
from __future__ import annotations

import sys

from po_materials import po_review, po_send, po_send_poll
from shared import sheet_ids, smartsheet_client
from shared.kill_switch import SystemState, check_system_state


def stage(n: int, label: str) -> None:
    print(f"\n[stage {n}] {label}")


def main() -> int:
    print("po_materials.po_send smoke test")
    print("===============================")

    # ---- Stage 1: kill switch ACTIVE -----------------------------------
    stage(1, "kill switch system.state")
    try:
        state = check_system_state()
    except Exception as exc:  # noqa: BLE001
        print(f"  FAIL — check_system_state raised: {exc!r}")
        return 1
    print(f"  OK — system.state = {state.value!r}")
    if state is not SystemState.ACTIVE:
        print(f"  WARN — state is {state.value}; po_send_poll would short-circuit via @require_active.")

    # ---- Stage 2: PO config binding sanity (cross-wiring guards) --------
    stage(2, "PO CONFIG binds the PO sheet/workspace (not safety/progress)")
    cfg = po_send.CONFIG
    poll_cfg = po_send_poll.CONFIG
    problems: list[str] = []
    if cfg.workstream_tag != "po_materials":
        problems.append(f"workstream_tag={cfg.workstream_tag!r}, expected 'po_materials'")
    if not isinstance(cfg.recipient_lookup, po_send._VendorRecipientLookup):
        problems.append("recipient_lookup is NOT the ITS_Vendors _VendorRecipientLookup binding")
    if poll_cfg.poll_sheet_id != sheet_ids.SHEET_PO_PENDING_REVIEW:
        problems.append(
            f"poll_sheet_id={poll_cfg.poll_sheet_id}, expected "
            f"SHEET_PO_PENDING_REVIEW={sheet_ids.SHEET_PO_PENDING_REVIEW}"
        )
    if poll_cfg.f22_workspace_id != sheet_ids.WORKSPACE_PURCHASE_ORDERS:
        problems.append(
            "poll f22_workspace_id is NOT the ITS — Purchase Orders workspace "
            f"(got {poll_cfg.f22_workspace_id}, expected {sheet_ids.WORKSPACE_PURCHASE_ORDERS})"
        )
    if po_review.STATUS_SENDING in poll_cfg.dispatch_statuses:
        problems.append("SENDING is a dispatch candidate — the no-double-send exclusion is broken")
    if problems:
        for problem in problems:
            print(f"  FAIL — {problem}")
        return 1
    print("  OK — workstream_tag='po_materials'; recipients ← ITS_Vendors; "
          "F22 ← ITS — Purchase Orders workspace; SENDING excluded")

    # ---- Stage 3: ITS_Config readable (PO-scoped) ----------------------
    stage(3, "ITS_Config keys (from_mailbox + poll_interval + scheduled_send_local)")
    from_mailbox = po_send.weekly_send._read_str_setting(
        po_send.CFG_FROM_MAILBOX, po_send.DEFAULT_FROM_MAILBOX, workstream=po_send.WORKSTREAM,
    )
    print(f"  OK — from_mailbox = {from_mailbox!r}")
    scheduled_spec = po_send_poll._read_str_setting(
        po_send_poll.CFG_SCHEDULED_SEND_LOCAL, po_send_poll.DEFAULT_SCHEDULED_SEND_LOCAL,
    )
    print(f"  OK — scheduled_send_local = {scheduled_spec!r}")
    poll_interval = po_send_poll._read_str_setting(
        po_send_poll.CFG_POLL_INTERVAL, str(po_send_poll.DEFAULT_POLL_INTERVAL),
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

    # ---- Stage 5: PO_Pending_Review schema check -----------------------
    stage(5, "PO_Pending_Review sheet reachable + expected columns present")
    try:
        rows = smartsheet_client.get_rows(po_review.SHEET_ID)
    except Exception as exc:  # noqa: BLE001
        print(f"  FAIL — get_rows raised: {exc!r}")
        return 1
    expected = {
        po_review.COL_JOB_PROJECT, po_review.COL_JOB_ID, po_review.COL_WEEK_OF,
        po_review.COL_COMPILED_PDF, po_review.COL_EMAIL_BODY, po_review.COL_APPROVE_SCHEDULED,
        po_review.COL_SEND_NOW, po_review.COL_SEND_STATUS, po_review.COL_SENT_AT,
        po_review.COL_NOTES, po_review.COL_WORKSTREAM,
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

    # ---- Stage 6: F22 approver authority (PO workspace, §46) ------------
    stage(6, "F22 approver set = ITS — Purchase Orders workspace membership (§46)")
    try:
        approvers = smartsheet_client.list_workspace_share_emails(
            sheet_ids.WORKSPACE_PURCHASE_ORDERS
        )
    except Exception as exc:  # noqa: BLE001
        print(f"  FAIL — list_workspace_share_emails raised: {exc!r}")
        return 1
    if approvers:
        print(f"  OK — {len(approvers)} approver(s) shared into the ITS — Purchase Orders workspace")
    else:
        print(
            "  WARN — EMPTY approver set: no individual shares on the ITS — Purchase Orders "
            "workspace. F22 fails CLOSED → every PO send is blocked (EMPTY_ALLOWLIST). Share "
            "each PO approver into the workspace before the live send (§46/D11)."
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
    stage(8, "po_send_poll filter on known-empty state")
    candidates = po_send_poll._filter_dispatch_candidates([])
    if candidates != []:
        print(f"  FAIL — filter on empty list returned {candidates!r}")
        return 1
    print("  OK — filter on empty list returns empty list")

    print("\nAll stages green.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
