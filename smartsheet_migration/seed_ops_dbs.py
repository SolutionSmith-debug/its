"""Seed Operations DBs (Vendor DB + Subcontractor DB) from Bradley 1 FL parse.

For each named vendor block in Bradley 1's Financial Ledger source:
- Subcontractor category → Subcontractor DB row (Primary Scope = "Other" default)
- All other categories → Vendor DB row (Vendor Type derived from FL category)

Rows are minimal stubs; PMs/admins backfill contact info, payment terms,
preferred status, etc. via UI or future automation. The Notes field
captures provenance so future review can verify the auto-seed.

CI note: All API calls inside functions, guarded by __main__.
"""
from __future__ import annotations

import argparse
import sys
from typing import Iterable

from ss_api import api, add_rows
from migrate_fl import fetch_source_rows, find_blocks, chunks

VENDOR_DB = 7278304330469252
SUBCONTRACTOR_DB = 1230913068289924

# FL category → Vendor DB Vendor Type (picklist)
CATEGORY_TO_VENDOR_TYPE = {
    "Vendor":              "Material",       # default — most src "Vendor:" rows are material; PMs adjust
    "Survey/Engineering":  "Service",
    "Equipment Rentals":   "Equipment",
    "Testing":             "Service",
    "Permit":              "Service",
    "Insurance":           "Service",
    "Bonding":             "Service",
    "Various":             "Other",
}


def fetch_col_map(sheet_id: int) -> dict[str, int]:
    s = api("GET", f"/sheets/{sheet_id}")
    return {c["title"]: c["id"] for c in s["columns"]}


def fetch_row_count(sheet_id: int) -> int:
    s = api("GET", f"/sheets/{sheet_id}")
    return len(s.get("rows", []))


def build_row(values: dict, col_map: dict[str, int]) -> dict:
    cells = []
    for title, val in values.items():
        if val is None or val == "":
            continue
        if title not in col_map:
            continue
        cells.append({"columnId": col_map[title], "value": val, "strict": True})
    return {"cells": cells, "toBottom": True}


def emit_seed_rows(blocks: list[dict]) -> tuple[list[dict], list[dict], list[str]]:
    """Return (vendor_rows, subcontractor_rows, log)."""
    vendor_rows: list[dict] = []
    sub_rows: list[dict] = []
    log: list[str] = []

    for b in blocks:
        vendor = b["vendor"]
        category = b["category"]
        if not vendor:
            log.append(f"Skipped orphan unnamed {category} block (src_row={b['src_row']})")
            continue

        provenance_note = (
            f"Auto-seeded from Bradley 1 FL on 2026-05-17. "
            f"Original FL category: {category}. Review and complete."
        )

        if category == "Subcontractor":
            sub_rows.append({
                "Subcontractor": vendor,
                "Primary Scope": "Other",  # default; PMs assign actual scope
                "Last Project Worked": "Bradley 1",
                "Notes": provenance_note,
            })
        else:
            vtype = CATEGORY_TO_VENDOR_TYPE.get(category, "Other")
            vendor_rows.append({
                "Vendor": vendor,
                "Vendor Type": vtype,
                "Notes": provenance_note,
            })

    return vendor_rows, sub_rows, log


def write_rows(sheet_id: int, rows: list[dict], label: str) -> int:
    col_map = fetch_col_map(sheet_id)
    payloads = [build_row(r, col_map) for r in rows]
    total = 0
    for batch in chunks(payloads, 200):
        resp = add_rows(sheet_id, batch)
        n = len(resp.get("result", []))
        total += n
        print(f"  [{label}] batch wrote {n} rows")
    return total


def run(mode: str, force: bool = False) -> int:
    print(f"=== Ops DB seed — mode={mode} force={force} ===")
    rows = fetch_source_rows()
    blocks = find_blocks(rows)
    print(f"Bradley 1 blocks parsed: {len(blocks)}")
    vendor_rows, sub_rows, log = emit_seed_rows(blocks)
    print(f"Vendor DB rows to write: {len(vendor_rows)}")
    print(f"Subcontractor DB rows to write: {len(sub_rows)}")
    if log:
        for line in log:
            print(f"  ! {line}")

    # Per-DB breakdown
    print("\n--- Vendor DB stubs ---")
    for r in vendor_rows:
        print(f"  {r['Vendor']:<40} type={r['Vendor Type']}")
    print("\n--- Subcontractor DB stubs ---")
    for r in sub_rows:
        print(f"  {r['Subcontractor']:<40} scope={r['Primary Scope']}")

    if mode == "dry":
        return 0

    # Idempotency guard per DB
    for sid, label in [(VENDOR_DB, "Vendor DB"), (SUBCONTRACTOR_DB, "Subcontractor DB")]:
        n = fetch_row_count(sid)
        if n > 0 and not force:
            print(f"\nREFUSING — {label} has {n} existing rows. Re-run with --force.", file=sys.stderr)
            return 3

    # Write
    print()
    if vendor_rows:
        written = write_rows(VENDOR_DB, vendor_rows, "Vendor DB")
        print(f"Vendor DB: wrote {written} rows")
    if sub_rows:
        written = write_rows(SUBCONTRACTOR_DB, sub_rows, "Subcontractor DB")
        print(f"Subcontractor DB: wrote {written} rows")
    return 0


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--mode", choices=["dry", "seed"], required=True)
    p.add_argument("--force", action="store_true")
    args = p.parse_args()
    return run(args.mode, args.force)


if __name__ == "__main__":
    sys.exit(main())
