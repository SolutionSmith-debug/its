"""Tests for po_materials/rfq_send.py — the ADR-0004 R3 RFQ send instantiation.

rfq_send is a thin binding over the shared engine `safety_reports.weekly_send.send_one_row`;
the dispatch logic itself is covered by tests/test_weekly_send.py + test_send_engine_seam.py.
These tests pin the RFQ-specific binding and the cross-lane guards a config typo would trip:
recipients from ITS_Vendors (by Vendor Key riding the COL_JOB_ID protocol slot), the RFQ
subject/attachment carrying the rfq_number, the numberless-RFQ refusal, the DISTINCT
`Workstream=po_materials_rfq` contamination guard (bidirectional — neither po_send nor
rfq_send can dispatch the other's rows), and the R3 TWO-attachment envelope (RFQ PDF + the
fillable xlsx quote form) with its PDF-only degrade.

All external services mocked. Live coverage: the operator dark-ship smoke + e2e.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from po_materials import rfq_review, rfq_send, vendors
from safety_reports.weekly_send import EnvelopeContext

_PURCHASER = {
    "entity": "Evergreen Renewables LLC",
    "invoice_routing": {
        "to": "invoices@example.com",
        "cc": ["ap-lead@example.com", "finance@example.com"],
    },
}
_VENDOR = {
    vendors.COL_VENDOR_NAME: "Platt Electric Supply",
    vendors.COL_CONTACT_NAME: "Sam Seller",
    vendors.COL_CONTACT_EMAIL: "quotes@platt.example",
}

# The Notes seed carries the (rfq, vendor) join + the R4 form Box file id.
_NOTES = "rfq_id=5; rfq_number=RFQ-2026.001-001; vendor_key=VEN-000001; form_box_file_id=99887766"


def _row(**kw):
    base = {
        "_row_id": 90,
        rfq_review.COL_JOB_PROJECT: "2026.001 — Sunrise Solar",
        rfq_review.COL_JOB_ID: "VEN-000001",          # the Vendor Key rides this protocol slot
        rfq_review.COL_WEEK_OF: "2026-07-09",          # the RFQ date rides this slot
        rfq_review.COL_COMPILED_PDF: "https://app.box.com/file/88",
        rfq_review.COL_EMAIL_BODY: "Hello Sam — please quote the attached RFQ.",
        rfq_review.COL_SEND_STATUS: rfq_review.STATUS_PENDING,
        rfq_review.COL_NOTES: _NOTES,
        rfq_review.COL_WORKSTREAM: "po_materials_rfq",  # the DISTINCT lane tag → guard passes
        rfq_review.COL_RECIPIENT_TO: "STALE@display.example",
        rfq_review.COL_CC: "STALE-cc@display.example",
    }
    base.update(kw)
    return base


def _download(file_id: str) -> bytes:
    """Distinguish the primary RFQ PDF (Box file 88, from COL_COMPILED_PDF) from the
    R4 quote form (Box file 99887766, from the Notes) — both ride the SAME box_client."""
    return b"%PDF-rfq" if str(file_id) == "88" else b"PK\x03\x04xlsx-form"


@pytest.fixture
def stub(mocker) -> dict[str, MagicMock]:
    # rfq_send.send_one_row routes through weekly_send.send_one_row, so the engine's
    # collaborators are patched on weekly_send.*; the RFQ recipient/envelope/quote-form
    # collaborators (vendors, terms_lib) are patched on rfq_send.*. box_client is one module
    # shared by both the primary-PDF fetch (engine) and the form fetch (the R3 resolver).
    from safety_reports import weekly_send
    return {
        "get_row": mocker.patch.object(weekly_send.smartsheet_client, "get_row", return_value=_row()),
        "update_rows": mocker.patch.object(weekly_send.smartsheet_client, "update_rows"),
        "download": mocker.patch.object(weekly_send.box_client, "download_file", side_effect=_download),
        "send_mail": mocker.patch.object(weekly_send.graph_client, "send_mail"),
        "send_large": mocker.patch.object(weekly_send.graph_client, "send_mail_large_attachment"),
        "from_mailbox": mocker.patch.object(weekly_send, "_read_str_setting", return_value="procurement@evergreenmirror.com"),
        "log": mocker.patch.object(weekly_send.error_log, "log"),
        "recipient_health": mocker.patch.object(weekly_send.recipient_health, "report_unhealthy_recipient"),
        "get_vendor": mocker.patch.object(rfq_send.vendors, "get_vendor_by_key", return_value=dict(_VENDOR)),
        "purchaser": mocker.patch.object(rfq_send.terms_lib, "load_purchaser_config", return_value=_PURCHASER),
        # active_jobs must NOT be consulted — an RFQ resolves recipients from ITS_Vendors.
        "get_job": mocker.patch.object(
            weekly_send.active_jobs, "get_job",
            side_effect=AssertionError("active_jobs must not be consulted for an RFQ send"),
        ),
    }


# ---- binding correctness (the DISTINCT lane tag) -------------------------


def test_config_binds_the_distinct_rfq_lane_tag():
    cfg = rfq_send.CONFIG
    # The DISTINCT lane tag is what makes cross-lane dispatch impossible — NOT 'po_materials'.
    assert cfg.workstream_tag == "po_materials_rfq"
    assert cfg.workstream_tag == rfq_review.WORKSTREAM_TAG
    assert cfg.workstream_tag != "po_materials"
    assert cfg.config_workstream == "po_materials"
    assert cfg.from_mailbox_cfg_key == "po_materials.rfq_send.from_mailbox"
    assert cfg.from_mailbox_default == "procurement@evergreenmirror.com"
    assert cfg.review is rfq_review or cfg.review.__name__.endswith("rfq_review")
    assert isinstance(cfg.recipient_lookup, rfq_send._VendorRecipientLookup)
    assert isinstance(cfg.envelope_builder, rfq_send._RfqEnvelope)
    # The R3 sequence-attachment seam is bound (every OTHER SendConfig leaves it None).
    assert isinstance(cfg.extra_attachments, rfq_send._RfqQuoteFormAttachment)


# ---- recipient resolution (ITS_Vendors by Vendor Key; invoice-routing CC) ----


def test_vendor_lookup_resolves_email_and_invoice_cc(mocker):
    mocker.patch.object(rfq_send.vendors, "get_vendor_by_key", return_value=dict(_VENDOR))
    mocker.patch.object(rfq_send.terms_lib, "load_purchaser_config", return_value=_PURCHASER)
    to, cc = rfq_send._VendorRecipientLookup()("VEN-000001")
    assert to == "quotes@platt.example"
    assert list(cc) == ["ap-lead@example.com", "finance@example.com"]


def test_vendor_lookup_returns_none_for_unknown_key(mocker):
    mocker.patch.object(rfq_send.vendors, "get_vendor_by_key", return_value=None)
    assert rfq_send._VendorRecipientLookup()("VEN-999999") is None


def test_vendor_lookup_returns_none_for_blank_email(mocker):
    blank = dict(_VENDOR, **{vendors.COL_CONTACT_EMAIL: "   "})
    mocker.patch.object(rfq_send.vendors, "get_vendor_by_key", return_value=blank)
    assert rfq_send._VendorRecipientLookup()("VEN-000001") is None


# ---- envelope (subject + attachment carry the RFQ number) -------------------


def test_envelope_carries_rfq_number_and_entity(mocker):
    mocker.patch.object(rfq_send.terms_lib, "load_purchaser_config", return_value=_PURCHASER)
    ctx = EnvelopeContext(project_name="2026.001 — Sunrise Solar", week="2026-07-09", row=_row())
    subject, attachment = rfq_send._RfqEnvelope()(ctx)
    assert subject == "Request for Quote RFQ-2026.001-001 — 2026.001 — Sunrise Solar — Evergreen Renewables LLC"
    assert attachment.endswith(".pdf")
    assert "RFQ-2026.001-001" in attachment


def test_envelope_returns_none_for_a_numberless_rfq(mocker):
    mocker.patch.object(rfq_send.terms_lib, "load_purchaser_config", return_value=_PURCHASER)
    ctx = EnvelopeContext(project_name="Sunrise", week="2026-07-09", row=_row(**{rfq_review.COL_NOTES: "rfq_id=5"}))
    assert rfq_send._RfqEnvelope()(ctx) is None


# ---- the R3 quote-form extra-attachment resolver ----------------------------


def test_quote_form_resolver_returns_the_xlsx_triple(stub):
    ctx = EnvelopeContext(project_name="Sunrise", week="2026-07-09", row=_row())
    extras = rfq_send._RfqQuoteFormAttachment()(ctx)
    assert len(extras) == 1
    name, ctype, data = extras[0]
    assert name.endswith(".xlsx")
    assert ctype == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    assert data == b"PK\x03\x04xlsx-form"


def test_quote_form_resolver_returns_empty_when_no_form_box_id(stub):
    # An RFQ that filed PDF-only (no form_box_file_id in the Notes) → no extra attachment.
    ctx = EnvelopeContext(
        project_name="Sunrise", week="2026-07-09",
        row=_row(**{rfq_review.COL_NOTES: "rfq_id=5; rfq_number=RFQ-2026.001-001; vendor_key=VEN-000001"}),
    )
    assert rfq_send._RfqQuoteFormAttachment()(ctx) == []


def test_quote_form_resolver_degrades_to_empty_on_box_error(stub):
    from safety_reports import weekly_send
    stub["download"].side_effect = weekly_send.box_client.BoxError("transient")
    ctx = EnvelopeContext(project_name="Sunrise", week="2026-07-09", row=_row())
    assert rfq_send._RfqQuoteFormAttachment()(ctx) == []


# ---- end-to-end dispatch through the shared engine --------------------------


def test_send_dispatches_two_attachments_pdf_and_xlsx_form(stub):
    result = rfq_send.send_one_row(90)
    assert result.status == "sent"
    kw = stub["send_mail"].call_args.kwargs
    # Recipient from ITS_Vendors, NOT the stale display columns.
    assert kw["to"] == ["quotes@platt.example"]
    assert "STALE" not in str(kw["to"]) and "STALE" not in str(kw["cc"])
    assert kw["subject"] == "Request for Quote RFQ-2026.001-001 — 2026.001 — Sunrise Solar — Evergreen Renewables LLC"
    # TWO attachments: the price-free RFQ PDF (application/pdf) + the fillable xlsx form.
    atts = kw["attachments"]
    assert len(atts) == 2
    assert atts[0]["contentType"] == "application/pdf"
    assert atts[0]["contentBytes"] == b"%PDF-rfq"
    assert atts[1]["name"].endswith(".xlsx")
    assert atts[1]["contentType"] == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    assert atts[1]["contentBytes"] == b"PK\x03\x04xlsx-form"
    stub["get_job"].assert_not_called()


def test_send_pdf_only_when_form_box_id_absent(stub):
    stub["get_row"].return_value = _row(
        **{rfq_review.COL_NOTES: "rfq_id=5; rfq_number=RFQ-2026.001-001; vendor_key=VEN-000001"}
    )
    result = rfq_send.send_one_row(90)
    assert result.status == "sent"
    atts = stub["send_mail"].call_args.kwargs["attachments"]
    assert len(atts) == 1  # PDF-only
    assert atts[0]["contentType"] == "application/pdf"


def test_send_pdf_only_when_form_download_fails(stub):
    from safety_reports import weekly_send

    def _dl(file_id: str) -> bytes:
        if str(file_id) == "88":
            return b"%PDF-rfq"
        raise weekly_send.box_client.BoxError("form fetch blip")

    stub["download"].side_effect = _dl
    result = rfq_send.send_one_row(90)
    assert result.status == "sent"  # the essential RFQ PDF still sends
    atts = stub["send_mail"].call_args.kwargs["attachments"]
    assert len(atts) == 1


def test_send_helds_on_unknown_vendor_key(stub):
    stub["get_vendor"].return_value = None
    result = rfq_send.send_one_row(90)
    assert result.status == "held_no_recipient"
    stub["send_mail"].assert_not_called()


def test_send_helds_a_numberless_rfq_row_never_sends(stub):
    stub["get_row"].return_value = _row(**{rfq_review.COL_NOTES: "rfq_id=5"})
    result = rfq_send.send_one_row(90)
    assert result.status == "held_missing_envelope"
    stub["send_mail"].assert_not_called()


# ---- prove-the-control-bites: the contamination guard bites BOTH directions ----


def test_rfq_send_hard_helds_a_po_materials_tagged_row(stub):
    # Direction 1: a row tagged 'po_materials' (a PO row, or a mis-copied row) handed to
    # rfq_send → HARD-HELD, because rfq_send binds 'po_materials_rfq'. Never dispatched.
    stub["get_row"].return_value = _row(**{rfq_review.COL_WORKSTREAM: "po_materials"})
    result = rfq_send.send_one_row(90)
    assert result.status == "held_workstream_mismatch"
    stub["send_mail"].assert_not_called()


def test_po_send_hard_helds_an_rfq_tagged_row(stub):
    # Direction 2 (the mirror): an RFQ row tagged 'po_materials_rfq' handed to po_send →
    # HARD-HELD, because po_send binds 'po_materials'. Neither lane can dispatch the other's
    # rows — cross-lane dispatch is structurally impossible.
    from po_materials import po_send
    stub["get_row"].return_value = _row(**{rfq_review.COL_WORKSTREAM: rfq_review.WORKSTREAM_TAG})
    result = po_send.send_one_row(90)
    assert result.status == "held_workstream_mismatch"
    stub["send_mail"].assert_not_called()
