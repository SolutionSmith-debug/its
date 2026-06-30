"""Unit tests for shared.active_jobs_writer (the portal-as-writer up-sync write twin).

Mocks `shared.smartsheet_client` wholesale (get_rows / add_rows / update_rows / get_row) —
no live Smartsheet. Covers: find-or-create BOTH branches, CC explosion into CC 1..5 (incl.
lossless >5 overflow), lifecycle→Active picklist mapping, the Slice-6 portal-owned Job-ID
(written = job_id, no read-back), the non-clobber invariant, and the progress-config block.
"""
from __future__ import annotations

from typing import Any

import pytest

from shared import active_jobs_writer


def _job(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "job_id": "JOB-000017",
        "project_name": "Acme Solar 01",
        "lifecycle": "active",
        "address": "1 Main St",
        "stakeholder_name": "Sam Stakeholder",
        "stakeholder_email": "sam@acme.example",
        "stakeholder_phone": "5551234",
        "safety_contact_name": "Pat Safety",
        "safety_contact_email": "pat@acme.example",
        "safety_cc": ["a@x.com", "b@x.com"],
        "progress_contact_name": "Riley Progress",
        "progress_contact_email": "riley@acme.example",
        "progress_cc": ["c@x.com"],
        "mirror_version": 1,
    }
    base.update(over)
    return base


# The full portal-owned column set this module is allowed to write (no Notes / system cols).
_PORTAL_COLUMNS = {
    "Job ID", "Project Name", "Address", "Stakeholder Name", "Stakeholder Email", "Stakeholder Phone",
    "Active", "Portal Job Key", "CC 1", "CC 2", "CC 3", "CC 4", "CC 5",
}
_SAFETY_CONTACT_COLUMNS = {"Safety Reports Contact Name", "Safety Reports Contact Email"}


@pytest.fixture
def ss(mocker):
    return {
        "get_rows": mocker.patch("shared.smartsheet_client.get_rows", return_value=[]),
        "add_rows": mocker.patch("shared.smartsheet_client.add_rows", return_value=[999]),
        "update_rows": mocker.patch("shared.smartsheet_client.update_rows", return_value=None),
        "get_row": mocker.patch(
            "shared.smartsheet_client.get_row",
            return_value={"_row_id": 999, "Job ID": "JOB-NEW"},
        ),
    }


def _added_cells(ss) -> dict[str, Any]:
    """The cells dict passed to the add_rows CREATE call."""
    _sheet_id, rows = ss["add_rows"].call_args.args
    return rows[0]


# ---- find-or-create: CREATE branch ---------------------------------------


def test_create_branch_finds_then_adds_and_writes_job_id(ss):
    row_id, canonical = active_jobs_writer.upsert_job(
        active_jobs_writer.SAFETY_WRITE_CONFIG, _job()
    )
    # find-or-create keyed on Portal Job Key == job_id
    assert ss["get_rows"].call_args.kwargs["filters"] == {"Portal Job Key": "JOB-000017"}
    ss["add_rows"].assert_called_once()
    ss["update_rows"].assert_not_called()
    cells = _added_cells(ss)
    assert cells["Project Name"] == "Acme Solar 01"
    assert cells["Address"] == "1 Main St"
    assert cells["Portal Job Key"] == "JOB-000017"
    assert cells["Safety Reports Contact Email"] == "pat@acme.example"
    assert cells["Safety Reports Contact Name"] == "Pat Safety"
    # Slice 6: the portal owns the number → Job ID is WRITTEN (== job_id), NOT auto-numbered, and
    # canonical is returned directly with NO read-back round-trip.
    assert cells["Job ID"] == "JOB-000017"
    ss["get_row"].assert_not_called()
    assert (row_id, canonical) == (999, "JOB-000017")


def test_create_only_writes_portal_owned_columns(ss):
    active_jobs_writer.upsert_job(active_jobs_writer.SAFETY_WRITE_CONFIG, _job())
    keys = set(_added_cells(ss).keys())
    assert keys == _PORTAL_COLUMNS | _SAFETY_CONTACT_COLUMNS
    assert "Notes" not in keys  # never clobber the operator's column
    assert "Job ID" in keys  # Slice 6: portal-owned, WRITTEN = job_id (no AUTO_NUMBER)


# ---- find-or-create: UPDATE branch (non-clobber) -------------------------


def test_update_branch_updates_existing_row_without_clobbering(ss):
    ss["get_rows"].return_value = [{
        "_row_id": 42,
        "Job ID": "JOB-000099",            # a drifted Job ID — Slice 6 self-heals it to == job_id
        "Notes": "operator-owned note",     # must survive untouched
        "Project Name": "stale name",
    }]
    row_id, canonical = active_jobs_writer.upsert_job(
        active_jobs_writer.SAFETY_WRITE_CONFIG, _job()
    )
    ss["update_rows"].assert_called_once()
    ss["add_rows"].assert_not_called()
    ss["get_row"].assert_not_called()  # canonical == job_id, no extra read
    _sheet_id, updates = ss["update_rows"].call_args.args
    payload = updates[0]
    assert payload["_row_id"] == 42
    # Non-clobber: only _row_id + the portal-owned columns are present (Job ID is now portal-owned).
    assert set(payload.keys()) == {"_row_id"} | _PORTAL_COLUMNS | _SAFETY_CONTACT_COLUMNS
    assert "Notes" not in payload
    assert payload["Project Name"] == "Acme Solar 01"  # portal value overwrites the stale one
    assert payload["Job ID"] == "JOB-000017"           # self-heal: drifted Job ID → job_id
    assert (row_id, canonical) == (42, "JOB-000017")   # canonical == job_id, not the drifted value


# ---- CC explosion --------------------------------------------------------


def test_cc_explodes_into_five_slots_with_lossless_overflow(ss):
    job = _job(safety_cc=["a@x.com", "b@x.com", "c@x.com", "d@x.com", "e@x.com", "f@x.com"])
    active_jobs_writer.upsert_job(active_jobs_writer.SAFETY_WRITE_CONFIG, job)
    cells = _added_cells(ss)
    assert cells["CC 1"] == "a@x.com"
    assert cells["CC 4"] == "d@x.com"
    # 5th+ packed comma-joined into the last slot (active_jobs re-splits on comma) — lossless.
    assert cells["CC 5"] == "e@x.com, f@x.com"


def test_cc_pads_blank_slots(ss):
    active_jobs_writer.upsert_job(
        active_jobs_writer.SAFETY_WRITE_CONFIG, _job(safety_cc=["a@x.com"])
    )
    cells = _added_cells(ss)
    assert cells["CC 1"] == "a@x.com"
    assert cells["CC 2"] == "" and cells["CC 5"] == ""


def test_cc_empty_when_absent(ss):
    active_jobs_writer.upsert_job(
        active_jobs_writer.SAFETY_WRITE_CONFIG, _job(safety_cc=[])
    )
    cells = _added_cells(ss)
    assert all(cells[f"CC {i}"] == "" for i in range(1, 6))


# ---- lifecycle → Active picklist -----------------------------------------


@pytest.mark.parametrize(
    "lifecycle,expected",
    [
        ("active", "Active"),
        ("inactive", "Inactive"),
        ("archived", "Archived"),
        ("ARCHIVED", "Archived"),   # case-insensitive
        ("bogus", "bogus"),         # unknown passes through verbatim → registry rejects at write
    ],
)
def test_lifecycle_maps_to_active_picklist(ss, lifecycle, expected):
    active_jobs_writer.upsert_job(
        active_jobs_writer.SAFETY_WRITE_CONFIG, _job(lifecycle=lifecycle)
    )
    assert _added_cells(ss)["Active"] == expected


# ---- progress config reads the progress payload block --------------------


def test_progress_config_writes_progress_contact_block(ss):
    active_jobs_writer.upsert_job(active_jobs_writer.PROGRESS_WRITE_CONFIG, _job())
    cells = _added_cells(ss)
    # Wrote to the PROGRESS sheet's contact columns, sourced from the progress payload keys.
    assert cells["Progress Reports Contact Email"] == "riley@acme.example"
    assert cells["Progress Reports Contact Name"] == "Riley Progress"
    assert cells["CC 1"] == "c@x.com"
    # The safety block never leaks onto the progress sheet (mix-up prevention).
    assert "Safety Reports Contact Email" not in cells
    assert "Safety Reports Contact Name" not in cells
    # Routed to the progress sheet, not the safety one.
    assert ss["add_rows"].call_args.args[0] == active_jobs_writer.PROGRESS_WRITE_CONFIG.sheet_id


# ---- guards --------------------------------------------------------------


def test_missing_job_id_raises_value_error(ss):
    with pytest.raises(ValueError):
        active_jobs_writer.upsert_job(active_jobs_writer.SAFETY_WRITE_CONFIG, _job(job_id=""))
    ss["add_rows"].assert_not_called()
    ss["update_rows"].assert_not_called()
