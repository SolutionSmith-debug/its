"""WSR_human_review access — the Phase-5 weekly review/approve/send surface.

One row per (Job, Week). `weekly_generate` upserts the row when it compiles a
weekly packet (the Email Body is seeded from a FIXED template and is THE source of
truth `weekly_send` transmits — the reviewer edits it before approving). A human
flips `Approve for Scheduled Send` (or `Send Now`); Smartsheet `MODIFIED_BY`
captures the actor, which the F22 gate (`shared.approval_verification`) verifies
before `weekly_send` dispatches.

This module is the single source of truth for the WSR schema + the body template,
shared between the WRITE side (`weekly_generate`, Phase 5b) and the READ/send side
(`weekly_send`, Phase 5c). It supersedes `WPR_Pending_Review` for the portal flow.

Write discipline (weekly_generate)
----------------------------------
On CREATE: seed Email Body = the fixed template, Send Status = PENDING, Compiled PDF
+ resolved Recipient TO/CC display. On an EXISTING row, a recompile updates ONLY the
Compiled PDF link + the Recipient TO/CC display + Notes — it NEVER touches the
Email Body (the human's edits win) or ANY approval/send-status column (only a human
flips approval; F22 verifies the actor — a system flip would fail-closed). Re-sending
an updated packet is therefore a deliberate operator re-approval, never automatic.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from shared import sheet_ids, smartsheet_client

# ---- Column titles (mirror scripts/migrations/build_wsr_human_review_sheet.py) ----

COL_JOB_PROJECT = "Job / Project"            # primary
COL_JOB_ID = "Job ID"                        # join key → active_jobs (TO/CC at send)
COL_WEEK_OF = "Week Of"                      # DATE — the Saturday
COL_COMPILED_PDF = "Compiled PDF"            # Box link to the compiled weekly packet
COL_EMAIL_BODY = "Email Body"                # editable; source of truth for the send
COL_RECIPIENT_TO = "Recipient TO"            # display (authoritative source = active_jobs)
COL_CC = "CC"                                # display
COL_APPROVE_SCHEDULED = "Approve for Scheduled Send"  # CHECKBOX (the F22 gate column)
COL_SEND_NOW = "Send Now"                    # CHECKBOX (immediate, out-of-band)
COL_APPROVED_BY = "Approved By"
COL_APPROVED_AT = "Approved At"
COL_SEND_STATUS = "Send Status"              # PICKLIST
COL_SENT_AT = "Sent At"
COL_NOTES = "Notes"

# Send Status picklist values (match the migration's SEND_STATUS_OPTIONS).
STATUS_PENDING = "PENDING"
STATUS_SENT = "SENT"
STATUS_FAILED = "FAILED"
STATUS_HELD = "HELD"
# Transient WRITE-AHEAD intent marker. weekly_send sets this immediately BEFORE the
# irreversible Graph send, then flips it to SENT. It is NOT a dispatch candidate
# (weekly_send_poll.DISPATCH_STATUSES = {PENDING, FAILED}), so a row left in SENDING
# (a post-send SENT-stamp failure, or a daemon death mid-send) is NEVER re-dispatched
# — converting the double-send failure mode into a fail-safe stuck-unsent state that
# watchdog Check N surfaces. The live picklist has validation=false, so this value is
# writable even before it is added as a formal dropdown option (a tidy follow-up).
STATUS_SENDING = "SENDING"

SHEET_ID = sheet_ids.SHEET_WSR_HUMAN_REVIEW

# The "Approved At" / "Sent At" columns are Smartsheet ABSTRACT_DATETIME (the user-facing
# "Date/Time" type). ABSTRACT_DATETIME is tz-NAIVE: it stores/displays the literal value and
# REJECTS any UTC offset or 'Z' suffix (live-verified — a `...-07:00` value returns
# errorCode 5536). So we write Pacific wall-clock as a naive `YYYY-MM-DDTHH:MM:SS` string and
# the grid reads in local time (operator decision, 2026-06-09). The legacy plain "DATETIME"
# type is NOT creatable on a user column (docs/tech_debt.md) — ABSTRACT_DATETIME is.
WSR_DISPLAY_TZ = "America/Los_Angeles"


def to_wsr_datetime(value: datetime | str | None) -> str:
    """Format an instant as a Smartsheet ABSTRACT_DATETIME cell value — naive Pacific
    wall-clock `YYYY-MM-DDTHH:MM:SS` (no offset, no 'Z', no microseconds; see WSR_DISPLAY_TZ).

    Accepts an aware/naive datetime, an ISO-8601 string (e.g. the F22 verdict's UTC
    `modified_at` `...+00:00`), or None (→ now). An aware value is converted to Pacific; a
    naive value is taken as-is. Stripping the offset is load-bearing: ABSTRACT_DATETIME
    rejects an offset-bearing string outright (errorCode 5536).
    """
    if value is None:
        dt = datetime.now(ZoneInfo(WSR_DISPLAY_TZ))
    elif isinstance(value, str):
        dt = datetime.fromisoformat(value)
    else:
        dt = value
    if dt.tzinfo is not None:
        dt = dt.astimezone(ZoneInfo(WSR_DISPLAY_TZ))
    return dt.replace(tzinfo=None, microsecond=0).isoformat()


def email_body_template(
    *, contact_name: str, week_label: str, job_name: str, evergreen_contact: str
) -> str:
    """The fixed seed body (brief §E). The reviewer edits this in WSR before approval;
    the edited text is what `weekly_send` transmits. Greeting falls back gracefully
    when the safety-reports contact name is blank."""
    greeting = contact_name.strip() or "team"
    return (
        f"Good morning {greeting} — please see the attached documents for the week "
        f"of {week_label} for {job_name}. Reach out to {evergreen_contact} with any "
        f"questions.\n\nThank you,\nEvergreen Renewables"
    )


def find_row(sheet_id: int, job_id: str, week_of: date) -> dict[str, Any] | None:
    """Return the WSR row for (Job ID, Week Of), or None. The (job, week) identity."""
    want_job = (job_id or "").strip()
    want_week = week_of.isoformat()
    for r in smartsheet_client.get_rows(sheet_id):
        if str(r.get(COL_JOB_ID) or "").strip() != want_job:
            continue
        cell_week = r.get(COL_WEEK_OF)
        cell_week_iso = cell_week.isoformat() if isinstance(cell_week, date) else str(cell_week or "")[:10]
        if cell_week_iso == want_week:
            return r
    return None


def add_wsr_row(
    sheet_id: int,
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
    """APPEND a new WSR_human_review row for (job, week); return its row ID.

    APPEND-ONLY (operator decision 2026-06-09): every weekly compilation creates a NEW
    PENDING row — a prior compilation's row (especially a SENT one) is NEVER overwritten, so
    the send history is preserved (you can see WHAT was sent, WHEN, and which packet). Seeds
    Email Body (the fixed template — the reviewer edits THIS row's body before approving) +
    Send Status=PENDING. Multiple rows per (job, week) are expected; weekly_send_poll
    dispatches each independently by row ID (a SENT row is skipped), so the reviewer approves
    the latest compilation. (Supersedes the prior find-or-update `upsert_row`, whose in-place
    UPDATE clobbered an already-SENT row's Compiled-PDF link — the bug this fixes.)
    """
    [row_id] = smartsheet_client.add_rows(
        sheet_id,
        [{
            COL_JOB_PROJECT: job_project,
            COL_JOB_ID: job_id,
            COL_WEEK_OF: week_of.isoformat(),
            COL_COMPILED_PDF: compiled_pdf_link,
            COL_EMAIL_BODY: email_body,
            COL_RECIPIENT_TO: recipient_to,
            COL_CC: cc_display,
            COL_SEND_STATUS: STATUS_PENDING,
            COL_NOTES: notes,
        }],
    )
    return row_id
