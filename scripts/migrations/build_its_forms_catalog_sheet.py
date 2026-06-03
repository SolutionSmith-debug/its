"""One-shot migration: create ITS_Forms_Catalog under ITS — Operations / Safety Portal.

Safety-Portal prerequisite (blueprint workstreams/safety-portal/brief.md §3, Q6).
ITS_Forms_Catalog is the office-PM/operator-maintained list of forms the portal
offers, optionally job-scoped. Built here so the portal (Phase 4) can read a
stable sheet ID; nothing reads it until the portal is built.

Creates (find-or-create, idempotent):
  1. The shared "Safety Portal" FOLDER under ITS — Operations (WORKSPACE_OPERATIONS),
     if absent. build_its_active_jobs_sheet.py find-or-creates the SAME folder by
     name, so the two build scripts are order-independent.
  2. The ITS_Forms_Catalog SHEET inside that folder.

Schema (Q6: Form Version column DROPPED — versioning is implicit in the form_code
directory name, jha-v1 -> jha-v2 are two distinct rows; Available For Jobs ADDED):
  Form Name          TEXT_NUMBER  (primary; portal picker display)
  Form Code          TEXT_NUMBER  (stable key matching the code form.ts directory EXACTLY)
  Active             PICKLIST     (Active / Inactive / Archived; only Active is offered)
  Description        TEXT_NUMBER  (tooltip text)
  Display Order      TEXT_NUMBER  (sort key)
  Available For Jobs TEXT_NUMBER  (CSV of Job IDs, OR empty = available on all jobs)
  Last Modified      DATETIME     (system MODIFIED_DATE)
  Modified By        CONTACT_LIST (system MODIFIED_BY)

Cutover sequence (FLIP precedes SEED — seed_its_forms_catalog.py reads SHEET_FORMS_CATALOG):
  1. THIS script (build the folder + sheet); note the printed IDs.
  2. Flip SHEET_FORMS_CATALOG (and FOLDER_OPERATIONS_SAFETY_PORTAL) in shared/sheet_ids.py.
  3. seed_its_forms_catalog.py (populate the 4 locked v1 forms).
  4. Verify, then rely on the sheet.

Convention: LIVE-write by default; pass --dry-run to preview (matches the
seed_its_project_routing.py / build_its_trusted_contacts_sheet.py migration family).

Auth: ITS_SMARTSHEET_TOKEN from macOS Keychain.

Run from ~/its:
    python3 scripts/migrations/build_its_forms_catalog_sheet.py --dry-run
    python3 scripts/migrations/build_its_forms_catalog_sheet.py

Exit 0 on success or no-op; nonzero on any error.
"""
from __future__ import annotations

import argparse
import sys
from typing import Any

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[2]))

from shared import sheet_ids, smartsheet_client  # noqa: E402

WORKSPACE = sheet_ids.WORKSPACE_OPERATIONS  # 7217130472007556 (ITS — Operations)
FOLDER_NAME = "Safety Portal"
SHEET_NAME = "ITS_Forms_Catalog"

ACTIVE_OPTIONS = ["Active", "Inactive", "Archived"]

COLUMN_SCHEMA: list[dict[str, Any]] = [
    {"title": "Form Name", "type": "TEXT_NUMBER", "primary": True},
    {
        "title": "Form Code",
        "type": "TEXT_NUMBER",
        "description": (
            "Stable key matching the code form.ts directory EXACTLY (e.g. "
            "'jha-v1'). A versioned form (jha-v1 -> jha-v2) is a distinct row. "
            "This is the rendering contract — drift breaks the portal."
        ),
    },
    {
        "title": "Active",
        "type": "PICKLIST",
        "options": ACTIVE_OPTIONS,
        "description": "Only 'Active' rows are offered in the portal form picker.",
    },
    {"title": "Description", "type": "TEXT_NUMBER", "description": "Tooltip text in the picker."},
    {"title": "Display Order", "type": "TEXT_NUMBER", "description": "Ascending sort key for the picker."},
    {
        "title": "Available For Jobs",
        "type": "TEXT_NUMBER",
        "description": (
            "CSV of Job IDs this form is offered on, OR empty (= available on "
            "all jobs). E.g. 'bradley-1,bradley-2' for a job-scoped variant."
        ),
    },
    {"title": "Last Modified", "type": "DATETIME", "systemColumnType": "MODIFIED_DATE"},
    {"title": "Modified By", "type": "CONTACT_LIST", "systemColumnType": "MODIFIED_BY"},
]


def ensure_safety_portal_folder(*, dry_run: bool) -> int | None:
    """Find-or-create the "Safety Portal" folder under ITS — Operations.

    Idempotent + order-independent: build_its_active_jobs_sheet.py
    find-or-creates the SAME folder by name, so either build may run first.
    Returns the folder ID, or None on a dry-run where the folder doesn't exist
    yet (nothing for the sheet step to preview-create against).
    """
    existing = smartsheet_client.find_folder_by_name_in_workspace(WORKSPACE, FOLDER_NAME)
    if existing is not None:
        print(f"[skip] folder {FOLDER_NAME!r} already present (folder_id={existing}).")
        return existing
    if dry_run:
        print(f"[dry-run] Would create folder {FOLDER_NAME!r} in workspace {WORKSPACE}.")
        return None
    new_id = smartsheet_client.create_folder_in_workspace(WORKSPACE, FOLDER_NAME)
    print(f"[ok] created folder {FOLDER_NAME!r} (folder_id={new_id}).")
    print(
        f"[bootstrap] Update shared/sheet_ids.py:\n"
        f"    FOLDER_OPERATIONS_SAFETY_PORTAL = {new_id}"
    )
    return new_id


def build_forms_catalog_sheet(*, dry_run: bool) -> tuple[str, int | None]:
    """Create ITS_Forms_Catalog in the Safety Portal folder. Idempotent.

    Returns (status, sheet_id) where status is "created", "exists", or "dry-run".
    """
    folder_id = ensure_safety_portal_folder(dry_run=dry_run)
    if folder_id is None:
        print(
            f"[dry-run] Would create sheet {SHEET_NAME!r} in the new folder with "
            f"columns: {[c['title'] for c in COLUMN_SCHEMA]}."
        )
        return "dry-run", None

    existing_id = smartsheet_client.find_sheet_by_name_in_folder(folder_id, SHEET_NAME)
    if existing_id is not None:
        print(f"[skip] sheet {SHEET_NAME!r} already present (sheet_id={existing_id}).")
        return "exists", existing_id

    if dry_run:
        print(
            f"[dry-run] Would create sheet {SHEET_NAME!r} in folder {folder_id} with "
            f"columns: {[c['title'] for c in COLUMN_SCHEMA]}."
        )
        return "dry-run", None

    new_sheet_id = smartsheet_client.create_sheet_in_folder(folder_id, SHEET_NAME, COLUMN_SCHEMA)
    print(f"[ok] created sheet {SHEET_NAME!r} in folder {folder_id} (sheet_id={new_sheet_id}).")
    print(f"[bootstrap] Update shared/sheet_ids.py:\n    SHEET_FORMS_CATALOG = {new_sheet_id}")
    return "created", new_sheet_id


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build ITS_Forms_Catalog (Safety Portal prerequisite)."
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Preview the folder/sheet that would be created without writing.",
    )
    args = parser.parse_args()

    print(f"[info] Workspace ITS — Operations = {WORKSPACE}")
    print(f"[info] Folder = {FOLDER_NAME!r} | Sheet = {SHEET_NAME!r}")
    print(f"[info] Mode: {'DRY-RUN' if args.dry_run else 'LIVE WRITE'}")
    print()

    status, sheet_id = build_forms_catalog_sheet(dry_run=args.dry_run)

    print()
    print("Summary:")
    print(f"  {SHEET_NAME}: {status} (id={sheet_id})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
