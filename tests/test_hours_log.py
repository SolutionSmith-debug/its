"""Unit tests for progress_reports.hours_log — per-job Hours Log find-or-create + idempotent
upsert + amend-supersede. All Smartsheet / capacity I/O is mocked; no test touches live state."""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from progress_reports import hours_log


@pytest.fixture
def sc(mocker):
    return {
        "find_folder": mocker.patch(
            "progress_reports.hours_log.smartsheet_client.find_folder_by_name_in_workspace",
            return_value=None,
        ),
        "create_folder": mocker.patch(
            "progress_reports.hours_log.smartsheet_client.create_folder_in_workspace",
            return_value=7001,
        ),
        "find_sheet": mocker.patch(
            "progress_reports.hours_log.smartsheet_client.find_sheet_by_name_in_folder",
            return_value=None,
        ),
        "create_sheet": mocker.patch(
            "progress_reports.hours_log.smartsheet_client.create_sheet_in_folder",
            return_value=9001,
        ),
        "styles": mocker.patch(
            "progress_reports.hours_log.smartsheet_client.apply_column_styles", return_value=None
        ),
        "get_rows": mocker.patch(
            "progress_reports.hours_log.smartsheet_client.get_rows", return_value=[]
        ),
        "add_rows": mocker.patch(
            "progress_reports.hours_log.smartsheet_client.add_rows", return_value=[555]
        ),
        "update_rows": mocker.patch(
            "progress_reports.hours_log.smartsheet_client.update_rows", return_value=None
        ),
        "capacity": mocker.patch(
            "progress_reports.hours_log.sheet_capacity.check_create_headroom",
            return_value=SimpleNamespace(note="", ok=True, current=1, ceiling=100, margin=50),
        ),
        "route": mocker.patch(
            "progress_reports.hours_log.sheet_capacity.route_breach_to_review_queue",
            return_value=None,
        ),
        "folder_name": mocker.patch(
            "progress_reports.hours_log.safety_naming.job_folder_name", side_effect=lambda p: p
        ),
        "log": mocker.patch("progress_reports.hours_log.error_log.log", return_value=None),
        "review": mocker.patch(
            "progress_reports.hours_log.review_queue.add", return_value=1
        ),
        "get_setting": mocker.patch(
            "progress_reports.hours_log.smartsheet_client.get_setting", return_value="15000"
        ),
    }


# ---- sheet name (50-char cap) --------------------------------------------


def test_sheet_name_short_is_verbatim():
    assert hours_log.hours_log_sheet_name("Bradley 1") == "Bradley 1 — Hours Log"


def test_sheet_name_truncates_long_prefix_to_cap():
    name = hours_log.hours_log_sheet_name("X" * 80)
    assert len(name) <= hours_log.SHEET_NAME_MAX
    assert name.endswith(hours_log.SHEET_SUFFIX)


# ---- ensure_hours_log_sheet ----------------------------------------------


def test_ensure_returns_existing_sheet_without_create(sc):
    sc["find_folder"].return_value = 7777
    sc["find_sheet"].return_value = 4242
    assert hours_log.ensure_hours_log_sheet("Job One") == 4242
    sc["create_sheet"].assert_not_called()
    sc["capacity"].assert_not_called()  # no create branch → no capacity check


def test_ensure_creates_sheet_when_missing(sc):
    sid = hours_log.ensure_hours_log_sheet("Job One")
    assert sid == 9001
    sc["create_sheet"].assert_called_once()
    sc["styles"].assert_called_once_with(9001, hours_log.HOURS_LOG_STYLES)
    sc["capacity"].assert_called_once()  # A1 tripwire runs only on create


def test_ensure_reuses_existing_folder(sc):
    sc["find_folder"].return_value = 7777
    hours_log.ensure_hours_log_sheet("Job One")
    sc["create_folder"].assert_not_called()


def test_ensure_capacity_breach_warns_but_still_creates(sc):
    sc["capacity"].return_value = SimpleNamespace(note="", ok=False, current=99, ceiling=100, margin=1)
    hours_log.ensure_hours_log_sheet("Job One")
    sc["route"].assert_called_once()          # breach enqueued to the Review Queue
    sc["create_sheet"].assert_called_once()   # advisory — the create STILL proceeds


# ---- upsert_entry_row ----------------------------------------------------


def _entry_kwargs(**over: Any) -> dict[str, Any]:
    kw: dict[str, Any] = dict(
        entry_uuid="T1", work_date="2026-06-27", personnel="Alice Crew",
        hours="8", started="07:00", ended="15:00", notes="footings",
        recorded_at="2026-06-27T15:00:00-07:00",
    )
    kw.update(over)
    return kw


def test_upsert_adds_new_row_when_absent(sc):
    sc["get_rows"].return_value = []  # find_entry_row → None
    assert hours_log.upsert_entry_row(9001, **_entry_kwargs()) == 555
    sc["add_rows"].assert_called_once()
    cells = sc["add_rows"].call_args.args[1][0]
    assert cells[hours_log.COL_ENTRY_UUID] == "T1"
    assert cells[hours_log.COL_PERSONNEL] == "Alice Crew"
    assert cells[hours_log.COL_STATUS] == hours_log.STATUS_ACTIVE


def test_upsert_idempotent_noop_when_present(sc):
    sc["get_rows"].return_value = [{"_row_id": 888, hours_log.COL_ENTRY_UUID: "T1"}]
    assert hours_log.upsert_entry_row(9001, **_entry_kwargs()) == 888
    sc["add_rows"].assert_not_called()  # immutable entry → no re-add (crash-replay safe)


# ---- supersede_entry_row -------------------------------------------------


def test_supersede_marks_prior_row(sc):
    sc["get_rows"].return_value = [{"_row_id": 888, hours_log.COL_ENTRY_UUID: "T1"}]
    assert hours_log.supersede_entry_row(9001, "T1", "T2") is True
    upd = sc["update_rows"].call_args.args[1][0]
    assert upd["_row_id"] == 888
    assert upd[hours_log.COL_STATUS] == hours_log.STATUS_SUPERSEDED
    assert upd[hours_log.COL_SUPERSEDED_BY] == "T2"


def test_supersede_false_when_prior_missing(sc):
    sc["get_rows"].return_value = []
    assert hours_log.supersede_entry_row(9001, "T1", "T2") is False
    sc["update_rows"].assert_not_called()


# ---- check_row_cap (§51 A5 row-cap watchdog, SoR-safe WARN-only) ----------


def test_row_cap_noop_under_threshold(sc):
    hours_log.check_row_cap(9001, "Job One — Hours Log", row_count=100)  # < 15000
    sc["review"].assert_not_called()
    assert not any(
        c.kwargs.get("error_code") == "hours_log_row_cap_warn" for c in sc["log"].call_args_list
    )


def test_row_cap_warns_and_enqueues_over_threshold(sc):
    hours_log.check_row_cap(9001, "Job One — Hours Log", row_count=15000)  # >= threshold
    sc["review"].assert_called_once()
    assert sc["review"].call_args.kwargs["workstream"] == "progress_reports"
    assert any(
        c.kwargs.get("error_code") == "hours_log_row_cap_warn" for c in sc["log"].call_args_list
    )
    # NEVER deletes — no update/delete of rows on the cap path
    sc["update_rows"].assert_not_called()


def test_row_cap_counts_via_get_rows_when_count_none(sc):
    sc["get_rows"].return_value = [{"_row_id": i} for i in range(15001)]  # over threshold
    hours_log.check_row_cap(9001, "Job One — Hours Log")  # no row_count → counts get_rows
    sc["review"].assert_called_once()


def test_row_cap_check_never_raises_on_read_failure(sc):
    sc["get_setting"].side_effect = RuntimeError("smartsheet down")
    # advisory: a check failure is swallowed to a WARN, never propagates to block the mirror
    hours_log.check_row_cap(9001, "Job One — Hours Log", row_count=99999)
    assert any(
        c.kwargs.get("error_code") == "hours_log_row_cap_check_failed"
        for c in sc["log"].call_args_list
    )
