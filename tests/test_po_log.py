"""Tests for po_materials/po_log.py — the operator ledger mirror. Smartsheet mocked.

Run with: pytest -q tests/test_po_log.py
"""
from __future__ import annotations

import pytest

from po_materials import po_log


def test_notes_round_trip_d1_id() -> None:
    notes = po_log.notes_for_filed_row(42)
    assert notes == "d1_id=42"
    assert po_log.row_d1_id({po_log.COL_NOTES: notes}) == 42
    assert po_log.row_d1_id({po_log.COL_NOTES: po_log.notes_for_filed_row(7, extra="hand note")}) == 7
    # The reviewer may append prose; the parser tolerates it.
    assert po_log.row_d1_id({po_log.COL_NOTES: "d1_id=9; superseded manually"}) == 9
    assert po_log.row_d1_id({po_log.COL_NOTES: "keyed in by operator"}) is None
    assert po_log.row_d1_id({}) is None


def test_format_total_cents() -> None:
    assert po_log.format_total_cents(147_286) == "$1,472.86"
    assert po_log.format_total_cents(0) == "$0.00"
    assert po_log.format_total_cents(13_000_000) == "$130,000.00"
    assert po_log.format_total_cents(5) == "$0.05"


def test_append_filed_row_writes_pending_review(mocker) -> None:
    add = mocker.patch(
        "po_materials.po_log.smartsheet_client.add_rows", return_value=[111]
    )
    row_id = po_log.append_filed_row(
        po_number="2026.001.2.0.0",
        job_project="2026.001 — Sunrise Solar",
        job_id="JOB-000017",
        vendor_name="Chint Power Systems",
        vendor_key="VEN-000001",
        total_cents=147_286,
        pdf_link="https://app.box.com/file/1",
        supersedes_display="",
        terms_profile="standard_17",
        created_by="admin.alex",
        created_at_iso="2026-07-09",
        notes=po_log.notes_for_filed_row(7),
    )
    assert row_id == 111
    (sheet_id, rows), _ = add.call_args
    assert sheet_id == po_log.SHEET_ID
    [cells] = rows
    assert cells[po_log.COL_STATUS] == po_log.STATUS_PENDING_REVIEW
    assert cells[po_log.COL_TOTAL] == "$1,472.86"
    assert cells[po_log.COL_NOTES] == "d1_id=7"


def test_stamp_status_rejects_illegal_value(mocker) -> None:
    find = mocker.patch("po_materials.po_log.find_row_by_po_number")
    with pytest.raises(ValueError):
        po_log.stamp_status("2026.001.2.0.0", "queued")  # not a ledger status
    find.assert_not_called()


def test_stamp_status_noop_when_settled(mocker) -> None:
    """An already-at-target row writes NOTHING — the per-cycle status pass must not
    re-write settled rows every 90s."""
    mocker.patch(
        "po_materials.po_log.find_row_by_po_number",
        return_value={"_row_id": 5, po_log.COL_STATUS: "sent",
                      po_log.COL_SENT_AT: "2026-07-09"},
    )
    update = mocker.patch("po_materials.po_log.smartsheet_client.update_rows")
    assert po_log.stamp_status("2026.001.2.0.0", "sent", sent_at_iso="2026-07-09") is False
    update.assert_not_called()


def test_stamp_status_writes_transition_and_sent_at(mocker) -> None:
    mocker.patch(
        "po_materials.po_log.find_row_by_po_number",
        return_value={"_row_id": 5, po_log.COL_STATUS: "approved", po_log.COL_SENT_AT: None},
    )
    update = mocker.patch("po_materials.po_log.smartsheet_client.update_rows")
    assert po_log.stamp_status("2026.001.2.0.0", "sent", sent_at_iso="2026-07-10") is True
    (sheet_id, [payload]), _ = update.call_args
    assert sheet_id == po_log.SHEET_ID
    assert payload["_row_id"] == 5
    assert payload[po_log.COL_STATUS] == "sent"
    assert payload[po_log.COL_SENT_AT] == "2026-07-10"


def test_stamp_superseded_records_successor(mocker) -> None:
    mocker.patch(
        "po_materials.po_log.find_row_by_po_number",
        return_value={"_row_id": 8, po_log.COL_STATUS: "sent",
                      po_log.COL_SUPERSEDED_BY: None},
    )
    update = mocker.patch("po_materials.po_log.smartsheet_client.update_rows")
    assert po_log.stamp_status(
        "2026.001.2.0.0", "superseded", superseded_by="2026.001.2.1.0"
    ) is True
    (_, [payload]), _ = update.call_args
    assert payload[po_log.COL_STATUS] == "superseded"
    assert payload[po_log.COL_SUPERSEDED_BY] == "2026.001.2.1.0"


def test_stamp_status_missing_row_returns_false(mocker) -> None:
    mocker.patch("po_materials.po_log.find_row_by_po_number", return_value=None)
    update = mocker.patch("po_materials.po_log.smartsheet_client.update_rows")
    assert po_log.stamp_status("2026.001.2.0.0", "approved") is False
    update.assert_not_called()


def test_find_po_number_by_d1_id_scans_notes(mocker) -> None:
    rows = [
        {"_row_id": 1, po_log.COL_PO_NUMBER: "2026.001.2.0.0", po_log.COL_NOTES: "d1_id=7"},
        {"_row_id": 2, po_log.COL_PO_NUMBER: "2026.001.2.1.0", po_log.COL_NOTES: "d1_id=9"},
        {"_row_id": 3, po_log.COL_PO_NUMBER: "2019.111.1.0.0", po_log.COL_NOTES: "hand-issued"},
    ]
    mocker.patch("po_materials.po_log.smartsheet_client.get_rows", return_value=rows)
    assert po_log.find_po_number_by_d1_id(9) == "2026.001.2.1.0"
    assert po_log.find_po_number_by_d1_id(404) is None
