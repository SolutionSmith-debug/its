"""RFQ_Log access — the operator-visible ledger of the outbound RFQ lane (R2).

One row per **(rfq, vendor)** — an RFQ fans out to N vendors (ADR-0004 decision 12),
and each vendor's copy has its own PDF, review row, and send lifecycle, so the
ledger grain matches the send grain. D1 (`rfqs`, via the Worker) remains the
authoritative RFQ status machine; this ITS-owned sheet (§51) is the ledger the
office reads without portal access — the PO_Log/Estimate_Log posture,
mirror-not-master.

`rfq_poll` pass ① APPENDS the row at filing time (Status=filed); pass ② stamps
`sent` after a successful status-sync POST (D1 first, then the mirror); the R4
round-trip close stamps responded/closed. `queued` exists for hand rows.

Write discipline (the estimate_log contract)
--------------------------------------------
* `append_row` is APPEND-ONLY and idempotent-by-caller: check `find_row` first
  (the crash-retry guard — re-servicing a queued RFQ must not duplicate a
  (rfq, vendor) row).
* `update_status` writes ONLY LEGAL_STATUSES values (`shared.picklist_validation`
  gates the actual write once the sheet registers) and SKIPS the update when the
  row is already at the target.
* FLIP precedes SEED: every write refuses while `sheet_ids.SHEET_RFQ_LOG` is the 0
  placeholder — run scripts/migrations/build_rfq_log_sheet.py and flip the printed
  id first (builder-precedes-seed).
* Smartsheet failures propagate typed — the caller's fence classifies them.
* `Created At` is a NAIVE PACIFIC WALL-CLOCK string in a TEXT_NUMBER cell
  ("YYYY-MM-DD HH:MM:SS") — ABSTRACT_DATETIME is not API-creatable (errorCode
  1142); the estimate_log convention.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from shared import sheet_ids, smartsheet_client

# ---- Column titles (mirror scripts/migrations/build_rfq_log_sheet.py) ----
COL_RFQ_NUMBER = "RFQ Number"      # primary — the D1 rfqs.rfq_number identity
COL_JOB_NO = "Job #"
COL_VENDOR_KEY = "Vendor Key"      # VEN-###### — the (rfq, vendor) grain key
COL_VENDOR_NAME = "Vendor Name"    # SoR snapshot at filing time (display)
COL_STATUS = "Status"              # PICKLIST — lowercase D1 vocabulary
COL_BOX_PDF_FILE_ID = "Box PDF File ID"
COL_REVIEW_ROW_ID = "Review Row ID"  # the RFQ_Pending_Review row this copy rides
COL_DETAIL = "Detail"
COL_CREATED_AT = "Created At"      # naive Pacific wall-clock string
COL_WORKSTREAM = "Workstream"      # PICKLIST — always 'po_materials' (the sub-lane)

# The LEDGER tag stays the parent sub-lane 'po_materials' (mirroring Estimate_Log —
# a ledger is not a send surface). The REVIEW sheet's tag is the DISTINCT send-lane
# value 'po_materials_rfq' (rfq_review.WORKSTREAM_TAG) — do not conflate the two.
WORKSTREAM_TAG = "po_materials"

# The D1 rfqs status vocabulary this ledger mirrors at the (rfq, vendor) grain.
# queued = hand/pre-filing rows; filed = PDF rendered + filed + review row staged;
# sent = the PR-D send half dispatched this vendor's copy; responded = the vendor's
# quote round-tripped (R4); closed / canceled are terminal.
STATUS_QUEUED = "queued"
STATUS_FILED = "filed"
STATUS_SENT = "sent"
STATUS_RESPONDED = "responded"
STATUS_CLOSED = "closed"
STATUS_CANCELED = "canceled"
LEGAL_STATUSES: frozenset[str] = frozenset({
    STATUS_QUEUED, STATUS_FILED, STATUS_SENT, STATUS_RESPONDED, STATUS_CLOSED,
    STATUS_CANCELED,
})
# Terminal-or-later states pass ② never moves forward from (forward-only sync).
SETTLED_STATUSES: frozenset[str] = frozenset({
    STATUS_SENT, STATUS_RESPONDED, STATUS_CLOSED, STATUS_CANCELED,
})

_PACIFIC = ZoneInfo("America/Los_Angeles")


def _sheet_id() -> int:
    """The live RFQ_Log sheet id — refuses the 0 placeholder (FLIP precedes SEED)."""
    sheet_id = sheet_ids.SHEET_RFQ_LOG
    if not sheet_id:
        raise RuntimeError(
            "sheet_ids.SHEET_RFQ_LOG is still the 0 placeholder — run "
            "scripts/migrations/build_rfq_log_sheet.py and flip the printed id "
            "before any RFQ_Log write (builder-precedes-seed)."
        )
    return sheet_id


def sheet_id() -> int:
    """Public accessor for the flat RFQ_Log sheet id (the rfq_review.sheet_id()
    shape) — rfq_poll's ledger-row inline attach targets it."""
    return _sheet_id()


def created_at_now() -> str:
    """Naive Pacific wall-clock 'YYYY-MM-DD HH:MM:SS' for the Created At cell."""
    return datetime.now(_PACIFIC).strftime("%Y-%m-%d %H:%M:%S")


def find_row(
    rfq_number: str, vendor_key: str, *, sheet_id: int | None = None
) -> dict[str, Any] | None:
    """The ledger row for this (rfq_number, vendor_key), or None — the caller-side
    idempotency guard for crash-retried filings (a re-served RFQ must find-or-skip,
    never duplicate a vendor's row). `sheet_id` retargets a per-job tracking sheet
    (Feature A mirror — structure-cloned from this Log, so the columns match);
    None = the flat RFQ_Log."""
    rows = smartsheet_client.get_rows(
        sheet_id if sheet_id is not None else _sheet_id(),
        filters={COL_RFQ_NUMBER: rfq_number, COL_VENDOR_KEY: vendor_key},
    )
    return rows[0] if rows else None


def append_row(
    *,
    rfq_number: str,
    job_no: str,
    vendor_key: str,
    vendor_name: str,
    status: str,
    box_pdf_file_id: str = "",
    review_row_id: str = "",
    detail: str = "",
    created_at: str | None = None,
    sheet_id: int | None = None,
) -> int:
    """APPEND one (rfq, vendor) ledger row; return its Smartsheet row ID.

    `status` must be REGISTRY-legal (`LEGAL_STATUSES`); the Workstream cell is
    hard-populated `po_materials` (the red-team #8 posture — a brand-new sheet has
    no pre-backfill excuse for an absent tag). Caller guards duplicates via
    `find_row` first. `sheet_id` retargets a per-job tracking sheet (Feature A
    mirror — structure-cloned from this Log by shared/job_sheet, so this writes
    it unchanged); None = the flat RFQ_Log.
    """
    if status not in LEGAL_STATUSES:
        raise ValueError(
            f"illegal RFQ_Log status {status!r} (legal: {sorted(LEGAL_STATUSES)})"
        )
    [row_id] = smartsheet_client.add_rows(
        sheet_id if sheet_id is not None else _sheet_id(),
        [{
            COL_RFQ_NUMBER: rfq_number,
            COL_JOB_NO: job_no,
            COL_VENDOR_KEY: vendor_key,
            COL_VENDOR_NAME: vendor_name,
            COL_STATUS: status,
            COL_BOX_PDF_FILE_ID: box_pdf_file_id,
            COL_REVIEW_ROW_ID: review_row_id,
            COL_DETAIL: detail,
            COL_CREATED_AT: created_at if created_at is not None else created_at_now(),
            COL_WORKSTREAM: WORKSTREAM_TAG,
        }],
    )
    return row_id


def update_status(
    rfq_number: str,
    vendor_key: str,
    status: str,
    detail: str | None = None,
) -> bool:
    """Stamp a (rfq, vendor) row's Status (+ optional Detail). Returns True iff an
    update was written (False = row missing OR already at target with nothing new
    to add) — the estimate_log.update_status contract."""
    if status not in LEGAL_STATUSES:
        raise ValueError(
            f"illegal RFQ_Log status {status!r} (legal: {sorted(LEGAL_STATUSES)})"
        )
    row = find_row(rfq_number, vendor_key)
    if row is None:
        return False

    cells: dict[str, Any] = {}
    if str(row.get(COL_STATUS) or "") != status:
        cells[COL_STATUS] = status
    if detail is not None and str(row.get(COL_DETAIL) or "") != detail:
        cells[COL_DETAIL] = detail
    if not cells:
        return False

    smartsheet_client.update_rows(
        _sheet_id(), [{"_row_id": int(row["_row_id"]), **cells}]
    )
    return True
