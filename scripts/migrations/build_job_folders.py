"""Build the "Jobs" parent folders — per-job tracking areas for subcontracts + POs (Feature A).

Creates one top-level "Jobs" folder in each of the two workstream workspaces:

    ITS — Subcontracts     / Jobs   → shared/sheet_ids.py FOLDER_SC_JOBS
    ITS — Purchase Orders  / Jobs   → shared/sheet_ids.py FOLDER_PO_JOBS

Each "Jobs" folder is the parent of the DYNAMIC per-job tracking folders that
`shared/job_sheet.ensure_job_sheet` find-or-creates at filing time (folder named by
`safety_naming.job_folder_name(job_name)` — the SAME name as the per-job Box folder —
holding a "Subcontracts" / "Purchase Orders" sheet cloned structure-only from the flat
Log). The flat Logs in each workspace's Control folder STAY the ledger SoR mirror of D1;
the per-job sheets are supplementary operator visibility.

First run 2026-07-13 (live mirror) created:

    FOLDER_SC_JOBS = 2979676269373316
    FOLDER_PO_JOBS = 8609175803586436

Idempotent: find-or-creates each folder by name and skips if already present, so a
re-run prints the existing IDs instead of duplicating (FLIP precedes SEED — flip any
new printed ID into shared/sheet_ids.py before relying on the constants).

Prereq: build_subcontracts_workspace.py + build_purchase_orders_workspace.py have been
run and WORKSPACE_SUBCONTRACTS / WORKSPACE_PURCHASE_ORDERS flipped in shared/sheet_ids.py.

    python3 scripts/migrations/build_job_folders.py --dry-run
    python3 scripts/migrations/build_job_folders.py
"""
from __future__ import annotations

import argparse
import sys

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[2]))

from shared import sheet_ids, smartsheet_client  # noqa: E402

FOLDER_NAME = "Jobs"

# (label, workspace_id, sheet_ids constant name) per target workspace.
TARGETS: list[tuple[str, int, str]] = [
    ("ITS — Subcontracts", sheet_ids.WORKSPACE_SUBCONTRACTS, "FOLDER_SC_JOBS"),
    ("ITS — Purchase Orders", sheet_ids.WORKSPACE_PURCHASE_ORDERS, "FOLDER_PO_JOBS"),
]


def ensure_jobs_folder(
    label: str, workspace_id: int, constant_name: str, *, dry_run: bool
) -> int | None:
    """Find-or-create the "Jobs" folder in one workspace. Idempotent."""
    if not workspace_id:
        print(f"[error] workspace id for {label!r} is still 0 in shared/sheet_ids.py.\n"
              f"        Run its build_*_workspace.py first and flip the printed id.",
              file=sys.stderr)
        raise SystemExit(2)
    existing = smartsheet_client.find_folder_by_name_in_workspace(workspace_id, FOLDER_NAME)
    if existing is not None:
        print(f"[skip] {label}: folder {FOLDER_NAME!r} already present (folder_id={existing}).")
        print(f"[bootstrap] shared/sheet_ids.py:\n    {constant_name} = {existing}")
        return existing
    if dry_run:
        print(f"[dry-run] Would create folder {FOLDER_NAME!r} in {label} (workspace {workspace_id}).")
        return None
    new_id = smartsheet_client.create_folder_in_workspace(workspace_id, FOLDER_NAME)
    print(f"[ok] {label}: created folder {FOLDER_NAME!r} (folder_id={new_id}).")
    print(f"[bootstrap] Update shared/sheet_ids.py:\n    {constant_name} = {new_id}")
    return new_id


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build the per-job 'Jobs' parent folders (subcontracts + purchase orders)."
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    print(f"[info] Folder = {FOLDER_NAME!r} in {len(TARGETS)} workspaces")
    print(f"[info] Mode: {'DRY-RUN' if args.dry_run else 'LIVE WRITE'}\n")

    results: list[tuple[str, int | None]] = []
    for label, workspace_id, constant_name in TARGETS:
        results.append(
            (label, ensure_jobs_folder(label, workspace_id, constant_name, dry_run=args.dry_run))
        )

    print("\nSummary:")
    for label, folder_id in results:
        print(f"  {label} / {FOLDER_NAME}: id={folder_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
