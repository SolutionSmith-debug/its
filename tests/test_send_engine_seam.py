"""S5a seam tests — the send engine serves a THIRD workstream without surgery.

The PO program's one edit to the proven live transmitter replaces the flat
`active_jobs_config`/`recipient_resolver`/`report_label` SendConfig fields with two
bound callables (`recipient_lookup`, `envelope_builder`). These tests are the seam's
controls-that-bite:

  * byte-equivalence — WeeklyReportEnvelope reproduces the pre-seam subject +
    attachment strings EXACTLY (a one-character drift here changes what customers see
    on every safety/progress email);
  * the ActiveJobsRecipientLookup contract (None on unknown job; resolver applied);
  * a synthetic po_materials-shaped binding dispatches with a VENDOR recipient and a
    PO envelope through the UNMODIFIED engine — the S5b bind, proven before it exists;
  * an unknown Vendor Key HELDs (fail toward not-sending), same as an unknown job.

All external services mocked (same stubs as tests/test_weekly_send.py).
"""
from __future__ import annotations

import re
from types import SimpleNamespace
from typing import cast

import pytest

from safety_reports import weekly_send, wsr_review
from shared import active_jobs

# ---- Envelope byte-equivalence --------------------------------------------


def test_weekly_report_envelope_reproduces_the_legacy_strings_exactly():
    ctx = weekly_send.EnvelopeContext(project_name="Bradley 1", week="2026-05-30", row={})
    subject, attachment = weekly_send.WeeklyReportEnvelope(report_label="Weekly Safety Report")(ctx)
    # The pre-S5a literals from send_one_row, frozen: any drift is customer-visible.
    assert subject == "Weekly Safety Report — Bradley 1 — week of 2026-05-30"
    assert attachment == "Weekly Safety Report — 2026-05-30.pdf"


def test_both_live_configs_bind_the_weekly_envelope_and_their_own_sheet():
    from progress_reports import progress_send

    s, p = weekly_send.CONFIG, progress_send.CONFIG
    assert isinstance(s.envelope_builder, weekly_send.WeeklyReportEnvelope)
    assert isinstance(p.envelope_builder, weekly_send.WeeklyReportEnvelope)
    assert s.envelope_builder.report_label == "Weekly Safety Report"
    assert isinstance(s.recipient_lookup, weekly_send.ActiveJobsRecipientLookup)
    assert s.recipient_lookup.active_jobs_config is active_jobs.SAFETY_ACTIVE_JOBS_CONFIG
    assert p.recipient_lookup.active_jobs_config is active_jobs.PROGRESS_ACTIVE_JOBS_CONFIG  # type: ignore[attr-defined]


# ---- ActiveJobsRecipientLookup contract ------------------------------------


def test_active_jobs_lookup_returns_none_on_unknown_job(mocker):
    mocker.patch.object(weekly_send.active_jobs, "get_job", return_value=None)
    lookup = weekly_send.ActiveJobsRecipientLookup(
        active_jobs_config=active_jobs.SAFETY_ACTIVE_JOBS_CONFIG,
        resolver=weekly_send._resolve_safety_recipients,
    )
    assert lookup("JOB-404") is None


def test_active_jobs_lookup_applies_the_bound_resolver(mocker):
    job = SimpleNamespace(safety_reports_contact_email=" pm@x.com ", cc_emails=("cc@x.com",))
    get_job = mocker.patch.object(weekly_send.active_jobs, "get_job", return_value=job)
    lookup = weekly_send.ActiveJobsRecipientLookup(
        active_jobs_config=active_jobs.SAFETY_ACTIVE_JOBS_CONFIG,
        resolver=weekly_send._resolve_safety_recipients,
    )
    assert lookup("JOB-1") == ("pm@x.com", ("cc@x.com",))
    # The bound config is threaded into get_job — the cross-wiring guard.
    assert get_job.call_args.args[1] is active_jobs.SAFETY_ACTIVE_JOBS_CONFIG


# ---- Third-workstream (po_materials-shaped) binding proof -------------------

_VENDORS = {"VEN-000123": ("orders@chint.example", ["invoices@evergreenrenewables.com"])}
_PO_NOTES_TAG = re.compile(r"\[PO_NUMBER: ([^\]]+)\]")


def _vendor_lookup(key: str):
    return _VENDORS.get(key)


def _po_envelope(ctx: weekly_send.EnvelopeContext) -> tuple[str, str]:
    m = _PO_NOTES_TAG.search(str(ctx.row.get(wsr_review.COL_NOTES) or ""))
    po_number = m.group(1) if m else "UNKNOWN"
    return (
        f"Purchase Order {po_number} — {ctx.project_name} — Evergreen Renewables",
        f"{po_number}.pdf",
    )


def _po_config() -> weekly_send.SendConfig:
    return weekly_send.SendConfig(
        script_name="po_materials.po_send",
        workstream_tag="po_materials",
        config_workstream="po_materials",
        review=cast(weekly_send._ReviewModule, wsr_review),  # title-twin schema (S1 contract)
        recipient_lookup=_vendor_lookup,
        envelope_builder=_po_envelope,
        from_mailbox_cfg_key="po_materials.po_send.from_mailbox",
        from_mailbox_default="procurement@evergreenmirror.com",
        max_send_retries=3,
        upload_session_threshold_bytes=weekly_send.UPLOAD_SESSION_THRESHOLD_BYTES,
    )


def _po_row(**kw):
    base = {
        "_row_id": 90,
        wsr_review.COL_JOB_PROJECT: "Bradley 1",
        wsr_review.COL_JOB_ID: "VEN-000123",       # the Vendor Key rides the protocol slot
        wsr_review.COL_WEEK_OF: "2026-07-09",       # the PO date rides the protocol slot
        wsr_review.COL_COMPILED_PDF: "https://app.box.com/file/88",
        wsr_review.COL_EMAIL_BODY: "Please see the attached purchase order.",
        wsr_review.COL_SEND_STATUS: wsr_review.STATUS_PENDING,
        wsr_review.COL_NOTES: "[PO_NUMBER: 2026.001.2.0.0]",
        wsr_review.COL_WORKSTREAM: "po_materials",
    }
    base.update(kw)
    return base


@pytest.fixture
def po_stub(mocker):
    return {
        "get_row": mocker.patch.object(weekly_send.smartsheet_client, "get_row", return_value=_po_row()),
        "update_rows": mocker.patch.object(weekly_send.smartsheet_client, "update_rows"),
        "download": mocker.patch.object(weekly_send.box_client, "download_file", return_value=b"%PDF-po"),
        "send_mail": mocker.patch.object(weekly_send.graph_client, "send_mail"),
        "from_mailbox": mocker.patch.object(weekly_send, "_read_str_setting", return_value="procurement@evergreenmirror.com"),
        "log": mocker.patch.object(weekly_send.error_log, "log"),
        "recipient_health": mocker.patch.object(weekly_send.recipient_health, "report_unhealthy_recipient"),
        # Prove the engine takes NO active_jobs path under a third-workstream binding.
        "get_job": mocker.patch.object(weekly_send.active_jobs, "get_job", side_effect=AssertionError("active_jobs must not be consulted under a vendor lookup")),
    }


def test_po_shaped_binding_dispatches_vendor_recipient_and_po_envelope(po_stub):
    result = weekly_send.send_one_row(90, _po_config())
    assert result.status == "sent"
    kw = po_stub["send_mail"].call_args.kwargs
    assert kw["to"] == ["orders@chint.example"]
    assert kw["cc"] == ["invoices@evergreenrenewables.com"]
    assert kw["subject"] == "Purchase Order 2026.001.2.0.0 — Bradley 1 — Evergreen Renewables"
    assert kw["attachments"][0]["name"] == "2026.001.2.0.0.pdf"
    assert kw["body"] == "Please see the attached purchase order."
    # The write-ahead SENDING marker + SENT stamp ride through unchanged.
    calls = po_stub["update_rows"].call_args_list
    assert calls[0].args[1][0][wsr_review.COL_SEND_STATUS] == wsr_review.STATUS_SENDING
    assert calls[-1].args[1][0][wsr_review.COL_SEND_STATUS] == wsr_review.STATUS_SENT


def test_po_shaped_binding_helds_on_unknown_vendor_key(po_stub):
    po_stub["get_row"].return_value = _po_row(**{wsr_review.COL_JOB_ID: "VEN-999999"})
    result = weekly_send.send_one_row(90, _po_config())
    assert result.status == "held_no_recipient"
    po_stub["send_mail"].assert_not_called()


def test_po_shaped_binding_still_hard_helds_contamination(po_stub):
    # A safety-tagged row under the po_materials sender is contamination — the P1b
    # guard must fire before recipient resolution regardless of the new seam.
    po_stub["get_row"].return_value = _po_row(**{wsr_review.COL_WORKSTREAM: "safety"})
    result = weekly_send.send_one_row(90, _po_config())
    assert result.status == "held_workstream_mismatch"
    po_stub["send_mail"].assert_not_called()
