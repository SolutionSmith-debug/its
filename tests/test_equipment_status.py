"""Unit tests for progress_reports.equipment_status — per-job Equipment sheet find-or-create +
CHANGE-ONLY upsert + retire-off-job (snapshot re-projection). All Smartsheet / capacity I/O is
mocked; no test touches live state."""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from progress_reports import equipment_status


@pytest.fixture
def sc(mocker):
    return {
        "ensure_folder": mocker.patch(
            "progress_reports.equipment_status.hours_log._ensure_job_folder",
            return_value=7001,
        ),
        "find_sheet": mocker.patch(
            "progress_reports.equipment_status.smartsheet_client.find_sheet_by_name_in_folder",
            return_value=None,
        ),
        "create_sheet": mocker.patch(
            "progress_reports.equipment_status.smartsheet_client.create_sheet_in_folder",
            return_value=9001,
        ),
        "styles": mocker.patch(
            "progress_reports.equipment_status.smartsheet_client.apply_column_styles",
            return_value=None,
        ),
        "get_rows": mocker.patch(
            "progress_reports.equipment_status.smartsheet_client.get_rows", return_value=[]
        ),
        "add_rows": mocker.patch(
            "progress_reports.equipment_status.smartsheet_client.add_rows", return_value=[555]
        ),
        "update_rows": mocker.patch(
            "progress_reports.equipment_status.smartsheet_client.update_rows", return_value=None
        ),
        "capacity": mocker.patch(
            "progress_reports.equipment_status.sheet_capacity.check_create_headroom",
            return_value=SimpleNamespace(note="", ok=True, current=1, ceiling=100, margin=50),
        ),
        "route": mocker.patch(
            "progress_reports.equipment_status.sheet_capacity.route_breach_to_review_queue",
            return_value=None,
        ),
        "log": mocker.patch("progress_reports.equipment_status.error_log.log", return_value=None),
        "review": mocker.patch(
            "progress_reports.equipment_status.review_queue.add", return_value=1
        ),
        "get_setting": mocker.patch(
            "progress_reports.equipment_status.smartsheet_client.get_setting", return_value="15000"
        ),
    }


# ---- sheet name (50-char cap) --------------------------------------------


def test_sheet_name_short_is_verbatim():
    assert equipment_status.equipment_sheet_name("Bradley 1") == "Bradley 1 — Equipment"


def test_sheet_name_truncates_long_prefix_to_cap():
    name = equipment_status.equipment_sheet_name("X" * 80)
    assert len(name) <= equipment_status.SHEET_NAME_MAX
    assert name.endswith(equipment_status.SHEET_SUFFIX)


# ---- ensure_equipment_sheet ----------------------------------------------


def test_ensure_returns_existing_sheet_without_create(sc):
    sc["find_sheet"].return_value = 4242
    assert equipment_status.ensure_equipment_sheet("Job One") == 4242
    sc["create_sheet"].assert_not_called()
    sc["capacity"].assert_not_called()  # no create branch → no capacity check


def test_ensure_creates_sheet_when_missing(sc):
    sid = equipment_status.ensure_equipment_sheet("Job One")
    assert sid == 9001
    sc["create_sheet"].assert_called_once()
    sc["styles"].assert_called_once_with(9001, equipment_status.EQUIPMENT_STYLES)
    sc["capacity"].assert_called_once()  # A1 tripwire runs only on create


def test_ensure_delegates_folder_to_hours_log(sc):
    # The Equipment sheet reuses the Hours Log's per-job folder resolver (single authority).
    equipment_status.ensure_equipment_sheet("Job One")
    sc["ensure_folder"].assert_called_once_with("Job One")


def test_ensure_capacity_breach_warns_but_still_creates(sc):
    sc["capacity"].return_value = SimpleNamespace(note="", ok=False, current=99, ceiling=100, margin=1)
    equipment_status.ensure_equipment_sheet("Job One")
    sc["route"].assert_called_once()          # breach enqueued to the Review Queue
    sc["create_sheet"].assert_called_once()   # advisory — the create STILL proceeds


# ---- upsert_equipment_row (CHANGE-ONLY) ----------------------------------


def _upsert_kwargs(**over: Any) -> dict[str, Any]:
    kw: dict[str, Any] = dict(
        equipment_id="10", name="Unit Alpha", kind="skid-steer", unit_no="SK-001",
        status="fmc", status_note="", status_changed="2026-06-27",
        location="North lot", lat="37.7749", lon="-122.4194",
        location_read_at="2026-06-27T10:00:00-07:00", updated_at="2026-06-27T15:00:00-07:00",
    )
    kw.update(over)
    return kw


def _existing_row(**over: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "_row_id": 888,
        equipment_status.COL_EQUIPMENT_ID: "10",
        equipment_status.COL_EQUIPMENT: "Unit Alpha",
        equipment_status.COL_KIND: "skid-steer",
        equipment_status.COL_UNIT_NO: "SK-001",
        equipment_status.COL_STATUS: "fmc",
        equipment_status.COL_STATUS_NOTE: "",
        equipment_status.COL_STATUS_CHANGED: "2026-06-27",
        equipment_status.COL_LOCATION: "North lot",
        equipment_status.COL_LAT: "37.7749",
        equipment_status.COL_LON: "-122.4194",
        equipment_status.COL_LOCATION_READ_AT: "2026-06-27T10:00:00-07:00",
        equipment_status.COL_ON_JOB: equipment_status.ON_JOB_ACTIVE,
    }
    row.update(over)
    return row


def test_upsert_adds_new_row_when_absent(sc):
    sc["get_rows"].return_value = []  # find_equipment_row → None
    assert equipment_status.upsert_equipment_row(9001, **_upsert_kwargs()) == 555
    sc["add_rows"].assert_called_once()
    cells = sc["add_rows"].call_args.args[1][0]
    assert cells[equipment_status.COL_EQUIPMENT_ID] == "10"
    assert cells[equipment_status.COL_EQUIPMENT] == "Unit Alpha"
    assert cells[equipment_status.COL_ON_JOB] == equipment_status.ON_JOB_ACTIVE


def test_upsert_change_only_noop_when_identical(sc):
    sc["get_rows"].return_value = [_existing_row()]
    assert equipment_status.upsert_equipment_row(9001, **_upsert_kwargs()) == 888
    sc["update_rows"].assert_not_called()  # nothing changed → no needless write (no Updated At churn)
    sc["add_rows"].assert_not_called()


def test_upsert_change_updates_when_a_value_differs(sc):
    sc["get_rows"].return_value = [_existing_row()]
    equipment_status.upsert_equipment_row(9001, **_upsert_kwargs(status="down"))
    upd = sc["update_rows"].call_args.args[1][0]
    assert upd["_row_id"] == 888
    assert upd[equipment_status.COL_STATUS] == "down"
    assert upd[equipment_status.COL_ON_JOB] == equipment_status.ON_JOB_ACTIVE
    assert upd[equipment_status.COL_UPDATED_AT] == "2026-06-27T15:00:00-07:00"


def test_upsert_reactivates_row_that_was_off_job(sc):
    # A reappearing item: identical data but its On Job cell is Off Job → the mismatch drives an
    # update that flips it back to Active.
    sc["get_rows"].return_value = [_existing_row(**{equipment_status.COL_ON_JOB: equipment_status.ON_JOB_OFF})]
    equipment_status.upsert_equipment_row(9001, **_upsert_kwargs())
    upd = sc["update_rows"].call_args.args[1][0]
    assert upd[equipment_status.COL_ON_JOB] == equipment_status.ON_JOB_ACTIVE


# ---- retire_off_job (never delete) ---------------------------------------


def test_retire_marks_rows_not_in_snapshot_off_job(sc):
    sc["get_rows"].return_value = [
        {"_row_id": 1, equipment_status.COL_EQUIPMENT_ID: "10", equipment_status.COL_ON_JOB: "Active"},
        {"_row_id": 2, equipment_status.COL_EQUIPMENT_ID: "11", equipment_status.COL_ON_JOB: "Active"},
    ]
    retired = equipment_status.retire_off_job(9001, {"10"})  # 11 left the job
    assert retired == 1
    upd = sc["update_rows"].call_args.args[1]
    assert len(upd) == 1
    assert upd[0]["_row_id"] == 2
    assert upd[0][equipment_status.COL_ON_JOB] == equipment_status.ON_JOB_OFF
    # NEVER deletes — it is an update, not a delete
    assert sc["add_rows"].call_count == 0


def test_retire_is_idempotent_skips_already_off_job(sc):
    sc["get_rows"].return_value = [
        {"_row_id": 2, equipment_status.COL_EQUIPMENT_ID: "11", equipment_status.COL_ON_JOB: "Off Job"},
    ]
    retired = equipment_status.retire_off_job(9001, {"10"})
    assert retired == 0
    sc["update_rows"].assert_not_called()  # already Off Job → no needless write


def test_retire_leaves_in_snapshot_rows_untouched(sc):
    sc["get_rows"].return_value = [
        {"_row_id": 1, equipment_status.COL_EQUIPMENT_ID: "10", equipment_status.COL_ON_JOB: "Active"},
    ]
    assert equipment_status.retire_off_job(9001, {"10", "11"}) == 0
    sc["update_rows"].assert_not_called()


# ---- find_equipment_row --------------------------------------------------


def test_find_equipment_row_matches_by_id(sc):
    sc["get_rows"].return_value = [{"_row_id": 5, equipment_status.COL_EQUIPMENT_ID: "10"}]
    assert equipment_status.find_equipment_row(9001, "10") == {
        "_row_id": 5, equipment_status.COL_EQUIPMENT_ID: "10"
    }
    assert equipment_status.find_equipment_row(9001, "99") is None
    assert equipment_status.find_equipment_row(9001, "") is None


# ---- check_row_cap (§51 A5 row-cap watchdog, SoR-safe WARN-only) ----------


def test_row_cap_noop_under_threshold(sc):
    equipment_status.check_row_cap(9001, "Job One — Equipment", row_count=100)  # < 15000
    sc["review"].assert_not_called()


def test_row_cap_warns_and_enqueues_over_threshold(sc):
    equipment_status.check_row_cap(9001, "Job One — Equipment", row_count=15000)  # >= threshold
    sc["review"].assert_called_once()
    assert sc["review"].call_args.kwargs["workstream"] == "progress_reports"
    assert any(
        c.kwargs.get("error_code") == "equipment_status_row_cap_warn"
        for c in sc["log"].call_args_list
    )
    sc["update_rows"].assert_not_called()  # NEVER deletes/mutates rows on the cap path


def test_row_cap_check_never_raises_on_read_failure(sc):
    sc["get_setting"].side_effect = RuntimeError("smartsheet down")
    equipment_status.check_row_cap(9001, "Job One — Equipment", row_count=99999)
    assert any(
        c.kwargs.get("error_code") == "equipment_status_row_cap_check_failed"
        for c in sc["log"].call_args_list
    )
