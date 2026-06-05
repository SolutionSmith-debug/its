"""Tests for safety_reports/week_sheet.py.

All Smartsheet calls are mocked — these tests never hit the API. Live coverage
lives in tests/test_week_sheet_integration.py.
"""
from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock

import pytest

from safety_reports import week_sheet
from safety_reports.week_sheet import (
    COL_STATUS,
    COL_SUBMISSION_PDF,
    COL_SUBMISSION_UUID,
    COL_SUPERSEDED_BY,
    ROW_TYPE_ROLLUP,
    ROW_TYPE_SUBMISSION,
    STATUS_SUPERSEDED,
    ensure_week_sheet,
    find_submission_row,
    supersede_row,
    week_sheet_name,
    write_submission_row,
)
from shared import sheet_ids
from shared.error_log import Severity


@pytest.fixture
def stub_ss(mocker) -> dict[str, MagicMock]:
    return {
        "find_sheet": mocker.patch.object(
            week_sheet.smartsheet_client, "find_sheet_by_name_in_folder"
        ),
        "create_sheet": mocker.patch.object(
            week_sheet.smartsheet_client, "create_sheet_in_folder"
        ),
        "get_rows": mocker.patch.object(week_sheet.smartsheet_client, "get_rows"),
        "add_rows": mocker.patch.object(week_sheet.smartsheet_client, "add_rows"),
        "update_rows": mocker.patch.object(week_sheet.smartsheet_client, "update_rows"),
    }


@pytest.fixture
def stub_error_log(mocker) -> MagicMock:
    return mocker.patch.object(week_sheet.error_log, "log")


# ---- week_sheet_name (Saturday bucketing) --------------------------------


@pytest.mark.parametrize(
    "work_date,expected_saturday",
    [
        (date(2026, 6, 5), "2026-05-30"),   # Friday → week opened the prior Saturday
        (date(2026, 5, 30), "2026-05-30"),  # Saturday maps to itself
        (date(2026, 5, 31), "2026-05-30"),  # Sunday rolls forward to its Saturday
        (date(2027, 1, 1), "2026-12-26"),   # New-Year-straddling week keys by Saturday
    ],
)
def test_week_sheet_name_keys_on_saturday(work_date, expected_saturday):
    assert week_sheet_name("Bradley 1", work_date) == (
        f"Bradley 1 — week of {expected_saturday}"
    )


# ---- ensure_week_sheet ---------------------------------------------------


def test_ensure_week_sheet_existing_returns_without_create(stub_ss):
    stub_ss["find_sheet"].return_value = 8001
    sid = ensure_week_sheet("Bradley 1", date(2026, 6, 5))
    assert sid == 8001
    stub_ss["create_sheet"].assert_not_called()


def test_ensure_week_sheet_missing_creates_with_schema(stub_ss):
    stub_ss["find_sheet"].side_effect = [None, None]  # pre-find, post-find
    stub_ss["create_sheet"].return_value = 8002
    sid = ensure_week_sheet("Bradley 1", date(2026, 6, 5))
    assert sid == 8002
    # Created in the project's Field Reports folder with the Saturday name + schema.
    args = stub_ss["create_sheet"].call_args.args
    assert args[0] == sheet_ids.FOLDER_FIELD_REPORTS_BRADLEY_1
    assert args[1] == "Bradley 1 — week of 2026-05-30"
    cols = args[2]
    titles = [c["title"] for c in cols]
    assert COL_SUBMISSION_UUID in titles and COL_SUBMISSION_PDF in titles
    # exactly one primary, and it is TEXT_NUMBER
    primaries = [c for c in cols if c.get("primary")]
    assert len(primaries) == 1 and primaries[0]["type"] == "TEXT_NUMBER"


def test_ensure_week_sheet_race_warns_and_uses_first_match(stub_ss, stub_error_log):
    stub_ss["find_sheet"].side_effect = [None, 7000]  # pre-find None, post-find 7000
    stub_ss["create_sheet"].return_value = 9999
    sid = ensure_week_sheet("Bradley 1", date(2026, 6, 5))
    assert sid == 7000  # the survivor, not the just-created 9999
    stub_error_log.assert_called_once()
    call = stub_error_log.call_args
    assert call.args[0] == Severity.WARN
    assert call.kwargs.get("error_code") == "week_sheet_race_duplicate"
    assert "9999" in call.args[2] and "7000" in call.args[2]


def test_ensure_week_sheet_unknown_project_raises(stub_ss):
    with pytest.raises(KeyError):
        ensure_week_sheet("Atlantis", date(2026, 6, 5))
    stub_ss["find_sheet"].assert_not_called()


# ---- find_submission_row (dedupe authority) ------------------------------


def test_find_submission_row_matches_uuid(stub_ss):
    stub_ss["get_rows"].return_value = [
        {"_row_id": 1, COL_SUBMISSION_UUID: "u-other", "Row Type": ROW_TYPE_SUBMISSION},
        {"_row_id": 2, COL_SUBMISSION_UUID: "u-target", "Row Type": ROW_TYPE_SUBMISSION,
         COL_SUBMISSION_PDF: "https://app.box.com/file/5"},
    ]
    row = find_submission_row(8001, "u-target")
    assert row is not None and row["_row_id"] == 2
    assert row[COL_SUBMISSION_PDF] == "https://app.box.com/file/5"


def test_find_submission_row_ignores_rollup_rows(stub_ss):
    stub_ss["get_rows"].return_value = [
        {"_row_id": 9, COL_SUBMISSION_UUID: "u-target", "Row Type": ROW_TYPE_ROLLUP},
    ]
    assert find_submission_row(8001, "u-target") is None


def test_find_submission_row_no_match_returns_none(stub_ss):
    stub_ss["get_rows"].return_value = [
        {"_row_id": 1, COL_SUBMISSION_UUID: "u-other", "Row Type": ROW_TYPE_SUBMISSION},
    ]
    assert find_submission_row(8001, "u-target") is None


def test_find_submission_row_blank_uuid_returns_none_without_read(stub_ss):
    assert find_submission_row(8001, "") is None
    stub_ss["get_rows"].assert_not_called()


# ---- write_submission_row ------------------------------------------------


def test_write_submission_row_payload_and_label(stub_ss):
    stub_ss["add_rows"].return_value = [555]
    rid = write_submission_row(
        8001,
        submission_uuid="u1",
        form_code="jha-v1",
        work_date=date(2026, 6, 5),
        title="Job Hazard Analysis",
        box_link="https://app.box.com/file/7",
        submitted_at="2026-06-05T08:00:00-07:00",
        notes="[incomplete: 1]",
    )
    assert rid == 555
    sheet_id_arg, rows_arg = stub_ss["add_rows"].call_args.args
    assert sheet_id_arg == 8001
    row = rows_arg[0]
    assert row[COL_SUBMISSION_UUID] == "u1"
    assert row["Submission"] == "2026-06-05 — Job Hazard Analysis"
    assert row["Row Type"] == ROW_TYPE_SUBMISSION
    assert row[COL_STATUS] == "Active"
    assert row[COL_SUBMISSION_PDF] == "https://app.box.com/file/7"
    assert row["Notes"] == "[incomplete: 1]"


# ---- supersede_row (amend) -----------------------------------------------


def test_supersede_row_marks_prior_superseded(stub_ss):
    stub_ss["get_rows"].return_value = [
        {"_row_id": 42, COL_SUBMISSION_UUID: "u-prior", "Row Type": ROW_TYPE_SUBMISSION},
    ]
    assert supersede_row(8001, "u-prior", "u-new") is True
    sheet_id_arg, updates = stub_ss["update_rows"].call_args.args
    assert sheet_id_arg == 8001
    upd = updates[0]
    assert upd["_row_id"] == 42
    assert upd[COL_STATUS] == STATUS_SUPERSEDED
    assert upd[COL_SUPERSEDED_BY] == "u-new"


def test_supersede_row_missing_prior_returns_false_without_update(stub_ss):
    stub_ss["get_rows"].return_value = []
    assert supersede_row(8001, "u-prior", "u-new") is False
    stub_ss["update_rows"].assert_not_called()


# ---- review-hardening: schema completeness + whitespace UUID --------------


def test_week_sheet_schema_is_complete_and_typed():
    titles = [c["title"] for c in week_sheet.WEEK_SHEET_COLUMNS]
    for col in (
        week_sheet.COL_SUBMISSION, COL_SUBMISSION_UUID, week_sheet.COL_FORM_CODE,
        week_sheet.COL_WORK_DATE, week_sheet.COL_SUBMITTED_AT, COL_SUBMISSION_PDF,
        week_sheet.COL_ROW_TYPE, COL_STATUS, COL_SUPERSEDED_BY, week_sheet.COL_NOTES,
    ):
        assert col in titles, f"missing column {col!r}"
    primaries = [c for c in week_sheet.WEEK_SHEET_COLUMNS if c.get("primary")]
    assert len(primaries) == 1 and primaries[0]["type"] == "TEXT_NUMBER"
    work_date_col = next(c for c in week_sheet.WEEK_SHEET_COLUMNS if c["title"] == week_sheet.COL_WORK_DATE)
    assert work_date_col["type"] == "DATE"


def test_find_submission_row_whitespace_only_uuid_returns_none_without_read(stub_ss):
    assert find_submission_row(8001, "   ") is None
    stub_ss["get_rows"].assert_not_called()


# ---- Phase-5b rollup/compile helpers --------------------------------------

from safety_reports.week_sheet import (  # noqa: E402
    COL_COMPILE_NOW,
    COL_WORK_DATE,
    ROLLUP_LABEL,
    compile_now_requested,
    get_rollup_row,
    latest_submitted_at,
    list_submission_rows,
    upsert_rollup_row,
)


def test_list_submission_rows_excludes_superseded_and_rollup_and_orders(stub_ss):
    stub_ss["get_rows"].return_value = [
        {"_row_id": 9, COL_SUBMISSION_UUID: "r", "Row Type": ROW_TYPE_ROLLUP},  # rollup excluded
        {"_row_id": 1, COL_SUBMISSION_UUID: "b", "Row Type": ROW_TYPE_SUBMISSION,
         COL_STATUS: STATUS_SUPERSEDED, COL_WORK_DATE: "2026-06-01", week_sheet.COL_SUBMITTED_AT: "x"},
        {"_row_id": 2, COL_SUBMISSION_UUID: "a", "Row Type": ROW_TYPE_SUBMISSION,
         COL_STATUS: "Active", COL_WORK_DATE: "2026-06-02", week_sheet.COL_SUBMITTED_AT: "2026-06-02T09:00-07:00"},
        {"_row_id": 3, COL_SUBMISSION_UUID: "c", "Row Type": ROW_TYPE_SUBMISSION,
         COL_STATUS: "Active", COL_WORK_DATE: "2026-06-01", week_sheet.COL_SUBMITTED_AT: "2026-06-01T09:00-07:00"},
    ]
    active = list_submission_rows(8001, active_only=True)
    assert [r["_row_id"] for r in active] == [3, 2]  # superseded(1)+rollup(9) excluded; ordered by work date
    all_subs = list_submission_rows(8001, active_only=False)
    assert {r["_row_id"] for r in all_subs} == {1, 2, 3}  # superseded included, rollup still excluded


def test_get_rollup_row(stub_ss):
    stub_ss["get_rows"].return_value = [
        {"_row_id": 1, "Row Type": ROW_TYPE_SUBMISSION},
        {"_row_id": 2, "Row Type": ROW_TYPE_ROLLUP},
    ]
    r = get_rollup_row(8001)
    assert r is not None and r["_row_id"] == 2


@pytest.mark.parametrize("rollup,expected", [
    (None, False),
    ({COL_COMPILE_NOW: True}, True),
    ({COL_COMPILE_NOW: False}, False),
    ({}, False),
])
def test_compile_now_requested(rollup, expected):
    assert compile_now_requested(rollup) is expected


def test_latest_submitted_at_excludes_blanks():
    assert latest_submitted_at([
        {week_sheet.COL_SUBMITTED_AT: ""},
        {week_sheet.COL_SUBMITTED_AT: "2026-06-02T09:00-07:00"},
        {week_sheet.COL_SUBMITTED_AT: "   "},
        {week_sheet.COL_SUBMITTED_AT: "2026-06-01T09:00-07:00"},
    ]) == "2026-06-02T09:00-07:00"
    assert latest_submitted_at([{week_sheet.COL_SUBMITTED_AT: ""}]) == ""  # all blank → ''
    assert latest_submitted_at([]) == ""


def test_upsert_rollup_row_create_sets_rollup_type_and_clears_compile_now(stub_ss):
    stub_ss["add_rows"].return_value = [42]
    rid = upsert_rollup_row(8001, packet_link="L", compiled_at="2026-06-05T09:00-07:00",
                            manifest_note="2 subs")
    assert rid == 42
    row = stub_ss["add_rows"].call_args.args[1][0]
    assert row["Row Type"] == ROW_TYPE_ROLLUP and row["Submission"] == ROLLUP_LABEL
    assert row[COL_COMPILE_NOW] is False  # cleared on write
    assert row[COL_SUBMISSION_PDF] == "L"


def test_upsert_rollup_row_update_threads_row_id_and_clears_compile_now(stub_ss):
    rid = upsert_rollup_row(8001, packet_link="L2", compiled_at="t", manifest_note="m",
                            existing_rollup_row_id=77)
    assert rid == 77
    stub_ss["add_rows"].assert_not_called()
    upd = stub_ss["update_rows"].call_args.args[1][0]
    assert upd["_row_id"] == 77 and upd[COL_COMPILE_NOW] is False
