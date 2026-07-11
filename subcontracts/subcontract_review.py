"""Subcontract_Pending_Review access — the subcontract review/approve/send surface (SC S4).

The subcontract twin of ``safety_reports.wsr_review`` / ``po_materials.po_review``. The
Subcontract_Pending_Review sheet is a **WSR schema twin** (S1 contract): column titles +
types are IDENTICAL to WSR_human_review, so the shared send engine
(``safety_reports.weekly_send`` + ``send_poll_core``) binds a subcontract ``SendConfig`` in
S4 without engine surgery. Three protocol-titled slots carry subcontract semantics (the
live sheet's column descriptions say so):

    "Job ID"       ← the **Sub Key** (SUB-###### — recipient join key → ITS_Subcontractors
                     ``Contact Email``, subcontractors.py:45)
    "Week Of"      ← the **agreement date** (the date printed on the subcontract preamble)
    "Compiled PDF" ← the **Subcontract.docx** Box link (BUILD_DECISIONS #3: NO PDF render in
                     S3c — the operator reviews the editable .docx in Word before wet
                     signature; the review row inline-attaches BOTH .docx + .xlsx
                     best-effort, but that attach is subcontract_poll's job, not this
                     module's). The column TITLE is frozen (schema-twin); its CONTENT is a
                     document link, not a single compiled PDF.

§42 — why a thin re-export, not a clone: identical rationale to po_review — under
"parameterize, not clone" (Op Stds §14) a second copy of the COL_*/STATUS_*/
``to_wsr_datetime`` surface would silently drift from the shared schema. This module binds
only what genuinely differs: ``SHEET_ID``, the ``Workstream`` tag (``'subcontracts'`` — the
P1b contamination guard HARD-HELDs any other tag on this sheet), the subcontractor-facing
seed body template, and the Notes-encoded sc_id join.

Notes-encoded sc_id (§19)
-------------------------
Each review row's Notes cell is seeded with a machine-parsable ``sc_id=<n>`` prefix (the D1
subcontracts id). Two consumers: (a) `subcontract_poll`'s drafts pass dedupes a crash-retry
via `find_row_by_sc_id` (add_wsr_row is APPEND-ONLY, so without the check a lost mark-filed
would mint a duplicate review row every cycle); (b) the status pass joins an approved/SENT
review row back to its D1 row for /status-sync. The reviewer may append prose AFTER the
prefix; the parser tolerates it.

It structurally satisfies ``safety_reports.weekly_send._ReviewModule``, so the S4
subcontract ``SendConfig`` binds ``review=cast(_ReviewModule, subcontract_review)`` exactly
as safety binds wsr_review and po binds po_review (pinned by
tests/test_subcontract_review.py).
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
# 'safety'/'progress'/'po_materials' tag here — or a 'subcontracts' tag on WSR/WPR/PO —
# is itself a contamination signal the send guard HARD-HELDs (defense in depth).
WORKSTREAM_TAG = "subcontracts"

# The bound review sheet (S1: build_subcontract_pending_review_sheet.py). Currently 0 —
# ships DARK until the operator runs the S1 builder and flips sheet_ids.py. The daemon gate
# keeps subcontract_poll off until then, so a dark get_rows(0) is never reached in practice.
SHEET_ID = sheet_ids.SHEET_SUBCONTRACT_PENDING_REVIEW

_SC_ID_RE = re.compile(r"(?:^|;\s*)sc_id=(\d+)(?:;|$)")
# sc_number is a contractual id ({YYYY}.{NNN}.{site}.{supersede}.{rev}); match up to the
# next '; ' tag boundary or end so a reviewer's appended prose can't bleed in.
_SC_NUMBER_RE = re.compile(r"(?:^|;\s*)sc_number=([^;]+?)(?:;|$)")

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
    "add_sc_review_row",
    "find_row",
    "find_row_by_sc_id",
    "notes_for_review_row",
    "row_sc_id",
    "row_sc_number",
    "row_supersedes_sc_id",
    "sc_email_body_template",
    "to_wsr_datetime",
]


def sc_email_body_template(
    *, contact_name: str, sc_number: str, job_name: str, contractor_entity: str
) -> str:
    """The fixed subcontractor-facing seed body. The reviewer edits THIS row's body before
    approving; the edited text is what the S4 send transmits — this seed is a starting
    point, never the human's voice. Greeting falls back gracefully when the subcontractor
    contact name is blank. A subcontract's terminal state is `executed` (wet signature) and
    the package includes the Schedule of Values (Annex C), so the wording asks the
    subcontractor to review, sign, and return a fully-signed copy — not the PO 'confirm
    receipt / reference on invoices' wording."""
    greeting = contact_name.strip() or "team"
    return (
        f"Hello {greeting} — please find attached Subcontract {sc_number} for the "
        f"{job_name} project, together with the Schedule of Values (Annex C). Kindly "
        f"review, execute (sign), and return a fully-signed copy at your earliest "
        f"convenience.\n\nThank you,\n{contractor_entity}"
    )


def notes_for_review_row(
    sc_id: int, sc_number: str, *, supersedes_sc_id: int | None = None
) -> str:
    """Build the Notes seed: the machine-parsable ``sc_id=<n>`` prefix (§19 — see module
    docstring) + the subcontract number + the optional predecessor D1 id (the status pass
    mirrors the superseded flip from it)."""
    parts = [f"sc_id={sc_id}", f"sc_number={sc_number}"]
    if supersedes_sc_id is not None:
        parts.append(f"supersedes_sc_id={supersedes_sc_id}")
    return "; ".join(parts)


def row_sc_id(row: dict[str, Any]) -> int | None:
    """Extract the Notes-encoded D1 sc_id from a review row, or None (a row whose Notes lost
    the prefix cannot be status-synced — surfaced by the status pass, never guessed)."""
    m = _SC_ID_RE.search(str(row.get(COL_NOTES) or ""))
    return int(m.group(1)) if m else None


def row_sc_number(row: dict[str, Any]) -> str | None:
    """Extract the Notes-encoded contractual subcontract number, or None. THE source the S4
    subcontract send envelope reads for the subcontractor-facing subject + attachment name
    (the sheet has no dedicated sc_number column — it is a WSR schema twin). subcontract_poll
    seeds it via `notes_for_review_row`; the reviewer edits only the Email Body, never Notes.
    A row whose Notes lost the tag cannot name its subcontract — subcontract_send REFUSES to
    send a numberless subcontract rather than guess (a legal document must carry its
    number)."""
    m = _SC_NUMBER_RE.search(str(row.get(COL_NOTES) or ""))
    return m.group(1).strip() if m else None


def row_supersedes_sc_id(row: dict[str, Any]) -> int | None:
    """Extract the Notes-encoded predecessor D1 id, or None (non-superseding subcontract)."""
    m = re.search(r"(?:^|;\s*)supersedes_sc_id=(\d+)(?:;|$)", str(row.get(COL_NOTES) or ""))
    return int(m.group(1)) if m else None


def find_row_by_sc_id(sc_id: int) -> dict[str, Any] | None:
    """The review row whose Notes carry ``sc_id=<sc_id>``, or None. The drafts-pass
    crash-retry dedupe (add_sc_review_row is APPEND-ONLY — without this check a lost
    mark-filed receipt would mint a duplicate review row every cycle). Full-scan is required:
    the id lives in Notes, not a filterable column."""
    for row in smartsheet_client.get_rows(SHEET_ID):
        if row_sc_id(row) == sc_id:
            return row
    return None


def add_sc_review_row(
    *,
    job_project: str,
    sub_key: str,
    agreement_date: date,
    package_link: str,
    recipient_to: str,
    cc_display: str,
    email_body: str,
    notes: str,
) -> int:
    """APPEND a new PENDING Subcontract_Pending_Review row; return its row ID.

    Delegates to the canonical writer ``wsr_review.add_wsr_row`` (zero schema
    duplication, §14), hard-binding this module's ``SHEET_ID`` +
    ``Workstream='subcontracts'`` and mapping the subcontract semantics into the protocol
    slots: ``sub_key`` → the "Job ID" column (the ITS_Subcontractors recipient join key the
    S4 send resolves TO from at dispatch), ``agreement_date`` → "Week Of", ``package_link``
    → "Compiled PDF" (the Subcontract.docx Box link — BUILD_DECISIONS #3; NO PDF render in
    S3c). `notes` MUST come from `notes_for_review_row` (the sc_id join rides it). Send
    Status seeds PENDING; approval columns are HUMAN-ONLY (F22 verifies the flip actor
    against the ITS — Subcontracts workspace share list, §46, before any dispatch).
    """
    return wsr_review.add_wsr_row(
        SHEET_ID,
        job_project=job_project,
        job_id=sub_key,
        week_of=agreement_date,
        compiled_pdf_link=package_link,
        recipient_to=recipient_to,
        cc_display=cc_display,
        email_body=email_body,
        notes=notes,
        workstream=WORKSTREAM_TAG,
    )
