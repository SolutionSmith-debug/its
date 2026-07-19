"""Unit tests for po_materials/rfq_send_poll.py — the ADR-0004 R3 RFQ send dispatcher.

The dispatch body lives in `safety_reports/send_poll_core.py` (parameterized by
`DaemonConfig`) and is exhaustively tested by tests/test_send_poll_core.py +
tests/test_weekly_send_poll.py. These tests pin the thin RFQ entry's BINDING — it polls the
RFQ_Pending_Review sheet, gates F22 against the ITS — Purchase Orders workspace (the SAME
procurement approver set as POs), dispatches through `rfq_send.send_one_row`, ships DARK,
and (unusually) constructs while its review sheet is still a 0 placeholder
(`allow_placeholder_sheet=True`, builder-precedes-seed).

Data-plane mocks target `send_poll_core.*`; the heartbeat / watchdog / stamp / window SEAMS
stay patched on the entry (the core resolves them by injection from the entry).
"""
from __future__ import annotations

from typing import Any

import pytest

from po_materials import rfq_review, rfq_send_poll
from safety_reports import send_poll_core
from safety_reports.weekly_send import SendResult
from shared import sheet_ids
from shared.approval_verification import ApprovalVerdict, VerdictReason


def _row(
    *, row_id: int, send_now: bool = True, scheduled: bool = False,
    send_status: str = rfq_review.STATUS_PENDING,
    notes: str = "rfq_id=5; rfq_number=RFQ-2026.001-001; vendor_key=VEN-000001",
) -> dict[str, Any]:
    return {
        "_row_id": row_id,
        rfq_review.COL_JOB_PROJECT: "2026.001 — Sunrise Solar",
        rfq_review.COL_JOB_ID: "VEN-000001",
        rfq_review.COL_WEEK_OF: "2026-07-09",
        rfq_review.COL_SEND_NOW: send_now,
        rfq_review.COL_APPROVE_SCHEDULED: scheduled,
        rfq_review.COL_SEND_STATUS: send_status,
        rfq_review.COL_NOTES: notes,
    }


def _get_setting_polling_on(key: str, workstream: str | None = None) -> str:
    if key == rfq_send_poll.CFG_POLLING_ENABLED:
        return "true"
    raise send_poll_core.smartsheet_client.SmartsheetNotFoundError(f"no stub for {key}")


@pytest.fixture
def _patch_all(mocker):
    return {
        "get_rows": mocker.patch("safety_reports.send_poll_core.smartsheet_client.get_rows", return_value=[]),
        "send_one_row": mocker.patch(
            "po_materials.rfq_send.send_one_row",  # CONFIG.send_fn late-binds here
            return_value=SendResult(status="sent", row_id=0, project_name="Sunrise Solar"),
        ),
        "get_setting": mocker.patch(
            "safety_reports.send_poll_core.smartsheet_client.get_setting",
            side_effect=_get_setting_polling_on,
        ),
        "workspace_shares": mocker.patch(
            "safety_reports.send_poll_core.smartsheet_client.list_workspace_share_emails",
            return_value=frozenset({"alex@evergreenmirror.com"}),
        ),
        "verify_approval": mocker.patch(
            "safety_reports.send_poll_core.approval_verification.verify_approval",
            return_value=ApprovalVerdict(
                verified=True, reason=VerdictReason.AUTHORIZED, actor="alex@evergreenmirror.com",
            ),
        ),
        "hb": mocker.patch.object(rfq_send_poll, "_write_heartbeat"),
        "hb_row": mocker.patch.object(rfq_send_poll, "_write_heartbeat_row"),
        "marker": mocker.patch.object(rfq_send_poll, "_write_watchdog_marker"),
        "stamp": mocker.patch.object(rfq_send_poll, "_stamp_approval"),
    }


def test_config_binds_the_rfq_sheet_and_po_workspace():
    cfg = rfq_send_poll.CONFIG
    assert cfg.config_workstream == "po_materials"
    # Poll the RFQ review sheet (a 0 placeholder until built), gate F22 against the ITS —
    # Purchase Orders workspace (the SAME procurement approvers as POs).
    assert cfg.poll_sheet_id == sheet_ids.SHEET_RFQ_PENDING_REVIEW
    assert cfg.f22_workspace_id == sheet_ids.WORKSPACE_PURCHASE_ORDERS
    assert cfg.f22_workspace_id != sheet_ids.WORKSPACE_SAFETY_PORTAL
    assert cfg.f22_workspace_id != sheet_ids.WORKSPACE_PROGRESS_REPORTING
    # Dark-ship: constructs while the review sheet is still a 0 placeholder.
    assert cfg.allow_placeholder_sheet is True
    # SENDING is excluded from dispatch (no double-send).
    assert rfq_review.STATUS_SENDING not in cfg.dispatch_statuses
    assert cfg.dispatch_statuses == frozenset({rfq_review.STATUS_PENDING, rfq_review.STATUS_FAILED})


def test_default_polling_enabled_is_false_fail_safe():
    # CO-1 / HOUSE_REFLEXES §5: a send daemon's row-absent default must be dark (False).
    assert rfq_send_poll.DEFAULT_POLLING_ENABLED is False
    assert rfq_send_poll.CONFIG.default_polling_enabled is False


def test_gate_false_is_a_noop_no_send(_patch_all):
    # PROVE-IT-BITES: with the send gate off (the shipped dark default), the cycle
    # short-circuits (skipped_disabled) and dispatches nothing, even an approved send_now row.
    _patch_all["get_setting"].side_effect = (
        send_poll_core.smartsheet_client.SmartsheetNotFoundError("row absent")
    )
    _patch_all["get_rows"].return_value = [_row(row_id=90)]
    stats = rfq_send_poll.poll_once()
    assert stats.skipped_disabled is True
    assert stats.dispatched == 0 and stats.sent == 0
    _patch_all["send_one_row"].assert_not_called()


def test_poll_dispatches_an_approved_row_through_rfq_send(_patch_all):
    _patch_all["get_rows"].return_value = [_row(row_id=90)]
    stats = rfq_send_poll.poll_once()
    assert stats.dispatched == 1 and stats.sent == 1
    _patch_all["stamp"].assert_called_once()
    _patch_all["send_one_row"].assert_called_once_with(90)


def test_poll_blocks_when_approval_unverified(_patch_all):
    _patch_all["get_rows"].return_value = [_row(row_id=90)]
    _patch_all["verify_approval"].return_value = ApprovalVerdict(
        verified=False, reason=VerdictReason.UNAUTHORIZED_ACTOR, actor="stranger@evil.example",
    )
    stats = rfq_send_poll.poll_once()
    assert stats.blocked == 1 and stats.sent == 0
    _patch_all["send_one_row"].assert_not_called()


def test_sending_status_row_is_never_dispatched(_patch_all):
    _patch_all["get_rows"].return_value = [_row(row_id=90, send_status=rfq_review.STATUS_SENDING)]
    stats = rfq_send_poll.poll_once()
    assert stats.dispatched == 0
    _patch_all["send_one_row"].assert_not_called()
