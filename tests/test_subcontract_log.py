"""Tests for subcontracts/subcontract_log.py append/find sheet-targeting. Smartsheet mocked.

The module's option-set parity + schema coverage lives in tests/test_subcontract_s1.py;
this file covers the `sheet_id` parameterization (Feature A — the per-job tracking
sheet mirror; the SAME builder writes the flat ledger AND the per-job clone).

Run with: pytest -q tests/test_subcontract_log.py
"""
from __future__ import annotations

from typing import Any

from subcontracts import subcontract_log


def _append_kwargs(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "sc_number": "2026.001.2.0.0",
        "job_project": "2026.001 — Sunrise Solar",
        "job_id": "JOB-000042",
        "subcontractor_name": "Bright Spark Electric",
        "sub_key": "SUB-000001",
        "total_cents": 1_000_000,
        "pdf_link": "https://app.box.com/file/1",
        "supersedes_display": "",
        "terms_profile": "standard_subcontract",
        "created_by": "admin.alex",
        "created_at_iso": "2026-07-13",
        "notes": subcontract_log.notes_for_filed_row(42),
    }
    base.update(over)
    return base


def test_append_filed_row_defaults_to_flat_log(mocker) -> None:
    add = mocker.patch(
        "subcontracts.subcontract_log.smartsheet_client.add_rows", return_value=[111]
    )
    row_id = subcontract_log.append_filed_row(**_append_kwargs())
    assert row_id == 111
    (sheet_id, rows), _ = add.call_args
    assert sheet_id == subcontract_log.SHEET_ID
    [cells] = rows
    assert cells[subcontract_log.COL_STATUS] == subcontract_log.STATUS_PENDING_REVIEW
    assert cells[subcontract_log.COL_NOTES] == "d1_id=42"


def test_append_filed_row_explicit_sheet_id_targets_that_sheet(mocker) -> None:
    """`sheet_id=` redirects the append to a per-job tracking sheet (Feature A);
    the cells are IDENTICAL to the flat-Log row (same builder, cloned schema)."""
    add = mocker.patch(
        "subcontracts.subcontract_log.smartsheet_client.add_rows", return_value=[222]
    )
    row_id = subcontract_log.append_filed_row(**_append_kwargs(), sheet_id=987_654)
    assert row_id == 222
    (sheet_id, rows), _ = add.call_args
    assert sheet_id == 987_654
    [cells] = rows
    assert cells[subcontract_log.COL_SC_NUMBER] == "2026.001.2.0.0"


def test_find_row_by_sc_number_targets_requested_sheet(mocker) -> None:
    """The idempotency guard runs against the TARGET sheet: default = the flat
    Subcontract_Log; explicit sheet_id = the per-job sheet (independent idempotency)."""
    get_rows = mocker.patch(
        "subcontracts.subcontract_log.smartsheet_client.get_rows", return_value=[]
    )
    assert subcontract_log.find_row_by_sc_number("2026.001.2.0.0") is None
    assert get_rows.call_args.args[0] == subcontract_log.SHEET_ID

    row = {"_row_id": 9, subcontract_log.COL_SC_NUMBER: "2026.001.2.0.0"}
    get_rows.return_value = [row]
    assert subcontract_log.find_row_by_sc_number("2026.001.2.0.0", sheet_id=987_654) == row
    assert get_rows.call_args.args[0] == 987_654
