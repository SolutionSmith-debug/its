"""PO_Pending_Review access — the PO review/approve/send surface (PO S4).

The PO twin of ``safety_reports.wsr_review`` / ``progress_reports.wpr_review``. The
PO_Pending_Review sheet is a **WSR schema twin** (S1 contract): column titles + types
are IDENTICAL to WSR_human_review, so the shared send engine
(``safety_reports.weekly_send`` + ``send_poll_core``) binds a PO ``SendConfig`` in S5
without engine surgery. Three protocol-titled slots carry PO semantics (the live
sheet's column descriptions say so):

    "Job ID"       ← the **Vendor Key** (VEN-###### — recipient join key → ITS_Vendors)
    "Week Of"      ← the **PO date** (the date printed on the PO)
    "Compiled PDF" ← the **PO PDF** Box link

§42 — why a thin re-export, not a clone: identical rationale to wpr_review — under
"parameterize, not clone" (Op Stds §14) a second copy of the COL_*/STATUS_*/
``to_wsr_datetime`` surface would silently drift from the shared schema. This module
binds only what genuinely differs: ``SHEET_ID``, the ``Workstream`` tag
(``'po_materials'`` — the P1b contamination guard HARD-HELDs any other tag on this
sheet), the vendor-facing seed body template, and the Notes-encoded po_id join.

Notes-encoded po_id (§19)
-------------------------
Each review row's Notes cell is seeded with a machine-parsable ``po_id=<n>`` prefix
(the D1 purchase_orders id). Two consumers: (a) `po_poll`'s drafts pass dedupes a
crash-retry via `find_row_by_po_id` (add_wsr_row is APPEND-ONLY, so without the check
a lost mark-filed would mint a duplicate review row every cycle); (b) the status pass
joins an approved/SENT review row back to its D1 row for /status-sync. The reviewer
may append prose AFTER the prefix; the parser tolerates it.

It structurally satisfies ``safety_reports.weekly_send._ReviewModule``, so the S5 PO
``SendConfig`` binds ``review=cast(_ReviewModule, po_review)`` exactly as safety binds
wsr_review and progress binds wpr_review (pinned by tests/test_po_review.py).
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

# Report-family tag this sheet enforces (P1b cross-workstream send guard). A
# 'safety'/'progress' tag here — or a 'po_materials' tag on WSR/WPR — is itself a
# contamination signal the send guard HARD-HELDs (defense in depth).
WORKSTREAM_TAG = "po_materials"

# The bound review sheet (S1: build_po_pending_review_sheet.py, flipped 2026-07-09).
SHEET_ID = sheet_ids.SHEET_PO_PENDING_REVIEW

_PO_ID_RE = re.compile(r"(?:^|;\s*)po_id=(\d+)(?:;|$)")
# po_number is a contractual id ({YYYY.NNN}.{site}.{supersede}.{rev}); match up to the
# next '; ' tag boundary or end so a reviewer's appended prose can't bleed in.
_PO_NUMBER_RE = re.compile(r"(?:^|;\s*)po_number=([^;]+?)(?:;|$)")

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
    "SHEET_ID",
    "STATUS_FAILED",
    "STATUS_HELD",
    "STATUS_PENDING",
    "STATUS_SENDING",
    "STATUS_SENT",
    "WORKSTREAM_TAG",
    "WSR_DISPLAY_TZ",
    "add_po_review_row",
    "find_row",
    "find_row_by_po_id",
    "notes_for_review_row",
    "po_email_body_template",
    "row_po_id",
    "row_po_number",
    "row_supersedes_po_id",
    "to_wsr_datetime",
]


def po_email_body_template(
    *, contact_name: str, po_number: str, job_name: str, purchaser_entity: str
) -> str:
    """The fixed vendor-facing seed body. The reviewer edits THIS row's body before
    approving; the edited text is what the S5 send transmits — this seed is a
    starting point, never the human's voice. Greeting falls back gracefully when the
    vendor contact name is blank."""
    greeting = contact_name.strip() or "team"
    return (
        f"Hello {greeting} — please find attached Purchase Order {po_number} for the "
        f"{job_name} project. Kindly confirm receipt, countersign, and return a copy "
        f"at your earliest convenience. Please reference PO {po_number} on all "
        f"invoices and correspondence.\n\nThank you,\n{purchaser_entity}"
    )


def notes_for_review_row(
    po_id: int, po_number: str, *, supersedes_po_id: int | None = None
) -> str:
    """Build the Notes seed: the machine-parsable ``po_id=<n>`` prefix (§19 — see
    module docstring) + the PO number + the optional predecessor D1 id (the status
    pass mirrors the superseded flip from it)."""
    parts = [f"po_id={po_id}", f"po_number={po_number}"]
    if supersedes_po_id is not None:
        parts.append(f"supersedes_po_id={supersedes_po_id}")
    return "; ".join(parts)


def row_po_id(row: dict[str, Any]) -> int | None:
    """Extract the Notes-encoded D1 po_id from a review row, or None (a row whose
    Notes lost the prefix cannot be status-synced — surfaced by the status pass,
    never guessed)."""
    m = _PO_ID_RE.search(str(row.get(COL_NOTES) or ""))
    return int(m.group(1)) if m else None


def row_po_number(row: dict[str, Any]) -> str | None:
    """Extract the Notes-encoded contractual PO number, or None. THE source the S5 PO
    send envelope reads for the vendor-facing subject + attachment name (the sheet has
    no dedicated po_number column — it is a WSR schema twin). po_poll seeds it via
    `notes_for_review_row`; the reviewer edits only the Email Body, never Notes. A row
    whose Notes lost the tag cannot name its PO — po_send REFUSES to send a numberless
    PO rather than guess (a legal document must carry its number)."""
    m = _PO_NUMBER_RE.search(str(row.get(COL_NOTES) or ""))
    return m.group(1).strip() if m else None


def row_supersedes_po_id(row: dict[str, Any]) -> int | None:
    """Extract the Notes-encoded predecessor D1 id, or None (non-superseding PO)."""
    m = re.search(r"(?:^|;\s*)supersedes_po_id=(\d+)(?:;|$)", str(row.get(COL_NOTES) or ""))
    return int(m.group(1)) if m else None


def find_row_by_po_id(po_id: int) -> dict[str, Any] | None:
    """The review row whose Notes carry ``po_id=<po_id>``, or None. The drafts-pass
    crash-retry dedupe (add_po_review_row is APPEND-ONLY — without this check a lost
    mark-filed receipt would mint a duplicate review row every cycle)."""
    for row in smartsheet_client.get_rows(SHEET_ID):
        if row_po_id(row) == po_id:
            return row
    return None


def add_po_review_row(
    *,
    job_project: str,
    vendor_key: str,
    po_date: date,
    pdf_link: str,
    recipient_to: str,
    cc_display: str,
    email_body: str,
    notes: str,
) -> int:
    """APPEND a new PENDING PO_Pending_Review row; return its row ID.

    Delegates to the canonical writer ``wsr_review.add_wsr_row`` (zero schema
    duplication, §14), hard-binding this module's ``SHEET_ID`` +
    ``Workstream='po_materials'`` and mapping the PO semantics into the protocol
    slots: ``vendor_key`` → the "Job ID" column (the ITS_Vendors recipient join key
    the S5 send resolves TO from at dispatch), ``po_date`` → "Week Of",
    ``pdf_link`` → "Compiled PDF". `notes` MUST come from `notes_for_review_row`
    (the po_id join rides it). Send Status seeds PENDING; approval columns are
    HUMAN-ONLY (F22 verifies the flip actor against the ITS — Purchase Orders
    workspace share list, §46, before any dispatch).
    """
    return wsr_review.add_wsr_row(
        SHEET_ID,
        job_project=job_project,
        job_id=vendor_key,
        week_of=po_date,
        compiled_pdf_link=pdf_link,
        recipient_to=recipient_to,
        cc_display=cc_display,
        email_body=email_body,
        notes=notes,
        workstream=WORKSTREAM_TAG,
    )
