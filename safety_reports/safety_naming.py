"""Shared folder/sheet naming for Safety Portal filing — PR-K.

Purpose
    ONE source of truth for the per-job folder name + the per-week label, so the
    Box mirror tree and the Smartsheet tree are named identically. Box files into
    `ROOT → job_folder_name(project) → week_label(date) → PDFs`; Smartsheet files
    into `WORKSPACE → job_folder_name(project) folder → "<project> — week_label(date)"
    sheet`. Same sanitization + same week key on both sides.

Invariants
    Pure naming (no I/O, no external send) — imports only `safety_week`. The Box
    mirror tree is config-gated by `CFG_BOX_PORTAL_ROOT` (read by the callers, not
    here): unset → the legacy category path stays active (a pull is inert).

Failure modes
    None — deterministic string functions. A non-printable / path-like project name
    is sanitized; an empty result falls back to the raw stripped name.

Consumers
    `safety_reports.week_sheet` (Smartsheet folder + week-sheet names),
    `safety_reports.intake._resolve_portal_box_folder` + `safety_reports.weekly_generate`
    (Box mirror-tree folders).
"""
from __future__ import annotations

from datetime import date

from shared import safety_week

# ITS_Config key for the Box "ITS Safety Portal" root folder ID. Config-GATED: when
# unset/blank the portal Box path keeps its legacy category behavior (so pulling
# PR-K is inert); the operator sets it after creating the Box root to activate the
# mirror tree. Read by intake/weekly_generate via their `_read_str_setting`.
CFG_BOX_PORTAL_ROOT = "safety_reports.box.portal_root_folder_id"


def job_folder_name(project_name: str) -> str:
    """Sanitize a project display name into a folder/sheet title.

    Drop non-printable chars, turn `/` (path-like to Smartsheet AND Box) into `-`,
    strip surrounding whitespace; fall back to the raw stripped name if sanitizing
    empties it. Used as BOTH the Smartsheet per-job folder title AND the Box per-job
    folder name — identical sanitization on both sides (e.g. "Bradley 1").
    """
    cleaned = "".join(ch for ch in project_name if ch.isprintable())
    return cleaned.replace("/", "-").strip() or project_name.strip()


def week_label(any_date_in_week: date) -> str:
    """The week portion, keyed on the Saturday that opens the date's week.

    e.g. `"week of 2026-05-30"`. The Box per-week FOLDER name AND the tail of the
    Smartsheet week-SHEET name (`"<project> — week of <Sat>"`). Any day Sat→Fri maps
    to the same label, so intake (a work-date) and weekly_generate (the week start)
    produce an identical Box week-folder name for the same week.
    """
    saturday = safety_week.week_bounds(any_date_in_week).start
    return f"week of {saturday.isoformat()}"
