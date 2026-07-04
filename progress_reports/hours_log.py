"""Per-job Hours Log — the P7 standing tracker for field-reported crew hours (Track 2, Slice 1).

One-way-up mirror of the D1 `time_entries` SoR into a per-job **standing** "Hours Log" Smartsheet
in the `ITS — Progress Reporting` workspace, in the SAME per-job folder as the week sheets (found
by `safety_naming.job_folder_name`, so they sit side by side). SEND-FREE + AI-FREE (Op Stds v19
§51 — ITS-owned structured-SoR write-back); the daemon that drives it (`field_ops.fieldops_sync`
hours pass) is the capability-gated actuator.

Design (operator-confirmed defaults, 2026-07-04):
- **Single standing sheet per job** (`<Job> — Hours Log`), append-only forever — NOT per-week.
  Archive-on-closure (move to the Archive workspace when the job closes) + a SoR-safe row-cap
  monitor are tracked fast-follows; this module NEVER deletes a row (the accumulating SoR rule).
- **Progress workspace only**, single-destination (unlike the dual-sheet job-identity up-sync).
- find-or-create the sheet (+ the A1 capacity margin-check on the create branch, advisory);
  idempotent upsert by `Entry UUID` (== `time_entries.uuid`); an amend APPENDS its own row and
  marks the prior row Superseded (append-only edit chain, mirrors `week_sheet.supersede_row`).

The module is a thin Smartsheet write helper (like `week_sheet` / `active_jobs_writer`) — NOT a
daemon entry point, so it is not itself in `GATED_SCRIPTS`; its sole caller `fieldops_sync` is.
"""
from __future__ import annotations

from typing import Any

from safety_reports import safety_naming
from shared import error_log, sheet_capacity, sheet_ids, smartsheet_client
from shared.error_log import Severity

SCRIPT_NAME = "progress_reports.hours_log"

WORKSPACE_ID = sheet_ids.WORKSPACE_PROGRESS_REPORTING

# Smartsheet sheet-name cap (HTTP 400 errorCode 1041) — same constant as week_sheet.SHEET_NAME_MAX.
SHEET_NAME_MAX = 50
SHEET_SUFFIX = " — Hours Log"

# ---- Column titles (single source of truth for reads + writes) ----
COL_ENTRY = "Entry"              # primary (TEXT_NUMBER): "<work date> — <personnel>" human label
COL_ENTRY_UUID = "Entry UUID"    # find-or-create + amend key (== time_entries.uuid)
COL_WORK_DATE = "Work Date"      # DATE (field-reported work day)
COL_PERSONNEL = "Personnel"      # DISPLAY NAME (personnel.name) — NEVER a username (House Reflex §5)
COL_HOURS = "Hours"
COL_STARTED = "Started"
COL_ENDED = "Ended"
COL_NOTES = "Notes"
COL_STATUS = "Status"            # Active | Superseded — TEXT controlled-vocab, NOT a PICKLIST
COL_SUPERSEDED_BY = "Superseded By"
COL_RECORDED_AT = "Recorded At"  # server record time (created_at), pre-formatted Pacific ISO

# Controlled vocabulary (TEXT cells, NOT picklist — avoids the REGISTRY-parity footgun; mirrors
# week_sheet's Row Type/Status choice).
STATUS_ACTIVE = "Active"
STATUS_SUPERSEDED = "Superseded"

# Schema passed to create_sheet_in_folder. Order = left-to-right UI order. Exactly one primary
# (TEXT_NUMBER, Smartsheet requirement).
HOURS_LOG_COLUMNS: list[dict[str, Any]] = [
    {"title": COL_ENTRY, "type": "TEXT_NUMBER", "primary": True},
    {"title": COL_ENTRY_UUID, "type": "TEXT_NUMBER"},
    {"title": COL_WORK_DATE, "type": "DATE"},
    {"title": COL_PERSONNEL, "type": "TEXT_NUMBER"},
    {"title": COL_HOURS, "type": "TEXT_NUMBER"},
    {"title": COL_STARTED, "type": "TEXT_NUMBER"},
    {"title": COL_ENDED, "type": "TEXT_NUMBER"},
    {"title": COL_NOTES, "type": "TEXT_NUMBER"},
    {"title": COL_STATUS, "type": "TEXT_NUMBER"},
    {"title": COL_SUPERSEDED_BY, "type": "TEXT_NUMBER"},
    {"title": COL_RECORDED_AT, "type": "TEXT_NUMBER"},
]

# Cosmetic styling — Smartsheet format-descriptor strings (mirror week_sheet's palette). Applied
# AFTER create, best-effort (the API ignores width/format at POST).
FMT_PRIMARY = ",,1,,,,,,38,7,,,,,,,"       # bold + dark-green text + light-green bg
FMT_DATE = ",,,,,,,,,,,,,,,,2"             # MMM_D_YYYY
STATUS_ACTIVE_FMT = ",,,,,,,,,7,,,,,,,"    # light-green bg
STATUS_SUPERSEDED_FMT = ",,,,,,,,,18,,,,,,,"  # light-gray bg

HOURS_LOG_STYLES: list[dict[str, Any]] = [
    {"title": COL_ENTRY, "width": 240, "format": FMT_PRIMARY},
    {"title": COL_ENTRY_UUID, "width": 90},
    {"title": COL_WORK_DATE, "width": 110, "format": FMT_DATE},
    {"title": COL_PERSONNEL, "width": 170},
    {"title": COL_HOURS, "width": 80},
    {"title": COL_STARTED, "width": 130},
    {"title": COL_ENDED, "width": 130},
    {"title": COL_NOTES, "width": 300},
    {"title": COL_STATUS, "width": 110},
    {"title": COL_SUPERSEDED_BY, "width": 120},
    {"title": COL_RECORDED_AT, "width": 160},
]


def hours_log_sheet_name(project_name: str) -> str:
    """`'<Job> — Hours Log'`, prefix-truncated to the 50-char cap (errorCode 1041).

    The ` — Hours Log` suffix is preserved WHOLE (it names the sheet within the per-job folder);
    the project prefix is truncated when needed — no identity is lost because the per-job FOLDER
    already carries the full `project_name` and the sheet is only ever resolved find-or-create
    WITHIN that folder. Names ≤50 chars are byte-identical to the untruncated form.
    """
    prefix = project_name.strip()
    budget = SHEET_NAME_MAX - len(SHEET_SUFFIX)
    if len(prefix) > budget:
        prefix = prefix[:budget].rstrip()
    return f"{prefix}{SHEET_SUFFIX}"


def _apply_styles_best_effort(sheet_id: int) -> None:
    """Apply `HOURS_LOG_STYLES` to a freshly-created sheet. Cosmetic only — a failure WARNs (never
    silent) but must NOT fail the already-created sheet (the data path is unaffected)."""
    try:
        smartsheet_client.apply_column_styles(sheet_id, HOURS_LOG_STYLES)
    except Exception as exc:  # noqa: BLE001 — cosmetic; never fail sheet creation
        error_log.log(
            Severity.WARN, SCRIPT_NAME,
            f"hours-log styling failed (sheet {sheet_id}): {type(exc).__name__}: {exc!r}",
            error_code="hours_log_style_failed",
        )


def _folder_name(project_name: str) -> str:
    """Per-job folder title/key — the SAME source of truth week_sheet uses, so the Hours Log lands
    in the job's existing folder rather than a parallel one."""
    return safety_naming.job_folder_name(project_name)


def _ensure_job_folder(project_name: str) -> int:
    """Find-or-create the per-job folder in the progress workspace (idempotent, race-tolerant).

    Resolves the SAME folder the week sheets use (identical name via `safety_naming`). Two
    concurrent creators can both pass the find (Smartsheet does not enforce folder-name
    uniqueness) — re-find after create, adopt the first match, WARN the duplicate for cleanup
    (mirrors week_sheet._ensure_job_folder). Bounded blast radius: one extra empty folder.
    """
    name = _folder_name(project_name)
    existing = smartsheet_client.find_folder_by_name_in_workspace(WORKSPACE_ID, name)
    if existing is not None:
        return existing
    folder_id = smartsheet_client.create_folder_in_workspace(WORKSPACE_ID, name)
    post_find = smartsheet_client.find_folder_by_name_in_workspace(WORKSPACE_ID, name)
    if post_find is not None and post_find != folder_id:
        error_log.log(
            Severity.WARN, SCRIPT_NAME,
            f"duplicate per-job folder {name!r} in the progress workspace "
            f"(project={project_name!r}); using first match {post_find}, manual cleanup "
            f"needed for {folder_id}.",
            error_code="hours_log_folder_race_duplicate",
        )
        return post_find
    return folder_id


def _warn_on_thin_headroom(sheet_name: str) -> None:
    """A1 sheet-count tripwire, run before each CREATE. ADVISORY, never blocking (mirrors
    week_sheet._warn_on_thin_headroom): a margin breach WARNs + enqueues the operator signal, then
    the create PROCEEDS. Belt-and-suspenders fail-open — any exception is reduced to a WARN."""
    try:
        headroom = sheet_capacity.check_create_headroom(WORKSPACE_ID)
    except Exception as exc:  # noqa: BLE001 — advisory tripwire; never block the create
        error_log.log(
            Severity.WARN, SCRIPT_NAME,
            f"sheet-capacity headroom check raised (create proceeds unguarded): {exc!r}",
            error_code="sheet_capacity_check_failed",
        )
        return
    if headroom.note:
        error_log.log(
            Severity.WARN, SCRIPT_NAME,
            f"sheet-capacity check fail-open before creating {sheet_name!r}: {headroom.note}",
            error_code="sheet_capacity_check_failed",
        )
        return
    if headroom.ok:
        return
    error_log.log(
        Severity.WARN, SCRIPT_NAME,
        (
            f"sheet-count margin breach in workspace {WORKSPACE_ID}: "
            f"{headroom.current}/{headroom.ceiling} (margin {headroom.margin}) — creating "
            f"{sheet_name!r} anyway (advisory tripwire; see the Review-Queue row)."
        ),
        error_code="sheet_capacity_margin_breach",
    )
    try:
        sheet_capacity.route_breach_to_review_queue(
            WORKSPACE_ID, headroom, workstream="progress_reports"
        )
    except Exception as exc:  # noqa: BLE001 — the enqueue failing must not block the create
        error_log.log(
            Severity.WARN, SCRIPT_NAME,
            f"could not enqueue the sheet-capacity breach to ITS_Review_Queue: {exc!r}",
            error_code="sheet_capacity_rq_failed",
        )


def ensure_hours_log_sheet(project_name: str) -> int:
    """Find-or-create the job's single standing Hours Log sheet; return its sheet ID.

    Idempotent: a second call returns the same sheet with no write. Race-tolerant at both the
    folder and sheet levels (re-find after create, adopt first, WARN the duplicate). The A1
    capacity tripwire runs ONLY on the create branch.
    """
    folder_id = _ensure_job_folder(project_name)
    name = hours_log_sheet_name(project_name)

    existing = smartsheet_client.find_sheet_by_name_in_folder(folder_id, name)
    if existing is not None:
        return existing

    _warn_on_thin_headroom(name)
    sheet_id = smartsheet_client.create_sheet_in_folder(folder_id, name, HOURS_LOG_COLUMNS)
    post_find = smartsheet_client.find_sheet_by_name_in_folder(folder_id, name)
    if post_find is not None and post_find != sheet_id:
        error_log.log(
            Severity.WARN, SCRIPT_NAME,
            f"duplicate Hours Log sheet {name!r} under folder {folder_id} "
            f"(project={project_name!r}); using first match {post_find}, manual cleanup "
            f"needed for {sheet_id}.",
            error_code="hours_log_sheet_race_duplicate",
        )
        return post_find
    _apply_styles_best_effort(sheet_id)  # cosmetic; create path only
    return sheet_id


def find_entry_row(sheet_id: int, entry_uuid: str) -> dict[str, Any] | None:
    """Return the Hours Log row whose Entry UUID == `entry_uuid`, or None. The dedupe/amend
    authority — an idempotent re-mirror and an amend both resolve the target through this."""
    key = (entry_uuid or "").strip()
    if not key:
        return None
    for row in smartsheet_client.get_rows(sheet_id):
        if str(row.get(COL_ENTRY_UUID) or "").strip() == key:
            return row
    return None


def upsert_entry_row(
    sheet_id: int,
    *,
    entry_uuid: str,
    work_date: str,
    personnel: str,
    hours: str,
    started: str,
    ended: str,
    notes: str,
    recorded_at: str,
) -> int:
    """Idempotent find-or-create of one time entry as an Active Hours Log row; return its row ID.

    A time entry is IMMUTABLE once mirrored (an edit is a new amend uuid, never a mutation of this
    row), so on a find-hit this is a NO-OP that returns the existing row ID — making a re-mirror
    (crash-before-mark-mirrored replay) safe. On a miss it appends a new Active row. `personnel` is
    the resolved DISPLAY NAME (never a username); all values are pre-formatted strings.
    """
    existing = find_entry_row(sheet_id, entry_uuid)
    if existing is not None:
        return int(existing["_row_id"])

    label = f"{work_date} — {personnel}".strip(" —") or (work_date or entry_uuid)
    [row_id] = smartsheet_client.add_rows(
        sheet_id,
        [
            {
                COL_ENTRY: label,
                COL_ENTRY_UUID: entry_uuid,
                COL_WORK_DATE: work_date,
                COL_PERSONNEL: personnel,
                COL_HOURS: hours,
                COL_STARTED: started,
                COL_ENDED: ended,
                COL_NOTES: notes,
                COL_STATUS: STATUS_ACTIVE,
                COL_RECORDED_AT: recorded_at,
                "_formats": {COL_STATUS: STATUS_ACTIVE_FMT},  # green status cell
            }
        ],
    )
    return row_id


def supersede_entry_row(sheet_id: int, prior_uuid: str, new_uuid: str) -> bool:
    """Mark the prior entry's row Superseded, pointing it at the amending Entry UUID.

    Returns True if the prior row was found + updated, False if the amend names an entry we never
    mirrored (the caller logs it). NEVER deletes — the superseded row stays for the audit trail
    (mirrors week_sheet.supersede_row).
    """
    prior = find_entry_row(sheet_id, prior_uuid)
    if prior is None:
        return False
    smartsheet_client.update_rows(
        sheet_id,
        [
            {
                "_row_id": prior["_row_id"],
                COL_STATUS: STATUS_SUPERSEDED,
                COL_SUPERSEDED_BY: new_uuid,
                "_formats": {COL_STATUS: STATUS_SUPERSEDED_FMT},  # gray status cell
            }
        ],
    )
    return True
