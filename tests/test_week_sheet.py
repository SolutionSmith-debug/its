"""Tests for safety_reports/week_sheet.py.

All Smartsheet calls are mocked — these tests never hit the API. Live coverage
lives in tests/test_week_sheet_integration.py.
"""
from __future__ import annotations

from datetime import date
from typing import Any
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
    SAFETY_WEEK_SHEET_CONFIG,
    STATUS_SUPERSEDED,
    WeekSheetConfig,
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
        "find_folder": mocker.patch.object(
            week_sheet.smartsheet_client, "find_folder_by_name_in_workspace"
        ),
        "create_folder": mocker.patch.object(
            week_sheet.smartsheet_client, "create_folder_in_workspace"
        ),
        "find_sheet": mocker.patch.object(
            week_sheet.smartsheet_client, "find_sheet_by_name_in_folder"
        ),
        "create_sheet": mocker.patch.object(
            week_sheet.smartsheet_client, "create_sheet_in_folder"
        ),
        "get_rows": mocker.patch.object(week_sheet.smartsheet_client, "get_rows"),
        "add_rows": mocker.patch.object(
            week_sheet.smartsheet_client, "add_rows", return_value=[99999]
        ),
        "update_rows": mocker.patch.object(week_sheet.smartsheet_client, "update_rows"),
        "apply_styles": mocker.patch.object(
            week_sheet.smartsheet_client, "apply_column_styles"
        ),
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


def test_week_sheet_name_short_project_is_unchanged():
    """A name already <= 50 chars is byte-identical to the pre-cap behavior
    (so every existing week sheet still resolves find-or-create)."""
    name = week_sheet_name("ZZ Portal Proof", date(2026, 6, 13))
    assert name == "ZZ Portal Proof — week of 2026-06-13"
    assert len(name) <= week_sheet.SHEET_NAME_MAX


def test_week_sheet_name_long_project_is_truncated_to_cap():
    """A 30+ char project name (the live JOB-000013 failure) is bounded to the
    50-char Smartsheet cap — without this, create_sheet_in_folder 400s with
    errorCode 1041 and the portal submission can never file."""
    # The exact live regression: "I don't know project name Montgomery" (36) →
    # composed 57 chars before the cap.
    name = week_sheet_name("I don't know project name Montgomery", date(2026, 6, 13))
    assert len(name) <= week_sheet.SHEET_NAME_MAX  # the hard requirement
    # The week-label suffix is preserved WHOLE (it disambiguates weeks in the folder);
    # only the project prefix is trimmed.
    assert name.endswith(" — week of 2026-06-13")
    assert name.startswith("I don't know project name")


def test_week_sheet_name_preserves_full_week_label_under_truncation():
    """Even with a pathologically long project name, the entire week label
    survives — truncation only ever eats the project prefix, never the date key."""
    name = week_sheet_name("X" * 200, date(2026, 5, 30))
    assert len(name) == week_sheet.SHEET_NAME_MAX
    assert name.endswith(" — week of 2026-05-30")


# ---- ensure_week_sheet ---------------------------------------------------


def test_ensure_week_sheet_existing_returns_without_create(stub_ss):
    stub_ss["find_folder"].return_value = 4242  # per-job folder already exists
    stub_ss["find_sheet"].return_value = 8001
    sid = ensure_week_sheet(SAFETY_WEEK_SHEET_CONFIG, "Bradley 1", date(2026, 6, 5))
    assert sid == 8001
    stub_ss["create_folder"].assert_not_called()
    stub_ss["create_sheet"].assert_not_called()


def test_ensure_week_sheet_missing_creates_with_schema(stub_ss):
    stub_ss["find_folder"].return_value = 4242  # folder exists; sheet does not
    stub_ss["find_sheet"].side_effect = [None, None]  # pre-find, post-find
    stub_ss["create_sheet"].return_value = 8002
    sid = ensure_week_sheet(SAFETY_WEEK_SHEET_CONFIG, "Bradley 1", date(2026, 6, 5))
    assert sid == 8002
    # Created INSIDE the per-job folder with the Saturday name + schema.
    args = stub_ss["create_sheet"].call_args.args
    assert args[0] == 4242
    assert args[1] == "Bradley 1 — week of 2026-05-30"
    cols = args[2]
    titles = [c["title"] for c in cols]
    assert COL_SUBMISSION_UUID in titles and COL_SUBMISSION_PDF in titles
    # exactly one primary, and it is TEXT_NUMBER
    primaries = [c for c in cols if c.get("primary")]
    assert len(primaries) == 1 and primaries[0]["type"] == "TEXT_NUMBER"
    # Cosmetic styling applied to the NEW sheet (widths + format), best-effort.
    stub_ss["apply_styles"].assert_called_once_with(8002, week_sheet.WEEK_SHEET_STYLES)


def test_ensure_week_sheet_existing_does_not_restyle(stub_ss):
    stub_ss["find_sheet"].return_value = 8001  # already exists → find path
    ensure_week_sheet(SAFETY_WEEK_SHEET_CONFIG, "Bradley 1", date(2026, 6, 5))
    stub_ss["apply_styles"].assert_not_called()


def test_ensure_week_sheet_styling_failure_does_not_block(stub_ss, stub_error_log):
    # Cosmetic: a styling failure WARNs but the sheet id is still returned.
    stub_ss["find_folder"].return_value = 4242
    stub_ss["find_sheet"].side_effect = [None, None]
    stub_ss["create_sheet"].return_value = 8002
    stub_ss["apply_styles"].side_effect = week_sheet.smartsheet_client.SmartsheetError("boom")
    assert ensure_week_sheet(SAFETY_WEEK_SHEET_CONFIG, "Bradley 1", date(2026, 6, 5)) == 8002
    assert stub_error_log.called


def test_ensure_week_sheet_race_warns_and_uses_first_match(stub_ss, stub_error_log):
    stub_ss["find_folder"].return_value = 4242  # folder exists (no folder race here)
    stub_ss["find_sheet"].side_effect = [None, 7000]  # pre-find None, post-find 7000
    stub_ss["create_sheet"].return_value = 9999
    sid = ensure_week_sheet(SAFETY_WEEK_SHEET_CONFIG, "Bradley 1", date(2026, 6, 5))
    assert sid == 7000  # the survivor, not the just-created 9999
    stub_error_log.assert_called_once()
    call = stub_error_log.call_args
    assert call.args[0] == Severity.WARN
    assert call.kwargs.get("error_code") == "week_sheet_race_duplicate"
    assert "9999" in call.args[2] and "7000" in call.args[2]


def test_ensure_week_sheet_precreates_rollup_placeholder(stub_ss):
    """On CREATE, an empty Rollup row is written so the Compile Now trigger exists immediately
    (an operator can request an on-demand compile for a never-yet-compiled week)."""
    stub_ss["find_folder"].return_value = 4242
    stub_ss["find_sheet"].side_effect = [None, None]
    stub_ss["create_sheet"].return_value = 8002
    ensure_week_sheet(SAFETY_WEEK_SHEET_CONFIG, "Bradley 1", date(2026, 6, 5))
    stub_ss["add_rows"].assert_called_once()
    row = stub_ss["add_rows"].call_args.args[1][0]
    assert row[week_sheet.COL_ROW_TYPE] == week_sheet.ROW_TYPE_ROLLUP
    assert row[week_sheet.COL_COMPILE_NOW] is False  # trigger starts UNchecked


def test_ensure_week_sheet_existing_does_not_precreate_rollup(stub_ss):
    """The FIND path must NOT write a placeholder — the Rollup row already exists."""
    stub_ss["find_sheet"].return_value = 8001
    ensure_week_sheet(SAFETY_WEEK_SHEET_CONFIG, "Bradley 1", date(2026, 6, 5))
    stub_ss["add_rows"].assert_not_called()


def test_ensure_week_sheet_rollup_placeholder_failure_does_not_block(stub_ss, stub_error_log):
    """A transient placeholder-write failure WARNs but the sheet id is still returned (intake
    needs the sheet; the next compile creates the Rollup row)."""
    stub_ss["find_folder"].return_value = 4242
    stub_ss["find_sheet"].side_effect = [None, None]
    stub_ss["create_sheet"].return_value = 8002
    stub_ss["add_rows"].side_effect = week_sheet.smartsheet_client.SmartsheetError("boom")
    assert ensure_week_sheet(SAFETY_WEEK_SHEET_CONFIG, "Bradley 1", date(2026, 6, 5)) == 8002
    assert stub_error_log.called


def test_ensure_week_sheet_unknown_project_auto_provisions(stub_ss):
    # A brand-new job (no hardcoded map) self-provisions its folder + sheet.
    stub_ss["find_folder"].side_effect = [None, None]  # pre-find, post-find (no race)
    stub_ss["create_folder"].return_value = 5555
    stub_ss["find_sheet"].side_effect = [None, None]
    stub_ss["create_sheet"].return_value = 8003
    sid = ensure_week_sheet(SAFETY_WEEK_SHEET_CONFIG, "Atlantis", date(2026, 6, 5))
    assert sid == 8003
    stub_ss["create_folder"].assert_called_once_with(
        sheet_ids.WORKSPACE_SAFETY_PORTAL, "Atlantis"
    )
    # the week sheet is created INSIDE the just-provisioned folder
    assert stub_ss["create_sheet"].call_args.args[0] == 5555


def test_ensure_week_sheet_creates_folder_under_workspace_when_missing(stub_ss):
    stub_ss["find_folder"].side_effect = [None, None]
    stub_ss["create_folder"].return_value = 6001
    stub_ss["find_sheet"].return_value = 8010  # sheet already exists in the new folder
    ensure_week_sheet(SAFETY_WEEK_SHEET_CONFIG, "Bradley 1", date(2026, 6, 5))
    stub_ss["create_folder"].assert_called_once_with(
        sheet_ids.WORKSPACE_SAFETY_PORTAL, "Bradley 1"
    )


def test_ensure_week_sheet_folder_race_warns_and_uses_first_match(stub_ss, stub_error_log):
    stub_ss["find_folder"].side_effect = [None, 6000]  # pre None, post 6000 (folder race)
    stub_ss["create_folder"].return_value = 9090
    stub_ss["find_sheet"].return_value = 8050  # sheet exists in the survivor folder
    sid = ensure_week_sheet(SAFETY_WEEK_SHEET_CONFIG, "Bradley 1", date(2026, 6, 5))
    assert sid == 8050
    # the sheet lookup used the survivor folder (6000), not the just-created 9090
    assert stub_ss["find_sheet"].call_args.args[0] == 6000
    stub_error_log.assert_called_once()
    call = stub_error_log.call_args
    assert call.args[0] == Severity.WARN
    assert call.kwargs.get("error_code") == "week_sheet_folder_race_duplicate"
    assert "9090" in call.args[2] and "6000" in call.args[2]


@pytest.mark.parametrize("raw,expected", [
    ("Bradley 1", "Bradley 1"),
    ("A/B Job", "A-B Job"),    # slash → dash (Smartsheet path-safety)
    ("  Padded  ", "Padded"),  # surrounding whitespace stripped
])
def test_folder_name_sanitizes(raw, expected):
    assert week_sheet._folder_name(raw) == expected


def test_ensure_week_sheet_uses_sanitized_folder_name(stub_ss):
    stub_ss["find_folder"].return_value = 4242
    stub_ss["find_sheet"].return_value = 8001
    ensure_week_sheet(SAFETY_WEEK_SHEET_CONFIG, "A/B Job", date(2026, 6, 5))
    # folder lookup uses the sanitized name; the sheet name keeps the raw project.
    assert stub_ss["find_folder"].call_args.args == (
        sheet_ids.WORKSPACE_SAFETY_PORTAL, "A-B Job"
    )


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
    assert row["_formats"][COL_STATUS] == week_sheet.STATUS_ACTIVE_FMT  # green status cell


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
    assert upd["_formats"][COL_STATUS] == week_sheet.STATUS_SUPERSEDED_FMT  # gray status cell


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
    any_compile_now_requested,
    append_rollup_row,
    clear_compile_now_on_rollups,
    compile_now_requested,
    get_rollup_row,
    latest_submitted_at,
    list_rollup_rows,
    list_submission_rows,
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


def test_list_rollup_rows_orders_and_get_rollup_returns_latest(stub_ss):
    # Append-only: many Rollup snapshots; ordered by Submitted At (placeholder '' first), and
    # get_rollup_row returns the LATEST (the no-new-docs watermark).
    stub_ss["get_rows"].return_value = [
        {"_row_id": 1, "Row Type": ROW_TYPE_SUBMISSION},
        {"_row_id": 2, "Row Type": ROW_TYPE_ROLLUP, week_sheet.COL_SUBMITTED_AT: "2026-06-05T09:00-07:00"},
        {"_row_id": 9, "Row Type": ROW_TYPE_ROLLUP, week_sheet.COL_SUBMITTED_AT: ""},  # placeholder
        {"_row_id": 3, "Row Type": ROW_TYPE_ROLLUP, week_sheet.COL_SUBMITTED_AT: "2026-06-06T09:00-07:00"},
    ]
    assert [r["_row_id"] for r in list_rollup_rows(8001)] == [9, 2, 3]
    assert get_rollup_row(8001)["_row_id"] == 3  # latest by Submitted At


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


def test_append_rollup_row_sets_rollup_type_and_clears_compile_now(stub_ss):
    stub_ss["add_rows"].return_value = [42]
    rid = append_rollup_row(8001, packet_link="L", compiled_at="2026-06-05T09:00-07:00",
                            manifest_note="2 subs")
    assert rid == 42
    row = stub_ss["add_rows"].call_args.args[1][0]
    assert row["Row Type"] == ROW_TYPE_ROLLUP and row["Submission"] == ROLLUP_LABEL
    assert row[COL_COMPILE_NOW] is False  # the new snapshot is written un-triggered
    assert row[COL_SUBMISSION_PDF] == "L"


def test_append_rollup_row_always_adds_never_updates(stub_ss):
    # APPEND-ONLY: append_rollup_row ADDS a new snapshot — it must never update_rows, so a
    # prior compilation's Rollup row (packet link + manifest) is never overwritten.
    stub_ss["add_rows"].return_value = [43]
    append_rollup_row(8001, packet_link="L2", compiled_at="t", manifest_note="m")
    stub_ss["add_rows"].assert_called_once()
    stub_ss["update_rows"].assert_not_called()


@pytest.mark.parametrize("rows,expected", [
    ([], False),
    ([{COL_COMPILE_NOW: False}, {COL_COMPILE_NOW: True}], True),
    ([{COL_COMPILE_NOW: False}, {}], False),
])
def test_any_compile_now_requested(rows, expected):
    assert any_compile_now_requested(rows) is expected


def test_clear_compile_now_on_rollups_clears_only_checked(stub_ss):
    rollup_rows = [
        {"_row_id": 5, COL_COMPILE_NOW: True},
        {"_row_id": 6, COL_COMPILE_NOW: False},
        {"_row_id": 7, COL_COMPILE_NOW: True},
        {COL_COMPILE_NOW: True},  # no _row_id → skipped
    ]
    clear_compile_now_on_rollups(8001, rollup_rows)
    upd = stub_ss["update_rows"].call_args.args[1]
    assert [u["_row_id"] for u in upd] == [5, 7]
    assert all(u[COL_COMPILE_NOW] is False for u in upd)


def test_clear_compile_now_on_rollups_noop_when_none_checked(stub_ss):
    clear_compile_now_on_rollups(8001, [{"_row_id": 5, COL_COMPILE_NOW: False}])
    stub_ss["update_rows"].assert_not_called()


# ── P1a parameterization: contamination gate + byte-identical safety binding ──


def test_safety_config_reproduces_current_behavior_byte_identically():
    """The safety binding is byte-identical: the exact workspace pin + the
    unchanged name builder (object identity), so every find-or-create resolves the
    SAME folders/sheets — zero behavior change, zero migration."""
    cfg = SAFETY_WEEK_SHEET_CONFIG
    assert cfg.workspace_id == sheet_ids.WORKSPACE_SAFETY_PORTAL
    assert cfg.key_builder is week_sheet_name
    assert week_sheet._folder_name("Bradley 1") == "Bradley 1"
    assert (
        cfg.key_builder("Bradley 1", date(2026, 6, 5))
        == "Bradley 1 — week of 2026-05-30"
    )
    # the 50-char cap still bites identically (long prefix truncated, suffix whole)
    assert len(cfg.key_builder("X" * 200, date(2026, 5, 30))) == week_sheet.SHEET_NAME_MAX


def test_week_sheet_config_requires_all_fields():
    """No field defaults to a safety value — a forgotten or malformed field fails
    LOUDLY at construction (the contamination gate), never a silent fall-through."""
    make: Any = WeekSheetConfig
    with pytest.raises(TypeError):
        make()  # both fields missing
    with pytest.raises(TypeError):
        make(workspace_id=sheet_ids.WORKSPACE_SAFETY_PORTAL)  # key_builder missing
    with pytest.raises(TypeError):
        make(workspace_id=123, key_builder="not-callable")  # malformed builder
    with pytest.raises(ValueError):
        make(workspace_id=0, key_builder=week_sheet_name)  # non-positive workspace


def test_ensure_week_sheet_requires_config():
    """`config` is a required first positional with NO default — omitting it raises
    TypeError at the call, never a silent file into the safety workspace."""
    call: Any = ensure_week_sheet
    with pytest.raises(TypeError):
        call("Bradley 1", date(2026, 6, 5))  # config omitted


def test_progress_config_targets_progress_workspace():
    """P3: the progress binding pins the PROGRESS workspace + REUSES the safety name
    builder by identity (safety/progress share the weekly Sat→Fri cadence) — only the
    workspace differs, so the same (project, date) yields the same sheet NAME in a
    different workspace, and the contamination gate's positive-int rule still holds."""
    cfg = week_sheet.PROGRESS_WEEK_SHEET_CONFIG
    assert cfg.workspace_id == sheet_ids.WORKSPACE_PROGRESS_REPORTING
    assert cfg.workspace_id != sheet_ids.WORKSPACE_SAFETY_PORTAL
    assert cfg.workspace_id > 0
    assert cfg.key_builder is week_sheet_name  # same builder, by identity (no clone)
    d = date(2026, 6, 5)
    assert cfg.key_builder("Bradley 1", d) == SAFETY_WEEK_SHEET_CONFIG.key_builder("Bradley 1", d)
