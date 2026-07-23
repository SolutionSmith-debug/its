"""Build ITS_Active_Jobs_Progress — the progress workspace's own physical Active-Jobs sheet.

P2 / job-tracker pivot Slice 4 (the topology revision co-located with P2). The progress
workspace gets its OWN physical Active-Jobs sheet (NOT the shared ITS_Active_Jobs), so the
safety and progress workspaces are fed independently by one writer and can never drift.
Created in the "Control" folder of "ITS — Progress Reporting".

Mirrors ITS_Active_Jobs (build_its_active_jobs_sheet.py) — shared identity columns +
Stakeholder columns — with two deliberate changes:
  * the recipient columns are PROGRESS-family: "Progress Reports Contact Email/Name"
    (+ CC 1–5) instead of the safety contact;
  * a new "Portal Job Key" TEXT column — the bridge that carries the typed D1 `job_id`.
    It is the cross-sheet shared identity, the mirror daemon's crash-safe find-or-create
    idempotency key (Slice 5), and the downstream join key. (Operator-manual companion:
    add the SAME "Portal Job Key" TEXT column to the existing ITS_Active_Jobs — see the
    runbook; both columns must exist before the Slice-5 mirror daemon's first run.)

Job ID column type (§42 — why TEXT): plain TEXT_NUMBER on BOTH Active-Jobs sheets
since P2.5 Slice 6 — the portal assigns the canonical JOB-###### (Worker job_counter,
migration 0022) and shared/active_jobs_writer.py WRITES it into the cell on every mirror
upsert; an AUTO_NUMBER would reject those writes and assign a conflicting sequence.
(The original rationale here — safety-side AUTO_NUMBER with mirror read-back — was the
pre-Slice-6 design; the TEXT choice it argued for stands, the premise is superseded.)
The progress sheet remains a PURE DOWNSTREAM MIRROR (never read back into D1); rows are
find-or-created by Portal Job Key, not by Job ID.

Cutover sequence (FLIP precedes SEED):
  1. build_progress_reporting_workspace.py → flip WORKSPACE_PROGRESS_REPORTING.
  2. THIS script (find-or-creates the "Control" folder + the sheet) — note the printed ids.
  3. Flip FOLDER_PROGRESS_CONTROL + SHEET_ACTIVE_JOBS_PROGRESS in shared/sheet_ids.py.
     (Flipping SHEET_ACTIVE_JOBS_PROGRESS activates the picklist_validation REGISTRY entry
     for its "Active" column via the `if sheet_ids.SHEET_ACTIVE_JOBS_PROGRESS:` guard.)

Convention: LIVE-write by default; pass --dry-run to preview.
Auth: ITS_SMARTSHEET_TOKEN from macOS Keychain.

    python3 scripts/migrations/build_its_active_jobs_progress_sheet.py --dry-run
    python3 scripts/migrations/build_its_active_jobs_progress_sheet.py
"""
from __future__ import annotations

import argparse
import sys
from typing import Any

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[2]))

from shared import sheet_ids, smartsheet_client  # noqa: E402

WORKSPACE = sheet_ids.WORKSPACE_PROGRESS_REPORTING
FOLDER_NAME = "Control"
SHEET_NAME = "ITS_Active_Jobs_Progress"

ACTIVE_OPTIONS = ["Active", "Inactive", "Archived"]

COLUMN_SCHEMA: list[dict[str, Any]] = [
    {"title": "Project Name", "type": "TEXT_NUMBER", "primary": True,
     "description": "Portal dropdown display; == ITS_Project_Routing Project Name."},
    {"title": "Job ID", "type": "TEXT_NUMBER",
     "description": ("The canonical job key (JOB-####), MIRRORED from the safety "
                     "ITS_Active_Jobs by the Slice-5 daemon so both sheets show the same "
                     "id. TEXT (not AUTO_NUMBER) because this sheet is a pure downstream "
                     "mirror keyed by Portal Job Key, not Job ID.")},
    {"title": "Portal Job Key", "type": "TEXT_NUMBER",
     "description": ("The typed D1 portal job_id. Cross-sheet shared identity + the "
                     "mirror daemon's crash-safe find-or-create idempotency key + the "
                     "downstream join bridge. The SAME column is added to ITS_Active_Jobs.")},
    {"title": "Address", "type": "TEXT_NUMBER",
     "description": "Full street address; office-PM-sourced (a wrong address is worse than a blank one)."},
    {"title": "Stakeholder Name", "type": "TEXT_NUMBER"},
    {"title": "Stakeholder Email", "type": "TEXT_NUMBER",
     "description": "Fallback TO recipient when Progress Reports Contact Email is blank (progress recipient resolver, P5)."},
    {"title": "Stakeholder Phone", "type": "TEXT_NUMBER"},
    {"title": "Progress Reports Contact Email", "type": "TEXT_NUMBER",
     "description": "The weekly progress-report TO recipient. Resolved at send time (P5)."},
    {"title": "Progress Reports Contact Name", "type": "TEXT_NUMBER",
     "description": "Greeting target on the weekly progress email."},
    {"title": "CC 1", "type": "TEXT_NUMBER"},
    {"title": "CC 2", "type": "TEXT_NUMBER"},
    {"title": "CC 3", "type": "TEXT_NUMBER"},
    {"title": "CC 4", "type": "TEXT_NUMBER"},
    {"title": "CC 5", "type": "TEXT_NUMBER",
     "description": "CC recipients (one email per slot, or comma-separated; progress_send flattens + de-dups). TEXT, not CONTACT_LIST — external emails survive API read-back (operator decision 2026-06-05)."},
    {"title": "Active", "type": "PICKLIST", "options": ACTIVE_OPTIONS,
     "description": "Lifecycle (Active / Inactive / Archived). Only 'Active' rows feed the progress send."},
    {"title": "Notes", "type": "TEXT_NUMBER"},
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
        print(f"[bootstrap] shared/sheet_ids.py:\n    SHEET_ACTIVE_JOBS_PROGRESS = {existing_id}")
        return "exists", existing_id

    if dry_run:
        print(f"[dry-run] Would create sheet {SHEET_NAME!r} in folder {folder_id} with "
              f"columns: {[c['title'] for c in COLUMN_SCHEMA]}.")
        return "dry-run", None

    new_id = smartsheet_client.create_sheet_in_folder(folder_id, SHEET_NAME, COLUMN_SCHEMA)
    print(f"[ok] created {SHEET_NAME!r} in folder {folder_id} (sheet_id={new_id}).")
    print(f"[bootstrap] Update shared/sheet_ids.py:\n    SHEET_ACTIVE_JOBS_PROGRESS = {new_id}")
    print("[reminder] Operator-manual: add a 'Portal Job Key' TEXT column to the EXISTING "
          "ITS_Active_Jobs too (both columns must exist before the Slice-5 mirror daemon runs).")
    return "created", new_id


def main() -> int:
    parser = argparse.ArgumentParser(description="Build ITS_Active_Jobs_Progress (progress workspace Active-Jobs sheet).")
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
