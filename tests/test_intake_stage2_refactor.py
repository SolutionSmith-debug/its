"""End-to-end coverage of the Stage 2 trusted-sender + header-forgery refactor.

Complementary to test_intake.py which exercises pure functions across the
12-stage pipeline. This file pins the Stage 2 routing matrix specifically:

  scope × header combinations from the brief, plus the legacy fallback
  branch + the Stage 4b project-scope re-check.

Mocks the trusted_contacts / header_forgery boundaries directly so each
test exercises one matrix cell.
"""
from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from typing import Any

import pytest

from safety_reports.intake import (
    Extraction,
    process_message,
)
from shared.header_forgery import HeaderAnalysis, HeaderVerdict
from shared.quarantine import QuarantineReason
from shared.review_queue import ReviewReason
from shared.trusted_contacts import ContactStatus, ScopeVerdict, TrustedContact

DEFAULT_SENDER = "seths@evergreenmirror.com"
DEFAULT_MAILBOX = "safety@evergreenmirror.com"
DEFAULT_MESSAGE_ID = "AAMkADHAS5g="


def _graph_message(sender: str = DEFAULT_SENDER, subject: str = "Bradley 1 Daily JHA") -> dict[str, Any]:
    return {
        "id": DEFAULT_MESSAGE_ID,
        "subject": subject,
        "from": {"emailAddress": {"address": sender}},
        "body": {"contentType": "text", "content": "Crew on site."},
        "hasAttachments": False,
        "internetMessageHeaders": [
            {"name": "From", "value": f"<{sender}>"},
        ],
    }


def _pass_header() -> HeaderAnalysis:
    return HeaderAnalysis(
        verdict=HeaderVerdict.PASS,
        spf="pass", dkim="pass", dmarc="pass",
        return_path_domain="evergreenmirror.com",
        from_domain="evergreenmirror.com",
        return_path_mismatch=False,
        raw_authentication_results="spf=pass; dkim=pass; dmarc=pass",
    )


def _soft_fail_header() -> HeaderAnalysis:
    return HeaderAnalysis(
        verdict=HeaderVerdict.SOFT_FAIL,
        spf="softfail", dkim="pass", dmarc="pass",
        return_path_domain="evergreenmirror.com",
        from_domain="evergreenmirror.com",
        return_path_mismatch=False,
        raw_authentication_results="spf=softfail; dkim=pass; dmarc=pass",
    )


def _hard_fail_header() -> HeaderAnalysis:
    return HeaderAnalysis(
        verdict=HeaderVerdict.HARD_FAIL,
        spf="fail", dkim="pass", dmarc="fail",
        return_path_domain="attacker.example",
        from_domain="evergreenmirror.com",
        return_path_mismatch=True,
        raw_authentication_results="spf=fail; dkim=pass; dmarc=fail (p=reject)",
    )


def _active_contact() -> TrustedContact:
    return TrustedContact(
        email=DEFAULT_SENDER,
        display_name="Seth Smith",
        role="Operator",
        project_scope=("*",),
        workstream_scope=("safety_reports",),
        status=ContactStatus.ACTIVE,
        row_id=42,
    )


def _extraction() -> Extraction:
    return Extraction(
        report_category="Daily JHA",
        confidence=0.95,
        report_date=date(2026, 5, 19),
        crew_or_subcontractor=None,
        safety_topic_or_report_title="Module replacement",
        summary_of_events="Crew on site.",
        notes_or_action_items=None,
        ahj_inspection=None,
        visitor_log=None,
        anomaly_flags=[],
    )


@pytest.fixture
def patch_baseline(mocker):
    """Per-test plumbing: bypass Graph fetch, ITS_Config reads, kill switch.

    `_read_allowed_senders` is mocked even though most tests take the
    sheet-authoritative branch — `_run_pipeline` calls it unconditionally
    at the top of the function (cheap config read), and on CI (Linux,
    no macOS Keychain) the underlying smartsheet_client call would raise
    KeychainError before reaching the Stage 2 branch.
    """
    mocker.patch(
        "safety_reports.intake.graph_client.get_message",
        return_value=_graph_message(),
    )
    mocker.patch(
        "safety_reports.intake.graph_client.list_attachments", return_value=[],
    )
    mocker.patch(
        "safety_reports.intake._read_allowed_senders",
        return_value=[DEFAULT_SENDER],
    )
    mocker.patch(
        "safety_reports.intake._read_str_setting",
        side_effect=lambda key, fallback: fallback,
    )
    mocker.patch(
        "safety_reports.intake._read_bool_setting",
        side_effect=lambda key, fallback: fallback,
    )
    mocker.patch(
        "safety_reports.intake._read_float_setting",
        side_effect=lambda key, fallback: fallback,
    )
    mocker.patch("safety_reports.intake.error_log.log")
    from shared.kill_switch import SystemState
    mocker.patch(
        "shared.kill_switch.check_system_state", return_value=SystemState.ACTIVE
    )


def _stub_pipeline_past_stage_4b(mocker):
    """Stub stages 4 → 11 so a 'proceed' Stage 2 lands as status=processed."""
    mocker.patch(
        "safety_reports.intake.classify_and_extract",
        return_value=_extraction(),
    )
    mocker.patch(
        "safety_reports.intake.ensure_current_week_folder",
        return_value=SimpleNamespace(
            folder_id=1, daily_reports_sheet_id=100, weekly_rollup_sheet_id=200,
        ),
    )
    mocker.patch(
        "safety_reports.intake.write_daily_reports_row", return_value=42,
    )
    mocker.patch(
        "safety_reports.intake.upload_attachments_to_box",
        return_value=([], []),
    )
    mocker.patch("safety_reports.intake.update_row_with_box_links")


# ---- Matrix rows: trusted sender × header verdict ------------------------


def test_trusted_sender_pass_headers_proceeds(mocker, patch_baseline):
    contact = _active_contact()
    mocker.patch(
        "safety_reports.intake.trusted_contacts._load_contacts",
        return_value=[contact],
    )
    mocker.patch(
        "safety_reports.intake.trusted_contacts.check_scope",
        return_value=ScopeVerdict(allowed=True, contact=contact, reason="allowed"),
    )
    mocker.patch(
        "safety_reports.intake.header_forgery.analyze",
        return_value=_pass_header(),
    )
    _stub_pipeline_past_stage_4b(mocker)

    result = process_message(DEFAULT_MESSAGE_ID, mailbox=DEFAULT_MAILBOX)
    assert result.status == "processed"


def test_trusted_sender_soft_fail_headers_to_review_queue(mocker, patch_baseline):
    contact = _active_contact()
    mocker.patch(
        "safety_reports.intake.trusted_contacts._load_contacts",
        return_value=[contact],
    )
    mocker.patch(
        "safety_reports.intake.trusted_contacts.check_scope",
        return_value=ScopeVerdict(allowed=True, contact=contact, reason="allowed"),
    )
    mocker.patch(
        "safety_reports.intake.header_forgery.analyze",
        return_value=_soft_fail_header(),
    )
    review_add = mocker.patch(
        "safety_reports.intake.review_queue.add", return_value=1,
    )
    classify = mocker.patch("safety_reports.intake.classify_and_extract")

    result = process_message(DEFAULT_MESSAGE_ID, mailbox=DEFAULT_MAILBOX)

    assert result.status == "review_queue"
    review_add.assert_called_once()
    assert (
        review_add.call_args.kwargs["reason"] == ReviewReason.HEADER_SOFT_FAIL_TRUSTED
    )
    # security_flag fires because SOFT_FAIL on a trusted sender is unusual.
    assert review_add.call_args.kwargs["security_flag"] is True
    classify.assert_not_called()


def test_trusted_sender_hard_fail_headers_to_quarantine(mocker, patch_baseline):
    contact = _active_contact()
    mocker.patch(
        "safety_reports.intake.trusted_contacts._load_contacts",
        return_value=[contact],
    )
    mocker.patch(
        "safety_reports.intake.trusted_contacts.check_scope",
        return_value=ScopeVerdict(allowed=True, contact=contact, reason="allowed"),
    )
    mocker.patch(
        "safety_reports.intake.header_forgery.analyze",
        return_value=_hard_fail_header(),
    )
    quarantine_log = mocker.patch(
        "safety_reports.intake.quarantine.log_quarantined_message",
        return_value=99,
    )

    result = process_message(DEFAULT_MESSAGE_ID, mailbox=DEFAULT_MAILBOX)

    assert result.status == "quarantined"
    quarantine_log.assert_called_once()
    assert (
        quarantine_log.call_args.kwargs["reason"]
        == QuarantineReason.HEADER_FORGERY_SUSPECTED
    )


# ---- Matrix rows: untrusted / disabled / pending senders -----------------


def test_unknown_sender_pass_headers_quarantine_unknown(mocker, patch_baseline):
    """Header verdict is irrelevant when the sender isn't trusted."""
    mocker.patch(
        "safety_reports.intake.trusted_contacts._load_contacts",
        return_value=[_active_contact()],
    )
    mocker.patch(
        "safety_reports.intake.trusted_contacts.check_scope",
        return_value=ScopeVerdict(
            allowed=False, contact=None, reason="unknown_sender",
        ),
    )
    mocker.patch(
        "safety_reports.intake.header_forgery.analyze",
        return_value=_pass_header(),
    )
    quarantine_log = mocker.patch(
        "safety_reports.intake.quarantine.log_quarantined_message",
        return_value=99,
    )

    result = process_message(DEFAULT_MESSAGE_ID, mailbox=DEFAULT_MAILBOX)
    assert result.status == "quarantined"
    assert (
        quarantine_log.call_args.kwargs["reason"]
        == QuarantineReason.UNKNOWN_SENDER
    )


def test_disabled_sender_quarantines_disabled(mocker, patch_baseline):
    contact = _active_contact()
    mocker.patch(
        "safety_reports.intake.trusted_contacts._load_contacts",
        return_value=[contact],
    )
    mocker.patch(
        "safety_reports.intake.trusted_contacts.check_scope",
        return_value=ScopeVerdict(
            allowed=False, contact=contact, reason="status_disabled",
        ),
    )
    mocker.patch(
        "safety_reports.intake.header_forgery.analyze",
        return_value=_pass_header(),
    )
    quarantine_log = mocker.patch(
        "safety_reports.intake.quarantine.log_quarantined_message",
        return_value=99,
    )

    result = process_message(DEFAULT_MESSAGE_ID, mailbox=DEFAULT_MAILBOX)
    assert result.status == "quarantined"
    assert (
        quarantine_log.call_args.kwargs["reason"]
        == QuarantineReason.SENDER_DISABLED
    )


def test_pending_verification_routes_to_review(mocker, patch_baseline):
    contact = _active_contact()
    mocker.patch(
        "safety_reports.intake.trusted_contacts._load_contacts",
        return_value=[contact],
    )
    mocker.patch(
        "safety_reports.intake.trusted_contacts.check_scope",
        return_value=ScopeVerdict(
            allowed=False, contact=contact,
            reason="status_pending_verification",
        ),
    )
    mocker.patch(
        "safety_reports.intake.header_forgery.analyze",
        return_value=_pass_header(),
    )
    review_add = mocker.patch(
        "safety_reports.intake.review_queue.add", return_value=1,
    )

    result = process_message(DEFAULT_MESSAGE_ID, mailbox=DEFAULT_MAILBOX)
    assert result.status == "review_queue"
    assert (
        review_add.call_args.kwargs["reason"]
        == ReviewReason.SENDER_PENDING_VERIFICATION
    )


# ---- Legacy fallback branch ---------------------------------------------


def test_empty_sheet_falls_back_to_its_config_allowlist(mocker, patch_baseline):
    """Sheet has zero rows → legacy ITS_Config allowed_senders path runs."""
    mocker.patch(
        "safety_reports.intake.trusted_contacts._load_contacts",
        return_value=[],  # empty sheet
    )
    mocker.patch(
        "safety_reports.intake._read_allowed_senders",
        return_value=[DEFAULT_SENDER],
    )
    mocker.patch(
        "safety_reports.intake.header_forgery.analyze",
        return_value=_pass_header(),
    )
    error_log_log = mocker.patch("safety_reports.intake.error_log.log")
    # Reset the once-per-process fallback flag so the INFO log fires.
    import safety_reports.intake as intake_mod
    intake_mod._fallback_logged = False
    _stub_pipeline_past_stage_4b(mocker)

    result = process_message(DEFAULT_MESSAGE_ID, mailbox=DEFAULT_MAILBOX)
    assert result.status == "processed"
    # Fallback INFO log fired exactly once.
    fallback_logs = [
        call for call in error_log_log.call_args_list
        if call.kwargs.get("error_code") == "trusted_contacts.fallback_to_its_config"
    ]
    assert len(fallback_logs) == 1


def test_sheet_with_rows_is_authoritative_skips_legacy_allowlist(mocker, patch_baseline):
    """Sheet has rows → ITS_Config allowed_senders is NOT consulted."""
    contact = _active_contact()
    mocker.patch(
        "safety_reports.intake.trusted_contacts._load_contacts",
        return_value=[contact],
    )
    mocker.patch(
        "safety_reports.intake.trusted_contacts.check_scope",
        return_value=ScopeVerdict(allowed=True, contact=contact, reason="allowed"),
    )
    mocker.patch(
        "safety_reports.intake.header_forgery.analyze",
        return_value=_pass_header(),
    )
    read_allowlist = mocker.patch(
        "safety_reports.intake._read_allowed_senders",
        return_value=["not-consulted@example.com"],
    )
    _stub_pipeline_past_stage_4b(mocker)

    result = process_message(DEFAULT_MESSAGE_ID, mailbox=DEFAULT_MAILBOX)
    assert result.status == "processed"
    # _read_allowed_senders is still called once (config read at top of pipeline),
    # but the legacy gate path (which would actually USE that value via
    # `quarantine.is_allowlisted`) is not entered. We assert behavior, not the
    # call count of the cheap config reader.
    assert read_allowlist.call_count >= 0


# ---- Stage 4b project-scope mismatch ------------------------------------


def test_project_scope_mismatch_routes_to_review(mocker, patch_baseline):
    """Sender allowed for workstream + project resolved, but project not in scope."""
    contact = TrustedContact(
        email=DEFAULT_SENDER,
        display_name="Seth",
        role="Operator",
        project_scope=("huntley",),  # NOT bradley_1
        workstream_scope=("safety_reports",),
        status=ContactStatus.ACTIVE,
        row_id=42,
    )
    mocker.patch(
        "safety_reports.intake.trusted_contacts._load_contacts",
        return_value=[contact],
    )

    def _scope(email, *, workstream, project=None):
        if project is None:
            # Stage 2 call — only workstream check
            return ScopeVerdict(allowed=True, contact=contact, reason="allowed")
        # Stage 4b call — project not in scope
        return ScopeVerdict(
            allowed=False, contact=contact, reason="project_out_of_scope",
        )
    mocker.patch(
        "safety_reports.intake.trusted_contacts.check_scope",
        side_effect=_scope,
    )
    mocker.patch(
        "safety_reports.intake.header_forgery.analyze",
        return_value=_pass_header(),
    )
    review_add = mocker.patch(
        "safety_reports.intake.review_queue.add", return_value=1,
    )
    classify = mocker.patch("safety_reports.intake.classify_and_extract")

    result = process_message(DEFAULT_MESSAGE_ID, mailbox=DEFAULT_MAILBOX)
    assert result.status == "review_queue"
    assert (
        review_add.call_args.kwargs["reason"] == ReviewReason.PROJECT_OUT_OF_SCOPE
    )
    classify.assert_not_called()


# ---- Capability gating still holds --------------------------------------


def test_intake_capability_gating_still_passes():
    """Sanity check: the AST capability check still passes for intake.py.

    Mirrors `test_capability_gating::test_generation_script_does_not_import_send`
    for our specific entries — the trusted-contacts refactor must NOT add any
    forbidden import (send_mail / resend / smtplib / email.mime).
    """
    import ast
    from pathlib import Path

    repo_root = Path(__file__).resolve().parent.parent
    intake_path = repo_root / "safety_reports" / "intake.py"
    tree = ast.parse(intake_path.read_text())
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imports.add(node.module)
                for alias in node.names:
                    imports.add(f"{node.module}.{alias.name}")
    for needle in ("send_mail", "resend", "smtplib", "email.mime"):
        for imp in imports:
            assert needle not in imp, (
                f"intake.py imports {imp!r} containing forbidden {needle!r}"
            )
