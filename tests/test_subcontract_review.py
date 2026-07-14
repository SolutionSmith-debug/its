"""Tests for subcontracts/subcontract_review.py — the WSR-schema-twin subcontract review module.

Pins the two S1/S4 contracts: (a) the module STRUCTURALLY satisfies
`safety_reports.weekly_send._ReviewModule` (S4 binds it as the subcontract SendConfig's
review module without engine surgery), and (b) the writer maps the subcontract semantics
into the protocol slots (Sub Key → "Job ID", agreement date → "Week Of", Subcontract.docx
Box link → "Compiled PDF") with Workstream='subcontracts'. Smartsheet mocked.

Run with: pytest -q tests/test_subcontract_review.py
"""
from __future__ import annotations

from datetime import date

from safety_reports import wsr_review
from subcontracts import subcontract_review

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
        assert hasattr(subcontract_review, attr), (
            f"subcontract_review missing _ReviewModule attr {attr!r}"
        )
    assert callable(subcontract_review.to_wsr_datetime)


def test_reexports_are_the_wsr_constants() -> None:
    """The twin re-exports (never re-declares) the shared schema — a drift between
    the two would silently break the S4 engine bind (§14)."""
    assert subcontract_review.COL_JOB_ID is wsr_review.COL_JOB_ID
    assert subcontract_review.COL_WEEK_OF is wsr_review.COL_WEEK_OF
    assert subcontract_review.COL_COMPILED_PDF is wsr_review.COL_COMPILED_PDF
    assert subcontract_review.STATUS_PENDING is wsr_review.STATUS_PENDING
    assert subcontract_review.to_wsr_datetime is wsr_review.to_wsr_datetime
    assert subcontract_review.WORKSTREAM_TAG == "subcontracts"


def test_add_sc_review_row_maps_protocol_slots(mocker) -> None:
    add = mocker.patch(
        "safety_reports.wsr_review.smartsheet_client.add_rows", return_value=[321]
    )
    row_id = subcontract_review.add_sc_review_row(
        job_project="2025.364 — Sunrise Solar",
        sub_key="SUB-000001",
        agreement_date=date(2025, 7, 11),
        package_link="https://app.box.com/file/1",
        recipient_to="ops@acme.example",
        cc_display="ap-lead@example.com",
        email_body="body",
        notes=subcontract_review.notes_for_review_row(7, "2025.364.1.1.2"),
    )
    assert row_id == 321
    (sheet_id, [cells]), _ = add.call_args
    assert sheet_id == subcontract_review.SHEET_ID
    # The three subcontract-semantics protocol slots.
    assert cells[subcontract_review.COL_JOB_ID] == "SUB-000001"
    assert cells[subcontract_review.COL_WEEK_OF] == "2025-07-11"
    assert cells[subcontract_review.COL_COMPILED_PDF] == "https://app.box.com/file/1"
    # Seeded state.
    assert cells[subcontract_review.COL_SEND_STATUS] == subcontract_review.STATUS_PENDING
    assert cells[subcontract_review.COL_WORKSTREAM] == "subcontracts"
    assert cells[subcontract_review.COL_NOTES] == "sc_id=7; sc_number=2025.364.1.1.2"


def test_notes_round_trip_sc_id_and_supersedes() -> None:
    notes = subcontract_review.notes_for_review_row(
        7, "2025.358.1.2.11", supersedes_sc_id=5
    )
    row = {subcontract_review.COL_NOTES: notes}
    assert subcontract_review.row_sc_id(row) == 7
    assert subcontract_review.row_supersedes_sc_id(row) == 5
    plain = {subcontract_review.COL_NOTES: subcontract_review.notes_for_review_row(9, "2025.364.1.1.2")}
    assert subcontract_review.row_sc_id(plain) == 9
    assert subcontract_review.row_supersedes_sc_id(plain) is None
    assert subcontract_review.row_sc_id({subcontract_review.COL_NOTES: "reviewer prose only"}) is None


def test_row_sc_number_extracts_the_contractual_number() -> None:
    # S4: the subcontract send envelope reads sc_number from Notes (no dedicated column — WSR twin).
    notes = subcontract_review.notes_for_review_row(7, "2025.364.1.1.2", supersedes_sc_id=5)
    assert subcontract_review.row_sc_number({subcontract_review.COL_NOTES: notes}) == "2025.364.1.1.2"
    # A supersede tag after sc_number must not bleed into the extracted value.
    assert (
        subcontract_review.row_sc_number(
            {subcontract_review.COL_NOTES: "sc_id=7; sc_number=2025.358.1.2.11; supersedes_sc_id=5"}
        )
        == "2025.358.1.2.11"
    )
    # A row that lost the tag → None (subcontract_send REFUSES to send a numberless subcontract).
    assert subcontract_review.row_sc_number({subcontract_review.COL_NOTES: "sc_id=7"}) is None
    assert subcontract_review.row_sc_number({subcontract_review.COL_NOTES: "reviewer prose only"}) is None


def test_find_row_by_sc_id_scans_notes(mocker) -> None:
    rows = [
        {"_row_id": 1, subcontract_review.COL_NOTES: "sc_id=7; sc_number=2025.364.1.1.2"},
        {"_row_id": 2, subcontract_review.COL_NOTES: "sc_id=9; sc_number=2025.358.1.2.11"},
        {"_row_id": 3, subcontract_review.COL_NOTES: ""},
    ]
    mocker.patch(
        "subcontracts.subcontract_review.smartsheet_client.get_rows", return_value=rows
    )
    found = subcontract_review.find_row_by_sc_id(9)
    assert found is not None and found["_row_id"] == 2
    assert subcontract_review.find_row_by_sc_id(404) is None


def test_email_body_template_mentions_subcontract_and_project() -> None:
    body = subcontract_review.sc_email_body_template(
        contact_name="Jordan",
        sc_number="2025.364.1.1.2",
        job_name="Sunrise Solar",
        contractor_entity="Evergreen Renewables LLC",
    )
    assert "Jordan" in body
    assert "2025.364.1.1.2" in body
    assert "Sunrise Solar" in body
    assert body.rstrip().endswith("Evergreen Renewables LLC")
    # Blank contact name degrades to the team greeting, never "Hello  —".
    fallback = subcontract_review.sc_email_body_template(
        contact_name="  ", sc_number="X", job_name="Y", contractor_entity="Z"
    )
    assert "Hello team" in fallback
