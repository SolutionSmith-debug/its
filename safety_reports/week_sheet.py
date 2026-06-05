"""Per-job, per-Saturday-week Smartsheet 'week sheet' for the Safety Portal pull model.

Purpose
-------
    One sheet per (job, Saturday→Friday week), `"<project> — week of <Saturday>"`,
    living in that project's Field Reports Smartsheet folder. The Phase-5 intake
    portal branch writes one row per HMAC-verified submission here (the durable
    per-submission record + the Box-link pointer); `weekly_generate` later appends a
    read-only rollup row (Phase 5b) and compiles the week's packet.

    This is the **Saturday→Friday** portal-flow sibling of `week_folder.py` (which is
    the legacy **Monday-ISO** email-path scaffold of two cloned template sheets).
    The two are deliberately separate (preservation-over-refactor, Op Stds §14): the
    email path is dormant but untouched; the portal flow gets its own week sheet whose
    schema it actually needs (a Submission-UUID dedupe key + a Box-link column — both
    absent from the legacy Daily Reports schema, a long-standing gap).

Schema (built via API on first create — no template needed, deploy-session safe)
--------------------------------------------------------------------------------
    Submission     (primary) — human label, e.g. "2026-06-05 — Job Hazard Analysis"
    Submission UUID          — the dedupe key (portal submission_uuid)
    Form Code                — e.g. "jha-v1"
    Work Date      (DATE)    — the form work-date (week membership keys on this)
    Submitted At             — portal created_at, Pacific ISO (everything Pacific)
    Submission PDF           — Box link to the rendered per-submission PDF
    Row Type                 — "Submission" | "Rollup" (TEXT controlled-vocab; kept
                               out of a PICKLIST so the picklist-sync registry need
                               not learn this sheet — Op Stds §14)
    Status                   — "Active" | "Superseded" (amend supersedes the prior)
    Superseded By            — the superseding submission_uuid (amend pointer)
    Notes                    — freeform (incomplete-checklist tags, etc.)

Idempotency / amendments
------------------------
    `ensure_week_sheet` is find-or-create (race-tolerant). `find_submission_row` is
    the Python-side dedupe authority — intake checks it before re-filing a re-pulled
    submission. `supersede_row` marks a prior submission row Superseded + points it
    at the amending UUID (Box keeps BOTH PDFs; the sheet shows the supersession).

Failure modes
-------------
    A `SmartsheetError` propagates to the caller (intake), which soft-fails the
    submission to status='error' so it re-pulls next cycle — never a silent drop. An
    unknown `project_name` (not in FIELD_REPORTS_FOLDER_BY_PROJECT) raises `KeyError`:
    writing to nowhere observable is worse than a loud refusal (CLAUDE.md "never silent").
"""
from __future__ import annotations

from datetime import date
from typing import Any

from shared import error_log, safety_week, sheet_ids, smartsheet_client
from shared.error_log import Severity

SCRIPT_NAME = "safety_reports.week_sheet"

# ---- Column titles (single source of truth for reads + writes) ----------

COL_SUBMISSION = "Submission"
COL_SUBMISSION_UUID = "Submission UUID"
COL_FORM_CODE = "Form Code"
COL_WORK_DATE = "Work Date"
COL_SUBMITTED_AT = "Submitted At"
COL_SUBMISSION_PDF = "Submission PDF"
COL_ROW_TYPE = "Row Type"
COL_STATUS = "Status"
COL_SUPERSEDED_BY = "Superseded By"
COL_NOTES = "Notes"

# Controlled vocabularies (TEXT cells, not PICKLIST — see module docstring).
ROW_TYPE_SUBMISSION = "Submission"
ROW_TYPE_ROLLUP = "Rollup"
STATUS_ACTIVE = "Active"
STATUS_SUPERSEDED = "Superseded"

# The schema passed to create_sheet_in_folder. Order = left-to-right UI order.
# Exactly one primary; Smartsheet requires the primary to be TEXT_NUMBER.
WEEK_SHEET_COLUMNS: list[dict[str, Any]] = [
    {"title": COL_SUBMISSION, "type": "TEXT_NUMBER", "primary": True},
    {"title": COL_SUBMISSION_UUID, "type": "TEXT_NUMBER"},
    {"title": COL_FORM_CODE, "type": "TEXT_NUMBER"},
    {"title": COL_WORK_DATE, "type": "DATE"},
    {"title": COL_SUBMITTED_AT, "type": "TEXT_NUMBER"},
    {"title": COL_SUBMISSION_PDF, "type": "TEXT_NUMBER"},
    {"title": COL_ROW_TYPE, "type": "TEXT_NUMBER"},
    {"title": COL_STATUS, "type": "TEXT_NUMBER"},
    {"title": COL_SUPERSEDED_BY, "type": "TEXT_NUMBER"},
    {"title": COL_NOTES, "type": "TEXT_NUMBER"},
]


def week_sheet_name(project_name: str, work_date: date) -> str:
    """Return the canonical week-sheet name for a (project, work-date).

    Name keys on the Saturday that opens the work-date's week, so every day
    Sat→Fri maps to one sheet, e.g. `"Bradley 1 — week of 2026-05-30"`.
    """
    saturday = safety_week.week_bounds(work_date).start
    return f"{project_name} — week of {saturday.isoformat()}"


def ensure_week_sheet(project_name: str, work_date: date) -> int:
    """Find-or-create the (project, week) sheet; return its sheet ID.

    Located in the project's Field Reports folder (FIELD_REPORTS_FOLDER_BY_PROJECT).
    Idempotent: a second call in the same week returns the same sheet with no
    write. Race-tolerant: two concurrent creators can both pass the find step
    (Smartsheet does not enforce sheet-name uniqueness) — we re-find after create
    and adopt the first match, WARN-logging the duplicate for operator cleanup
    (mirrors `week_folder.ensure_current_week_folder`). Bounded blast radius: one
    extra empty sheet.

    Raises `KeyError` for an unknown `project_name` (never silently write nowhere).
    """
    folder_id = sheet_ids.FIELD_REPORTS_FOLDER_BY_PROJECT[project_name]
    name = week_sheet_name(project_name, work_date)

    existing = smartsheet_client.find_sheet_by_name_in_folder(folder_id, name)
    if existing is not None:
        return existing

    sheet_id = smartsheet_client.create_sheet_in_folder(
        folder_id, name, WEEK_SHEET_COLUMNS
    )
    post_find = smartsheet_client.find_sheet_by_name_in_folder(folder_id, name)
    if post_find is not None and post_find != sheet_id:
        error_log.log(
            Severity.WARN,
            SCRIPT_NAME,
            (
                f"Duplicate week sheet {name!r} under folder {folder_id} "
                f"(project={project_name!r}); using first match {post_find}, "
                f"manual cleanup needed for {sheet_id}."
            ),
            error_code="week_sheet_race_duplicate",
        )
        return post_find
    return sheet_id


def find_submission_row(sheet_id: int, submission_uuid: str) -> dict[str, Any] | None:
    """Return the submission row whose Submission UUID == `submission_uuid`, or None.

    The Python-side dedupe authority (survives a wiped seen-set state file): intake
    calls this before re-filing a re-pulled submission. Matches Row Type=Submission
    so a rollup row can never shadow a submission lookup. The returned dict carries
    `_row_id` and `Submission PDF` (the Box link) so a re-pull can recover the link
    and still post the mark-filed receipt without re-uploading.
    """
    key = (submission_uuid or "").strip()
    if not key:
        return None
    for row in smartsheet_client.get_rows(sheet_id):
        if str(row.get(COL_SUBMISSION_UUID) or "").strip() != key:
            continue
        if (row.get(COL_ROW_TYPE) or "") == ROW_TYPE_SUBMISSION:
            return row
    return None


def write_submission_row(
    sheet_id: int,
    *,
    submission_uuid: str,
    form_code: str,
    work_date: date,
    title: str,
    box_link: str,
    submitted_at: str,
    notes: str = "",
) -> int:
    """Append one Active submission row; return the new row ID.

    `title` is the human label for the primary `Submission` column; `submitted_at`
    is a pre-formatted Pacific ISO string (caller converts the D1 unixepoch).
    """
    label = f"{work_date.isoformat()} — {title}".strip(" —")
    [row_id] = smartsheet_client.add_rows(
        sheet_id,
        [
            {
                COL_SUBMISSION: label or work_date.isoformat(),
                COL_SUBMISSION_UUID: submission_uuid,
                COL_FORM_CODE: form_code,
                COL_WORK_DATE: work_date.isoformat(),
                COL_SUBMITTED_AT: submitted_at,
                COL_SUBMISSION_PDF: box_link,
                COL_ROW_TYPE: ROW_TYPE_SUBMISSION,
                COL_STATUS: STATUS_ACTIVE,
                COL_NOTES: notes,
            }
        ],
    )
    return row_id


def supersede_row(sheet_id: int, prior_uuid: str, new_uuid: str) -> bool:
    """Mark the prior submission row Superseded, pointing it at the amending UUID.

    Returns True if a prior row was found and updated, False if the prior UUID has
    no row on this sheet (the amend names a submission we never filed — the caller
    logs it; Box still keeps both PDFs). Never deletes — the superseded row stays
    for the audit trail.
    """
    prior = find_submission_row(sheet_id, prior_uuid)
    if prior is None:
        return False
    smartsheet_client.update_rows(
        sheet_id,
        [
            {
                "_row_id": prior["_row_id"],
                COL_STATUS: STATUS_SUPERSEDED,
                COL_SUPERSEDED_BY: new_uuid,
            }
        ],
    )
    return True
