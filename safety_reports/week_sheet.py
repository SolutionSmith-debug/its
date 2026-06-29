"""Per-job, per-Saturday-week Smartsheet 'week sheet' for the Safety Portal pull model.

Purpose
-------
    One sheet per (job, Saturday→Friday week), `"<project> — week of <Saturday>"`,
    living in an auto-provisioned per-job folder at the surface of the ITS — Safety
    Portal workspace (`sheet_ids.WORKSPACE_SAFETY_PORTAL`), a sibling of the "Safety
    Portal" / "Form Catalog" folders. The Phase-5 intake
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
    submission to status='error' so it re-pulls next cycle — never a silent drop. A
    brand-new `project_name` self-provisions its per-job folder (find-or-create); the
    only failure mode is a transient Smartsheet error (loud, recoverable) — never a
    silent write-to-nowhere (CLAUDE.md "never silent").
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from typing import Any

from shared import error_log, sheet_ids, smartsheet_client
from shared.error_log import Severity

from . import safety_naming

SCRIPT_NAME = "safety_reports.week_sheet"


# ── Parameterize-not-clone (Op Stds §14 informed deviation) ──────────────────
# `week_sheet` was Safety-Portal-hardcoded (the workspace pin + the Sat→Fri name
# builder). To let a future progress workstream REUSE this find-or-create engine
# without cloning it, the two workstream-VARIANT knobs are lifted into a required,
# no-default config object. Everything else — the per-job folder convention, the
# WEEK_SHEET_COLUMNS schema, the styles, and every sheet-id-scoped helper — is
# workstream-INVARIANT and stays module-level, so the config is exactly two fields.
#
# WHY required + no default (the contamination gate): a caller that forgets a field
# gets a TypeError at construction/call — it can NEVER silently fall through to a
# Safety value and file a progress submission into the safety workspace. For the
# same reason `ensure_week_sheet`'s `config` is a required first positional with NO
# default; defaulting it to SAFETY_WEEK_SHEET_CONFIG would reintroduce exactly the
# cross-workstream bleed this object exists to prevent.
#
# WHY weekly is hardcoded (no `sheet_period`): the 2026-06-28 "monthly sheets"
# proposal was REVERTED 2026-06-29 — the sheet IS the week (keyed on the opening
# Saturday, `shared/safety_week.py`), so the weekly compile reads exactly one week
# sheet with no month-boundary straddle. Monthly stays a documented config-flip
# fallback armed by the `shared/sheet_capacity` margin-check, NOT a code path here.
#
# WHY SAFETY_WEEK_SHEET_CONFIG.key_builder IS `week_sheet_name` (object identity):
# binding the unchanged builder + the unchanged workspace pin makes every safety
# find-or-create produce byte-identical folder/sheet names and resolve the SAME
# existing sheet IDs — zero behavior change, zero migration.
@dataclass(frozen=True)
class WeekSheetConfig:
    """The two workstream-variant knobs `ensure_week_sheet` needs, bound per
    workstream. Frozen + no field defaults (the contamination gate)."""

    workspace_id: int
    # (project_name, work_date) -> the per-week sheet name / find-or-create key.
    key_builder: Callable[[str, date], str]

    def __post_init__(self) -> None:
        if not callable(self.key_builder):
            raise TypeError("WeekSheetConfig.key_builder must be callable")
        if not isinstance(self.workspace_id, int) or self.workspace_id <= 0:
            raise ValueError("WeekSheetConfig.workspace_id must be a positive int")

# Smartsheet hard cap on a sheet name — a longer name is rejected at create with
# HTTP 400 errorCode 1041 ("must be 50 characters in length or less"). The
# integration suites already shorten their sandbox names for this reason; the
# production name-builder (`week_sheet_name`) MUST honor it too, or a long
# `project_name` (e.g. a 30+ char job title) overflows the composed
# "<project> — week of <Sat>" name and the portal submission can never file.
SHEET_NAME_MAX = 50

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
# Operator trigger for an out-of-band recompile (Phase 5b weekly_generate). A
# CHECKBOX checked on the Rollup row forces a recompile even with no new docs;
# weekly_generate clears it after compiling. Absent on PR1-era sheets → falsy →
# Friday auto-compile is the only trigger (graceful).
COL_COMPILE_NOW = "Compile Now"

# Controlled vocabularies (TEXT cells, not PICKLIST — see module docstring).
ROW_TYPE_SUBMISSION = "Submission"
ROW_TYPE_ROLLUP = "Rollup"
STATUS_ACTIVE = "Active"
STATUS_SUPERSEDED = "Superseded"
# The Rollup row's fixed primary label (one Rollup row per week sheet).
ROLLUP_LABEL = "Weekly Rollup"

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
    {"title": COL_COMPILE_NOW, "type": "CHECKBOX"},
]

# ---- Cosmetic styling (PR-I) — Smartsheet format-descriptor strings + widths.
# Colors index the account palette (GET /serverinfo): 7=#E7F5E9 light green,
# 18=#E5E5E5 light gray, 38=#237F2E dark green. Format positions: 2=bold,
# 8=textColor, 9=backgroundColor, 16=dateFormat (2=MMM_D_YYYY). These APPROXIMATE
# the Evergreen brand green #3a5a40 — Smartsheet is palette-only (no exact hex);
# adjust the indices here to retune. Applied AFTER create (the API ignores
# width/format at POST — see smartsheet_client.apply_column_styles).
FMT_PRIMARY = ",,1,,,,,,38,7,,,,,,,"  # bold + dark-green text + light-green bg
FMT_DATE = ",,,,,,,,,,,,,,,,2"  # MMM_D_YYYY date display
STATUS_ACTIVE_FMT = ",,,,,,,,,7,,,,,,,"  # light-green bg
STATUS_SUPERSEDED_FMT = ",,,,,,,,,18,,,,,,,"  # light-gray bg

WEEK_SHEET_STYLES: list[dict[str, Any]] = [
    {"title": COL_SUBMISSION, "width": 320, "format": FMT_PRIMARY},
    {"title": COL_SUBMISSION_UUID, "width": 90},
    {"title": COL_FORM_CODE, "width": 180},
    {"title": COL_WORK_DATE, "width": 110, "format": FMT_DATE},
    {"title": COL_SUBMITTED_AT, "width": 160},
    {"title": COL_SUBMISSION_PDF, "width": 260},
    {"title": COL_ROW_TYPE, "width": 90},
    {"title": COL_STATUS, "width": 110},
    {"title": COL_SUPERSEDED_BY, "width": 120},
    {"title": COL_NOTES, "width": 300},
    {"title": COL_COMPILE_NOW, "width": 100},
]


def _apply_styles_best_effort(sheet_id: int) -> None:
    """Apply `WEEK_SHEET_STYLES` to a freshly-created week sheet, BEST-EFFORT.

    Cosmetic only — a styling failure WARNs (never silent) but must NOT fail the
    already-created sheet (the data path is unaffected)."""
    try:
        smartsheet_client.apply_column_styles(sheet_id, WEEK_SHEET_STYLES)
    except Exception as exc:  # noqa: BLE001 — cosmetic; never fail sheet creation
        error_log.log(
            Severity.WARN,
            SCRIPT_NAME,
            f"week-sheet styling failed (sheet {sheet_id}): {type(exc).__name__}: {exc!r}",
            error_code="week_sheet_style_failed",
        )


def week_sheet_name(project_name: str, work_date: date) -> str:
    """Return the canonical week-sheet name for a (project, work-date), bounded to
    Smartsheet's 50-char sheet-name cap (`SHEET_NAME_MAX`; errorCode 1041).

    Name keys on the Saturday that opens the work-date's week, so every day
    Sat→Fri maps to one sheet, e.g. `"Bradley 1 — week of 2026-05-30"`.

    When `"<project> — week of <Sat>"` would exceed the cap, the **project prefix**
    is truncated — the ` — week of <Sat>` suffix is preserved WHOLE because it is
    what disambiguates weeks within a per-job folder. Truncating the prefix loses
    no identity: the per-job FOLDER already carries the full `project_name`, and a
    week sheet is only ever resolved find-or-create WITHIN that folder, so two long
    project names that share a truncated prefix never collide (they live in
    different folders). For any name already ≤50 chars the output is byte-identical
    to the pre-cap behavior, so existing sheets resolve unchanged.
    """
    suffix = f" — {safety_naming.week_label(work_date)}"
    prefix = project_name.strip()
    budget = SHEET_NAME_MAX - len(suffix)
    if len(prefix) > budget:
        prefix = prefix[:budget].rstrip()
    return f"{prefix}{suffix}"


# The Safety-Portal binding: the exact current workspace pin + the unchanged
# Sat→Fri name builder. `key_builder` IS `week_sheet_name` (identity), so safety's
# find-or-create stays byte-identical — see the §14 note on WeekSheetConfig above.
SAFETY_WEEK_SHEET_CONFIG = WeekSheetConfig(
    workspace_id=sheet_ids.WORKSPACE_SAFETY_PORTAL,
    key_builder=week_sheet_name,
)


def _folder_name(project_name: str) -> str:
    """The per-job folder title + find/create key under WORKSPACE_SAFETY_PORTAL
    (e.g. "Bradley 1"). Thin delegate to `safety_naming.job_folder_name` — the
    single source of truth shared with the Box mirror tree (PR-K), so the per-job
    folder is named + sanitized identically in Smartsheet and Box.
    """
    return safety_naming.job_folder_name(project_name)


def _ensure_job_folder(config: WeekSheetConfig, project_name: str) -> int:
    """Find-or-create the per-job folder at the configured-workspace surface
    (`config.workspace_id`).

    A direct child of the workspace (sibling of the "Safety Portal" / "Form
    Catalog" folders), titled by `project_name`. Idempotent. Race-tolerant: two
    concurrent creators can both pass the find step (Smartsheet does not enforce
    folder-name uniqueness) — we re-find after create, adopt the first match, and
    WARN-log the duplicate for operator cleanup (mirrors the sheet-level guard
    below + `week_folder.ensure_current_week_folder`). Auto-provisions for a
    brand-new job — there is no per-project allowlist any more.
    """
    folder_name = _folder_name(project_name)
    existing = smartsheet_client.find_folder_by_name_in_workspace(
        config.workspace_id, folder_name
    )
    if existing is not None:
        return existing

    folder_id = smartsheet_client.create_folder_in_workspace(
        config.workspace_id, folder_name
    )
    post_find = smartsheet_client.find_folder_by_name_in_workspace(
        config.workspace_id, folder_name
    )
    if post_find is not None and post_find != folder_id:
        error_log.log(
            Severity.WARN,
            SCRIPT_NAME,
            (
                f"Duplicate per-job folder {folder_name!r} under workspace "
                f"{config.workspace_id} (project={project_name!r}); "
                f"using first match {post_find}, manual cleanup needed for {folder_id}."
            ),
            error_code="week_sheet_folder_race_duplicate",
        )
        return post_find
    return folder_id


def ensure_week_sheet(
    config: WeekSheetConfig, project_name: str, work_date: date
) -> int:
    """Find-or-create the (project, week) sheet; return its sheet ID.

    `config` (REQUIRED, no default) binds the workspace + the per-week name/key
    builder for the calling workstream — safety passes `SAFETY_WEEK_SHEET_CONFIG`.
    Located in an auto-provisioned per-job folder named `project_name` at the
    surface of `config.workspace_id` (a sibling of the "Safety Portal" / "Form
    Catalog" folders). The per-job folder AND the week sheet are BOTH
    find-or-create, so a brand-new job self-provisions on first submission — there
    is no hardcoded per-project folder map.

    Idempotent: a second call in the same week returns the same sheet with no
    write. Race-tolerant at both levels: concurrent creators can each pass the
    find step (Smartsheet does not enforce name uniqueness) for the folder and the
    sheet — we re-find after each create, adopt the first match, and WARN-log the
    duplicate for operator cleanup. Bounded blast radius: one extra empty folder
    and/or sheet.
    """
    folder_id = _ensure_job_folder(config, project_name)
    name = config.key_builder(project_name, work_date)

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
    _apply_styles_best_effort(sheet_id)  # cosmetic; only the create path (not find)
    _ensure_rollup_placeholder(sheet_id)
    return sheet_id


def _ensure_rollup_placeholder(sheet_id: int) -> None:
    """Pre-create the empty Rollup row at sheet creation so the Compile Now TRIGGER checkbox
    exists IMMEDIATELY — the operator can request an on-demand compile (compile_now_poll) for a
    never-yet-compiled week, not only after the first Friday run.

    `compiled_at=""` keeps the no-new-docs skip honest (`prior_compiled_at='' <` any real
    submission timestamp → compiles, never a spurious skip) and sorts FIRST in
    `list_rollup_rows` so a real compilation is always the latest. Best-effort: a transient
    write failure must NOT abort sheet creation (intake needs the sheet to file the
    submission); the first compile then APPENDS the first real Rollup snapshot. Runs only on
    the CREATE branch of ensure_week_sheet — the placeholder is the genesis row that hosts the
    Compile Now trigger before the first compile (append-only: compiles never overwrite it)."""
    try:
        append_rollup_row(
            sheet_id, packet_link="", compiled_at="",
            manifest_note="not yet compiled (placeholder)",
        )
    except smartsheet_client.SmartsheetError as exc:
        error_log.log(
            Severity.WARN, SCRIPT_NAME,
            f"could not pre-create the Rollup placeholder row on sheet {sheet_id}: {exc!r}",
            error_code="rollup_placeholder_failed",
        )


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
                "_formats": {COL_STATUS: STATUS_ACTIVE_FMT},  # green status cell
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
                "_formats": {COL_STATUS: STATUS_SUPERSEDED_FMT},  # gray status cell
            }
        ],
    )
    return True


# ---- Rollup / compile helpers (Phase 5b — weekly_generate) ---------------


def list_submission_rows(sheet_id: int, *, active_only: bool = True) -> list[dict[str, Any]]:
    """Return the per-submission rows on the week sheet (Row Type=Submission).

    `active_only` (default) excludes Superseded rows — the compile merges only the
    current version of each submission (amendments supersede; Box keeps both PDFs
    but the packet carries the live one). Ordered oldest-first by Work Date then
    Submitted At, so the merged packet reads Sat→Fri ascending (brief §D).
    """
    rows = [
        r for r in smartsheet_client.get_rows(sheet_id)
        if (r.get(COL_ROW_TYPE) or "") == ROW_TYPE_SUBMISSION
    ]
    if active_only:
        rows = [r for r in rows if (r.get(COL_STATUS) or STATUS_ACTIVE) != STATUS_SUPERSEDED]
    return sorted(
        rows,
        key=lambda r: (str(r.get(COL_WORK_DATE) or ""), str(r.get(COL_SUBMITTED_AT) or "")),
    )


def list_rollup_rows(sheet_id: int) -> list[dict[str, Any]]:
    """All Row Type=Rollup snapshot rows, oldest-compiled first (sorted by Submitted At).

    APPEND-ONLY (operator decision 2026-06-09): each compile writes a NEW immutable Rollup
    snapshot — a prior compilation's row is NEVER overwritten, so the week sheet keeps the
    full compile history. The placeholder (compiled_at='') sorts first; the most-recent real
    compilation is last (`[-1]` = the no-new-docs watermark)."""
    rows = [
        r for r in smartsheet_client.get_rows(sheet_id)
        if (r.get(COL_ROW_TYPE) or "") == ROW_TYPE_ROLLUP
    ]
    return sorted(rows, key=lambda r: str(r.get(COL_SUBMITTED_AT) or ""))


def get_rollup_row(sheet_id: int) -> dict[str, Any] | None:
    """Return the LATEST Row Type=Rollup snapshot row (most-recently compiled), or None.

    With append-only Rollups (one per compile) this is the newest by Submitted At — the row
    that carries the no-new-docs watermark. Use `list_rollup_rows` for the full history."""
    rows = list_rollup_rows(sheet_id)
    return rows[-1] if rows else None


def compile_now_requested(rollup_row: dict[str, Any] | None) -> bool:
    """True iff the operator checked Compile Now on the given Rollup row (force recompile)."""
    return bool(rollup_row and rollup_row.get(COL_COMPILE_NOW))


def any_compile_now_requested(rollup_rows: list[dict[str, Any]]) -> bool:
    """True iff ANY Rollup row has Compile Now checked — the operator may check the trigger
    on the latest (or any) Rollup snapshot to force a fresh compilation."""
    return any(compile_now_requested(r) for r in rollup_rows)


def selected_submission_row_ids(submission_rows: list[dict[str, Any]]) -> set[int]:
    """Row IDs of the submission rows the operator checked Compile Now on — the per-
    submission "include in this compile" selection (Part B / on-demand compile).

    The SAME COL_COMPILE_NOW checkbox is row-type-dependent: on the Rollup row it is the
    "compile now" TRIGGER (compile_now_requested); on a Submission row it means "include
    this row in the packet". An EMPTY set = no narrowing → compile the full week (Option-1
    default-all). A caller passes this set to weekly_generate._compile_job_week(selection=…)."""
    return {
        int(r["_row_id"])
        for r in submission_rows
        if r.get("_row_id") is not None and r.get(COL_COMPILE_NOW)
    }


def clear_compile_now(sheet_id: int, row_ids: set[int]) -> None:
    """Uncheck Compile Now on the given per-SUBMISSION rows — called AFTER a compile consumed
    the per-submission selection. No-op on an empty set. Raises on a Smartsheet error so a
    failed clear is visible (a stale checkbox would silently narrow the next compile)."""
    if not row_ids:
        return
    smartsheet_client.update_rows(
        sheet_id, [{"_row_id": rid, COL_COMPILE_NOW: False} for rid in sorted(row_ids)]
    )


def clear_compile_now_on_rollups(sheet_id: int, rollup_rows: list[dict[str, Any]]) -> None:
    """Uncheck Compile Now on any ROLLUP rows that had it set — called AFTER an append-only
    compile. The new snapshot is written with Compile Now=false, but the prior Rollup row the
    operator checked to TRIGGER the recompile must be cleared, or `any_compile_now_requested`
    stays True and it would re-fire every cycle. Clearing a transient trigger checkbox is NOT
    a record mutation (the packet link + manifest on the snapshot are untouched — the
    append-only invariant is about preserving compiled records, not the trigger). No-op when
    none are set. Raises on a Smartsheet error so a failed clear is visible (a stale trigger
    would loop)."""
    ids = [
        int(r["_row_id"])
        for r in rollup_rows
        if r.get("_row_id") is not None and r.get(COL_COMPILE_NOW)
    ]
    if not ids:
        return
    smartsheet_client.update_rows(
        sheet_id, [{"_row_id": rid, COL_COMPILE_NOW: False} for rid in sorted(ids)]
    )


def latest_submitted_at(submission_rows: list[dict[str, Any]]) -> str:
    """Max Submitted At across submission rows; '' if NONE has a usable value.

    Pacific ISO strings sort lexically, so this is the 'newest doc' watermark for the
    no-new-docs skip. BLANK Submitted At values are EXCLUDED (an empty string sorts
    below every timestamp, so including them would mask a genuinely-new blank-stamped
    submission and silently skip the recompile). A return of '' when rows exist is the
    caller's signal that it cannot prove 'no new docs' → it recompiles, never skips."""
    stamps = [s for s in (str(r.get(COL_SUBMITTED_AT) or "").strip() for r in submission_rows) if s]
    return max(stamps, default="")


def append_rollup_row(
    sheet_id: int,
    *,
    packet_link: str,
    compiled_at: str,
    manifest_note: str,
) -> int:
    """APPEND a new read-only Rollup snapshot row (Row Type=Rollup); return its row ID.

    APPEND-ONLY (operator decision 2026-06-09): every compile writes a NEW immutable
    snapshot — a prior compilation's Rollup row, packet link, and manifest are NEVER
    overwritten. Together with append-only WSR rows + distinct Box packet files, the week
    sheet keeps the full compile history, the WSR keeps the full send history, and Box keeps
    every weekly packet. The rollup row is a MANIFEST of what THIS packet contains — NOT an
    editable surface (that's the WSR_human_review row). `compiled_at` (Pacific ISO) lands in
    Submitted At so the no-new-docs skip can compare it to submission timestamps. Written
    with Compile Now=false; a forced recompile's trigger on the PRIOR rows is cleared by
    `clear_compile_now_on_rollups`.
    """
    [row_id] = smartsheet_client.add_rows(
        sheet_id,
        [{
            COL_SUBMISSION: ROLLUP_LABEL,
            COL_ROW_TYPE: ROW_TYPE_ROLLUP,
            COL_STATUS: STATUS_ACTIVE,
            COL_SUBMISSION_PDF: packet_link,
            COL_SUBMITTED_AT: compiled_at,
            COL_NOTES: manifest_note,
            COL_COMPILE_NOW: False,
        }],
    )
    return row_id
