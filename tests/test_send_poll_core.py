"""Unit tests for safety_reports/send_poll_core.py — the parameterized dispatch
core (P1c). Covers the no-default contamination gate + the positive proof that the
core dispatches against the CONFIG's ids/columns (never a hardcoded safety value).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from safety_reports import send_poll_core, weekly_send, weekly_send_poll, wsr_review
from safety_reports.send_poll_core import DaemonConfig
from safety_reports.weekly_send import SendResult
from shared import sheet_ids
from shared.approval_verification import ApprovalVerdict, VerdictReason


def _base_kwargs() -> dict:
    """Minimal VALID DaemonConfig kwargs (progress-shaped ids); override per test."""
    return dict(
        script_name="test.send_poll",
        config_workstream="test_ws",
        daemon_name="test.send_poll",
        lock_path=Path("/tmp/_t.lock"),
        watchdog_marker_dir=Path("/tmp/_wd"),
        watchdog_job_slug="test_slug",
        cfg_polling_enabled="test.polling_enabled",
        default_polling_enabled=True,
        cfg_scheduled_send_local="test.scheduled_send_local",
        default_scheduled_send_local="MON 07:00",
        send_tz="America/Los_Angeles",
        poll_sheet_id=9999,
        f22_workspace_id=8888,
        col_send_now="P Send Now",
        col_approve_scheduled="P Approve",
        col_send_status="P Status",
        col_notes="P Notes",
        col_approved_by="P Approved By",
        col_approved_at="P Approved At",
        dispatch_statuses=frozenset({"PENDING", "FAILED"}),
        status_pending="PENDING",
        status_failed="FAILED",
        max_send_retries=3,
        parse_retry_count=lambda n: 0,
        to_datetime=lambda d: "2026-06-01",
        wake_reasons=frozenset({VerdictReason.UNAUTHORIZED_ACTOR}),
        send_fn=lambda row_id: SendResult(status="sent", row_id=row_id),
    )


def _cfg(**overrides) -> DaemonConfig:
    return DaemonConfig(**{**_base_kwargs(), **overrides})


# ---- no-default contamination gate ---------------------------------------


def test_daemonconfig_rejects_all_missing():
    with pytest.raises(TypeError):
        DaemonConfig()  # type: ignore[call-arg]


@pytest.mark.parametrize("missing", ["send_fn", "poll_sheet_id", "f22_workspace_id", "col_send_now"])
def test_daemonconfig_rejects_a_missing_required_field(missing):
    kw = _base_kwargs()
    del kw[missing]
    with pytest.raises(TypeError):
        DaemonConfig(**kw)  # type: ignore[arg-type]


def test_post_init_rejects_non_callable_send_fn():
    with pytest.raises(TypeError):
        _cfg(send_fn="not-callable")  # type: ignore[arg-type]


@pytest.mark.parametrize("bad_id", [0, -1])
def test_post_init_rejects_non_positive_sheet_or_workspace(bad_id):
    with pytest.raises(ValueError):
        _cfg(poll_sheet_id=bad_id)
    with pytest.raises(ValueError):
        _cfg(f22_workspace_id=bad_id)


def test_post_init_rejects_sending_in_dispatch_statuses():
    # The load-bearing no-double-send exclusion — SENDING must never dispatch.
    with pytest.raises(ValueError):
        _cfg(dispatch_statuses=frozenset({"PENDING", "SENDING"}))


# ---- positive contamination proof ----------------------------------------


def test_core_dispatches_against_config_ids_not_a_safety_default(mocker):
    """A progress-shaped config drives the WHOLE cycle against ITS ids/columns —
    no hardcoded safety sheet/workspace leaks in."""
    sent: list[int] = []
    cfg = _cfg(send_fn=lambda rid: (sent.append(rid), SendResult(status="sent", row_id=rid))[1])

    get_rows = mocker.patch(
        "safety_reports.send_poll_core.smartsheet_client.get_rows",
        return_value=[{"_row_id": 1, "P Send Now": True, "P Status": "PENDING"}],
    )
    shares = mocker.patch(
        "safety_reports.send_poll_core.smartsheet_client.list_workspace_share_emails",
        return_value=frozenset({"a@b.com"}),
    )
    mocker.patch(
        "safety_reports.send_poll_core.smartsheet_client.get_setting",
        side_effect=send_poll_core.smartsheet_client.SmartsheetNotFoundError("stub"),
    )
    verify = mocker.patch(
        "safety_reports.send_poll_core.approval_verification.verify_approval",
        return_value=ApprovalVerdict(verified=True, reason=VerdictReason.AUTHORIZED, actor="a@b.com"),
    )
    mocker.patch("safety_reports.send_poll_core.error_log.log")

    result = send_poll_core.poll_inside_lock(
        cfg,
        write_liveness=lambda: None,
        write_row=lambda **k: None,
        write_watchdog_marker=lambda: None,
        stamp_approval=lambda rid, v: None,
        is_scheduled_window=lambda now, spec: True,
    )

    get_rows.assert_called_once_with(9999)              # the PROGRESS sheet, not WSR
    shares.assert_called_once_with(8888)                # the PROGRESS workspace, not WORKSPACE_SAFETY_PORTAL
    assert verify.call_args.args[0] == 9999             # F22 against the progress sheet
    assert verify.call_args.args[2] == "P Send Now"     # the progress column
    assert sent == [1] and result.sent == 1


# ---- safety binding sanity (the entry's CONFIG) --------------------------


def test_safety_config_binds_safety_values():
    c = weekly_send_poll.CONFIG
    assert c.poll_sheet_id == sheet_ids.SHEET_WSR_HUMAN_REVIEW
    assert c.f22_workspace_id == sheet_ids.WORKSPACE_SAFETY_PORTAL
    assert c.col_send_now == wsr_review.COL_SEND_NOW
    assert "SENDING" not in {s.upper() for s in c.dispatch_statuses}
    assert c.dispatch_statuses == frozenset({weekly_send.STATUS_PENDING, weekly_send.STATUS_FAILED})
    # send_fn late-binds weekly_send.send_one_row (patchable) — call shape (row_id, cfg).
    rec: list = []
    import safety_reports.weekly_send as ws

    def _capture(rid, cfg):
        rec.append((rid, cfg))
        return SendResult(status="sent", row_id=rid)

    orig = ws.send_one_row
    ws.send_one_row = _capture
    try:
        c.send_fn(42)
    finally:
        ws.send_one_row = orig
    assert rec == [(42, weekly_send.CONFIG)]


# ---- config-read transient fence (error-hygiene 2026-07-19) ---------------


def test_cycle_config_read_transient_error_falls_open_with_warn(mocker):
    """A generic SmartsheetError from get_setting (read-timeout / 5xx) during the cycle's
    scheduled-window config read must NOT escape to @its_error_log as a spurious CRITICAL:
    WARN `config_read_error` + the fallback spec, same disposition as the circuit-open
    branch. (_load_authorized_approvers — the F22 security gate — stays fail-CLOSED and is
    untouched by this fence.)"""
    cfg = _cfg()
    mocker.patch(
        "safety_reports.send_poll_core.smartsheet_client.get_rows",
        # Approve-scheduled checked (a dispatch candidate), no Send Now → the scheduled
        # path consults is_scheduled_window with the RESOLVED spec.
        return_value=[{"_row_id": 1, "P Approve": True, "P Status": "PENDING"}],
    )
    mocker.patch(
        "safety_reports.send_poll_core.smartsheet_client.list_workspace_share_emails",
        return_value=frozenset({"a@b.com"}),
    )
    mocker.patch(
        "safety_reports.send_poll_core.smartsheet_client.get_setting",
        side_effect=send_poll_core.smartsheet_client.SmartsheetError("read timeout"),
    )
    log = mocker.patch("safety_reports.send_poll_core.error_log.log")

    seen_specs: list[str] = []

    def _window(now, spec):
        seen_specs.append(spec)
        return False  # outside the window → row skipped, cycle completes

    result = send_poll_core.poll_inside_lock(
        cfg,
        write_liveness=lambda: None,
        write_row=lambda **k: None,
        write_watchdog_marker=lambda: None,
        stamp_approval=lambda rid, v: None,
        is_scheduled_window=_window,
    )  # must not raise

    codes = [kw.get("error_code") for _, kw in log.call_args_list]
    assert "config_read_error" in codes
    warn_calls = [
        (a, kw) for a, kw in log.call_args_list if kw.get("error_code") == "config_read_error"
    ]
    assert all(a[0] == send_poll_core.Severity.WARN for a, _ in warn_calls)
    assert seen_specs == [cfg.default_scheduled_send_local]  # fallback used
    assert result.skipped == 1 and result.errors == 0
