"""RFQ_Pending_Review access — the RFQ review/approve/send surface (R2).

The RFQ twin of ``po_materials.po_review``. The RFQ_Pending_Review sheet is a
**WSR schema twin** (the S1 contract, cloned from PO_Pending_Review COLUMN-FOR-
COLUMN by scripts/migrations/build_rfq_pending_review_sheet.py): column titles +
types are IDENTICAL to WSR_human_review, so the shared send engine
(``safety_reports.weekly_send`` + ``send_poll_core``) can bind an RFQ ``SendConfig``
in PR-D without engine surgery. Three protocol-titled slots carry RFQ semantics
(the live sheet's column descriptions say so):

    "Job ID"       ← the **Vendor Key** (VEN-###### — recipient join key → ITS_Vendors)
    "Week Of"      ← the **RFQ date** (the date printed on the RFQ)
    "Compiled PDF" ← the **RFQ PDF** Box link

One row per **(rfq, vendor)** (ADR-0004 decision 12) — the send grain.

THE WORKSTREAM TAG IS 'po_materials_rfq', NOT 'po_materials' — read this before PR-D
--------------------------------------------------------------------------------------
The P1b contamination guard (``weekly_send`` Stage 2b) distinguishes SENDER LANES:
a row whose tag ≠ the dispatching ``SendConfig.workstream_tag`` is HARD-HELD
fail-closed, but an ABSENT tag proceeds and a MATCHING tag sails through. po_send's
``SendConfig`` binds ``workstream_tag="po_materials"`` — so if RFQ rows carried
'po_materials', a misbound sheet id / an operator row-copy onto PO_Pending_Review
would let po_send_poll DISPATCH an RFQ row as if it were an approved PO. The
DISTINCT lane tag makes cross-lane dispatch IMPOSSIBLE: 'po_materials_rfq' fails
po_send's guard (and every other lane's), and PR-D's rfq_send MUST bind
``workstream_tag="po_materials_rfq"`` so only it can dispatch these rows. The value
is registered in ``shared.picklist_validation`` (the review sheet's Workstream
picklist admits ONLY this value) and hard-populated at row creation (red-team #8:
a brand-new sheet has no pre-backfill excuse for an absent tag). The RFQ_Log
LEDGER keeps the parent 'po_materials' tag — a ledger is not a send surface.

Notes-encoded join (§19, the po_review pattern)
-----------------------------------------------
Each review row's Notes cell is seeded ``rfq_id=<n>; rfq_number=<s>;
vendor_key=<k>``. Consumers: (a) pass ①'s crash-retry dedupe
(`find_row_by_rfq_vendor` — add_wsr_row is APPEND-ONLY); (b) pass ②'s status-sync
join back to the D1 row; (c) PR-D's send envelope (the vendor-facing subject +
attachment name read `row_rfq_number` — a numberless row is REFUSED, never guessed).
The vendor key ALSO rides the "Job ID" protocol slot (the engine's recipient join);
the Notes copy is the defensive duplicate the parser cross-checks.

FLIP precedes SEED: every write refuses while ``sheet_ids.SHEET_RFQ_PENDING_REVIEW``
is the 0 placeholder (builder-precedes-seed).
"""
from __future__ import annotations

import re
from datetime import date
from typing import Any

from safety_reports import wsr_review
from safety_reports.wsr_review import (
    COL_APPROVE_SCHEDULED,
    COL_APPROVED_AT,
    COL_APPROVED_BY,
    COL_CC,
    COL_COMPILED_PDF,
    COL_EMAIL_BODY,
    COL_JOB_ID,
    COL_JOB_PROJECT,
    COL_NOTES,
    COL_RECIPIENT_TO,
    COL_SEND_NOW,
    COL_SEND_STATUS,
    COL_SENT_AT,
    COL_WEEK_OF,
    COL_WORKSTREAM,
    STATUS_FAILED,
    STATUS_HELD,
    STATUS_PENDING,
    STATUS_SENDING,
    STATUS_SENT,
    WSR_DISPLAY_TZ,
    find_row,
    to_wsr_datetime,
)
from shared import sheet_ids, smartsheet_client

# The DISTINCT RFQ send-lane tag (see module docstring — NOT 'po_materials').
WORKSTREAM_TAG = "po_materials_rfq"

_RFQ_ID_RE = re.compile(r"(?:^|;\s*)rfq_id=(\d+)(?:;|$)")
# rfq_number is a contractual id; match up to the next '; ' tag boundary or end so a
# reviewer's appended prose can't bleed in (the po_review pattern).
_RFQ_NUMBER_RE = re.compile(r"(?:^|;\s*)rfq_number=([^;]+?)(?:;|$)")
_VENDOR_KEY_RE = re.compile(r"(?:^|;\s*)vendor_key=([^;]+?)(?:;|$)")

__all__ = [
    "COL_APPROVE_SCHEDULED",
    "COL_APPROVED_AT",
    "COL_APPROVED_BY",
    "COL_CC",
    "COL_COMPILED_PDF",
    "COL_EMAIL_BODY",
    "COL_JOB_ID",
    "COL_JOB_PROJECT",
    "COL_NOTES",
    "COL_RECIPIENT_TO",
    "COL_SEND_NOW",
    "COL_SEND_STATUS",
    "COL_SENT_AT",
    "COL_WEEK_OF",
    "COL_WORKSTREAM",
    "STATUS_FAILED",
    "STATUS_HELD",
    "STATUS_PENDING",
    "STATUS_SENDING",
    "STATUS_SENT",
    "WORKSTREAM_TAG",
    "WSR_DISPLAY_TZ",
    "add_rfq_review_row",
    "find_row",
    "find_row_by_rfq_vendor",
    "notes_for_review_row",
    "rfq_email_body_template",
    "row_rfq_id",
    "row_rfq_number",
    "row_vendor_key",
    "sheet_id",
    "to_wsr_datetime",
]


def sheet_id() -> int:
    """The live RFQ_Pending_Review sheet id — refuses the 0 placeholder (FLIP
    precedes SEED; run scripts/migrations/build_rfq_pending_review_sheet.py)."""
    sid = sheet_ids.SHEET_RFQ_PENDING_REVIEW
    if not sid:
        raise RuntimeError(
            "sheet_ids.SHEET_RFQ_PENDING_REVIEW is still the 0 placeholder — run "
            "scripts/migrations/build_rfq_pending_review_sheet.py and flip the "
            "printed id before any RFQ review write (builder-precedes-seed)."
        )
    return sid


def rfq_email_body_template(
    *, contact_name: str, rfq_number: str, job_name: str, purchaser_entity: str,
    due_date_display: str,
) -> str:
    """The fixed vendor-facing seed body. The reviewer edits THIS row's body before
    approving; the edited text is what PR-D's send transmits — this seed is a
    starting point, never the human's voice (the po_review posture). Greeting falls
    back gracefully when the vendor contact name is blank."""
    greeting = contact_name.strip() or "team"
    return (
        f"Hello {greeting} — please find attached Request for Quote {rfq_number} "
        f"for the {job_name} project. Kindly submit your quote on the attached form "
        f"or your own letterhead by {due_date_display}, referencing RFQ {rfq_number}. "
        f"This is a request for quote, not a purchase order.\n\n"
        f"Thank you,\n{purchaser_entity}"
    )


def notes_for_review_row(rfq_id: int, rfq_number: str, vendor_key: str) -> str:
    """Build the Notes seed: the machine-parsable ``rfq_id=<n>`` prefix (§19) + the
    RFQ number + the vendor key (the (rfq, vendor) grain — pass ② and PR-D's send
    both join on it)."""
    return f"rfq_id={rfq_id}; rfq_number={rfq_number}; vendor_key={vendor_key}"


def row_rfq_id(row: dict[str, Any]) -> int | None:
    """Extract the Notes-encoded D1 rfq_id from a review row, or None (a row whose
    Notes lost the prefix cannot be status-synced — surfaced, never guessed)."""
    m = _RFQ_ID_RE.search(str(row.get(COL_NOTES) or ""))
    return int(m.group(1)) if m else None


def row_rfq_number(row: dict[str, Any]) -> str | None:
    """Extract the Notes-encoded contractual RFQ number, or None. THE source PR-D's
    send envelope reads for the vendor-facing subject + attachment name (the sheet
    is a WSR schema twin — no dedicated rfq_number column). A row whose Notes lost
    the tag cannot name its RFQ — the send REFUSES a numberless row, never guesses."""
    m = _RFQ_NUMBER_RE.search(str(row.get(COL_NOTES) or ""))
    return m.group(1).strip() if m else None


def row_vendor_key(row: dict[str, Any]) -> str | None:
    """The row's vendor key: the "Job ID" protocol slot, cross-checked against the
    Notes copy when both are present (a mismatch returns None — a spliced/hand-edited
    row must never resolve a recipient), falling back to whichever exists."""
    slot = str(row.get(COL_JOB_ID) or "").strip()
    m = _VENDOR_KEY_RE.search(str(row.get(COL_NOTES) or ""))
    noted = m.group(1).strip() if m else ""
    if slot and noted:
        return slot if slot == noted else None
    return slot or noted or None


def find_row_by_rfq_vendor(rfq_id: int, vendor_key: str) -> dict[str, Any] | None:
    """The review row whose Notes carry ``rfq_id=<rfq_id>`` AND whose vendor key
    resolves to `vendor_key`, or None. Pass ①'s crash-retry dedupe (add_wsr_row is
    APPEND-ONLY — without this check a lost mark-filed receipt would mint a
    duplicate review row per vendor every cycle)."""
    for row in smartsheet_client.get_rows(sheet_id()):
        if row_rfq_id(row) == rfq_id and row_vendor_key(row) == vendor_key:
            return row
    return None


def add_rfq_review_row(
    *,
    job_project: str,
    vendor_key: str,
    rfq_date: date,
    pdf_link: str,
    recipient_to: str,
    cc_display: str,
    email_body: str,
    notes: str,
) -> int:
    """APPEND a new PENDING RFQ_Pending_Review row; return its row ID.

    Delegates to the canonical writer ``wsr_review.add_wsr_row`` (zero schema
    duplication, §14), hard-binding this module's sheet id +
    ``Workstream='po_materials_rfq'`` (the DISTINCT lane tag — module docstring)
    and mapping the RFQ semantics into the protocol slots: ``vendor_key`` → the
    "Job ID" column (the ITS_Vendors recipient join key PR-D's send resolves TO
    from at dispatch), ``rfq_date`` → "Week Of", ``pdf_link`` → "Compiled PDF".
    `notes` MUST come from `notes_for_review_row` (the rfq_id/vendor_key join rides
    it). Send Status seeds PENDING; approval columns are HUMAN-ONLY (F22 verifies
    the flip actor before any dispatch)."""
    return wsr_review.add_wsr_row(
        sheet_id(),
        job_project=job_project,
        job_id=vendor_key,
        week_of=rfq_date,
        compiled_pdf_link=pdf_link,
        recipient_to=recipient_to,
        cc_display=cc_display,
        email_body=email_body,
        notes=notes,
        workstream=WORKSTREAM_TAG,
    )
