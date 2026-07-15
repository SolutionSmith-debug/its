"""Unit tests for subcontracts/subcontract_send_poll.py — the SC-S4 subcontract send dispatcher.

The dispatch body lives in `safety_reports/send_poll_core.py` (parameterized by `DaemonConfig`)
and is exhaustively tested by tests/test_send_poll_core.py + tests/test_weekly_send_poll.py.
These tests pin the thin subcontract entry's BINDING — it polls the Subcontract_Pending_Review
sheet, gates F22 against the ITS — Subcontracts workspace, and dispatches through
`subcontract_send.send_one_row` — plus the no-double-send SENDING exclusion and the fail-safe
dark default.

Data-plane mocks target `send_poll_core.*`; the heartbeat / watchdog / stamp / window SEAMS
stay patched on the entry (the core resolves them by injection from the entry).
"""
from __future__ import annotations

from typing import Any

import pytest

from safety_reports import send_poll_core
from safety_reports.weekly_send import SendResult
from shared import sheet_ids
from shared.approval_verification import ApprovalVerdict, VerdictReason
from subcontracts import subcontract_review, subcontract_send_poll


def _row(
    *, row_id: int, send_now: bool = True, scheduled: bool = False,
    send_status: str = subcontract_review.STATUS_PENDING,
    notes: str = "sc_id=7; sc_number=2026.001.OR.0.0",
) -> dict[str, Any]:
    return {
        "_row_id": row_id,
        subcontract_review.COL_JOB_PROJECT: "2026.001 — Sunrise Solar",
        subcontract_review.COL_JOB_ID: "SUB-000001",
        subcontract_review.COL_WEEK_OF: "2026-07-09",
        subcontract_review.COL_SEND_NOW: send_now,
        subcontract_review.COL_APPROVE_SCHEDULED: scheduled,
        subcontract_review.COL_SEND_STATUS: send_status,
        subcontract_review.COL_NOTES: notes,
    }


def _get_setting_polling_on(key: str, workstream: str | None = None) -> str:
    """get_setting stub: enable the polling gate, fall back (NotFound) on every other key.

    Mirrors a live tenant where only ``subcontracts.subcontract_send.polling_enabled=true`` is seeded.
    """
    if key == subcontract_send_poll.CFG_POLLING_ENABLED:
        return "true"
    raise send_poll_core.smartsheet_client.SmartsheetNotFoundError(f"no stub for {key}")


@pytest.fixture
def _patch_all(mocker):
    return {
        "get_rows": mocker.patch("safety_reports.send_poll_core.smartsheet_client.get_rows", return_value=[]),
        "send_one_row": mocker.patch(
            "subcontracts.subcontract_send.send_one_row",  # CONFIG.send_fn late-binds here
            return_value=SendResult(status="sent", row_id=0, project_name="Sunrise Solar"),
        ),
        # polling_enabled DEFAULTS to False (fail-safe), so the dispatch tests must explicitly
        # enable polling; every OTHER config key still falls back (NotFound).
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
        "hb": mocker.patch.object(subcontract_send_poll, "_write_heartbeat"),
        "hb_row": mocker.patch.object(subcontract_send_poll, "_write_heartbeat_row"),
        "marker": mocker.patch.object(subcontract_send_poll, "_write_watchdog_marker"),
        "stamp": mocker.patch.object(subcontract_send_poll, "_stamp_approval"),
    }


def test_config_binds_the_subcontract_sheet_and_workspace_not_safety_progress_or_po():
    cfg = subcontract_send_poll.CONFIG
    assert cfg.config_workstream == "subcontracts"
    # The load-bearing cross-wiring guards: poll the subcontract review sheet, gate F22 against
    # the ITS — Subcontracts workspace — never safety's / progress's / PO's.
    assert cfg.poll_sheet_id == sheet_ids.SHEET_SUBCONTRACT_PENDING_REVIEW
    assert cfg.f22_workspace_id == sheet_ids.WORKSPACE_SUBCONTRACTS
    assert cfg.poll_sheet_id != sheet_ids.SHEET_PO_PENDING_REVIEW
    assert cfg.poll_sheet_id != sheet_ids.SHEET_WSR_HUMAN_REVIEW
    assert cfg.f22_workspace_id != sheet_ids.WORKSPACE_PURCHASE_ORDERS
    assert cfg.f22_workspace_id != sheet_ids.WORKSPACE_SAFETY_PORTAL
    # SENDING is excluded from dispatch (no double-send).
    assert subcontract_review.STATUS_SENDING not in cfg.dispatch_statuses
    assert cfg.dispatch_statuses == frozenset({subcontract_review.STATUS_PENDING, subcontract_review.STATUS_FAILED})


def test_default_polling_enabled_is_false_fail_safe():
    # HOUSE_REFLEXES §5: a send daemon's row-absent default must be dark (False), never
    # fail-open to SENDING. Pins the constant AND its propagation into the DaemonConfig.
    assert subcontract_send_poll.DEFAULT_POLLING_ENABLED is False
    assert subcontract_send_poll.CONFIG.default_polling_enabled is False


def test_missing_polling_config_skips_the_cycle_without_sending(_patch_all):
    # PROVE-IT-BITES: with NO polling_enabled row (get_setting raises NotFound for every key),
    # the cycle short-circuits fail-safe (skipped_disabled) instead of dispatching an approved row.
    _patch_all["get_setting"].side_effect = (
        send_poll_core.smartsheet_client.SmartsheetNotFoundError("row absent")
    )
    _patch_all["get_rows"].return_value = [_row(row_id=91)]
    stats = subcontract_send_poll.poll_once()
    assert stats.skipped_disabled is True
    assert stats.dispatched == 0 and stats.sent == 0
    _patch_all["send_one_row"].assert_not_called()


def test_poll_dispatches_an_approved_send_now_row_through_subcontract_send(_patch_all):
    _patch_all["get_rows"].return_value = [_row(row_id=91)]
    stats = subcontract_send_poll.poll_once()
    assert stats.dispatched == 1 and stats.sent == 1
    # The verified approver was stamped, then subcontract_send.send_one_row dispatched.
    _patch_all["stamp"].assert_called_once()
    _patch_all["send_one_row"].assert_called_once_with(91)


def test_poll_blocks_when_approval_unverified(_patch_all):
    _patch_all["get_rows"].return_value = [_row(row_id=91)]
    _patch_all["verify_approval"].return_value = ApprovalVerdict(
        verified=False, reason=VerdictReason.UNAUTHORIZED_ACTOR, actor="stranger@evil.example",
    )
    stats = subcontract_send_poll.poll_once()
    assert stats.blocked == 1 and stats.sent == 0
    _patch_all["send_one_row"].assert_not_called()


def test_sending_status_row_is_never_dispatched(_patch_all):
    # A row stuck in SENDING (a post-send stamp failure) must NOT be re-dispatched.
    _patch_all["get_rows"].return_value = [_row(row_id=91, send_status=subcontract_review.STATUS_SENDING)]
    stats = subcontract_send_poll.poll_once()
    assert stats.dispatched == 0
    _patch_all["send_one_row"].assert_not_called()


def test_filter_dispatch_candidates_excludes_sending():
    rows = [
        _row(row_id=1, send_status=subcontract_review.STATUS_PENDING),
        _row(row_id=2, send_status=subcontract_review.STATUS_SENDING),
        _row(row_id=3, send_status=subcontract_review.STATUS_FAILED),
    ]
    kept = {r["_row_id"] for r in subcontract_send_poll._filter_dispatch_candidates(rows)}
    assert kept == {1, 3}
