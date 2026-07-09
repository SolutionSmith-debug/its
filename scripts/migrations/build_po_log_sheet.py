"""Build PO_Log — the operator-visible purchase-order ledger (S1).

Creates the sheet in the "Control" folder of the "ITS — Purchase Orders" workspace.
One row per PO. **D1 is the authoritative PO store** (the Worker allocates numbers
atomically in D1, decision D7); this sheet is the downstream Smartsheet MIRROR the
`po_poll` status pass maintains so the office can see the ledger without portal
access — the same §51 ITS-owned-SoR posture as the rest of the workspace, but
mirror-not-master for the PO records themselves (contrast ITS_Vendors, where
Smartsheet IS the SoR).

Status machine (D7): draft → pending_review → approved → sent, with superseded /
canceled off-path. Values are LOWERCASE (they mirror the D1 `purchase_orders.status`
column verbatim — one vocabulary across both stores, no translation layer). Option-set
parity with shared/picklist_validation.py is test-pinned (tests/test_po_s1_sheets.py);
supersession chains via the Supersedes / Superseded By display columns.

`Total` is a DISPLAY string (e.g. "$2,096,517.60") — money math happens in integer
cents in D1/the render pipeline (D8); never parse this cell.

Idempotent: find-or-creates the "Control" folder by name and skips if a sheet named
PO_Log already exists in it (order-independent with the other S1 builders).

Prereq: build_purchase_orders_workspace.py has been run and WORKSPACE_PURCHASE_ORDERS
flipped in shared/sheet_ids.py.

    python3 scripts/migrations/build_po_log_sheet.py --dry-run
    python3 scripts/migrations/build_po_log_sheet.py
"""
from __future__ import annotations

import argparse
import sys
from typing import Any

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[2]))

from shared import sheet_ids, smartsheet_client  # noqa: E402

WORKSPACE = sheet_ids.WORKSPACE_PURCHASE_ORDERS
FOLDER_NAME = "Control"
SHEET_NAME = "PO_Log"

# Lowercase — mirrors the D1 purchase_orders.status vocabulary verbatim (D7).
STATUS_OPTIONS = ["draft", "pending_review", "approved", "sent", "superseded", "canceled"]

COLUMN_SCHEMA: list[dict[str, Any]] = [
    {"title": "PO Number", "type": "TEXT_NUMBER", "primary": True,
     "description": "The contractual identity {YYYY.NNN}.{site}.{supersede}.{rev} (D7). Allocated "
                    "atomically by the Worker in D1 — never derived from folder names; this sheet "
                    "mirrors it."},
    {"title": "Job / Project", "type": "TEXT_NUMBER"},
    {"title": "Job ID", "type": "TEXT_NUMBER",
     "description": "The portal job key the PO was drafted against (jobs table / ITS_Active_Jobs)."},
    {"title": "Vendor", "type": "TEXT_NUMBER", "description": "Vendor display name at generate time."},
    {"title": "Vendor Key", "type": "TEXT_NUMBER",
     "description": "VEN-###### join key → ITS_Vendors (the vendor SoR)."},
    {"title": "Status", "type": "PICKLIST", "options": STATUS_OPTIONS,
     "description": "D7 status machine, mirrored verbatim from D1 by the po_poll status pass."},
    {"title": "Total", "type": "TEXT_NUMBER",
     "description": "DISPLAY dollars only — authoritative money is integer cents in D1 (D8). Never "
                    "parse this cell."},
    {"title": "PO PDF", "type": "TEXT_NUMBER",
     "description": "Box link to the generated PO PDF (the §45/§47-filed artifact)."},
    {"title": "Supersedes", "type": "TEXT_NUMBER",
     "description": "PO Number this PO supersedes (D7 chaining; blank for first issues)."},
    {"title": "Superseded By", "type": "TEXT_NUMBER",
     "description": "PO Number that superseded this one (stamped when the successor reaches sent)."},
    {"title": "Terms Profile", "type": "TEXT_NUMBER",
     "description": "The pinned terms id+version rendered into the PDF (e.g. 'standard_17 v1', D6)."},
    {"title": "Created By", "type": "TEXT_NUMBER",
     "description": "Portal account that drafted the PO (display name — display-name-only attribution)."},
    {"title": "Created At", "type": "DATE",
     "description": "Draft-created date (DATE — the WSR/WPR-verified creatable type; naive Pacific)."},
    {"title": "Sent At", "type": "DATE",
     "description": "Dispatch date (DATE; naive Pacific). Stamped from PO_Pending_Review by the "
                    "status pass after SENT."},
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
        print(f"[bootstrap] shared/sheet_ids.py:\n    SHEET_PO_LOG = {existing_id}")
        return "exists", existing_id

    if dry_run:
        print(f"[dry-run] Would create sheet {SHEET_NAME!r} in folder {folder_id} with "
              f"columns: {[c['title'] for c in COLUMN_SCHEMA]}.")
        return "dry-run", None

    new_id = smartsheet_client.create_sheet_in_folder(folder_id, SHEET_NAME, COLUMN_SCHEMA)
    print(f"[ok] created {SHEET_NAME!r} in folder {folder_id} (sheet_id={new_id}).")
    print(f"[bootstrap] Update shared/sheet_ids.py:\n    SHEET_PO_LOG = {new_id}")
    return "created", new_id


def main() -> int:
    parser = argparse.ArgumentParser(description="Build PO_Log (PO S1 ledger mirror).")
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
