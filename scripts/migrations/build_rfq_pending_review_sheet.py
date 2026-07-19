"""Build RFQ_Pending_Review — the RFQ review/approve/send surface (ADR-0004 R2).

Creates the sheet in the "Control" folder of the "ITS — Purchase Orders" workspace.
One row per **(rfq, vendor)** awaiting human review (decision 12 — the send grain).
`rfq_poll` (R2) appends the row when it files a rendered RFQ PDF; a human edits the
Email Body, flips "Approve for Scheduled Send" (or "Send Now"); the F22 gate
verifies the cell-history actor before PR-D's `rfq_send` dispatches to the vendor.

**SCHEMA TWIN OF PO_Pending_Review** (which is itself the WSR schema twin): the
COLUMN_SCHEMA below clones build_po_pending_review_sheet.py's column set
COLUMN-FOR-COLUMN — titles, types, order — because the shared send engine
(`safety_reports.weekly_send` + `send_poll_core`) binds columns by the
`safety_reports.wsr_review` COL_* title constants; keeping the protocol titles is
what lets PR-D bind an `rfq_review` module without engine surgery. Three columns
carry RFQ semantics inside protocol-titled slots (descriptions say so on the live
sheet):

    "Job ID"       ← the **Vendor Key** (VEN-###### — recipient join key → ITS_Vendors)
    "Week Of"      ← the **RFQ Date**
    "Compiled PDF" ← the **RFQ PDF** Box link

THE ONLY DELIBERATE DELTA vs the PO builder: Workstream options =
["po_materials_rfq"], NOT ["po_materials"]. The P1b contamination guard passes a
MATCHING tag, so an RFQ row tagged 'po_materials' that ever reached po_send's
dispatch path would sail through its Stage-2b guard; the distinct lane tag makes
cross-lane dispatch structurally impossible (po_materials/rfq_review.py module
docstring — PR-D's rfq_send MUST bind workstream_tag='po_materials_rfq'). The tag
is hard-populated at row creation (red-team #8: a brand-new sheet has no
pre-backfill excuse for an absent Workstream).

Approved At / Sent At are DATE, NOT ABSTRACT_DATETIME — ABSTRACT_DATETIME is not
creatable via the API (errorCode 1142; the WPR builder documents the live-verified
lesson).

Idempotent: find-or-creates the "Control" folder by name and skips if a sheet named
RFQ_Pending_Review already exists in it (order-independent with the other builders).

Builder-precedes-seed: after this prints the new sheet id, flip
`shared/sheet_ids.py::SHEET_RFQ_PENDING_REVIEW` from its 0 placeholder —
`po_materials/rfq_review.py` refuses every write while the placeholder stands.

    python3 scripts/migrations/build_rfq_pending_review_sheet.py --dry-run
    python3 scripts/migrations/build_rfq_pending_review_sheet.py
"""
from __future__ import annotations

import argparse
import sys
from typing import Any

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[2]))

from shared import sheet_ids, smartsheet_client  # noqa: E402

WORKSPACE = sheet_ids.WORKSPACE_PURCHASE_ORDERS
FOLDER_NAME = "Control"
SHEET_NAME = "RFQ_Pending_Review"
# Lifecycle mirrors WSR/PO: PENDING → SENDING (write-ahead marker) → SENT; FAILED
# (retryable) / HELD (operator hold / held_no_recipient / contamination) off-path.
SEND_STATUS_OPTIONS = ["PENDING", "SENDING", "SENT", "FAILED", "HELD"]
# THE deliberate delta vs the PO twin — the DISTINCT RFQ send-lane tag (see module
# docstring; registered in shared/picklist_validation _RFQ_WORKSTREAM_VALUES).
WORKSTREAM_OPTIONS = ["po_materials_rfq"]

COLUMN_SCHEMA: list[dict[str, Any]] = [
    {"title": "Job / Project", "type": "TEXT_NUMBER", "primary": True},
    {"title": "Job ID", "type": "TEXT_NUMBER",
     "description": "PROTOCOL SLOT — for RFQs this carries the VENDOR KEY (VEN-######), the "
                    "ITS_Vendors join key rfq_send resolves the recipient (TO) from at send "
                    "time. Title kept 'Job ID' so the shared send engine binds unchanged "
                    "(schema-twin contract)."},
    {"title": "Week Of", "type": "DATE",
     "description": "PROTOCOL SLOT — for RFQs this carries the RFQ DATE. Title kept as 'Week "
                    "Of' for the engine bind (the schema-twin contract)."},
    {"title": "Compiled PDF", "type": "TEXT_NUMBER",
     "description": "PROTOCOL SLOT — the rendered price-free RFQ PDF's Box link. The PDF also "
                    "attaches to this row for one-click review."},
    {"title": "Email Body", "type": "TEXT_NUMBER",
     "description": "Editable body — THE source of truth rfq_send transmits to the vendor. The "
                    "reviewer may edit before approving."},
    {"title": "Recipient TO", "type": "TEXT_NUMBER",
     "description": "Display of the resolved vendor contact email. Authoritative source is "
                    "ITS_Vendors (by Vendor Key) at send time."},
    {"title": "CC", "type": "TEXT_NUMBER",
     "description": "Display of the resolved CC list (empty for RFQs unless PR-D adds routing). "
                    "Authoritative source is config at send time."},
    {"title": "Approve for Scheduled Send", "type": "CHECKBOX",
     "description": "Human approval gate. A person flips this; MODIFIED_BY auto-captures who; the "
                    "F22 gate verifies that actor is on the ITS — Purchase Orders share list (§46) "
                    "before dispatch."},
    {"title": "Send Now", "type": "CHECKBOX",
     "description": "Approve + dispatch immediately (out-of-band of the scheduled send window)."},
    {"title": "Approved By", "type": "CONTACT_LIST",
     "description": "Auto-stamped approver identity (the send daemon records the cell-history actor "
                    "of the approve flip)."},
    {"title": "Approved At", "type": "DATE",
     "description": "Approval date (DATE — ABSTRACT_DATETIME is not API-creatable, errorCode 1142). "
                    "Written via the review module's to_wsr_datetime (naive Pacific)."},
    {"title": "Send Status", "type": "PICKLIST", "options": SEND_STATUS_OPTIONS},
    {"title": "Sent At", "type": "DATE",
     "description": "Send date (DATE). Written via the review module's to_wsr_datetime (naive "
                    "Pacific)."},
    {"title": "Notes", "type": "TEXT_NUMBER",
     "description": "Machine join (rfq_id=<n>; rfq_number=<s>; vendor_key=<k>) + retry state / "
                    "hold reasons. The reviewer edits Email Body, never this."},
    {"title": "Workstream", "type": "PICKLIST", "options": WORKSTREAM_OPTIONS,
     "description": "Report-family tag (P1b send guard) → 'po_materials_rfq', DELIBERATELY "
                    "distinct from the PO lane's 'po_materials' so po_send can never dispatch an "
                    "RFQ row; any other tag is contamination the send guard HARD-HELDs."},
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
        print(f"[bootstrap] shared/sheet_ids.py:\n    SHEET_RFQ_PENDING_REVIEW = {existing_id}")
        return "exists", existing_id

    if dry_run:
        print(f"[dry-run] Would create sheet {SHEET_NAME!r} in folder {folder_id} with "
              f"columns: {[c['title'] for c in COLUMN_SCHEMA]}.")
        return "dry-run", None

    new_id = smartsheet_client.create_sheet_in_folder(folder_id, SHEET_NAME, COLUMN_SCHEMA)
    print(f"[ok] created {SHEET_NAME!r} in folder {folder_id} (sheet_id={new_id}).")
    print(f"[bootstrap] Update shared/sheet_ids.py (builder-precedes-seed — "
          f"rfq_review.py refuses writes until this flips):\n"
          f"    SHEET_RFQ_PENDING_REVIEW = {new_id}")
    return "created", new_id


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build RFQ_Pending_Review (ADR-0004 R2 review surface, PO schema twin)."
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
