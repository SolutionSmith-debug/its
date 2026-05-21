"""Tests for safety_reports/week_folder.py.

All Smartsheet calls are mocked — these tests never hit the API.
Integration coverage lives in tests/test_week_folder_integration.py.
"""
from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock

import pytest

from safety_reports import week_folder
from safety_reports.week_folder import (
    TEMPLATE_DAILY_REPORTS_SHEET_ID,
    TEMPLATE_WEEKLY_ROLLUP_SHEET_ID,
    WeekScaffold,
    ensure_current_week_folder,
)
from shared import sheet_ids
from shared.error_log import Severity


@pytest.fixture
def stub_smartsheet(mocker) -> dict[str, MagicMock]:
    """Patch all four smartsheet_client helpers used by week_folder."""
    return {
        "find_folder": mocker.patch.object(
            week_folder.smartsheet_client, "find_folder_by_name_in_folder"
        ),
        "create_folder": mocker.patch.object(
            week_folder.smartsheet_client, "create_folder_in_folder"
        ),
        "find_sheet": mocker.patch.object(
            week_folder.smartsheet_client, "find_sheet_by_name_in_folder"
        ),
        "copy_sheet": mocker.patch.object(
            week_folder.smartsheet_client,
            "create_sheet_in_folder_from_template",
        ),
    }


@pytest.fixture
def stub_error_log(mocker) -> MagicMock:
    return mocker.patch.object(week_folder.error_log, "log")


# ---- happy paths ---------------------------------------------------------


def test_folder_exists_returns_scaffold_without_creates(stub_smartsheet):
    """When the week folder + both sheets already exist, no create calls fire."""
    stub_smartsheet["find_folder"].return_value = 500
    stub_smartsheet["find_sheet"].side_effect = [42, 43]  # daily, weekly

    scaffold = ensure_current_week_folder("Bradley 1", week_start=date(2026, 3, 9))

    assert scaffold == WeekScaffold(
        folder_id=500,
        daily_reports_sheet_id=42,
        weekly_rollup_sheet_id=43,
    )
    stub_smartsheet["create_folder"].assert_not_called()
    stub_smartsheet["copy_sheet"].assert_not_called()


def test_folder_missing_creates_folder_and_both_sheets(stub_smartsheet):
    """First-time invocation creates the folder, then clones both templates."""
    # find_folder: None (initial) then None again (race check post-create)
    stub_smartsheet["find_folder"].side_effect = [None, None]
    stub_smartsheet["create_folder"].return_value = 600
    stub_smartsheet["find_sheet"].side_effect = [None, None]  # both missing
    stub_smartsheet["copy_sheet"].side_effect = [4242, 4343]

    scaffold = ensure_current_week_folder("Bradley 1", week_start=date(2026, 3, 9))

    assert scaffold == WeekScaffold(
        folder_id=600,
        daily_reports_sheet_id=4242,
        weekly_rollup_sheet_id=4343,
    )

    # Folder created with the expected name in the project's Field Reports folder.
    stub_smartsheet["create_folder"].assert_called_once_with(
        sheet_ids.FOLDER_FIELD_REPORTS_BRADLEY_1, "Week of 2026-03-09"
    )

    # Both sheets cloned from the correct templates, structure-only (include=[]).
    assert stub_smartsheet["copy_sheet"].call_count == 2
    call_kwargs = [c.kwargs for c in stub_smartsheet["copy_sheet"].call_args_list]
    assert call_kwargs[0] == {
        "folder_id": 600,
        "name": "Daily Reports — Week of 2026-03-09",
        "template_sheet_id": TEMPLATE_DAILY_REPORTS_SHEET_ID,
        "include": [],
    }
    assert call_kwargs[1] == {
        "folder_id": 600,
        "name": "Weekly Rollup — Week of 2026-03-09",
        "template_sheet_id": TEMPLATE_WEEKLY_ROLLUP_SHEET_ID,
        "include": [],
    }


def test_orphan_one_sheet_missing_only_creates_that_one(stub_smartsheet):
    """Folder exists, Daily Reports exists, Weekly Rollup missing → one clone."""
    stub_smartsheet["find_folder"].return_value = 500
    stub_smartsheet["find_sheet"].side_effect = [777, None]  # daily found, weekly missing
    stub_smartsheet["copy_sheet"].return_value = 8888

    scaffold = ensure_current_week_folder("Bradley 1", week_start=date(2026, 3, 9))

    assert scaffold.daily_reports_sheet_id == 777
    assert scaffold.weekly_rollup_sheet_id == 8888

    stub_smartsheet["create_folder"].assert_not_called()
    # Only the Weekly Rollup was cloned.
    stub_smartsheet["copy_sheet"].assert_called_once()
    assert stub_smartsheet["copy_sheet"].call_args.kwargs["template_sheet_id"] == (
        TEMPLATE_WEEKLY_ROLLUP_SHEET_ID
    )


# ---- unknown project -----------------------------------------------------


def test_unknown_project_raises_key_error(stub_smartsheet):
    """Typo in project_name surfaces as KeyError — never silent-skipped."""
    with pytest.raises(KeyError):
        ensure_current_week_folder("Atlantis", week_start=date(2026, 3, 9))

    # Confirm no Smartsheet call escaped before the lookup blew up.
    stub_smartsheet["find_folder"].assert_not_called()


# ---- week_start defaulting ----------------------------------------------


@pytest.mark.parametrize(
    "today,expected_monday_iso",
    [
        # Monday — stays put.
        (date(2026, 5, 18), "2026-05-18"),
        # Mid-week (Wednesday) — walks back two days.
        (date(2026, 5, 20), "2026-05-18"),
        # Sunday — walks back six days.
        (date(2026, 5, 24), "2026-05-18"),
        # Year boundary — Monday 2026-12-28 covers the last week of 2026.
        (date(2026, 12, 31), "2026-12-28"),
    ],
)
def test_week_start_defaults_to_monday_of_current_week(
    mocker, stub_smartsheet, today: date, expected_monday_iso: str
):
    """`date.today()` -> Monday-of-current-week for the folder name."""
    # Patch the bound `date` symbol on week_folder so date.today() returns our
    # value but date(...) construction still works as a real date.
    class FrozenDate(date):
        @classmethod
        def today(cls):
            return today

    mocker.patch.object(week_folder, "date", FrozenDate)
    stub_smartsheet["find_folder"].return_value = 500
    stub_smartsheet["find_sheet"].side_effect = [42, 43]

    ensure_current_week_folder("Bradley 1")

    assert stub_smartsheet["find_folder"].call_args_list[0].args[1] == (
        f"Week of {expected_monday_iso}"
    )


# ---- race-condition path -------------------------------------------------


def test_race_logs_warn_and_returns_first_match(stub_smartsheet, stub_error_log):
    """Race: pre-create find returns None, create returns ID A, post-create
    find returns ID B (≠ A). Helper WARNs to error_log and uses B.
    """
    stub_smartsheet["find_folder"].side_effect = [None, 700]  # pre-find, post-find
    stub_smartsheet["create_folder"].return_value = 999
    stub_smartsheet["find_sheet"].side_effect = [42, 43]

    scaffold = ensure_current_week_folder("Bradley 1", week_start=date(2026, 3, 9))

    assert scaffold.folder_id == 700  # the survivor, not the just-created 999.

    stub_error_log.assert_called_once()
    call = stub_error_log.call_args
    assert call.args[0] == Severity.WARN
    assert call.kwargs.get("error_code") == "week_folder_race_duplicate"
    # The orphan folder ID should appear in the message for operator cleanup.
    message = call.args[2]
    assert "999" in message
    assert "700" in message
