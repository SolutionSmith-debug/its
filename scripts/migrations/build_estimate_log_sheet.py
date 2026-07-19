"""Build Estimate_Log — the operator-visible vendor-estimate ledger (ADR-0004 E2).

Creates the sheet in the "Control" folder of the "ITS — Purchase Orders" workspace,
next to PO_Log. One row per uploaded vendor estimate. **D1 (`po_estimates`) is the
authoritative estimate status machine** (the Worker owns the pool + dedupe index);
this sheet is the downstream Smartsheet MIRROR the `estimate_poll` daemon maintains
so the office can see the import ledger without portal access — the PO_Log posture,
mirror-not-master.

Status machine (ADR-0004): pending → claimed → (refused | needs_review | extracted)
→ (imported | rejected), plus superseded. The ledger records the OPERATOR-visible
subset (lowercase, mirroring D1 verbatim) plus `received` for hand rows; pending/
claimed are transient pool states that never earn a ledger row.

Idempotent: find-or-creates the "Control" folder by name and skips if a sheet named
Estimate_Log already exists in it (order-independent with the other builders).

Builder-precedes-seed: after this prints the new sheet id, flip
`shared/sheet_ids.py::SHEET_ESTIMATE_LOG` from its 0 placeholder —
`po_materials/estimate_log.py` refuses every write while the placeholder stands.

Prereq: build_purchase_orders_workspace.py has been run and WORKSPACE_PURCHASE_ORDERS
flipped in shared/sheet_ids.py (already true on this deployment).

    python3 scripts/migrations/build_estimate_log_sheet.py --dry-run
    python3 scripts/migrations/build_estimate_log_sheet.py
"""
from __future__ import annotations

import argparse
import sys
from typing import Any

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[2]))

from shared import sheet_ids, smartsheet_client  # noqa: E402

WORKSPACE = sheet_ids.WORKSPACE_PURCHASE_ORDERS
FOLDER_NAME = "Control"
SHEET_NAME = "Estimate_Log"

# Lowercase — mirrors the D1 po_estimates.status vocabulary verbatim (plus the
# ledger-only 'received'). Keep in lockstep with po_materials/estimate_log.py
# LEGAL_STATUSES and the shared/picklist_validation REGISTRY entry.
STATUS_OPTIONS = [
    "received", "refused", "needs_review", "extracted", "imported", "rejected",
    "superseded",
]

# Mirrors po_materials/estimate_classify.py DOC_TYPES plus 'filled_form' (the E6
# Tier-0 xlsx round-trip class — created NOW so the live column needs no retrofit
# when E6 lands; keep in lockstep with shared/picklist_validation
# _ESTIMATE_DOC_TYPE_VALUES).
DOC_TYPE_OPTIONS = [
    "quote", "estimate", "proposal", "invoice", "ap_report", "filled_form", "other",
]

COLUMN_SCHEMA: list[dict[str, Any]] = [
    {"title": "Estimate UUID", "type": "TEXT_NUMBER", "primary": True,
     "description": "The D1 pool row identity (po_estimates.est_uuid) — the join key the "
                    "daemon's idempotency guard uses. Never reused."},
    {"title": "Job #", "type": "TEXT_NUMBER",
     "description": "The job number the upload was bound to (est:v1 HMAC-covered)."},
    {"title": "Filename", "type": "TEXT_NUMBER",
     "description": "The uploaded filename (display only — identity is body-derived, "
                    "never taken from the filename; ADR-0004)."},
    {"title": "Doc Type", "type": "PICKLIST", "options": DOC_TYPE_OPTIONS,
     "description": "Deterministic classifier verdict (estimate_classify). invoice/ap_report "
                    "are REFUSED from the PO path — never parsed as line items."},
    {"title": "Status", "type": "PICKLIST", "options": STATUS_OPTIONS,
     "description": "Mirrors the D1 estimate status machine verbatim (lowercase). refused / "
                    "needs_review stamped by estimate_poll; extracted by the PR-B extraction "
                    "pass; imported/rejected/superseded from the SPA dispose flow."},
    {"title": "Vendor Name", "type": "TEXT_NUMBER",
     "description": "BODY-DERIVED vendor identity (PR-B extraction) — blank until extracted. "
                    "Advisory display only; never writes ITS_Vendors (read-only SoR, "
                    "ADR-0004 decision 9)."},
    {"title": "Quote Number", "type": "TEXT_NUMBER",
     "description": "Body-derived quote number (PR-B extraction) — blank until extracted."},
    {"title": "SHA-256", "type": "TEXT_NUMBER",
     "description": "Content digest (signed at upload; the D1 partial-unique dedupe key)."},
    {"title": "Box File ID", "type": "TEXT_NUMBER",
     "description": "Box file id of the filed original ('<est_uuid> - <filename>' under the "
                    "job's Purchase Orders/Vendor Quotes folder). Blank for refused docs."},
    {"title": "Detail", "type": "TEXT_NUMBER",
     "description": "Machine disposition reason (e.g. wrong_doc_type:invoice, "
                    "screen:suspicious:L2:pdf_active_content) — never file bytes."},
    {"title": "Received At", "type": "TEXT_NUMBER",
     "description": "Naive Pacific wall-clock 'YYYY-MM-DD HH:MM:SS' (ABSTRACT_DATETIME is "
                    "not API-creatable, errorCode 1142)."},
    {"title": "Workstream", "type": "PICKLIST", "options": ["po_materials"],
     "description": "Hard-populated 'po_materials' at row creation (brand-new sheet — no "
                    "pre-backfill excuse for an absent tag)."},
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
        print(f"[bootstrap] shared/sheet_ids.py:\n    SHEET_ESTIMATE_LOG = {existing_id}")
        return "exists", existing_id

    if dry_run:
        print(f"[dry-run] Would create sheet {SHEET_NAME!r} in folder {folder_id} with "
              f"columns: {[c['title'] for c in COLUMN_SCHEMA]}.")
        return "dry-run", None

    new_id = smartsheet_client.create_sheet_in_folder(folder_id, SHEET_NAME, COLUMN_SCHEMA)
    print(f"[ok] created {SHEET_NAME!r} in folder {folder_id} (sheet_id={new_id}).")
    print(f"[bootstrap] Update shared/sheet_ids.py (builder-precedes-seed — "
          f"estimate_log.py refuses writes until this flips):\n"
          f"    SHEET_ESTIMATE_LOG = {new_id}")
    return "created", new_id


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build Estimate_Log (ADR-0004 E2 vendor-estimate ledger mirror)."
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
