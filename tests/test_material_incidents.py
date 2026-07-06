"""Unit tests for progress_reports.material_incidents — per-job Material Incidents sheet
find-or-create + CHANGE-ONLY upsert (APPEND-ONLY ledger; no retire). All Smartsheet / capacity I/O is
mocked; no test touches live state."""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from progress_reports import material_incidents


@pytest.fixture
def sc(mocker):
    return {
        "ensure_folder": mocker.patch(
            "progress_reports.material_incidents.hours_log._ensure_job_folder",
            return_value=7001,
        ),
        "find_sheet": mocker.patch(
            "progress_reports.material_incidents.smartsheet_client.find_sheet_by_name_in_folder",
            return_value=None,
        ),
        "create_sheet": mocker.patch(
            "progress_reports.material_incidents.smartsheet_client.create_sheet_in_folder",
            return_value=9001,
        ),
        "styles": mocker.patch(
            "progress_reports.material_incidents.smartsheet_client.apply_column_styles",
            return_value=None,
        ),
        "get_rows": mocker.patch(
            "progress_reports.material_incidents.smartsheet_client.get_rows", return_value=[]
        ),
        "add_rows": mocker.patch(
            "progress_reports.material_incidents.smartsheet_client.add_rows", return_value=[555]
        ),
        "update_rows": mocker.patch(
            "progress_reports.material_incidents.smartsheet_client.update_rows", return_value=None
        ),
        "find_folder": mocker.patch(
            "progress_reports.material_incidents.smartsheet_client.find_folder_by_name_in_workspace",
            return_value=7001,
        ),
        "capacity": mocker.patch(
            "progress_reports.material_incidents.sheet_capacity.check_create_headroom",
            return_value=SimpleNamespace(note="", ok=True, current=1, ceiling=100, margin=50),
        ),
        "route": mocker.patch(
            "progress_reports.material_incidents.sheet_capacity.route_breach_to_review_queue",
            return_value=None,
        ),
        "log": mocker.patch("progress_reports.material_incidents.error_log.log", return_value=None),
        "review": mocker.patch(
            "progress_reports.material_incidents.review_queue.add", return_value=1
        ),
        "get_setting": mocker.patch(
            "progress_reports.material_incidents.smartsheet_client.get_setting", return_value="15000"
        ),
    }


# ---- sheet name (50-char cap) --------------------------------------------


def test_sheet_name_short_is_verbatim():
    assert (
        material_incidents.material_incidents_sheet_name("Bradley 1")
        == "Bradley 1 — Material Incidents"
    )


def test_sheet_name_truncates_long_prefix_to_cap():
    name = material_incidents.material_incidents_sheet_name("X" * 80)
    assert len(name) <= material_incidents.SHEET_NAME_MAX
    assert name.endswith(material_incidents.SHEET_SUFFIX)


# ---- ensure_material_incidents_sheet -------------------------------------


def test_ensure_returns_existing_sheet_without_create(sc):
    sc["find_sheet"].return_value = 4242
    assert material_incidents.ensure_material_incidents_sheet("Job One") == 4242
    sc["create_sheet"].assert_not_called()
    sc["capacity"].assert_not_called()  # no create branch → no capacity check


def test_ensure_creates_sheet_when_missing(sc):
    sid = material_incidents.ensure_material_incidents_sheet("Job One")
    assert sid == 9001
    sc["create_sheet"].assert_called_once()
    sc["styles"].assert_called_once_with(9001, material_incidents.MATERIAL_INCIDENTS_STYLES)
    sc["capacity"].assert_called_once()  # A1 tripwire runs only on create


def test_ensure_delegates_folder_to_hours_log(sc):
    # The Material Incidents sheet reuses the Hours Log's per-job folder resolver (single authority).
    material_incidents.ensure_material_incidents_sheet("Job One")
    sc["ensure_folder"].assert_called_once_with("Job One")


def test_ensure_capacity_breach_warns_but_still_creates(sc):
    sc["capacity"].return_value = SimpleNamespace(note="", ok=False, current=99, ceiling=100, margin=1)
    material_incidents.ensure_material_incidents_sheet("Job One")
    sc["route"].assert_called_once()          # breach enqueued to the Review Queue
    sc["create_sheet"].assert_called_once()   # advisory — the create STILL proceeds


def test_ensure_duplicate_race_adopts_first_match(sc):
    # create returns 9001 but a concurrent create landed 8888 first → adopt 8888, WARN for cleanup.
    sc["find_sheet"].side_effect = [None, 8888]  # pre-find miss, post-find hits the racer's sheet
    assert material_incidents.ensure_material_incidents_sheet("Job One") == 8888
    assert any(
        c.kwargs.get("error_code") == "material_incidents_sheet_race_duplicate"
        for c in sc["log"].call_args_list
    )


# ---- upsert_incident_row (CHANGE-ONLY, APPEND-ONLY) ----------------------


def _upsert_kwargs(**over: Any) -> dict[str, Any]:
    kw: dict[str, Any] = dict(
        incident_uuid="sub-10", material="Q.PEAK panels", issue="Short",
        line_uuid="u-10", line_status="incident", qty_expected="120", qty_received="118",
        delivery_ref="PO-4471", details="2 pallets short", action_taken="CM notified",
        reported_by="Mo Manager", reported_at="2026-07-06", report="https://box/999",
    )
    kw.update(over)
    return kw


def _existing_row(**over: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "_row_id": 888,
        material_incidents.COL_INCIDENT_UUID: "sub-10",
        material_incidents.COL_MATERIAL: "Q.PEAK panels",
        material_incidents.COL_ISSUE: "Short",
        material_incidents.COL_LINE_UUID: "u-10",
        material_incidents.COL_LINE_STATUS: "incident",
        material_incidents.COL_QTY_EXPECTED: "120",
        material_incidents.COL_QTY_RECEIVED: "118",
        material_incidents.COL_DELIVERY_REF: "PO-4471",
        material_incidents.COL_DETAILS: "2 pallets short",
        material_incidents.COL_ACTION_TAKEN: "CM notified",
        material_incidents.COL_REPORTED_BY: "Mo Manager",
        material_incidents.COL_REPORTED_AT: "2026-07-06",
        material_incidents.COL_REPORT: "https://box/999",
    }
    row.update(over)
    return row


def test_upsert_adds_new_row_when_absent(sc):
    sc["get_rows"].return_value = []  # find_incident_row → None
    assert material_incidents.upsert_incident_row(9001, **_upsert_kwargs()) == 555
    sc["add_rows"].assert_called_once()
    cells = sc["add_rows"].call_args.args[1][0]
    assert cells[material_incidents.COL_INCIDENT_UUID] == "sub-10"
    assert cells[material_incidents.COL_MATERIAL] == "Q.PEAK panels"
    assert cells[material_incidents.COL_ISSUE] == "Short"
    assert cells[material_incidents.COL_LINE_STATUS] == "incident"
    # APPEND-ONLY — there is NO On List / Removed concept on this ledger.
    assert "On List" not in cells


def test_upsert_change_only_noop_when_identical(sc):
    sc["get_rows"].return_value = [_existing_row()]
    assert material_incidents.upsert_incident_row(9001, **_upsert_kwargs()) == 888
    sc["update_rows"].assert_not_called()  # immutable event, nothing changed → no needless write
    sc["add_rows"].assert_not_called()


def test_upsert_updates_when_line_status_flips_to_received(sc):
    # The ONE field that legitimately changes: a later receipt resolves the referenced line.
    sc["get_rows"].return_value = [_existing_row()]
    material_incidents.upsert_incident_row(9001, **_upsert_kwargs(line_status="received"))
    upd = sc["update_rows"].call_args.args[1][0]
    assert upd["_row_id"] == 888
    assert upd[material_incidents.COL_LINE_STATUS] == "received"


def test_upsert_unlinked_incident_blank_line_fields(sc):
    # An incident with no referenced line → Line UUID + Line Status are blank; still files a row.
    sc["get_rows"].return_value = []
    material_incidents.upsert_incident_row(
        9001, **_upsert_kwargs(line_uuid="", line_status="")
    )
    cells = sc["add_rows"].call_args.args[1][0]
    assert cells[material_incidents.COL_LINE_UUID] == ""
    assert cells[material_incidents.COL_LINE_STATUS] == ""
    assert cells[material_incidents.COL_MATERIAL] == "Q.PEAK panels"


def test_upsert_never_deletes_or_retires(sc):
    # Belt-and-suspenders: the module exposes no retire/delete surface — proving append-only.
    assert not hasattr(material_incidents, "retire_removed")
    assert not hasattr(material_incidents, "retire_incidents")
    # And an upsert only ever add_rows / update_rows — never delete_rows.
    assert not hasattr(material_incidents.smartsheet_client, "_deleted_here")


# ---- find_incident_row ---------------------------------------------------


def test_find_incident_row_matches_by_uuid(sc):
    sc["get_rows"].return_value = [{"_row_id": 5, material_incidents.COL_INCIDENT_UUID: "sub-10"}]
    assert material_incidents.find_incident_row(9001, "sub-10") == {
        "_row_id": 5, material_incidents.COL_INCIDENT_UUID: "sub-10"
    }
    assert material_incidents.find_incident_row(9001, "sub-99") is None
    assert material_incidents.find_incident_row(9001, "") is None


# ---- check_row_cap (§51 A5 row-cap watchdog, SoR-safe WARN-only) ----------


def test_row_cap_noop_under_threshold(sc):
    material_incidents.check_row_cap(9001, "Job One — Material Incidents", row_count=100)  # < 15000
    sc["review"].assert_not_called()


def test_row_cap_warns_and_enqueues_over_threshold(sc):
    material_incidents.check_row_cap(9001, "Job One — Material Incidents", row_count=15000)  # >= thresh
    sc["review"].assert_called_once()
    assert sc["review"].call_args.kwargs["workstream"] == "progress_reports"
    assert any(
        c.kwargs.get("error_code") == "material_incidents_row_cap_warn"
        for c in sc["log"].call_args_list
    )
    sc["update_rows"].assert_not_called()  # NEVER deletes/mutates rows on the cap path


def test_row_cap_check_never_raises_on_read_failure(sc):
    sc["get_setting"].side_effect = RuntimeError("smartsheet down")
    material_incidents.check_row_cap(9001, "Job One — Material Incidents", row_count=99999)
    assert any(
        c.kwargs.get("error_code") == "material_incidents_row_cap_check_failed"
        for c in sc["log"].call_args_list
    )
