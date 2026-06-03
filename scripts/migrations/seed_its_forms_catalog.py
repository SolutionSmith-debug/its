"""One-shot migration: seed ITS_Forms_Catalog with the 4 locked v1 forms.

Cutover companion to build_its_forms_catalog_sheet.py (FLIP precedes SEED):
  1. build_its_forms_catalog_sheet.py (build folder + sheet).
  2. Flip SHEET_FORMS_CATALOG in shared/sheet_ids.py to the printed id.
  3. THIS script (populate). It READS SHEET_FORMS_CATALOG, so step 2 must
     precede it — seeding against the 0 placeholder raises RuntimeError.
  4. Verify, then rely on the sheet.

Seeds the four locked v1 forms (mission §8 "All four daily forms"). Each Form
Code MUST match the future portal Phase-4 form.ts directory EXACTLY — these are
a rendering contract; drift breaks the portal. Available For Jobs is empty on all
four (= available on every job). The job-scoped jha-bradley-v1 variant is NOT
seeded here — its form code doesn't exist yet; add that row only when the variant
ships, keyed to real Job IDs (a meeting decision, not pre-empted by this build).

Idempotency: rows are matched by Form Code (exact); existing rows are skipped,
not overwritten.

Convention: LIVE-write by default; pass --dry-run to preview.

Auth: ITS_SMARTSHEET_TOKEN from macOS Keychain.

CLI:
    python3 scripts/migrations/seed_its_forms_catalog.py --dry-run
    python3 scripts/migrations/seed_its_forms_catalog.py
"""
from __future__ import annotations

import argparse
import sys

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[2]))

from shared import sheet_ids, smartsheet_client  # noqa: E402

# (Form Name, Form Code, Display Order, Description). Form Code == code form.ts
# directory (blueprint workstreams/safety-portal/brief.md §3 + §6 directory list).
FORMS: list[tuple[str, str, str, str]] = [
    ("Job Hazard Analysis", "jha-v1", "10",
     "Identify task hazards and controls before work begins."),
    ("Daily Site Safety Worksheet", "daily-site-safety-v1", "20",
     "Daily site safety check: conditions, PPE, and hazards."),
    ("Equipment Pre-Inspection", "equipment-preinspection-v1", "30",
     "Pre-use inspection of equipment before operation."),
    ("Toolbox Talk", "toolbox-talk-v1", "40",
     "Brief pre-shift safety talk with the crew."),
]


def _existing_form_codes(sheet_id: int) -> set[str]:
    rows = smartsheet_client.get_rows(sheet_id)
    out: set[str] = set()
    for r in rows:
        code = r.get("Form Code")
        if isinstance(code, str) and code.strip():
            out.add(code.strip())
    return out


def seed_forms_catalog(*, dry_run: bool) -> tuple[int, int, int]:
    """Seed FORMS into ITS_Forms_Catalog. Returns (added, skipped, total)."""
    sheet_id = sheet_ids.SHEET_FORMS_CATALOG
    if not sheet_id:
        raise RuntimeError(
            "SHEET_FORMS_CATALOG=0 placeholder. Run "
            "scripts/migrations/build_its_forms_catalog_sheet.py and flip "
            "shared/sheet_ids.py before seeding."
        )

    existing = _existing_form_codes(sheet_id)
    rows_to_add: list[dict] = []
    skipped = 0
    for name, code, order, description in FORMS:
        if code in existing:
            print(f"[skip] already present: {code}")
            skipped += 1
            continue
        rows_to_add.append({
            "Form Name": name,
            "Form Code": code,
            "Active": "Active",
            "Description": description,
            "Display Order": order,
            "Available For Jobs": "",  # empty = available on all jobs.
        })

    if not rows_to_add:
        print("[info] No new rows to add.")
        return 0, skipped, len(FORMS)

    if dry_run:
        print(f"[dry-run] Would add {len(rows_to_add)} rows:")
        for r in rows_to_add:
            print(f"  + {r['Form Name']}  (Form Code={r['Form Code']}, Order={r['Display Order']})")
        return len(rows_to_add), skipped, len(FORMS)

    new_ids = smartsheet_client.add_rows(sheet_id, rows_to_add)
    for r, rid in zip(rows_to_add, new_ids, strict=True):
        print(f"[ok] added {r['Form Code']} (row_id={rid})")
    return len(rows_to_add), skipped, len(FORMS)


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed ITS_Forms_Catalog (4 locked v1 forms).")
    parser.add_argument(
        "--dry-run", action="store_true", help="Print what would be added without writing."
    )
    args = parser.parse_args()

    print(f"[info] Target sheet id = {sheet_ids.SHEET_FORMS_CATALOG}")
    print(f"[info] Mode: {'DRY-RUN' if args.dry_run else 'LIVE WRITE'}")
    print()

    added, skipped, total = seed_forms_catalog(dry_run=args.dry_run)

    print()
    print("Summary:")
    print(f"  Forms scanned: {total}")
    print(f"  Rows {'planned' if args.dry_run else 'added'}: {added}")
    print(f"  Skipped (already present): {skipped}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
