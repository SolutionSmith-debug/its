"""Unit tests for po_materials/po_send_poll.py — the S5b PO send dispatcher.

The dispatch body lives in `safety_reports/send_poll_core.py` (parameterized by
`DaemonConfig`) and is exhaustively tested by tests/test_send_poll_core.py +
tests/test_weekly_send_poll.py. These tests pin the thin PO entry's BINDING — it polls the
PO_Pending_Review sheet, gates F22 against the ITS — Purchase Orders workspace, and
dispatches through `po_send.send_one_row` — plus the no-double-send SENDING exclusion.

Data-plane mocks target `send_poll_core.*`; the heartbeat / watchdog / stamp / window
SEAMS stay patched on the entry (the core resolves them by injection from the entry).
"""
from __future__ import annotations

from typing import Any

import pytest

from po_materials import po_review, po_send_poll
from safety_reports import send_poll_core
from safety_reports.weekly_send import SendResult
from shared import sheet_ids
from shared.approval_verification import ApprovalVerdict, VerdictReason


def _row(
    *, row_id: int, send_now: bool = True, scheduled: bool = False,
    send_status: str = po_review.STATUS_PENDING, notes: str = "po_id=7; po_number=2026.001.2.0.0",
) -> dict[str, Any]:
    return {
        "_row_id": row_id,
        po_review.COL_JOB_PROJECT: "2026.001 — Sunrise Solar",
        po_review.COL_JOB_ID: "VEN-000001",
        po_review.COL_WEEK_OF: "2026-07-09",
        po_review.COL_SEND_NOW: send_now,
        po_review.COL_APPROVE_SCHEDULED: scheduled,
        po_review.COL_SEND_STATUS: send_status,
        po_review.COL_NOTES: notes,
    }


@pytest.fixture
def _patch_all(mocker):
    return {
        "get_rows": mocker.patch("safety_reports.send_poll_core.smartsheet_client.get_rows", return_value=[]),
        "send_one_row": mocker.patch(
            "po_materials.po_send.send_one_row",  # CONFIG.send_fn late-binds here
            return_value=SendResult(status="sent", row_id=0, project_name="Sunrise Solar"),
        ),
        "get_setting": mocker.patch(
            "safety_reports.send_poll_core.smartsheet_client.get_setting",
            side_effect=send_poll_core.smartsheet_client.SmartsheetNotFoundError("default test stub"),
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
        "hb": mocker.patch.object(po_send_poll, "_write_heartbeat"),
        "hb_row": mocker.patch.object(po_send_poll, "_write_heartbeat_row"),
        "marker": mocker.patch.object(po_send_poll, "_write_watchdog_marker"),
        "stamp": mocker.patch.object(po_send_poll, "_stamp_approval"),
    }


def test_config_binds_the_po_sheet_and_workspace_not_safety_or_progress():
    cfg = po_send_poll.CONFIG
    assert cfg.config_workstream == "po_materials"
    # The load-bearing cross-wiring guards: poll the PO review sheet, gate F22 against
    # the ITS — Purchase Orders workspace — never safety's or progress's.
    assert cfg.poll_sheet_id == sheet_ids.SHEET_PO_PENDING_REVIEW
    assert cfg.f22_workspace_id == sheet_ids.WORKSPACE_PURCHASE_ORDERS
    assert cfg.poll_sheet_id != sheet_ids.SHEET_WSR_HUMAN_REVIEW
    assert cfg.poll_sheet_id != sheet_ids.SHEET_WPR_HUMAN_REVIEW
    assert cfg.f22_workspace_id != sheet_ids.WORKSPACE_SAFETY_PORTAL
    assert cfg.f22_workspace_id != sheet_ids.WORKSPACE_PROGRESS_REPORTING
    # SENDING is excluded from dispatch (no double-send).
    assert po_review.STATUS_SENDING not in cfg.dispatch_statuses
    assert cfg.dispatch_statuses == frozenset({po_review.STATUS_PENDING, po_review.STATUS_FAILED})


def test_poll_dispatches_an_approved_send_now_row_through_po_send(_patch_all):
    _patch_all["get_rows"].return_value = [_row(row_id=90)]
    stats = po_send_poll.poll_once()
    assert stats.dispatched == 1 and stats.sent == 1
    # The verified approver was stamped, then po_send.send_one_row dispatched.
    _patch_all["stamp"].assert_called_once()
    _patch_all["send_one_row"].assert_called_once_with(90)


def test_poll_blocks_when_approval_unverified(_patch_all):
    _patch_all["get_rows"].return_value = [_row(row_id=90)]
    _patch_all["verify_approval"].return_value = ApprovalVerdict(
        verified=False, reason=VerdictReason.UNAUTHORIZED_ACTOR, actor="stranger@evil.example",
    )
    stats = po_send_poll.poll_once()
    assert stats.blocked == 1 and stats.sent == 0
    _patch_all["send_one_row"].assert_not_called()


def test_sending_status_row_is_never_dispatched(_patch_all):
    # A row stuck in SENDING (a post-send stamp failure) must NOT be re-dispatched.
    _patch_all["get_rows"].return_value = [_row(row_id=90, send_status=po_review.STATUS_SENDING)]
    stats = po_send_poll.poll_once()
    assert stats.dispatched == 0
    _patch_all["send_one_row"].assert_not_called()


def test_filter_dispatch_candidates_excludes_sending():
    rows = [
        _row(row_id=1, send_status=po_review.STATUS_PENDING),
        _row(row_id=2, send_status=po_review.STATUS_SENDING),
        _row(row_id=3, send_status=po_review.STATUS_FAILED),
    ]
    kept = {r["_row_id"] for r in po_send_poll._filter_dispatch_candidates(rows)}
    assert kept == {1, 3}
