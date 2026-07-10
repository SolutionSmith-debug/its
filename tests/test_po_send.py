"""Tests for po_materials/po_send.py — the S5b PO send instantiation.

po_send is a thin binding over the shared engine `safety_reports.weekly_send.send_one_row`;
the dispatch logic itself is covered by tests/test_weekly_send.py + test_send_engine_seam.py.
These tests pin the PO-specific binding and the cross-workstream guards a config typo would
trip: recipients from ITS_Vendors (by Vendor Key riding the COL_JOB_ID protocol slot), the
invoice-routing CC, the PO subject/attachment carrying the po_number, the numberless-PO
refusal, and the `Workstream=po_materials` contamination guard.

All external services mocked. Live coverage: scripts/smoke_test_po_send.py + the operator e2e.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from po_materials import po_review, po_send, vendors
from safety_reports.weekly_send import EnvelopeContext

_PURCHASER = {
    "entity": "Evergreen Renewables LLC",
    "invoice_routing": {
        "to": "invoices@evergreenrenewables.com",
        "cc": ["tealap@evergreenrenewables.com", "benf@evergreenrenewables.com"],
    },
}
_VENDOR = {
    vendors.COL_VENDOR_NAME: "Chint Power Systems (CPS)",
    vendors.COL_CONTACT_NAME: "Sam Seller",
    vendors.COL_CONTACT_EMAIL: "orders@chint.example",
}


def _row(**kw):
    base = {
        "_row_id": 90,
        po_review.COL_JOB_PROJECT: "2026.001 — Sunrise Solar",
        po_review.COL_JOB_ID: "VEN-000001",           # the Vendor Key rides this protocol slot
        po_review.COL_WEEK_OF: "2026-07-09",           # the PO date rides this slot
        po_review.COL_COMPILED_PDF: "https://app.box.com/file/88",
        po_review.COL_EMAIL_BODY: "Hello Sam — attached PO. Please countersign.",
        po_review.COL_SEND_STATUS: po_review.STATUS_PENDING,
        po_review.COL_NOTES: "po_id=7; po_number=2026.001.2.0.0",
        po_review.COL_WORKSTREAM: "po_materials",      # present-matching tag → guard passes
        po_review.COL_RECIPIENT_TO: "STALE@display.example",
        po_review.COL_CC: "STALE-cc@display.example",
    }
    base.update(kw)
    return base


@pytest.fixture
def stub(mocker) -> dict[str, MagicMock]:
    # po_send.send_one_row routes through weekly_send.send_one_row, so the engine's
    # collaborators are patched on weekly_send.*; the PO recipient/envelope collaborators
    # (vendors, terms_lib) are patched on po_send.*.
    from safety_reports import weekly_send
    return {
        "get_row": mocker.patch.object(weekly_send.smartsheet_client, "get_row", return_value=_row()),
        "update_rows": mocker.patch.object(weekly_send.smartsheet_client, "update_rows"),
        "download": mocker.patch.object(weekly_send.box_client, "download_file", return_value=b"%PDF-po"),
        "send_mail": mocker.patch.object(weekly_send.graph_client, "send_mail"),
        "send_large": mocker.patch.object(weekly_send.graph_client, "send_mail_large_attachment"),
        "from_mailbox": mocker.patch.object(weekly_send, "_read_str_setting", return_value="procurement@evergreenmirror.com"),
        "log": mocker.patch.object(weekly_send.error_log, "log"),
        "recipient_health": mocker.patch.object(weekly_send.recipient_health, "report_unhealthy_recipient"),
        "get_vendor": mocker.patch.object(po_send.vendors, "get_vendor_by_key", return_value=dict(_VENDOR)),
        "purchaser": mocker.patch.object(po_send.terms_lib, "load_purchaser_config", return_value=_PURCHASER),
        # active_jobs must NOT be consulted — a PO resolves recipients from ITS_Vendors.
        "get_job": mocker.patch.object(
            weekly_send.active_jobs, "get_job",
            side_effect=AssertionError("active_jobs must not be consulted for a PO send"),
        ),
    }


# ---- binding correctness (the cross-workstream contamination gate) -------


def test_config_binds_po_not_safety_or_progress():
    cfg = po_send.CONFIG
    assert cfg.workstream_tag == "po_materials"
    assert cfg.config_workstream == "po_materials"
    assert cfg.from_mailbox_cfg_key == "po_materials.po_send.from_mailbox"
    assert cfg.from_mailbox_default == "procurement@evergreenmirror.com"
    # The review sheet is the PO one (a WSR twin re-export), never WSR/WPR.
    assert cfg.review is po_review or cfg.review.__name__.endswith("po_review")
    assert isinstance(cfg.recipient_lookup, po_send._VendorRecipientLookup)
    assert isinstance(cfg.envelope_builder, po_send._PoEnvelope)


# ---- recipient resolution (ITS_Vendors by Vendor Key; invoice-routing CC) ----


def test_vendor_lookup_resolves_email_and_invoice_cc(mocker):
    mocker.patch.object(po_send.vendors, "get_vendor_by_key", return_value=dict(_VENDOR))
    mocker.patch.object(po_send.terms_lib, "load_purchaser_config", return_value=_PURCHASER)
    to, cc = po_send._VendorRecipientLookup()("VEN-000001")
    assert to == "orders@chint.example"
    assert list(cc) == ["tealap@evergreenrenewables.com", "benf@evergreenrenewables.com"]


def test_vendor_lookup_returns_none_for_unknown_key(mocker):
    mocker.patch.object(po_send.vendors, "get_vendor_by_key", return_value=None)
    assert po_send._VendorRecipientLookup()("VEN-999999") is None


def test_vendor_lookup_returns_none_for_blank_email(mocker):
    blank = dict(_VENDOR, **{vendors.COL_CONTACT_EMAIL: "   "})
    mocker.patch.object(po_send.vendors, "get_vendor_by_key", return_value=blank)
    assert po_send._VendorRecipientLookup()("VEN-000001") is None


# ---- envelope (subject + attachment carry the PO number) --------------------


def test_envelope_carries_po_number_and_entity(mocker):
    mocker.patch.object(po_send.terms_lib, "load_purchaser_config", return_value=_PURCHASER)
    ctx = EnvelopeContext(project_name="2026.001 — Sunrise Solar", week="2026-07-09", row=_row())
    subject, attachment = po_send._PoEnvelope()(ctx)
    assert subject == "Purchase Order 2026.001.2.0.0 — 2026.001 — Sunrise Solar — Evergreen Renewables LLC"
    # Attachment name is JOB-PREFIXED (2026-07 convention — job name in the document name).
    assert attachment == "2026.001 — Sunrise Solar_PO_2026.001.2.0.0.pdf"


def test_envelope_blank_project_falls_back_to_number_only_attachment(mocker):
    # A job-scoped PO always has a project, but a blank Job/Project cell must degrade, not crash:
    # the attachment unifies onto the SAME fallback the Box/Smartsheet surfaces use (`PO <n>.pdf`),
    # NOT po_send's pre-2026-07 bare `<n>.pdf` (the intended fix of the old 3-vs-1 name drift).
    mocker.patch.object(po_send.terms_lib, "load_purchaser_config", return_value=_PURCHASER)
    ctx = EnvelopeContext(project_name="", week="2026-07-09", row=_row())
    _subject, attachment = po_send._PoEnvelope()(ctx)
    assert attachment == "PO 2026.001.2.0.0.pdf"


def test_envelope_returns_none_for_a_numberless_po(mocker):
    # None → the engine HELDs (held_missing_envelope), mirroring recipient_lookup's
    # None→HELD convention — never a numberless PO to a vendor, never a raise.
    mocker.patch.object(po_send.terms_lib, "load_purchaser_config", return_value=_PURCHASER)
    ctx = EnvelopeContext(project_name="Sunrise", week="2026-07-09", row=_row(**{po_review.COL_NOTES: "po_id=7"}))
    assert po_send._PoEnvelope()(ctx) is None


# ---- end-to-end dispatch through the shared engine --------------------------


def test_send_dispatches_vendor_recipient_and_po_envelope(stub):
    result = po_send.send_one_row(90)
    assert result.status == "sent"
    kw = stub["send_mail"].call_args.kwargs
    # Recipient from ITS_Vendors, NOT the stale display columns.
    assert kw["to"] == ["orders@chint.example"]
    assert kw["cc"] == ["tealap@evergreenrenewables.com", "benf@evergreenrenewables.com"]
    assert "STALE" not in str(kw["to"]) and "STALE" not in str(kw["cc"])
    # Body = the human-edited Email Body; PO PDF attached with the JOB-PREFIXED filename.
    assert kw["body"] == "Hello Sam — attached PO. Please countersign."
    assert kw["subject"] == "Purchase Order 2026.001.2.0.0 — 2026.001 — Sunrise Solar — Evergreen Renewables LLC"
    assert kw["attachments"][0]["name"] == "2026.001 — Sunrise Solar_PO_2026.001.2.0.0.pdf"
    assert kw["attachments"][0]["contentBytes"] == b"%PDF-po"
    # active_jobs.get_job was NOT consulted (the fixture would AssertionError).
    stub["get_job"].assert_not_called()


def test_send_helds_on_unknown_vendor_key(stub):
    stub["get_vendor"].return_value = None
    result = po_send.send_one_row(90)
    assert result.status == "held_no_recipient"
    stub["send_mail"].assert_not_called()


def test_send_hard_helds_a_contaminated_row(stub):
    stub["get_row"].return_value = _row(**{po_review.COL_WORKSTREAM: "safety"})
    result = po_send.send_one_row(90)
    assert result.status == "held_workstream_mismatch"
    stub["send_mail"].assert_not_called()


def test_send_helds_a_numberless_po_row_never_sends(stub):
    # End-to-end: a review row whose Notes lost the po_number tag HELDs
    # (held_missing_envelope) — operator-visible, never a numberless PO to the vendor,
    # and (unlike a raise) the row is flipped to HELD so it is not re-dispatched.
    stub["get_row"].return_value = _row(**{po_review.COL_NOTES: "po_id=7"})
    result = po_send.send_one_row(90)
    assert result.status == "held_missing_envelope"
    stub["send_mail"].assert_not_called()
