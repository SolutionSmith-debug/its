"""Tests for subcontracts/subcontract_send.py — the SC-S4 subcontract send instantiation.

subcontract_send is a thin binding over the shared engine
`safety_reports.weekly_send.send_one_row`; the dispatch logic itself is covered by
tests/test_weekly_send.py + test_send_engine_seam.py. These tests pin the subcontract-specific
binding and the cross-workstream guards a config typo would trip: the recipient from
ITS_Subcontractors (by Sub Key riding the COL_JOB_ID protocol slot) with an EMPTY CC, the
subcontract subject/attachment carrying the sc_number, the ZIP package attachment with an
`application/zip` content-type, the numberless-subcontract refusal, and the
`Workstream=subcontracts` contamination guard.

All external services mocked. Live coverage: scripts/smoke_test_subcontract_send.py + the
operator e2e.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from safety_reports.weekly_send import EnvelopeContext
from subcontracts import subcontract_review, subcontract_send, subcontractors

_CONTRACTOR = {"entity": "Evergreen Renewables LLC"}
_SUBCONTRACTOR = {
    subcontractors.COL_SUB_NAME: "Ace Electrical Co.",
    subcontractors.COL_SUB_KEY: "SUB-000001",
    subcontractors.COL_CONTACT_NAME: "Dana Sub",
    subcontractors.COL_CONTACT_EMAIL: "contracts@ace.example",
}


def _row(**kw):
    base = {
        "_row_id": 91,
        subcontract_review.COL_JOB_PROJECT: "2026.001 — Sunrise Solar",
        subcontract_review.COL_JOB_ID: "SUB-000001",       # the Sub Key rides this protocol slot
        subcontract_review.COL_WEEK_OF: "2026-07-09",       # the agreement date rides this slot
        subcontract_review.COL_COMPILED_PDF: "https://app.box.com/file/99",  # the Package.zip link
        subcontract_review.COL_EMAIL_BODY: "Hello Dana — attached subcontract. Please execute.",
        subcontract_review.COL_SEND_STATUS: subcontract_review.STATUS_PENDING,
        subcontract_review.COL_NOTES: "sc_id=7; sc_number=2026.001.OR.0.0",
        subcontract_review.COL_WORKSTREAM: "subcontracts",  # present-matching tag → guard passes
        subcontract_review.COL_RECIPIENT_TO: "STALE@display.example",
        subcontract_review.COL_CC: "STALE-cc@display.example",
    }
    base.update(kw)
    return base


@pytest.fixture
def stub(mocker) -> dict[str, MagicMock]:
    # subcontract_send.send_one_row routes through weekly_send.send_one_row, so the engine's
    # collaborators are patched on weekly_send.*; the subcontract recipient/envelope
    # collaborators (subcontractors, terms_lib) are patched on subcontract_send.*.
    from safety_reports import weekly_send
    return {
        "get_row": mocker.patch.object(weekly_send.smartsheet_client, "get_row", return_value=_row()),
        "update_rows": mocker.patch.object(weekly_send.smartsheet_client, "update_rows"),
        "download": mocker.patch.object(weekly_send.box_client, "download_file", return_value=b"PK\x03\x04zip"),
        "send_mail": mocker.patch.object(weekly_send.graph_client, "send_mail"),
        "send_large": mocker.patch.object(weekly_send.graph_client, "send_mail_large_attachment"),
        "from_mailbox": mocker.patch.object(weekly_send, "_read_str_setting", return_value="procurement@evergreenmirror.com"),
        "log": mocker.patch.object(weekly_send.error_log, "log"),
        "recipient_health": mocker.patch.object(weekly_send.recipient_health, "report_unhealthy_recipient"),
        "get_sub": mocker.patch.object(subcontract_send.subcontractors, "get_subcontractor_by_key", return_value=dict(_SUBCONTRACTOR)),
        "contractor": mocker.patch.object(subcontract_send.terms_lib, "load_contractor_config", return_value=_CONTRACTOR),
        # active_jobs must NOT be consulted — a subcontract resolves recipients from ITS_Subcontractors.
        "get_job": mocker.patch.object(
            weekly_send.active_jobs, "get_job",
            side_effect=AssertionError("active_jobs must not be consulted for a subcontract send"),
        ),
    }


# ---- binding correctness (the cross-workstream contamination gate) -------


def test_config_binds_subcontracts_not_safety_progress_or_po():
    cfg = subcontract_send.CONFIG
    assert cfg.workstream_tag == "subcontracts"
    assert cfg.config_workstream == "subcontracts"
    assert cfg.from_mailbox_cfg_key == "subcontracts.subcontract_send.from_mailbox"
    assert cfg.from_mailbox_default == "procurement@evergreenmirror.com"
    # The review sheet is the subcontract one (a WSR twin re-export), never WSR/WPR/PO.
    assert cfg.review is subcontract_review or cfg.review.__name__.endswith("subcontract_review")
    assert isinstance(cfg.recipient_lookup, subcontract_send._SubcontractorRecipientLookup)
    assert isinstance(cfg.envelope_builder, subcontract_send._SubcontractEnvelope)


# ---- recipient resolution (ITS_Subcontractors by Sub Key; EMPTY CC) ------


def test_subcontractor_lookup_resolves_email_with_empty_cc(mocker):
    mocker.patch.object(subcontract_send.subcontractors, "get_subcontractor_by_key", return_value=dict(_SUBCONTRACTOR))
    to, cc = subcontract_send._SubcontractorRecipientLookup()("SUB-000001")
    assert to == "contracts@ace.example"
    assert list(cc) == []  # EMPTY CC by design — a subcontract has a single external recipient


def test_subcontractor_lookup_returns_none_for_unknown_key(mocker):
    mocker.patch.object(subcontract_send.subcontractors, "get_subcontractor_by_key", return_value=None)
    assert subcontract_send._SubcontractorRecipientLookup()("SUB-999999") is None


def test_subcontractor_lookup_returns_none_for_blank_email(mocker):
    blank = dict(_SUBCONTRACTOR, **{subcontractors.COL_CONTACT_EMAIL: "   "})
    mocker.patch.object(subcontract_send.subcontractors, "get_subcontractor_by_key", return_value=blank)
    assert subcontract_send._SubcontractorRecipientLookup()("SUB-000001") is None


# ---- envelope (subject + ZIP attachment carry the sc_number) ----------------


def test_envelope_carries_sc_number_entity_and_zip_attachment(mocker):
    mocker.patch.object(subcontract_send.terms_lib, "load_contractor_config", return_value=_CONTRACTOR)
    ctx = EnvelopeContext(project_name="2026.001 — Sunrise Solar", week="2026-07-09", row=_row())
    subject, attachment = subcontract_send._SubcontractEnvelope()(ctx)
    assert subject == "Subcontract 2026.001.OR.0.0 — 2026.001 — Sunrise Solar — Evergreen Renewables LLC"
    # Attachment is the JOB-PREFIXED combined .zip package (the SC-S4 send artifact).
    assert attachment == "2026.001 — Sunrise Solar_Subcontract Package_2026.001.OR.0.0.zip"


def test_envelope_blank_project_falls_back_to_number_only_zip(mocker):
    mocker.patch.object(subcontract_send.terms_lib, "load_contractor_config", return_value=_CONTRACTOR)
    ctx = EnvelopeContext(project_name="", week="2026-07-09", row=_row())
    _subject, attachment = subcontract_send._SubcontractEnvelope()(ctx)
    assert attachment == "Subcontract Package 2026.001.OR.0.0.zip"


def test_envelope_returns_none_for_a_numberless_subcontract(mocker):
    # None → the engine HELDs (held_missing_envelope) — never a numberless subcontract, never a raise.
    mocker.patch.object(subcontract_send.terms_lib, "load_contractor_config", return_value=_CONTRACTOR)
    ctx = EnvelopeContext(project_name="Sunrise", week="2026-07-09", row=_row(**{subcontract_review.COL_NOTES: "sc_id=7"}))
    assert subcontract_send._SubcontractEnvelope()(ctx) is None


# ---- end-to-end dispatch through the shared engine --------------------------


def test_send_dispatches_subcontractor_recipient_empty_cc_and_zip_envelope(stub):
    result = subcontract_send.send_one_row(91)
    assert result.status == "sent"
    kw = stub["send_mail"].call_args.kwargs
    # Recipient from ITS_Subcontractors, NOT the stale display columns; EMPTY CC.
    assert kw["to"] == ["contracts@ace.example"]
    assert not kw["cc"]  # empty / None — no subcontract CC list
    assert "STALE" not in str(kw["to"]) and "STALE" not in str(kw["cc"])
    # Body = the human-edited Email Body; the ZIP package attached with the JOB-PREFIXED name.
    assert kw["body"] == "Hello Dana — attached subcontract. Please execute."
    assert kw["subject"] == "Subcontract 2026.001.OR.0.0 — 2026.001 — Sunrise Solar — Evergreen Renewables LLC"
    att = kw["attachments"][0]
    assert att["name"] == "2026.001 — Sunrise Solar_Subcontract Package_2026.001.OR.0.0.zip"
    assert att["contentBytes"] == b"PK\x03\x04zip"
    # THE key new-behavior invariant: the .zip attachment is labeled application/zip (not pdf).
    assert att["contentType"] == "application/zip"
    stub["get_job"].assert_not_called()


def test_send_helds_on_unknown_sub_key(stub):
    stub["get_sub"].return_value = None
    result = subcontract_send.send_one_row(91)
    assert result.status == "held_no_recipient"
    stub["send_mail"].assert_not_called()


def test_send_hard_helds_a_contaminated_row(stub):
    stub["get_row"].return_value = _row(**{subcontract_review.COL_WORKSTREAM: "safety"})
    result = subcontract_send.send_one_row(91)
    assert result.status == "held_workstream_mismatch"
    stub["send_mail"].assert_not_called()


def test_send_helds_a_numberless_subcontract_row_never_sends(stub):
    stub["get_row"].return_value = _row(**{subcontract_review.COL_NOTES: "sc_id=7"})
    result = subcontract_send.send_one_row(91)
    assert result.status == "held_missing_envelope"
    stub["send_mail"].assert_not_called()
