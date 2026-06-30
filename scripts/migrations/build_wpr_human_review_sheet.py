"""Build WPR_human_review — the weekly PROGRESS review/approve/send surface.

P2 of the Progress Reporting program. The structural twin of WSR_human_review
(scripts/migrations/build_wsr_human_review_sheet.py) — IDENTICAL schema, except the
Workstream tag is the report family 'progress' (the P1b cross-workstream send guard
HARD-HELDs a row whose Workstream != the sending workstream, so a 'safety' row here
or a 'progress' row on WSR is a contamination signal). Created in the "Control"
folder of the "ITS — Progress Reporting" workspace.

NOTE: distinct from the DECOMMISSIONED WPR_Pending_Review (SHEET_WPR_PENDING_REVIEW,
ITS — Human Review) — that one is retired; this is the new progress send surface.

One row per (Job, Week). progress_weekly_generate (P4) dual-writes the rollup here
(the editable Email Body is THE source of truth progress_send (P5) reads); a human
flips "Approve for Scheduled Send" (Smartsheet MODIFIED_BY auto-captures the approver
— the §46 workspace-membership predicate verifies that actor is authorized before the
send; "auto-stamped" = identity recorded, NOT auto-send). The compiled weekly PDF
also attaches to the row for one-click review.

Idempotent: find-or-creates the "Control" folder by name (order-independent with
build_its_active_jobs_progress_sheet.py + build_progress_reporting_workspace.py) and
skips if a sheet named WPR_human_review already exists in it.

Prereq: build_progress_reporting_workspace.py has been run and
WORKSPACE_PROGRESS_REPORTING flipped in shared/sheet_ids.py.

    python3 scripts/migrations/build_wpr_human_review_sheet.py --dry-run
    python3 scripts/migrations/build_wpr_human_review_sheet.py
"""
from __future__ import annotations

import argparse
import sys
from typing import Any

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[2]))

from shared import sheet_ids, smartsheet_client  # noqa: E402

WORKSPACE = sheet_ids.WORKSPACE_PROGRESS_REPORTING
FOLDER_NAME = "Control"
SHEET_NAME = "WPR_human_review"
# Lifecycle mirrors WSR: PENDING → SENDING (write-ahead marker) → SENT; FAILED
# (retryable) / HELD (operator hold) off-path. SENDING is the transient in-flight
# state the poller never dispatches on (send_poll_core.DaemonConfig.dispatch_statuses).
SEND_STATUS_OPTIONS = ["PENDING", "SENDING", "SENT", "FAILED", "HELD"]
WORKSTREAM_OPTIONS = ["progress"]  # P1b cross-workstream send guard; WPR is the PROGRESS review sheet

COLUMN_SCHEMA: list[dict[str, Any]] = [
    {"title": "Job / Project", "type": "TEXT_NUMBER", "primary": True},
    {"title": "Job ID", "type": "TEXT_NUMBER",
     "description": "The ITS_Active_Jobs_Progress Job ID — the join key progress_send uses to resolve TO/CC."},
    {"title": "Week Of", "type": "DATE", "description": "The Saturday that starts the Sat→Fri week (shared.safety_week.week_key)."},
    {"title": "Compiled PDF", "type": "TEXT_NUMBER",
     "description": "Box link to the compiled weekly progress packet. The PDF also attaches to this row for one-click review."},
    {"title": "Email Body", "type": "TEXT_NUMBER",
     "description": "Editable body — THE source of truth progress_send transmits. The reviewer may edit before approving."},
    {"title": "Recipient TO", "type": "TEXT_NUMBER", "description": "Display of the resolved progress-reports contact (TO). Authoritative source is active_jobs_progress at send time."},
    {"title": "CC", "type": "TEXT_NUMBER", "description": "Display of the resolved CC 1–5 list. Authoritative source is active_jobs_progress at send time."},
    {"title": "Approve for Scheduled Send", "type": "CHECKBOX",
     "description": "Human approval gate. A person flips this; MODIFIED_BY auto-captures who; §46 workspace-membership verifies that actor is authorized before the Monday send."},
    {"title": "Send Now", "type": "CHECKBOX", "description": "Approve + dispatch immediately (out-of-band of the scheduled Monday send)."},
    {"title": "Approved By", "type": "CONTACT_LIST", "description": "Auto-stamped approver identity (the send daemon records the cell-history actor of the approve flip)."},
    # DATE (not ABSTRACT_DATETIME): matches the LIVE WSR_human_review schema (verified
    # 2026-06-29 — WSR's Approved At/Sent At are type=DATE). ABSTRACT_DATETIME is NOT
    # creatable via the API (errorCode 1142, "reserved for project sheets"); the WSR
    # builder's ABSTRACT_DATETIME schema is a latent bug masked by idempotency (see
    # docs/tech_debt.md). wpr_review.to_wsr_datetime writes a naive Pacific string, which
    # the live WSR DATE columns accept end-to-end — so DATE is exact parity.
    {"title": "Approved At", "type": "DATE",
     "description": "Approval date (DATE — mirrors the live WSR schema). Written via wpr_review.to_wsr_datetime (naive Pacific)."},
    {"title": "Send Status", "type": "PICKLIST", "options": SEND_STATUS_OPTIONS},
    {"title": "Sent At", "type": "DATE",
     "description": "Send date (DATE — mirrors the live WSR schema). Written via wpr_review.to_wsr_datetime (naive Pacific)."},
    {"title": "Notes", "type": "TEXT_NUMBER", "description": "Retry state / late-send flags / failure context."},
    {"title": "Workstream", "type": "PICKLIST", "options": WORKSTREAM_OPTIONS,
     "description": "Report-family tag (P1b cross-workstream send guard). WPR is the progress sheet → 'progress'."},
    {"title": "Last Modified", "type": "DATETIME", "systemColumnType": "MODIFIED_DATE"},
    {"title": "Modified By", "type": "CONTACT_LIST", "systemColumnType": "MODIFIED_BY"},
]


def _require_workspace() -> int:
    if not WORKSPACE:
        print("[error] WORKSPACE_PROGRESS_REPORTING is still 0 in shared/sheet_ids.py.\n"
              "        Run build_progress_reporting_workspace.py first and flip the printed id.",
              file=sys.stderr)
        raise SystemExit(2)
    return WORKSPACE


def ensure_control_folder(workspace_id: int, *, dry_run: bool) -> int | None:
    """Find-or-create the "Control" folder. Idempotent + order-independent."""
    existing = smartsheet_client.find_folder_by_name_in_workspace(workspace_id, FOLDER_NAME)
    if existing is not None:
        print(f"[skip] folder {FOLDER_NAME!r} already present (folder_id={existing}).")
        return existing
    if dry_run:
        print(f"[dry-run] Would create folder {FOLDER_NAME!r} in workspace {workspace_id}.")
        return None
    new_id = smartsheet_client.create_folder_in_workspace(workspace_id, FOLDER_NAME)
    print(f"[ok] created folder {FOLDER_NAME!r} (folder_id={new_id}).")
    print(f"[bootstrap] Update shared/sheet_ids.py:\n    FOLDER_PROGRESS_CONTROL = {new_id}")
    return new_id


def build_sheet(*, dry_run: bool) -> tuple[str, int | None]:
    workspace_id = _require_workspace()
    folder_id = ensure_control_folder(workspace_id, dry_run=dry_run)
    if folder_id is None:
        print(f"[dry-run] Would create sheet {SHEET_NAME!r} with columns: {[c['title'] for c in COLUMN_SCHEMA]}.")
        return "dry-run", None

    existing_id = smartsheet_client.find_sheet_by_name_in_folder(folder_id, SHEET_NAME)
    if existing_id is not None:
        print(f"[skip] sheet {SHEET_NAME!r} already present (sheet_id={existing_id}).")
        print(f"[bootstrap] shared/sheet_ids.py:\n    SHEET_WPR_HUMAN_REVIEW = {existing_id}")
        return "exists", existing_id

    if dry_run:
        print(f"[dry-run] Would create sheet {SHEET_NAME!r} in folder {folder_id} with "
              f"columns: {[c['title'] for c in COLUMN_SCHEMA]}.")
        return "dry-run", None

    new_id = smartsheet_client.create_sheet_in_folder(folder_id, SHEET_NAME, COLUMN_SCHEMA)
    print(f"[ok] created {SHEET_NAME!r} in folder {folder_id} (sheet_id={new_id}).")
    print(f"[bootstrap] Update shared/sheet_ids.py:\n    SHEET_WPR_HUMAN_REVIEW = {new_id}")
    return "created", new_id


def main() -> int:
    parser = argparse.ArgumentParser(description="Build WPR_human_review (weekly progress review surface).")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    print(f"[info] Workspace ITS — Progress Reporting = {WORKSPACE}")
    print(f"[info] Folder = {FOLDER_NAME!r} | Sheet = {SHEET_NAME!r}")
    print(f"[info] Mode: {'DRY-RUN' if args.dry_run else 'LIVE WRITE'}\n")

    status, sheet_id = build_sheet(dry_run=args.dry_run)
    print(f"\nSummary:\n  {SHEET_NAME}: {status} (id={sheet_id})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
