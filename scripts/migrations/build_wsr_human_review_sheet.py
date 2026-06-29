"""Build WSR_human_review — the Phase-5 weekly review/approve/send surface.

Creates the central review sheet in the standalone "ITS –– Safety Portal" workspace's
"Safety Portal" folder (FOLDER id 6663869084002180; amendment b). This is NOT the old
WPR_Pending_Review (which lives in ITS — Human Review) — it supersedes it for the
portal safety flow.

One row per (Job, Week). weekly_generate dual-writes the rollup here (the editable
Email Body is THE source of truth weekly_send reads); a human flips "Approve for
Scheduled Send" (Smartsheet MODIFIED_BY auto-captures the approver — the F22 gate
verifies that actor is authorized; "auto-stamped" = identity recorded, NOT auto-send).
The compiled weekly PDF also attaches to the row for one-click review.

Schema (mirrors WPR_Pending_Review + the amendment-b additions):
    Job / Project (primary) · Job ID (AUTO_NUMBER join key → active_jobs TO/CC) ·
    Week Of (Saturday) · Compiled PDF (Box link; PDF also attached) ·
    Email Body (editable, source of truth for send) · Recipient TO · CC (display) ·
    Approve for Scheduled Send · Send Now · Approved By/At · Send Status · Sent At · Notes

Idempotent: skips if a sheet named WSR_human_review already exists in the folder.

    python3 scripts/migrations/build_wsr_human_review_sheet.py --dry-run
    python3 scripts/migrations/build_wsr_human_review_sheet.py
"""
from __future__ import annotations

import argparse
import sys
from typing import Any

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[2]))

import requests  # type: ignore[import-untyped]  # noqa: E402

from shared import keychain, smartsheet_client  # noqa: E402

FOLDER_SAFETY_PORTAL = 6663869084002180  # ITS –– Safety Portal / Safety Portal
SHEET_NAME = "WSR_human_review"
# Lifecycle: PENDING → SENDING (write-ahead marker, weekly_send Stage 6) → SENT;
# FAILED (retryable) / HELD (operator hold) are off-path. SENDING is a transient
# in-flight state the poller never dispatches on (weekly_send_poll.DISPATCH_STATUSES).
SEND_STATUS_OPTIONS = ["PENDING", "SENDING", "SENT", "FAILED", "HELD"]
WORKSTREAM_OPTIONS = ["safety"]  # P1b cross-workstream send guard; WSR is the safety review sheet

COLUMN_SCHEMA: list[dict[str, Any]] = [
    {"title": "Job / Project", "type": "TEXT_NUMBER", "primary": True},
    {"title": "Job ID", "type": "TEXT_NUMBER",
     "description": "The ITS_Active_Jobs AUTO_NUMBER Job ID — the join key weekly_send uses to resolve TO/CC."},
    {"title": "Week Of", "type": "DATE", "description": "The Saturday that starts the Sat→Fri week (shared.safety_week.week_key)."},
    {"title": "Compiled PDF", "type": "TEXT_NUMBER",
     "description": "Box link to the compiled weekly packet. The PDF also attaches to this row for one-click review."},
    {"title": "Email Body", "type": "TEXT_NUMBER",
     "description": "Editable body — THE source of truth weekly_send transmits. The reviewer may edit before approving."},
    {"title": "Recipient TO", "type": "TEXT_NUMBER", "description": "Display of the resolved safety-reports contact (TO). Authoritative source is active_jobs at send time."},
    {"title": "CC", "type": "TEXT_NUMBER", "description": "Display of the resolved CC 1–5 list. Authoritative source is active_jobs at send time."},
    {"title": "Approve for Scheduled Send", "type": "CHECKBOX",
     "description": "Human approval gate. A person flips this; MODIFIED_BY auto-captures who; F22 verifies that actor is authorized before the Monday send."},
    {"title": "Send Now", "type": "CHECKBOX", "description": "Approve + dispatch immediately (out-of-band of the scheduled Monday send)."},
    {"title": "Approved By", "type": "CONTACT_LIST", "description": "Auto-stamped approver identity (the send daemon records the cell-history actor of the approve flip)."},
    {"title": "Approved At", "type": "ABSTRACT_DATETIME",
     "description": "Naive Pacific wall-clock (ABSTRACT_DATETIME — the user Date/Time type; plain DATETIME is not creatable on a user column). Written via wsr_review.to_wsr_datetime."},
    {"title": "Send Status", "type": "PICKLIST", "options": SEND_STATUS_OPTIONS},
    {"title": "Sent At", "type": "ABSTRACT_DATETIME",
     "description": "Naive Pacific wall-clock (ABSTRACT_DATETIME). Written via wsr_review.to_wsr_datetime."},
    {"title": "Notes", "type": "TEXT_NUMBER", "description": "Retry state / late-send flags / failure context."},
    {"title": "Workstream", "type": "PICKLIST", "options": WORKSTREAM_OPTIONS,
     "description": "Report-family tag (P1b cross-workstream send guard). WSR is the safety sheet → 'safety'."},
    {"title": "Last Modified", "type": "DATETIME", "systemColumnType": "MODIFIED_DATE"},
    {"title": "Modified By", "type": "CONTACT_LIST", "systemColumnType": "MODIFIED_BY"},
]


def _existing_sheet_id() -> int | None:
    token = keychain.get_secret("ITS_SMARTSHEET_TOKEN")
    r = requests.get(f"https://api.smartsheet.com/2.0/folders/{FOLDER_SAFETY_PORTAL}",
                     headers={"Authorization": f"Bearer {token}"}, timeout=30)
    r.raise_for_status()
    for s in r.json().get("sheets", []):
        if s.get("name") == SHEET_NAME:
            return int(s["id"])
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description="Build WSR_human_review (Phase-5 review surface).")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    print(f"[info] Target folder: ITS –– Safety Portal / Safety Portal ({FOLDER_SAFETY_PORTAL})")
    print(f"[info] Mode: {'DRY-RUN' if args.dry_run else 'LIVE WRITE'}\n")

    existing = _existing_sheet_id()
    if existing is not None:
        print(f"[skip] {SHEET_NAME!r} already exists (sheet_id={existing}).")
        print(f"[bootstrap] shared/sheet_ids.py:\n    SHEET_WSR_HUMAN_REVIEW = {existing}")
        return 0

    if args.dry_run:
        print(f"[dry-run] Would create {SHEET_NAME!r} with columns: {[c['title'] for c in COLUMN_SCHEMA]}.")
        return 0

    new_id = smartsheet_client.create_sheet_in_folder(FOLDER_SAFETY_PORTAL, SHEET_NAME, COLUMN_SCHEMA)
    print(f"[ok] created {SHEET_NAME!r} (sheet_id={new_id}).")
    print(f"[bootstrap] Update shared/sheet_ids.py:\n    SHEET_WSR_HUMAN_REVIEW = {new_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
