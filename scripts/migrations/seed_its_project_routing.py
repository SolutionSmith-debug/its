"""One-shot migration: seed ITS_Project_Routing from the hardcoded
`shared.defaults.BOX_PROJECT_FOLDERS` dict (E1).

Cutover companion to the project-routing cluster:
  1. `build_its_project_routing_sheet.py` (one-time, builds the sheet).
  2. THIS script (one-time, populates from BOX_PROJECT_FOLDERS).
  3. Operator updates `SHEET_PROJECT_ROUTING` in `shared/sheet_ids.py`, then
     verifies parity (every project resolves the same folder ID via the sheet
     as it did via the dict) before relying on the sheet.

For each (project, folder_id) in BOX_PROJECT_FOLDERS this creates one
ITS_Project_Routing row: Project Name, Box Folder ID, Active=true, Notes
("Seeded from shared.defaults.BOX_PROJECT_FOLDERS on YYYY-MM-DD").

Idempotency: rows are matched by Project Name (exact); existing rows are
skipped, not overwritten. Project names + Box folder IDs are NOT PII, so both
dry-run and live print them.

Auth: ITS_SMARTSHEET_TOKEN from macOS Keychain.

CLI:
    python3 scripts/migrations/seed_its_project_routing.py [--dry-run]
"""
from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[2]))

from shared import defaults, sheet_ids, smartsheet_client  # noqa: E402


def _existing_project_names(sheet_id: int) -> set[str]:
    rows = smartsheet_client.get_rows(sheet_id)
    out: set[str] = set()
    for r in rows:
        name = r.get("Project Name")
        if isinstance(name, str) and name.strip():
            out.add(name.strip())
    return out


def seed_project_routing(*, dry_run: bool) -> tuple[int, int, int]:
    """Seed BOX_PROJECT_FOLDERS into ITS_Project_Routing.

    Returns (added, skipped, total). On dry-run, no writes happen but the
    summary reflects what WOULD have been added vs skipped.
    """
    sheet_id = sheet_ids.SHEET_PROJECT_ROUTING
    if not sheet_id:
        raise RuntimeError(
            "SHEET_PROJECT_ROUTING=0 placeholder. Run "
            "scripts/migrations/build_its_project_routing_sheet.py and update "
            "shared/sheet_ids.py before seeding."
        )

    source = defaults.BOX_PROJECT_FOLDERS
    print(f"[info] BOX_PROJECT_FOLDERS entries: {len(source)}")
    if not source:
        print("[info] Nothing to seed.")
        return 0, 0, 0

    existing = _existing_project_names(sheet_id)
    today = datetime.now(UTC).date().isoformat()

    rows_to_add: list[dict] = []
    skipped_count = 0
    for project_name, folder_id in source.items():
        if project_name in existing:
            print(f"[skip] already present: {project_name}")
            skipped_count += 1
            continue
        rows_to_add.append({
            "Project Name": project_name,
            "Box Folder ID": str(folder_id),
            "Active": True,
            "Notes": (
                f"Seeded from shared.defaults.BOX_PROJECT_FOLDERS on {today}"
            ),
        })

    if not rows_to_add:
        print("[info] No new rows to add.")
        return 0, skipped_count, len(source)

    if dry_run:
        print(f"[dry-run] Would add {len(rows_to_add)} rows:")
        for r in rows_to_add:
            print(f"  + {r['Project Name']}  (Box Folder ID={r['Box Folder ID']})")
        return len(rows_to_add), skipped_count, len(source)

    new_row_ids = smartsheet_client.add_rows(sheet_id, rows_to_add)
    for r, rid in zip(rows_to_add, new_row_ids, strict=True):
        print(f"[ok] added {r['Project Name']} (smartsheet row_id={rid})")
    return len(rows_to_add), skipped_count, len(source)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Seed ITS_Project_Routing from BOX_PROJECT_FOLDERS.",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print what would be added without writing to Smartsheet.",
    )
    args = parser.parse_args()

    print("[info] Source: shared.defaults.BOX_PROJECT_FOLDERS")
    print(f"[info] Target sheet id = {sheet_ids.SHEET_PROJECT_ROUTING}")
    print(f"[info] Mode: {'DRY-RUN' if args.dry_run else 'LIVE WRITE'}")
    print()

    added, skipped, total = seed_project_routing(dry_run=args.dry_run)

    print()
    print("Summary:")
    print(f"  BOX_PROJECT_FOLDERS entries scanned: {total}")
    print(f"  Rows {'planned' if args.dry_run else 'added'}: {added}")
    print(f"  Skipped (already present): {skipped}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
