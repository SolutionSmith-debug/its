"""WPR_human_review access — the weekly PROGRESS review/approve/send surface.

The progress twin of ``safety_reports.wsr_review``. The WPR and WSR sheets share an
IDENTICAL schema (``build_wpr_human_review_sheet.py`` mirrors
``build_wsr_human_review_sheet.py``), so this module REUSES wsr_review's column-title
constants, status constants, the ABSTRACT_DATETIME formatter, and the body template
rather than re-declaring them.

§42 — why a thin re-export, not a clone: under the locked "parameterize, not clone"
decision (Op Stds §14) a second copy of the COL_*/STATUS_*/``to_wsr_datetime`` surface
would be a maintenance hazard the moment the shared schema changes (the two would drift
silently). wsr_review is the de-facto home of the shared WSR/WPR schema; this module
binds only the two things that genuinely differ per workstream: the target ``SHEET_ID``
and the ``Workstream`` report-family tag (``'progress'``). When a third consumer appears
the schema gets hoisted to ``shared/`` (§14's ≥4-reuse threshold); until then this is the
minimal coupling.

It structurally satisfies ``safety_reports.weekly_send._ReviewModule``, so the progress
``SendConfig`` (P5) binds ``review=cast(_ReviewModule, wpr_review)`` exactly as safety
binds wsr_review; the progress ``DaemonConfig`` (P5) reads the COL_*/STATUS_* re-exports.
"""
from __future__ import annotations

from datetime import date

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
    email_body_template,
    find_row,
    to_wsr_datetime,
)
from shared import sheet_ids

# Report-family tag this sheet enforces (P1b cross-workstream send guard). The WPR
# sheet is the PROGRESS review sheet → 'progress'; a 'safety' tag here is itself a
# contamination signal the send guard HARD-HELDs (defense in depth).
WORKSTREAM_TAG = "progress"

# The bound review sheet. 0 until build_wpr_human_review_sheet.py is run and
# SHEET_WPR_HUMAN_REVIEW is flipped in shared/sheet_ids.py (FLIP precedes SEED).
SHEET_ID = sheet_ids.SHEET_WPR_HUMAN_REVIEW

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
    "add_wpr_row",
    "email_body_template",
    "find_row",
    "to_wsr_datetime",
]


def add_wpr_row(
    *,
    job_project: str,
    job_id: str,
    week_of: date,
    compiled_pdf_link: str,
    recipient_to: str,
    cc_display: str,
    email_body: str,
    notes: str,
) -> int:
    """APPEND a new PENDING WPR_human_review row for (job, week); return its row ID.

    Delegates to the canonical writer ``wsr_review.add_wsr_row`` (zero schema
    duplication, §14), hard-binding this module's ``SHEET_ID`` and ``Workstream='progress'``.
    The Workstream tag is what the progress send guard later verifies — a progress row
    that reached the safety sheet (or a safety row that reached this one) is HARD-HELD as
    contamination before any send.
    """
    return wsr_review.add_wsr_row(
        SHEET_ID,
        job_project=job_project,
        job_id=job_id,
        week_of=week_of,
        compiled_pdf_link=compiled_pdf_link,
        recipient_to=recipient_to,
        cc_display=cc_display,
        email_body=email_body,
        notes=notes,
        workstream=WORKSTREAM_TAG,
    )
