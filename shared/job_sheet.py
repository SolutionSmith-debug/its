"""Find-or-create a per-job Smartsheet tracking folder + sheet (Feature A).

Purpose
    Per-job OPERATOR VISIBILITY for the subcontract + purchase-order workstreams:
    every job that files a subcontract or PO gets its own folder (named by
    `safety_reports.safety_naming.job_folder_name`, the SAME sanitized name the
    per-job Box folder already uses — Box + Smartsheet line up) under the
    workspace's "Jobs" parent (`sheet_ids.FOLDER_SC_JOBS` / `FOLDER_PO_JOBS`,
    built by scripts/migrations/build_job_folders.py), holding one tracking
    sheet whose rows mirror that job's flat-Log ledger rows. The flat Logs
    (Subcontract_Log / PO_Log) STAY the ledger SoR mirror of D1 — this per-job
    sheet is purely supplementary, appended best-effort by the poll daemons.

Why dynamic find-or-create (unlike safety's week_folder)
    Safety pre-wires one folder constant per KNOWN project
    (`sheet_ids.FIELD_REPORTS_FOLDER_BY_PROJECT` — six hardcoded projects).
    Subcontract/PO jobs are DYNAMIC — they come from ITS_Active_Jobs via the
    portal — so the per-job folder must be resolved by NAME at filing time,
    exactly like the per-job Box folder (`_resolve_subcontract_box_folder` /
    `_resolve_po_box_folder`) already does. Same model as
    `progress_reports.hours_log._ensure_job_folder`, one level down (folder
    parent, not workspace parent).

Why clone the flat Log as the template
    The per-job sheet is created structure-only (`include=[]`) from the flat
    Log itself (`SHEET_SUBCONTRACT_LOG` / `SHEET_PO_LOG`), so its columns are
    byte-identical to the ledger's automatically — the SAME
    `append_filed_row(..., sheet_id=...)` writes both with no schema fork.

Idempotency + race safety
    Calling `ensure_job_sheet` twice returns the same sheet ID with no API
    writes on the second call. Smartsheet does not enforce folder/sheet name
    uniqueness, so two concurrent creators can both pass the find step; both
    levels re-find after create, WARN via `shared.error_log`, and adopt the
    FIRST match (mirrors `safety_reports.week_folder.ensure_current_week_folder`
    — duplicate cleanup is operator-manual, bounded blast radius: one empty
    orphan folder/sheet).

Failure modes
    `SmartsheetError` propagates — the callers (the poll daemons' fenced
    per-job append helpers) classify and WARN; a per-job failure must never
    fail the filing (Box + the flat Log are the SoR).
"""
from __future__ import annotations

from shared import error_log, smartsheet_client
from shared.error_log import Severity

SCRIPT_NAME = "shared.job_sheet"

# Smartsheet sheet-name cap (HTTP 400 errorCode 1041) — same constant as
# safety_reports.week_sheet.SHEET_NAME_MAX / progress_reports.hours_log. The
# per-job sheet names in use today ("Subcontracts" / "Purchase Orders") are
# fixed short strings; the truncation below is a defensive guard so a future
# composite name cannot 400 at create time. Folder names are uncapped.
SHEET_NAME_MAX = 50


def ensure_job_sheet(
    parent_folder_id: int,
    template_sheet_id: int,
    job_folder_name: str,
    sheet_name: str,
) -> int:
    """Find-or-create the per-job folder + tracking sheet; return the sheet ID.

    Args:
        parent_folder_id: the workspace's "Jobs" parent folder
            (`sheet_ids.FOLDER_SC_JOBS` / `FOLDER_PO_JOBS`).
        template_sheet_id: the sheet whose STRUCTURE the per-job sheet clones
            (`include=[]` — columns/picklists/descriptions, no rows). Pass the
            flat Log itself so the per-job columns match the ledger exactly.
        job_folder_name: the per-job folder title. Callers MUST pass
            `safety_naming.job_folder_name(job_name)` so the Smartsheet folder
            matches the per-job Box folder byte-for-byte.
        sheet_name: the tracking sheet's title inside the per-job folder
            (defensively truncated to the 50-char cap, errorCode 1041).

    Returns:
        The per-job tracking sheet's ID. Same call twice → same ID, zero
        API writes on the second invocation.

    Raises:
        SmartsheetError (typed hierarchy) — propagates for the caller's fence.
    """
    name = sheet_name.strip()
    if len(name) > SHEET_NAME_MAX:
        name = name[:SHEET_NAME_MAX].rstrip()

    folder_id = smartsheet_client.find_folder_by_name_in_folder(
        parent_folder_id, job_folder_name
    )
    if folder_id is None:
        folder_id = smartsheet_client.create_folder_in_folder(
            parent_folder_id, job_folder_name
        )
        # Race-safety post-create check (the week_folder pattern): two
        # concurrent creators can both pass the find above; adopt the first
        # match and WARN the orphan for manual cleanup.
        post_find = smartsheet_client.find_folder_by_name_in_folder(
            parent_folder_id, job_folder_name
        )
        if post_find is not None and post_find != folder_id:
            error_log.log(
                Severity.WARN,
                SCRIPT_NAME,
                (
                    f"duplicate per-job folder {job_folder_name!r} under parent "
                    f"{parent_folder_id}; using first match {post_find}, manual "
                    f"cleanup needed for {folder_id}."
                ),
                error_code="job_sheet_folder_race_duplicate",
            )
            folder_id = post_find

    sheet_id = smartsheet_client.find_sheet_by_name_in_folder(folder_id, name)
    if sheet_id is None:
        sheet_id = smartsheet_client.create_sheet_in_folder_from_template(
            folder_id=folder_id,
            name=name,
            template_sheet_id=template_sheet_id,
            include=[],
        )
        # Same race-safety at the sheet level (hours_log does both levels; a
        # duplicate sheet is worse than a duplicate folder — rows would split).
        post_find = smartsheet_client.find_sheet_by_name_in_folder(folder_id, name)
        if post_find is not None and post_find != sheet_id:
            error_log.log(
                Severity.WARN,
                SCRIPT_NAME,
                (
                    f"duplicate per-job sheet {name!r} under folder {folder_id} "
                    f"(job folder {job_folder_name!r}); using first match "
                    f"{post_find}, manual cleanup needed for {sheet_id}."
                ),
                error_code="job_sheet_sheet_race_duplicate",
            )
            sheet_id = post_find

    return sheet_id
