"""Build RFQ_Log — the operator-visible outbound-RFQ ledger (ADR-0004 R2).

Creates the sheet in the "Control" folder of the "ITS — Purchase Orders" workspace,
next to PO_Log / Estimate_Log. One row per **(rfq, vendor)** — an RFQ fans out to N
vendors (ADR-0004 decision 12) and each vendor's copy has its own PDF, review row,
and send lifecycle. **D1 (`rfqs`) is the authoritative RFQ status machine** (the
Worker owns the queue); this sheet is the downstream Smartsheet MIRROR the
`rfq_poll` daemon maintains so the office can see the RFQ ledger without portal
access — the PO_Log posture, mirror-not-master.

Status machine: queued → filed → sent → responded → closed, plus canceled. The
ledger records the lowercase D1 vocabulary verbatim; `queued` also serves hand rows.

Idempotent: find-or-creates the "Control" folder by name and skips if a sheet named
RFQ_Log already exists in it (order-independent with the other builders).

Builder-precedes-seed: after this prints the new sheet id, flip
`shared/sheet_ids.py::SHEET_RFQ_LOG` from its 0 placeholder —
`po_materials/rfq_log.py` refuses every write while the placeholder stands.

Prereq: build_purchase_orders_workspace.py has been run and WORKSPACE_PURCHASE_ORDERS
flipped in shared/sheet_ids.py (already true on this deployment).

    python3 scripts/migrations/build_rfq_log_sheet.py --dry-run
    python3 scripts/migrations/build_rfq_log_sheet.py
"""
from __future__ import annotations

import argparse
import sys
from typing import Any

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[2]))

from shared import sheet_ids, smartsheet_client  # noqa: E402

WORKSPACE = sheet_ids.WORKSPACE_PURCHASE_ORDERS
FOLDER_NAME = "Control"
SHEET_NAME = "RFQ_Log"

# Lowercase — mirrors the D1 rfqs status vocabulary at the (rfq, vendor) grain.
# Keep in lockstep with po_materials/rfq_log.py LEGAL_STATUSES and the
# shared/picklist_validation REGISTRY entry (_RFQ_LOG_STATUS_VALUES).
STATUS_OPTIONS = ["queued", "filed", "sent", "responded", "closed", "canceled"]

COLUMN_SCHEMA: list[dict[str, Any]] = [
    {"title": "RFQ Number", "type": "TEXT_NUMBER", "primary": True,
     "description": "The contractual RFQ number (D1 rfqs.rfq_number). One row per "
                    "(RFQ Number, Vendor Key) — the fan-out/send grain."},
    {"title": "Job #", "type": "TEXT_NUMBER",
     "description": "The job number the RFQ was composed against (rfq:v1 HMAC-covered)."},
    {"title": "Vendor Key", "type": "TEXT_NUMBER",
     "description": "VEN-###### — the addressed vendor (part of the row grain; the "
                    "recipient join key into ITS_Vendors)."},
    {"title": "Vendor Name", "type": "TEXT_NUMBER",
     "description": "ITS_Vendors SoR snapshot at filing time (display only — the send "
                    "resolves the live SoR row by Vendor Key at dispatch)."},
    {"title": "Status", "type": "PICKLIST", "options": STATUS_OPTIONS,
     "description": "Mirrors the D1 rfq status machine (lowercase). filed stamped by "
                    "rfq_poll pass 1; sent by pass 2 after a successful status-sync; "
                    "responded/closed from the R4 round-trip close."},
    {"title": "Box PDF File ID", "type": "TEXT_NUMBER",
     "description": "Box file id of this vendor's rendered RFQ PDF (under the job's "
                    "Purchase Orders/RFQs folder)."},
    {"title": "Review Row ID", "type": "TEXT_NUMBER",
     "description": "The RFQ_Pending_Review row id carrying this vendor's copy."},
    {"title": "Detail", "type": "TEXT_NUMBER",
     "description": "Machine context (e.g. the quotes-due date, fence reasons)."},
    {"title": "Created At", "type": "TEXT_NUMBER",
     "description": "Naive Pacific wall-clock 'YYYY-MM-DD HH:MM:SS' (ABSTRACT_DATETIME "
                    "is not API-creatable, errorCode 1142)."},
    {"title": "Workstream", "type": "PICKLIST", "options": ["po_materials"],
     "description": "Hard-populated 'po_materials' at row creation (the LEDGER keeps the "
                    "parent sub-lane tag; the REVIEW sheet's distinct send-lane tag "
                    "'po_materials_rfq' lives on RFQ_Pending_Review — see that builder)."},
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
        print(f"[bootstrap] shared/sheet_ids.py:\n    SHEET_RFQ_LOG = {existing_id}")
        return "exists", existing_id

    if dry_run:
        print(f"[dry-run] Would create sheet {SHEET_NAME!r} in folder {folder_id} with "
              f"columns: {[c['title'] for c in COLUMN_SCHEMA]}.")
        return "dry-run", None

    new_id = smartsheet_client.create_sheet_in_folder(folder_id, SHEET_NAME, COLUMN_SCHEMA)
    print(f"[ok] created {SHEET_NAME!r} in folder {folder_id} (sheet_id={new_id}).")
    print(f"[bootstrap] Update shared/sheet_ids.py (builder-precedes-seed — "
          f"rfq_log.py refuses writes until this flips):\n"
          f"    SHEET_RFQ_LOG = {new_id}")
    return "created", new_id


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build RFQ_Log (ADR-0004 R2 outbound-RFQ ledger mirror)."
    )
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
