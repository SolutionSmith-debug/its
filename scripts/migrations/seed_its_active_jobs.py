"""One-shot migration: seed ITS_Active_Jobs with the 6 canonical projects.

Cutover companion to build_its_active_jobs_sheet.py (FLIP precedes SEED):
  1. build_its_active_jobs_sheet.py (build folder + sheet).
  2. Flip SHEET_ACTIVE_JOBS in shared/sheet_ids.py to the printed id.
  3. THIS script (populate). It READS SHEET_ACTIVE_JOBS, so step 2 must precede
     it — seeding against the 0 placeholder raises RuntimeError.
  4. Verify, then rely on the sheet.

Seeds one row per project: Project Name (== ITS_Project_Routing primary key),
Job ID (kebab-case stable key), Address (BLANK — see below), Active="Active",
Notes (the address-pending flag).

ADDRESS sourcing (§4 — load-bearing): Address is sourced from live data ONLY and
is NEVER machine-invented (a wrong address is worse than a blank one — it feeds
the form Work Location + downstream PDF content). No structured address source
exists in the repo/config, so every row seeds a BLANK Address with a flag for the
office PM, who maintains this sheet.

Idempotency: rows are matched by Job ID (exact); existing rows are skipped, not
overwritten. Project names + Job IDs are not PII, so both dry-run and live print
them.

Convention: LIVE-write by default; pass --dry-run to preview.

Auth: ITS_SMARTSHEET_TOKEN from macOS Keychain.

CLI:
    python3 scripts/migrations/seed_its_active_jobs.py --dry-run
    python3 scripts/migrations/seed_its_active_jobs.py
"""
from __future__ import annotations

import argparse
import sys

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[2]))

from shared import sheet_ids, smartsheet_client  # noqa: E402

# (Project Name, Job ID). Project Name == ITS_Project_Routing primary key
# (shared.defaults.BOX_PROJECT_FOLDERS keys); Job ID is the kebab-case stable key
# the portal uses to map a selected job to its Box folder via routing (blueprint
# workstreams/safety-portal/brief.md §3: "bradley-1,bradley-2"). Bradley = BBCHS.
JOBS: list[tuple[str, str]] = [
    ("Bradley 1", "bradley-1"),
    ("Bradley 2", "bradley-2"),
    ("Brimfield 1", "brimfield-1"),
    ("Brimfield 2", "brimfield-2"),
    ("Huntley", "huntley"),
    ("Rockford", "rockford"),
]

ADDRESS_PENDING_NOTE = (
    "Address pending — office PM to fill (auto-fills form Work Location; "
    "do not machine-populate)."
)


def _existing_job_ids(sheet_id: int) -> set[str]:
    rows = smartsheet_client.get_rows(sheet_id)
    out: set[str] = set()
    for r in rows:
        jid = r.get("Job ID")
        if isinstance(jid, str) and jid.strip():
            out.add(jid.strip())
    return out


def seed_active_jobs(*, dry_run: bool) -> tuple[int, int, int]:
    """Seed JOBS into ITS_Active_Jobs. Returns (added, skipped, total)."""
    sheet_id = sheet_ids.SHEET_ACTIVE_JOBS
    if not sheet_id:
        raise RuntimeError(
            "SHEET_ACTIVE_JOBS=0 placeholder. Run "
            "scripts/migrations/build_its_active_jobs_sheet.py and flip "
            "shared/sheet_ids.py before seeding."
        )

    existing = _existing_job_ids(sheet_id)
    rows_to_add: list[dict] = []
    skipped = 0
    for project_name, job_id in JOBS:
        if job_id in existing:
            print(f"[skip] already present: {job_id}")
            skipped += 1
            continue
        rows_to_add.append({
            "Project Name": project_name,
            "Job ID": job_id,
            "Address": "",  # §4: blank + flagged; office PM fills from live data.
            "Active": "Active",
            "Notes": ADDRESS_PENDING_NOTE,
        })

    if not rows_to_add:
        print("[info] No new rows to add.")
        return 0, skipped, len(JOBS)

    if dry_run:
        print(f"[dry-run] Would add {len(rows_to_add)} rows:")
        for r in rows_to_add:
            print(f"  + {r['Project Name']}  (Job ID={r['Job ID']}, Active=Active, Address=BLANK)")
        return len(rows_to_add), skipped, len(JOBS)

    new_ids = smartsheet_client.add_rows(sheet_id, rows_to_add)
    for r, rid in zip(rows_to_add, new_ids, strict=True):
        print(f"[ok] added {r['Job ID']} (row_id={rid})")
    return len(rows_to_add), skipped, len(JOBS)


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed ITS_Active_Jobs (6 projects).")
    parser.add_argument(
        "--dry-run", action="store_true", help="Print what would be added without writing."
    )
    args = parser.parse_args()

    print(f"[info] Target sheet id = {sheet_ids.SHEET_ACTIVE_JOBS}")
    print(f"[info] Mode: {'DRY-RUN' if args.dry_run else 'LIVE WRITE'}")
    print()

    added, skipped, total = seed_active_jobs(dry_run=args.dry_run)

    print()
    print("Summary:")
    print(f"  Jobs scanned: {total}")
    print(f"  Rows {'planned' if args.dry_run else 'added'}: {added}")
    print(f"  Skipped (already present): {skipped}")
    print("  NOTE: all Address cells seeded BLANK — office PM must fill (§4).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
