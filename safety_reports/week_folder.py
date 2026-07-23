"""Find-or-create the per-week Field Reports scaffold for one project.

Each Forefront project (Bradley 1, Brimfield 1, etc.) has a Field Reports
sub-folder in the Smartsheet portfolio (see
`shared.sheet_ids.FIELD_REPORTS_FOLDER_BY_PROJECT`). Inside that folder
sits one sub-folder per workweek named ``Week of YYYY-MM-DD`` (Monday
ISO date), holding exactly two sheets:

    Daily Reports — Week of YYYY-MM-DD
    Weekly Rollup — Week of YYYY-MM-DD

Both sheets clone the Bradley 1 / Week of 2026-03-09 templates so every
project's weekly cadence ships with the same schema. The clone is
structure-only — column titles, picklists, descriptions — not the
template week's residual rows.

This helper is called from two places (neither yet wired — see
`safety_reports/intake.py` and the future `weekly_generate.py`):

  - per inbound safety email (intake.py), so the row-write target
    always exists.
  - Friday afternoon by `weekly_generate.py`, so the rollup target is
    ready before the WPR draft pass.

Idempotency is the single load-bearing property. Calling
`ensure_current_week_folder("Bradley 1")` twice in one process produces
no API writes on the second call and returns the same `WeekScaffold`.
A race between two concurrent callers can produce duplicate folders
(Smartsheet does not enforce folder-name uniqueness); when detected on
post-create find, this module WARNs via `shared.error_log` and returns
the first match — duplicate cleanup is operator manual per the
``docs/tech_debt.md`` race-condition entry.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from shared import error_log, sheet_ids, smartsheet_client
from shared.error_log import Severity

SCRIPT_NAME = "safety_reports.week_folder"

# Template sheets cloned forward for each new week. These are the
# Bradley 1 / Week of 2026-03-09 sheets — verified live 2026-05-21.
# Per-customer-repo invariant: replace at fork time if the blueprint
# is forked for a different customer's portfolio.
TEMPLATE_DAILY_REPORTS_SHEET_ID = 7503204592865156
TEMPLATE_WEEKLY_ROLLUP_SHEET_ID = 1173075633590148


@dataclass(frozen=True)
class WeekScaffold:
    """The three Smartsheet IDs that pin a week's Field Reports scaffold.

    `folder_id` — the per-week folder under the project's Field Reports
    subtree.
    `daily_reports_sheet_id` — the per-day intake target.
    `weekly_rollup_sheet_id` — the WPR draft + send target.
    """
    folder_id: int
    daily_reports_sheet_id: int
    weekly_rollup_sheet_id: int


def _monday_of(week_start: date | None) -> date:
    """Return the Monday on or before `week_start` (or today if None).

    `date.weekday()` returns 0 for Monday, so subtracting `weekday()`
    days walks back to that week's Monday. Idempotent on Mondays.
    """
    base = week_start if week_start is not None else date.today()
    return base - timedelta(days=base.weekday())


def _ensure_sheet(
    parent_folder_id: int,
    sheet_name: str,
    template_sheet_id: int,
) -> int:
    """Find sheet by name in folder; if missing, clone template; return ID.

    Structure-only clone (`include=[]`) — see
    `smartsheet_client.create_sheet_in_folder_from_template`.
    """
    existing = smartsheet_client.find_sheet_by_name_in_folder(
        parent_folder_id, sheet_name
    )
    if existing is not None:
        return existing
    return smartsheet_client.create_sheet_in_folder_from_template(
        folder_id=parent_folder_id,
        name=sheet_name,
        template_sheet_id=template_sheet_id,
        include=[],
    )


def ensure_current_week_folder(
    project_name: str,
    week_start: date | None = None,
) -> WeekScaffold:
    """Find-or-create the week's folder and its two sheets.

    Args:
        project_name: Forefront project name (must be a key in
            `sheet_ids.FIELD_REPORTS_FOLDER_BY_PROJECT`). Unknown names
            raise `KeyError` — silent-skipping a typo would write to
            nowhere observable, which violates the "never silent" rule
            (CLAUDE.md "Observable, recoverable, never silent").
        week_start: any date inside the target week. Defaults to today;
            the helper walks back to that week's Monday. Pass a
            different date to backfill a missed week or to schedule a
            future week from a cron.

    Returns:
        `WeekScaffold` with the resolved (folder_id, daily_reports,
        weekly_rollup) IDs. Same call twice produces the same scaffold
        with zero API writes on the second invocation.
    """
    field_reports_folder_id = sheet_ids.FIELD_REPORTS_FOLDER_BY_PROJECT[project_name]
    monday = _monday_of(week_start)
    folder_name = f"Week of {monday.isoformat()}"
    daily_reports_name = f"Daily Reports — Week of {monday.isoformat()}"
    weekly_rollup_name = f"Weekly Rollup — Week of {monday.isoformat()}"

    folder_id = smartsheet_client.find_folder_by_name_in_folder(
        field_reports_folder_id, folder_name
    )
    if folder_id is None:
        folder_id = smartsheet_client.create_folder_in_folder(
            field_reports_folder_id, folder_name
        )
        # Race-safety post-create check. Two concurrent callers can both
        # pass the find step above and both create the folder; Smartsheet
        # does not enforce folder-name uniqueness. WARN-log and return the
        # first match — operator cleans up duplicates manually per the
        # tech_debt.md entry. Bounded blast radius: the duplicate folder is
        # empty (we haven't created its sheets yet on the losing side).
        post_find = smartsheet_client.find_folder_by_name_in_folder(
            field_reports_folder_id, folder_name
        )
        if post_find is not None and post_find != folder_id:
            error_log.log(
                Severity.WARN,
                SCRIPT_NAME,
                (
                    f"Duplicate {folder_name!r} folders detected under "
                    f"field-reports folder {field_reports_folder_id} "
                    f"(project={project_name!r}); using first match "
                    f"{post_find}, manual cleanup needed for {folder_id}."
                ),
                error_code="week_folder_race_duplicate",
            )
            folder_id = post_find

    daily_reports_sheet_id = _ensure_sheet(
        folder_id, daily_reports_name, TEMPLATE_DAILY_REPORTS_SHEET_ID
    )
    weekly_rollup_sheet_id = _ensure_sheet(
        folder_id, weekly_rollup_name, TEMPLATE_WEEKLY_ROLLUP_SHEET_ID
    )

    return WeekScaffold(
        folder_id=folder_id,
        daily_reports_sheet_id=daily_reports_sheet_id,
        weekly_rollup_sheet_id=weekly_rollup_sheet_id,
    )
