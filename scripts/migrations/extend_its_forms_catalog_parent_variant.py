"""One-shot migration: ITS_Forms_Catalog → parent/variant model (Phase 4).

Adds two columns and reconciles the catalog rows to the Phase-4 V1 catalog:
five form-type PARENTS + variant rows for the two parents that have variants.

Columns added (TEXT, before the system columns; idempotent skip-if-present):
    Parent Form Code   — empty on a parent row; the parent's Form Code on a variant
    Variant Label      — the 3rd-picklist label; empty on a parent / no-variant form

Row reconcile (idempotent upsert + prune):
    PARENTS (Parent Form Code = ""):
      Job Hazard Analysis        / jha-v1                  (no variants → Form Code is the definition)
      Equipment Pre-Inspection   / equipment-preinspection (has variants → Form Code is the parent key)
      Toolbox Talk               / toolbox-talk            (has variants)
      Visitor Sign-In            / visitor-sign-in-v1      (no variants)
      HSS&E Work Observation     / hsse-work-observation-v1 (no variants)
    VARIANTS (Parent Form Code set; Form Code = the definition):
      Equipment: Telehandler/Forklift, Skid Steer
      Toolbox:   Back Sprains, PPE, Electrical, Ergonomics, Hard Hat
    PRUNE: any catalog row whose Form Code is not in the desired set (drops the old
    flat seed incl. daily-site-safety-v1 — Daily Site Safety is OUT in V1).

V1 catalog decision (operator, 2026-06-05): Daily Site Safety OUT; Visitor Sign-In
and HSS&E Work Observation IN; variants collapse under a parent + 3rd picklist.

Smartsheet REST API directly (add-column/typed row ops not surfaced through the
wrapper). Auth: ITS_SMARTSHEET_TOKEN from macOS Keychain.

    python3 scripts/migrations/extend_its_forms_catalog_parent_variant.py --dry-run
    python3 scripts/migrations/extend_its_forms_catalog_parent_variant.py
"""
from __future__ import annotations

import argparse
import sys
from typing import Any

import requests  # type: ignore[import-untyped]

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[2]))

from shared import keychain, sheet_ids  # noqa: E402

BASE = "https://api.smartsheet.com/2.0"
SHEET = sheet_ids.SHEET_FORMS_CATALOG
NEW_COLUMNS = ["Parent Form Code", "Variant Label"]

# (Form Name, Form Code, Parent Form Code, Variant Label, Display Order)
DESIRED: list[tuple[str, str, str, str, str]] = [
    ("Job Hazard Analysis", "jha-v1", "", "", "10"),
    ("Equipment Pre-Inspection", "equipment-preinspection", "", "", "20"),
    ("Equipment Pre-Inspection — Telehandler/Forklift", "equipment-telehandler-v1", "equipment-preinspection", "Telehandler/Forklift", "21"),
    ("Equipment Pre-Inspection — Skid Steer", "equipment-skid-steer-v1", "equipment-preinspection", "Skid Steer", "22"),
    ("Toolbox Talk", "toolbox-talk", "", "", "30"),
    ("Toolbox Talk — Back Sprains and Strains", "toolbox-talk-back-sprains-v1", "toolbox-talk", "Back Sprains and Strains", "31"),
    ("Toolbox Talk — Protective Clothing and Equipment (PPE)", "toolbox-talk-ppe-v1", "toolbox-talk", "Protective Clothing and Equipment (PPE)", "32"),
    ("Toolbox Talk — Electrical Safety", "toolbox-talk-electrical-v1", "toolbox-talk", "Electrical Safety", "33"),
    ("Toolbox Talk — Ergonomics / Back Safety", "toolbox-talk-ergonomics-v1", "toolbox-talk", "Ergonomics / Back Safety", "34"),
    ("Toolbox Talk — Hard Hat Safety", "toolbox-talk-hard-hat-v1", "toolbox-talk", "Hard Hat Safety", "35"),
    ("Visitor Sign-In", "visitor-sign-in-v1", "", "", "40"),
    ("HSS&E Work Observation", "hsse-work-observation-v1", "", "", "50"),
]
DESIRED_CODES = {row[1] for row in DESIRED}


def _headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {keychain.get_secret('ITS_SMARTSHEET_TOKEN')}",
            "Content-Type": "application/json"}


def _sheet() -> dict[str, Any]:
    r = requests.get(f"{BASE}/sheets/{SHEET}?include=objectValue", headers=_headers(), timeout=30)
    r.raise_for_status()
    return r.json()


def _pre_system_index(columns: list[dict[str, Any]]) -> int:
    return min((c["index"] for c in columns if c.get("systemColumnType")), default=len(columns))


def add_columns(*, dry_run: bool) -> None:
    present = {c["title"] for c in _sheet()["columns"]}
    missing = [t for t in NEW_COLUMNS if t not in present]
    if not missing:
        print("[skip] Parent Form Code + Variant Label already present.")
        return
    if dry_run:
        print(f"[dry-run] would add columns {missing} (TEXT) before the system columns.")
        return
    for title in missing:
        idx = _pre_system_index(_sheet()["columns"])
        r = requests.post(f"{BASE}/sheets/{SHEET}/columns", headers=_headers(),
                          json=[{"title": title, "type": "TEXT_NUMBER", "index": idx}], timeout=30)
        r.raise_for_status()
        print(f"[ok] added column {title!r} (id={r.json()['result'][0]['id']}).")


def _cell_value(row: dict[str, Any], col_id_by_title: dict[str, int], title: str) -> str:
    cid = col_id_by_title.get(title)
    for cell in row.get("cells", []):
        if cell.get("columnId") == cid:
            v = cell.get("value")
            return str(v) if v is not None else ""
    return ""


def reconcile_rows(*, dry_run: bool) -> None:
    sheet = _sheet()
    col_id_by_title = {c["title"]: c["id"] for c in sheet["columns"]}
    rows = sheet.get("rows", [])
    row_by_code: dict[str, dict[str, Any]] = {}
    for row in rows:
        code = _cell_value(row, col_id_by_title, "Form Code")
        if code:
            row_by_code[code] = row

    def cells_for(name: str, code: str, parent: str, variant: str, order: str) -> list[dict[str, Any]]:
        pairs = {"Form Name": name, "Form Code": code, "Parent Form Code": parent,
                 "Variant Label": variant, "Active": "Active", "Display Order": order,
                 "Available For Jobs": ""}
        return [{"columnId": col_id_by_title[t], "value": v} for t, v in pairs.items() if t in col_id_by_title]

    to_add, to_update = [], []
    for name, code, parent, variant, order in DESIRED:
        cells = cells_for(name, code, parent, variant, order)
        if code in row_by_code:
            to_update.append({"id": row_by_code[code]["id"], "cells": cells})
        else:
            to_add.append({"toBottom": True, "cells": cells})
    to_prune = [r["id"] for code, r in row_by_code.items() if code not in DESIRED_CODES]

    print(f"[plan] add={len(to_add)} update={len(to_update)} prune={len(to_prune)} "
          f"(prune codes: {[c for c in row_by_code if c not in DESIRED_CODES]})")
    if dry_run:
        print("[dry-run] no writes.")
        return
    if to_update:
        requests.put(f"{BASE}/sheets/{SHEET}/rows", headers=_headers(), json=to_update, timeout=30).raise_for_status()
        print(f"[ok] updated {len(to_update)} rows.")
    if to_add:
        requests.post(f"{BASE}/sheets/{SHEET}/rows", headers=_headers(), json=to_add, timeout=30).raise_for_status()
        print(f"[ok] added {len(to_add)} rows.")
    if to_prune:
        ids = ",".join(str(i) for i in to_prune)
        requests.delete(f"{BASE}/sheets/{SHEET}/rows?ids={ids}", headers=_headers(), timeout=30).raise_for_status()
        print(f"[ok] pruned {len(to_prune)} stale rows.")


def verify() -> None:
    sheet = _sheet()
    col_id_by_title = {c["title"]: c["id"] for c in sheet["columns"]}
    print("\n[verify] ITS_Forms_Catalog rows (Form Code | Parent | Variant):")
    for row in sheet.get("rows", []):
        code = _cell_value(row, col_id_by_title, "Form Code")
        parent = _cell_value(row, col_id_by_title, "Parent Form Code")
        variant = _cell_value(row, col_id_by_title, "Variant Label")
        print(f"    {code:<32} | {parent:<24} | {variant}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Extend ITS_Forms_Catalog to parent/variant (Phase 4).")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    print(f"[info] Sheet ITS_Forms_Catalog = {SHEET}")
    print(f"[info] Mode: {'DRY-RUN' if args.dry_run else 'LIVE WRITE'}\n")
    add_columns(dry_run=args.dry_run)
    reconcile_rows(dry_run=args.dry_run)
    if not args.dry_run:
        verify()
    print("\n[done]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
