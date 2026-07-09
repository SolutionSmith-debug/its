"""Build ITS_Vendors — the vendor source-of-record for the PO workstream (S1).

Creates the sheet in the "Control" folder of the "ITS — Purchase Orders" workspace
(build_purchase_orders_workspace.py; §46 workspace). ITS_Vendors is the SOLE vendor
SoR (decision D4 + the S1 Vendor-DB decision): the old Operations "Vendor DB"
(shared/sheet_ids.SHEET_VENDOR_DB) is retired-in-place — seed_its_vendors.py performs
the one-time row copy; zero Picklist_Sync_Config mappings referenced it (verified
live 2026-07-09), so no re-pointing is needed.

Sync posture (D4, the first bidirectional §51 instance — S2/S4 build the machinery):
Smartsheet (THIS sheet) = SoR; D1 = portal cache. Down-sync full-replace with a
dirty-row fence; up-sync bridge-key find-or-create on **Vendor Key** (VEN-######),
column-scoped, never-delete (deactivate via Active). The Vendor Key is the immutable
join identity: `po_send` resolves the vendor recipient (TO = Contact Email) from this
sheet BY KEY at send time — a blank Contact Email HOLDs the send (`held_no_recipient`),
never silently drops it.

Option-set parity: Region / Supply Categories / Default Terms Profile / Active option
lists here MUST stay set-equal to the matching frozensets in
shared/picklist_validation.py (REGISTRY gates every add_rows/update_rows write to this
sheet once SHEET_ITS_VENDORS is flipped). tests/test_po_s1_sheets.py pins the parity.

Supply Categories is MULTI_PICKLIST — the first in the migration family. Creation via
POST /sheets is expected to work on SDK 3.9 (MultiPicklistObjectValue verified); if the
live create rejects the type (the ABSTRACT_DATETIME errorCode-1142 class), fall back to
creating TEXT_NUMBER and retyping via update_column (the hours_log pattern), then update
this comment with the verified truth.

Idempotent: find-or-creates the "Control" folder by name and skips if a sheet named
ITS_Vendors already exists in it (order-independent with the other S1 builders).

Prereq: build_purchase_orders_workspace.py has been run and WORKSPACE_PURCHASE_ORDERS
flipped in shared/sheet_ids.py.

    python3 scripts/migrations/build_its_vendors_sheet.py --dry-run
    python3 scripts/migrations/build_its_vendors_sheet.py
"""
from __future__ import annotations

import argparse
import sys
from typing import Any

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[2]))

from shared import sheet_ids, smartsheet_client  # noqa: E402

WORKSPACE = sheet_ids.WORKSPACE_PURCHASE_ORDERS
FOLDER_NAME = "Control"
SHEET_NAME = "ITS_Vendors"

# Ordered for the dropdown UI; set-parity with shared/picklist_validation.py is
# test-pinned (tests/test_po_s1_sheets.py).
REGION_OPTIONS = ["West", "Midwest", "East", "National"]
SUPPLY_CATEGORY_OPTIONS = [
    "modules", "racking", "inverters", "electrical_bos", "wire", "switchgear",
    "combiners", "transformers", "fencing", "aggregate", "concrete",
    "tools_rentals", "other",
]
TERMS_PROFILE_OPTIONS = ["standard_17", "chint_vendor", "negotiated_gtc"]
ACTIVE_OPTIONS = ["Active", "Inactive", "Archived"]  # lifecycle set shared with ITS_Active_Jobs

COLUMN_SCHEMA: list[dict[str, Any]] = [
    {"title": "Vendor Name", "type": "TEXT_NUMBER", "primary": True},
    {"title": "Vendor Key", "type": "TEXT_NUMBER",
     "description": "VEN-###### — the immutable bridge key (D4). Allocated by seed_its_vendors.py / "
                    "the up-sync writer, NEVER edited by hand; the D1 cache joins on it and po_send "
                    "resolves the vendor recipient by it at send time."},
    {"title": "Address", "type": "TEXT_NUMBER",
     "description": "Vendor address block as printed on the PO Seller block (1–4 lines, comma-separated)."},
    {"title": "Contact Name", "type": "TEXT_NUMBER"},
    {"title": "Contact Email", "type": "TEXT_NUMBER",
     "description": "THE send-time recipient (TO) — po_send resolves it from this sheet by Vendor Key at "
                    "dispatch. Blank → the send is HELD (held_no_recipient), never silently dropped."},
    {"title": "Contact Phone", "type": "TEXT_NUMBER"},
    {"title": "Region", "type": "PICKLIST", "options": REGION_OPTIONS,
     "description": "Service region — the SPA vendor-picker filter chip axis (corpus: Oregon/West, "
                    "Illinois/Midwest, PA-MD-VA/East jobs)."},
    {"title": "Supply Categories", "type": "MULTI_PICKLIST", "options": SUPPLY_CATEGORY_OPTIONS,
     "description": "What the vendor supplies — the second SPA filter axis. Multi-select."},
    {"title": "Default Terms Profile", "type": "PICKLIST", "options": TERMS_PROFILE_OPTIONS,
     "description": "The terms-library profile preselected for this vendor's POs (D6; po_materials/terms "
                    "manifest ids). Drafts pin id+version at generate time."},
    {"title": "GTC Reference", "type": "TEXT_NUMBER",
     "description": "Box link to the negotiated GTC document for negotiated_gtc vendors (attach-not-"
                    "generate, D6). Blank for library-terms vendors."},
    {"title": "Active", "type": "PICKLIST", "options": ACTIVE_OPTIONS,
     "description": "Lifecycle (never-delete — deactivate; D4). Only Active vendors appear in the SPA "
                    "picker."},
    {"title": "Notes", "type": "TEXT_NUMBER"},
    {"title": "Last Modified", "type": "DATETIME", "systemColumnType": "MODIFIED_DATE"},
    {"title": "Modified By", "type": "CONTACT_LIST", "systemColumnType": "MODIFIED_BY"},
]


def _require_workspace() -> int:
    if not WORKSPACE:
        print("[error] WORKSPACE_PURCHASE_ORDERS is still 0 in shared/sheet_ids.py.\n"
              "        Run build_purchase_orders_workspace.py first and flip the printed id.",
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
    print(f"[bootstrap] Update shared/sheet_ids.py:\n    FOLDER_PO_CONTROL = {new_id}")
    return new_id


def build_sheet(*, dry_run: bool) -> tuple[str, int | None]:
    workspace_id = _require_workspace()
    folder_id = ensure_control_folder(workspace_id, dry_run=dry_run)
    if folder_id is None:
        print(f"[dry-run] Would create sheet {SHEET_NAME!r} with columns: "
              f"{[c['title'] for c in COLUMN_SCHEMA]}.")
        return "dry-run", None

    existing_id = smartsheet_client.find_sheet_by_name_in_folder(folder_id, SHEET_NAME)
    if existing_id is not None:
        print(f"[skip] sheet {SHEET_NAME!r} already present (sheet_id={existing_id}).")
        print(f"[bootstrap] shared/sheet_ids.py:\n    SHEET_ITS_VENDORS = {existing_id}")
        return "exists", existing_id

    if dry_run:
        print(f"[dry-run] Would create sheet {SHEET_NAME!r} in folder {folder_id} with "
              f"columns: {[c['title'] for c in COLUMN_SCHEMA]}.")
        return "dry-run", None

    new_id = smartsheet_client.create_sheet_in_folder(folder_id, SHEET_NAME, COLUMN_SCHEMA)
    print(f"[ok] created {SHEET_NAME!r} in folder {folder_id} (sheet_id={new_id}).")
    print(f"[bootstrap] Update shared/sheet_ids.py:\n    SHEET_ITS_VENDORS = {new_id}")
    return "created", new_id


def main() -> int:
    parser = argparse.ArgumentParser(description="Build ITS_Vendors (PO S1 vendor SoR).")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    print(f"[info] Workspace ITS — Purchase Orders = {WORKSPACE}")
    print(f"[info] Folder = {FOLDER_NAME!r} | Sheet = {SHEET_NAME!r}")
    print(f"[info] Mode: {'DRY-RUN' if args.dry_run else 'LIVE WRITE'}\n")

    status, sheet_id = build_sheet(dry_run=args.dry_run)
    print(f"\nSummary:\n  {SHEET_NAME}: {status} (id={sheet_id})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
