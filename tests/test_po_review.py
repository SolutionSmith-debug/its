"""Tests for po_materials/po_review.py — the WSR-schema-twin PO review module.

Pins the two S1/S5 contracts: (a) the module STRUCTURALLY satisfies
`safety_reports.weekly_send._ReviewModule` (S5 binds it as the PO SendConfig's
review module without engine surgery), and (b) the writer maps the PO semantics
into the protocol slots (Vendor Key → "Job ID", PO date → "Week Of", PO PDF →
"Compiled PDF") with Workstream='po_materials'. Smartsheet mocked.

Run with: pytest -q tests/test_po_review.py
"""
from __future__ import annotations

from datetime import date

from po_materials import po_review
from safety_reports import wsr_review

# The _ReviewModule protocol surface (safety_reports/weekly_send.py) — attribute
# names copied from the Protocol so a drift on either side reds this pin.
_REVIEW_MODULE_ATTRS = [
    "SHEET_ID",
    "COL_JOB_PROJECT",
    "COL_JOB_ID",
    "COL_WEEK_OF",
    "COL_COMPILED_PDF",
    "COL_EMAIL_BODY",
    "COL_SEND_STATUS",
    "COL_SENT_AT",
    "COL_NOTES",
    "COL_WORKSTREAM",
    "STATUS_PENDING",
    "STATUS_SENT",
    "STATUS_FAILED",
    "STATUS_HELD",
    "STATUS_SENDING",
    "to_wsr_datetime",
]


def test_structurally_satisfies_review_module_protocol() -> None:
    for attr in _REVIEW_MODULE_ATTRS:
        assert hasattr(po_review, attr), f"po_review missing _ReviewModule attr {attr!r}"
    assert callable(po_review.to_wsr_datetime)


def test_reexports_are_the_wsr_constants() -> None:
    """The twin re-exports (never re-declares) the shared schema — a drift between
    the two would silently break the S5 engine bind (§14)."""
    assert po_review.COL_JOB_ID is wsr_review.COL_JOB_ID
    assert po_review.COL_WEEK_OF is wsr_review.COL_WEEK_OF
    assert po_review.COL_COMPILED_PDF is wsr_review.COL_COMPILED_PDF
    assert po_review.STATUS_PENDING is wsr_review.STATUS_PENDING
    assert po_review.to_wsr_datetime is wsr_review.to_wsr_datetime
    assert po_review.WORKSTREAM_TAG == "po_materials"


def test_add_po_review_row_maps_protocol_slots(mocker) -> None:
    add = mocker.patch(
        "safety_reports.wsr_review.smartsheet_client.add_rows", return_value=[321]
    )
    row_id = po_review.add_po_review_row(
        job_project="2026.001 — Sunrise Solar",
        vendor_key="VEN-000001",
        po_date=date(2026, 7, 9),
        pdf_link="https://app.box.com/file/1",
        recipient_to="orders@chint.example",
        cc_display="tealap@evergreenrenewables.com",
        email_body="body",
        notes=po_review.notes_for_review_row(7, "2026.001.2.0.0"),
    )
    assert row_id == 321
    (sheet_id, [cells]), _ = add.call_args
    assert sheet_id == po_review.SHEET_ID
    # The three PO-semantics protocol slots.
    assert cells[po_review.COL_JOB_ID] == "VEN-000001"
    assert cells[po_review.COL_WEEK_OF] == "2026-07-09"
    assert cells[po_review.COL_COMPILED_PDF] == "https://app.box.com/file/1"
    # Seeded state.
    assert cells[po_review.COL_SEND_STATUS] == po_review.STATUS_PENDING
    assert cells[po_review.COL_WORKSTREAM] == "po_materials"
    assert cells[po_review.COL_NOTES] == "po_id=7; po_number=2026.001.2.0.0"


def test_notes_round_trip_po_id_and_supersedes() -> None:
    notes = po_review.notes_for_review_row(7, "2026.001.2.1.0", supersedes_po_id=5)
    row = {po_review.COL_NOTES: notes}
    assert po_review.row_po_id(row) == 7
    assert po_review.row_supersedes_po_id(row) == 5
    plain = {po_review.COL_NOTES: po_review.notes_for_review_row(9, "2026.001.2.0.0")}
    assert po_review.row_po_id(plain) == 9
    assert po_review.row_supersedes_po_id(plain) is None
    assert po_review.row_po_id({po_review.COL_NOTES: "reviewer prose only"}) is None


def test_find_row_by_po_id_scans_notes(mocker) -> None:
    rows = [
        {"_row_id": 1, po_review.COL_NOTES: "po_id=7; po_number=2026.001.2.0.0"},
        {"_row_id": 2, po_review.COL_NOTES: "po_id=9; po_number=2026.001.2.1.0"},
        {"_row_id": 3, po_review.COL_NOTES: ""},
    ]
    mocker.patch(
        "po_materials.po_review.smartsheet_client.get_rows", return_value=rows
    )
    found = po_review.find_row_by_po_id(9)
    assert found is not None and found["_row_id"] == 2
    assert po_review.find_row_by_po_id(404) is None


def test_email_body_template_mentions_po_and_project() -> None:
    body = po_review.po_email_body_template(
        contact_name="Jordan",
        po_number="2026.001.2.0.0",
        job_name="Sunrise Solar",
        purchaser_entity="Evergreen Renewables LLC",
    )
    assert "Jordan" in body
    assert "2026.001.2.0.0" in body
    assert "Sunrise Solar" in body
    assert body.rstrip().endswith("Evergreen Renewables LLC")
    # Blank contact name degrades to the team greeting, never "Hello  —".
    fallback = po_review.po_email_body_template(
        contact_name="  ", po_number="X", job_name="Y", purchaser_entity="Z"
    )
    assert "Hello team" in fallback
