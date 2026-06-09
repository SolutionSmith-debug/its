"""One-shot migration: create the Orphaned Reports sheet (Part C).

A portal submission whose Job ID is unknown (`job_not_found`) or not Active
(`job_inactive`) cannot be filed to a job's week sheet. Today it routes to
`ITS_Review_Queue` (a generic queue). Part C gives those JOB-orphans a dedicated
destination — this sheet — so the operator can re-home (re-file to a live job) or
discard them without them drowning the general Review Queue. (A `no_job_id` /
malformed submission is NOT an orphan-of-a-known-job and stays in ITS_Review_Queue.)

Creates (find-or-create, idempotent) the "Orphaned Reports" SHEET inside the existing
ITS –– Safety Portal folder (`sheet_ids.FOLDER_SAFETY_PORTAL`, alongside ITS_Active_Jobs
/ WSR_human_review). One row per orphaned submission.

Schema:
  Submission UUID  TEXT_NUMBER  (primary; the dedupe key + Box filename suffix)
  Job ID           TEXT_NUMBER  (the unresolved job key)
  Form Code        TEXT_NUMBER
  Work Date        DATE
  Submitted At     TEXT_NUMBER  (Pacific ISO)
  Actor            TEXT_NUMBER  (who submitted — submission actor_username)
  Submitted As     TEXT_NUMBER  (admin attribution, if any)
  Reason           PICKLIST     (job_not_found / job_inactive)
  Box Link         TEXT_NUMBER  (the rendered PDF, filed to the Orphaned Reports Box folder)
  Status           PICKLIST     (Pending / Re-homed / Discarded)
  Notes            TEXT_NUMBER  (operator free text + machine notes)

Cutover sequence (FLIP precedes USE — intake reads SHEET_ORPHANED_REPORTS; 0 = OFF):
  1. THIS script (build the sheet); note the printed ID.
  2. Flip SHEET_ORPHANED_REPORTS in shared/sheet_ids.py to that ID.
  3. Deploy ~/its (the running intake daemon picks it up) → orphans now route here.

Auth: ITS_SMARTSHEET_TOKEN from macOS Keychain. LIVE-write by default; --dry-run previews.

Run from ~/its:
    python3 scripts/migrations/build_orphaned_reports_sheet.py --dry-run
    python3 scripts/migrations/build_orphaned_reports_sheet.py

Exit 0 on success or no-op; nonzero on any error.
"""
from __future__ import annotations

import argparse
import sys
from typing import Any

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[2]))

from shared import sheet_ids, smartsheet_client  # noqa: E402

FOLDER_ID = sheet_ids.FOLDER_SAFETY_PORTAL  # ITS –– Safety Portal folder (existing)
SHEET_NAME = "Orphaned Reports"

REASON_OPTIONS = ["job_not_found", "job_inactive"]
STATUS_OPTIONS = ["Pending", "Re-homed", "Discarded"]

COLUMN_SCHEMA: list[dict[str, Any]] = [
    {"title": "Submission UUID", "type": "TEXT_NUMBER", "primary": True},
    {"title": "Job ID", "type": "TEXT_NUMBER"},
    {"title": "Form Code", "type": "TEXT_NUMBER"},
    {"title": "Work Date", "type": "DATE"},
    {"title": "Submitted At", "type": "TEXT_NUMBER"},
    {"title": "Actor", "type": "TEXT_NUMBER"},
    {"title": "Submitted As", "type": "TEXT_NUMBER"},
    {"title": "Reason", "type": "PICKLIST", "options": REASON_OPTIONS},
    {"title": "Box Link", "type": "TEXT_NUMBER"},
    {
        "title": "Status", "type": "PICKLIST", "options": STATUS_OPTIONS,
        "description": "Pending → operator triages; Re-homed → re-filed to a live job; Discarded.",
    },
    {"title": "Notes", "type": "TEXT_NUMBER"},
]


def build_orphaned_reports_sheet(*, dry_run: bool) -> tuple[str, int | None]:
    """Create "Orphaned Reports" in the Safety Portal folder. Idempotent.

    Returns (status, sheet_id) where status is "created", "exists", or "dry-run".
    """
    existing_id = smartsheet_client.find_sheet_by_name_in_folder(FOLDER_ID, SHEET_NAME)
    if existing_id is not None:
        print(f"[skip] sheet {SHEET_NAME!r} already present (sheet_id={existing_id}).")
        return "exists", existing_id

    if dry_run:
        print(
            f"[dry-run] Would create sheet {SHEET_NAME!r} in folder {FOLDER_ID} with "
            f"columns: {[c['title'] for c in COLUMN_SCHEMA]}."
        )
        return "dry-run", None

    new_sheet_id = smartsheet_client.create_sheet_in_folder(FOLDER_ID, SHEET_NAME, COLUMN_SCHEMA)
    print(f"[ok] created sheet {SHEET_NAME!r} in folder {FOLDER_ID} (sheet_id={new_sheet_id}).")
    print(f"[bootstrap] Update shared/sheet_ids.py:\n    SHEET_ORPHANED_REPORTS = {new_sheet_id}")
    return "created", new_sheet_id


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the Orphaned Reports sheet (Part C).")
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Preview the sheet that would be created without writing.",
    )
    args = parser.parse_args()

    print(f"[info] Folder ITS –– Safety Portal = {FOLDER_ID}")
    print(f"[info] Sheet = {SHEET_NAME!r}")
    print(f"[info] Mode: {'DRY-RUN' if args.dry_run else 'LIVE WRITE'}")
    print()

    status, sheet_id = build_orphaned_reports_sheet(dry_run=args.dry_run)

    print()
    print("Summary:")
    print(f"  {SHEET_NAME}: {status} (id={sheet_id})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
