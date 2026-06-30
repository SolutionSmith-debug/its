"""Tests for progress_reports/progress_send.py — the P5 PROGRESS send instantiation.

progress_send is a thin binding over the shared engine `safety_reports.weekly_send.
send_one_row`; the dispatch logic itself is covered by tests/test_weekly_send.py. These
tests pin the PROGRESS-specific binding and the cross-workstream guards a config typo
would trip: recipients from the PROGRESS sheet, the stakeholder fallback, the
`Workstream=progress` contamination guard, and the progress subject/label.

All external services mocked (patched on `weekly_send.*`, where the shared engine calls
them). Live coverage: scripts/smoke_test_progress_send.py + the operator e2e.
"""
from __future__ import annotations

import re
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from progress_reports import progress_send, wpr_review
from shared import active_jobs


def _row(**kw):
    base = {
        "_row_id": 70,
        wpr_review.COL_JOB_PROJECT: "Solar Ridge",
        wpr_review.COL_JOB_ID: "JOB-9",
        wpr_review.COL_WEEK_OF: "2026-06-26",
        wpr_review.COL_COMPILED_PDF: "https://app.box.com/file/99",
        wpr_review.COL_EMAIL_BODY: "Good morning Pat — weekly progress attached.",
        wpr_review.COL_SEND_STATUS: wpr_review.STATUS_PENDING,
        wpr_review.COL_NOTES: "",
        wpr_review.COL_WORKSTREAM: "progress",  # present-matching tag → the guard passes
        wpr_review.COL_RECIPIENT_TO: "STALE@display.example",
        wpr_review.COL_CC: "STALE-cc@display.example",
    }
    base.update(kw)
    return base


def _job(**kw):
    """A progress ActiveJob stand-in. Exposes BOTH the neutral `reports_contact_email`
    alias the progress resolver reads AND `stakeholder_email` (the fallback)."""
    base = dict(
        project_name="Solar Ridge", job_id="JOB-9",
        reports_contact_email="pm@evergreenmirror.com",
        reports_contact_name="Pat",
        stakeholder_email="owner@client.example",
        cc_emails=("cc1@x.com", "cc2@x.com"),
        is_active=True, active_status="Active",
    )
    base.update(kw)
    return SimpleNamespace(**base)


@pytest.fixture
def stub(mocker) -> dict[str, MagicMock]:
    # progress_send.send_one_row routes through weekly_send.send_one_row, so the engine's
    # collaborators are patched on weekly_send.*.
    from safety_reports import weekly_send
    return {
        "get_row": mocker.patch.object(weekly_send.smartsheet_client, "get_row", return_value=_row()),
        "update_rows": mocker.patch.object(weekly_send.smartsheet_client, "update_rows"),
        "get_job": mocker.patch.object(weekly_send.active_jobs, "get_job", return_value=_job()),
        "download": mocker.patch.object(weekly_send.box_client, "download_file", return_value=b"%PDF-progress"),
        "send_mail": mocker.patch.object(weekly_send.graph_client, "send_mail"),
        "send_large": mocker.patch.object(weekly_send.graph_client, "send_mail_large_attachment"),
        "from_mailbox": mocker.patch.object(weekly_send, "_read_str_setting", return_value="progress@evergreenmirror.com"),
        "log": mocker.patch.object(weekly_send.error_log, "log"),
    }


# ---- binding correctness (the cross-workstream contamination gate) -------


def test_config_binds_progress_not_safety():
    cfg = progress_send.CONFIG
    assert cfg.workstream_tag == "progress"
    assert cfg.config_workstream == "progress_reports"
    assert cfg.report_label == "Weekly Progress Report"
    # The load-bearing recipient-routing guard: recipients resolve from the PROGRESS
    # Active-Jobs sheet, never safety's.
    assert cfg.active_jobs_config is active_jobs.PROGRESS_ACTIVE_JOBS_CONFIG
    assert cfg.active_jobs_config is not active_jobs.SAFETY_ACTIVE_JOBS_CONFIG
    assert cfg.review is wpr_review or cfg.review.__name__.endswith("wpr_review")


def test_get_job_is_called_with_the_progress_active_jobs_config(stub):
    progress_send.send_one_row(70)
    # The CRITICAL trap (P4 Slice 1): a missing/safety config would resolve the wrong
    # contact column silently. Assert the progress config is threaded into get_job.
    _, kwargs = stub["get_job"].call_args
    args = stub["get_job"].call_args.args
    passed = (args[1] if len(args) > 1 else kwargs.get("config"))
    assert passed is active_jobs.PROGRESS_ACTIVE_JOBS_CONFIG


# ---- recipient resolution (progress contact w/ stakeholder fallback) -----


def test_resolver_prefers_progress_contact():
    to, cc = progress_send._resolve_progress_recipients(_job())
    assert to == "pm@evergreenmirror.com"
    assert list(cc) == ["cc1@x.com", "cc2@x.com"]


def test_resolver_falls_back_to_stakeholder_when_contact_blank():
    to, _cc = progress_send._resolve_progress_recipients(_job(reports_contact_email=""))
    assert to == "owner@client.example"


def test_resolver_returns_empty_when_both_blank():
    to, _cc = progress_send._resolve_progress_recipients(
        _job(reports_contact_email="", stakeholder_email="")
    )
    assert to == ""


# ---- happy send ----------------------------------------------------------


def test_progress_send_uses_progress_recipients_and_label(stub):
    result = progress_send.send_one_row(70)
    assert result.status == "sent"
    kw = stub["send_mail"].call_args.kwargs
    assert kw["to"] == ["pm@evergreenmirror.com"]          # progress contact, not stale display
    assert kw["cc"] == ["cc1@x.com", "cc2@x.com"]
    assert "STALE" not in str(kw["to"]) and "STALE" not in str(kw["cc"])
    assert kw["body"] == "Good morning Pat — weekly progress attached."
    assert kw["attachments"][0]["contentBytes"] == b"%PDF-progress"
    assert "Weekly Progress Report" in kw["subject"]
    assert "Solar Ridge" in kw["subject"] and "2026-06-26" in kw["subject"]
    upd = stub["update_rows"].call_args.args[1][0]
    assert upd[wpr_review.COL_SEND_STATUS] == wpr_review.STATUS_SENT
    assert re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}$", upd[wpr_review.COL_SENT_AT])


def test_progress_send_falls_back_to_stakeholder(stub):
    stub["get_job"].return_value = _job(reports_contact_email="")
    result = progress_send.send_one_row(70)
    assert result.status == "sent"
    assert stub["send_mail"].call_args.kwargs["to"] == ["owner@client.example"]


def test_both_contacts_blank_is_held(stub):
    stub["get_job"].return_value = _job(reports_contact_email="", stakeholder_email="")
    result = progress_send.send_one_row(70)
    assert result.status == "held_no_recipient"
    stub["send_mail"].assert_not_called()


# ---- cross-workstream contamination guard --------------------------------


def test_safety_tagged_row_on_wpr_sheet_is_hard_held(stub):
    # A row tagged 'safety' that reached the PROGRESS sender must NEVER transmit.
    stub["get_row"].return_value = _row(**{wpr_review.COL_WORKSTREAM: "safety"})
    result = progress_send.send_one_row(70)
    assert result.status == "held_workstream_mismatch"
    stub["send_mail"].assert_not_called()
    # CRITICAL fired naming the mismatch.
    assert any(
        c.kwargs.get("error_code") == "weekly_send.workstream_mismatch"
        for c in stub["log"].call_args_list
    )


def test_absent_tag_warns_and_proceeds(stub):
    stub["get_row"].return_value = _row(**{wpr_review.COL_WORKSTREAM: ""})
    result = progress_send.send_one_row(70)
    assert result.status == "sent"  # back-compat fail-open for a genuinely-absent tag
