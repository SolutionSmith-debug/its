"""Unit tests for safety_reports/portal_poll.py.

All external services mocked. Exercises the verify → dispatch → receipt cycle,
the fail-closed credential gate, the HMAC-reject path, and the seen-set
fast-paths. Structure mirrors tests/test_weekly_send_poll.py.
"""
from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any

import pytest

from safety_reports import portal_poll
from safety_reports.intake import ProcessResult
from safety_reports.portal_poll import DAEMON_NAME, _poll_inside_lock, poll_once


def _row(uuid: str = "u1") -> dict[str, Any]:
    return {
        "submission_uuid": uuid,
        "job_id": "JOB-1",
        "form_code": "jha-v1",
        "work_date": "2026-06-05",
        "payload_json": '{"work_location": "A"}',
        "amends_uuid": None,
        "hmac": "deadbeef",
        "created_at": 1_717_600_000,
    }


def _processed(uuid: str = "u1", link: str = "https://app.box.com/file/f9") -> ProcessResult:
    return ProcessResult(status="processed", message_id=uuid, correlation_id="c", box_link=link)


@pytest.fixture
def _patch_all(mocker):
    """Default mock surface covering the whole poll cycle."""
    return {
        "creds": mocker.patch.object(
            portal_poll, "_resolve_credentials",
            return_value=portal_poll._PortalCreds(
                base_url="https://portal.example.com", bearer="bearer", secret="secret",
            ),
        ),
        "get_pending": mocker.patch.object(
            portal_poll.portal_client, "get_pending", return_value=[]
        ),
        "mark_filed": mocker.patch.object(
            portal_poll.portal_client, "mark_filed", return_value=True
        ),
        "process": mocker.patch.object(
            portal_poll.intake, "process_portal_submission", return_value=_processed()
        ),
        "verify": mocker.patch.object(portal_poll, "_verify_row_hmac", return_value=True),
        "load_seen": mocker.patch.object(portal_poll, "_load_seen", return_value={}),
        "persist_seen": mocker.patch.object(portal_poll, "_persist_seen"),
        "hb": mocker.patch.object(portal_poll, "_write_heartbeat"),
        "hb_row": mocker.patch.object(portal_poll, "_write_heartbeat_row"),
        "wd": mocker.patch.object(portal_poll, "_write_watchdog_marker"),
        # Consecutive-fetch-failure counter (sustained-outage escalation) — mocked so tests
        # neither touch real state nor depend on the on-disk counter; default count=1 (first
        # failure → ERROR). A test raises the return to the threshold to exercise CRITICAL.
        "rec_fail": mocker.patch.object(portal_poll, "_record_fetch_failure", return_value=1),
        "reset_fail": mocker.patch.object(portal_poll, "_reset_fetch_failures"),
        "is_open": mocker.patch.object(portal_poll.circuit_breaker, "is_open", return_value=False),
        "log": mocker.patch.object(portal_poll.error_log, "log"),
        "review": mocker.patch.object(portal_poll.review_queue, "add"),
        "anomaly": mocker.patch.object(portal_poll.anomaly_logger, "check"),
        # Job-sync push leg: default to an empty set so the push short-circuits and
        # the existing cycle tests neither hit Smartsheet nor POST anything.
        "list_all_jobs": mocker.patch.object(
            portal_poll.active_jobs, "list_all_jobs", return_value=[]
        ),
        "push_jobs": mocker.patch.object(
            portal_poll.portal_client, "push_jobs",
            return_value={"ok": True, "upserted": 0, "deactivated": 0},
        ),
    }


# ---- happy path + receipt ------------------------------------------------


def test_empty_queue_ok_heartbeat(_patch_all):
    result = _poll_inside_lock()
    assert result.scanned == 0 and result.filed == 0
    _patch_all["hb"].assert_called_once()
    assert _patch_all["hb_row"].call_args.kwargs["status"] == "OK"


def test_verified_processed_row_is_filed_and_receipted(_patch_all):
    _patch_all["get_pending"].return_value = [_row("u1")]
    result = _poll_inside_lock()
    assert result.filed == 1 and result.scanned == 1
    _patch_all["verify"].assert_called_once()
    _patch_all["process"].assert_called_once()
    _patch_all["mark_filed"].assert_called_once_with(
        "https://portal.example.com", "bearer",
        submission_uuid="u1", box_link="https://app.box.com/file/f9",
    )
    # Recorded as filed in the seen-set for a future lost-receipt re-post.
    persisted = _patch_all["persist_seen"].call_args.args[0]
    assert persisted["u1"] == {"status": "filed", "box_link": "https://app.box.com/file/f9"}


def test_review_queue_result_is_drained_and_counted(_patch_all):
    _patch_all["get_pending"].return_value = [_row("u1")]
    _patch_all["process"].return_value = ProcessResult(
        status="review_queue", message_id="u1", correlation_id="c", box_link=None
    )
    result = _poll_inside_lock()
    assert result.reviewed == 1 and result.filed == 0
    # Drained with an empty link (the Review Queue entry is the durable record).
    _patch_all["mark_filed"].assert_called_once_with(
        "https://portal.example.com", "bearer", submission_uuid="u1", box_link="",
    )
    assert _patch_all["hb_row"].call_args.kwargs["status"] == "WARN"


def test_already_filed_result_is_receipted(_patch_all):
    _patch_all["get_pending"].return_value = [_row("u1")]
    _patch_all["process"].return_value = ProcessResult(
        status="already_filed", message_id="u1", correlation_id="c",
        box_link="https://app.box.com/file/old",
    )
    result = _poll_inside_lock()
    assert result.filed == 1
    assert _patch_all["mark_filed"].call_args.kwargs["box_link"] == "https://app.box.com/file/old"


# ---- HMAC reject (downgrade defense) -------------------------------------


def test_hmac_failure_rejects_without_dispatch_or_receipt(_patch_all):
    _patch_all["get_pending"].return_value = [_row("u1")]
    _patch_all["verify"].return_value = False
    result = _poll_inside_lock()
    assert result.rejected == 1 and result.filed == 0
    _patch_all["process"].assert_not_called()   # NEVER handed to intake
    _patch_all["mark_filed"].assert_not_called()  # NEVER drained (kept for forensics)
    _patch_all["review"].assert_called_once()     # flagged to Review Queue
    assert _patch_all["review"].call_args.kwargs["security_flag"] is True
    _patch_all["anomaly"].assert_called_once()    # tripwire fired
    # Recorded rejected so subsequent cycles don't re-flag (no 60s spam).
    persisted = _patch_all["persist_seen"].call_args.args[0]
    assert persisted["u1"] == {"status": "rejected"}
    assert _patch_all["hb_row"].call_args.kwargs["status"] == "WARN"


# ---- transient intake error → NOT drained --------------------------------


def test_intake_error_is_not_receipted(_patch_all):
    _patch_all["get_pending"].return_value = [_row("u1")]
    _patch_all["process"].return_value = ProcessResult(
        status="error", message_id="u1", correlation_id="c"
    )
    result = _poll_inside_lock()
    assert result.errors == 1 and result.filed == 0
    _patch_all["mark_filed"].assert_not_called()  # re-pull retries
    # NOT recorded in seen → re-processed next cycle.
    persisted = _patch_all["persist_seen"].call_args.args[0]
    assert "u1" not in persisted
    assert _patch_all["hb_row"].call_args.kwargs["status"] == "DEGRADED"


# ---- seen-set fast-paths -------------------------------------------------


def test_seen_filed_reposts_receipt_without_refiling(_patch_all):
    _patch_all["get_pending"].return_value = [_row("u1")]
    _patch_all["load_seen"].return_value = {
        "u1": {"status": "filed", "box_link": "https://app.box.com/file/keep"}
    }
    result = _poll_inside_lock()
    assert result.remarked == 1 and result.filed == 0
    _patch_all["verify"].assert_not_called()     # already verified before
    _patch_all["process"].assert_not_called()    # NOT re-filed
    _patch_all["mark_filed"].assert_called_once_with(
        "https://portal.example.com", "bearer",
        submission_uuid="u1", box_link="https://app.box.com/file/keep",
    )


def test_seen_rejected_is_skipped_silently(_patch_all):
    _patch_all["get_pending"].return_value = [_row("u1")]
    _patch_all["load_seen"].return_value = {"u1": {"status": "rejected"}}
    result = _poll_inside_lock()
    assert result.rejected == 0 and result.filed == 0  # not re-counted, not re-flagged
    _patch_all["verify"].assert_not_called()
    _patch_all["process"].assert_not_called()
    _patch_all["mark_filed"].assert_not_called()
    _patch_all["review"].assert_not_called()


# ---- per-row fence -------------------------------------------------------


def test_per_row_exception_does_not_kill_cycle(_patch_all):
    _patch_all["get_pending"].return_value = [_row("u1"), _row("u2")]
    _patch_all["process"].side_effect = [RuntimeError("boom"), _processed("u2")]
    result = _poll_inside_lock()
    assert result.errors == 1 and result.filed == 1  # u2 still processed
    assert _patch_all["hb_row"].call_args.kwargs["status"] == "DEGRADED"


def test_row_missing_uuid_is_flagged_not_dispatched(_patch_all):
    _patch_all["get_pending"].return_value = [_row("")]
    result = _poll_inside_lock()
    assert result.errors == 1
    _patch_all["process"].assert_not_called()


# ---- fail-closed credentials ---------------------------------------------


def test_missing_credentials_halts_without_polling(_patch_all):
    _patch_all["creds"].return_value = None
    result = _poll_inside_lock()
    assert result.halted_no_creds is True
    _patch_all["get_pending"].assert_not_called()  # FAIL-CLOSED: no poll
    assert _patch_all["hb_row"].call_args.kwargs["status"] == "ERROR"
    # A non-polling cycle must NOT fake the Check-C freshness marker (so a sustained no-creds
    # state surfaces via the staleness floor) — and missing creds won't self-heal → CRITICAL.
    _patch_all["wd"].assert_not_called()
    assert any(
        c.args and c.args[0] == portal_poll.Severity.CRITICAL
        and c.kwargs.get("error_code") == "portal_creds_missing"
        for c in _patch_all["log"].call_args_list
    )


# ---- pending fetch failure ------------------------------------------------


def test_pending_fetch_failure_writes_error_heartbeat(_patch_all):
    _patch_all["get_pending"].side_effect = portal_poll.portal_client.PortalTransportError("500")
    result = _poll_inside_lock()
    assert result.errors == 1
    assert _patch_all["hb_row"].call_args.kwargs["status"] == "ERROR"
    _patch_all["wd"].assert_not_called()  # failed cycle must NOT fake Check-C freshness
    _patch_all["rec_fail"].assert_called_once()  # consecutive-failure counter bumped
    # First failure (count=1 < threshold) → ERROR, not CRITICAL.
    assert any(
        c.args and c.args[0] == portal_poll.Severity.ERROR
        and c.kwargs.get("error_code") == "portal_pending_fetch_failed"
        for c in _patch_all["log"].call_args_list
    )


def test_sustained_fetch_failure_escalates_to_critical(_patch_all):
    _patch_all["get_pending"].side_effect = portal_poll.portal_client.PortalTransportError("500")
    _patch_all["rec_fail"].return_value = portal_poll.FETCH_FAIL_CRITICAL_THRESHOLD  # sustained
    result = _poll_inside_lock()
    assert result.errors == 1
    _patch_all["wd"].assert_not_called()
    assert any(
        c.args and c.args[0] == portal_poll.Severity.CRITICAL
        and c.kwargs.get("error_code") == "portal_pending_fetch_failed"
        for c in _patch_all["log"].call_args_list
    )


def test_pending_fetch_auth_failure_is_critical_immediately(_patch_all):
    # 401 = bad/rotated bearer → won't self-heal → CRITICAL on the FIRST failure (not via the
    # transient counter), and no faked Check-C marker.
    _patch_all["get_pending"].side_effect = portal_poll.portal_client.PortalAuthError("401")
    result = _poll_inside_lock()
    assert result.errors == 1
    _patch_all["wd"].assert_not_called()
    _patch_all["rec_fail"].assert_not_called()  # auth bypasses the transient counter
    assert any(
        c.args and c.args[0] == portal_poll.Severity.CRITICAL
        and c.kwargs.get("error_code") == "portal_pending_auth_failed"
        for c in _patch_all["log"].call_args_list
    )


# ---- job-sync push leg ----------------------------------------------------


def _job(job_id="JOB-000001", project_name="Bradley 1", *, is_active=True):
    return SimpleNamespace(job_id=job_id, project_name=project_name, is_active=is_active)


def test_job_sync_pushes_full_set_after_drain(_patch_all):
    _patch_all["list_all_jobs"].return_value = [
        _job("JOB-000001", "Bradley 1", is_active=True),
        _job("JOB-000007", "Atlantis", is_active=False),
    ]
    result = _poll_inside_lock()
    assert result.halted_no_creds is False
    _patch_all["push_jobs"].assert_called_once()
    args = _patch_all["push_jobs"].call_args.args
    # (base_url, bearer, payload) — the full set with active flags 1/0.
    assert args[0] == "https://portal.example.com" and args[1] == "bearer"
    assert args[2] == [
        {"job_id": "JOB-000001", "project_name": "Bradley 1", "active": 1},
        {"job_id": "JOB-000007", "project_name": "Atlantis", "active": 0},
    ]


def test_job_sync_empty_set_short_circuits_without_push(_patch_all):
    _patch_all["list_all_jobs"].return_value = []  # Smartsheet read miss / empty sheet
    _poll_inside_lock()
    _patch_all["push_jobs"].assert_not_called()  # never wipe the dropdown


def test_job_sync_failure_is_swallowed_and_does_not_affect_intake(_patch_all):
    # A filed submission + a failing job-sync push: the cycle still completes, the
    # intake drain stats are intact, and the failure is a WARN (not an intake error).
    _patch_all["get_pending"].return_value = [_row("u1")]
    _patch_all["list_all_jobs"].return_value = [_job()]
    _patch_all["push_jobs"].side_effect = portal_poll.portal_client.PortalTransportError("500")
    result = _poll_inside_lock()
    assert result.filed == 1            # intake drain unaffected
    assert result.errors == 0           # the push failure is NOT an intake error
    codes = [c.kwargs.get("error_code") for c in _patch_all["log"].call_args_list]
    assert "portal_job_sync_failed" in codes


# ---- poll_once outer gating ----------------------------------------------


def test_poll_once_skipped_when_polling_disabled(_patch_all, mocker):
    mocker.patch.object(portal_poll, "_polling_enabled", return_value=False)
    result = poll_once()
    assert result.skipped_disabled is True
    _patch_all["get_pending"].assert_not_called()


def test_poll_once_skipped_when_lock_held(_patch_all, mocker):
    mocker.patch.object(portal_poll, "_polling_enabled", return_value=True)

    @contextmanager
    def _held(_path):
        yield False

    mocker.patch.object(portal_poll, "_file_lock", _held)
    result = poll_once()
    assert result.skipped_locked is True
    _patch_all["get_pending"].assert_not_called()


# ---- wiring --------------------------------------------------------------


def test_daemon_name_is_stable():
    assert DAEMON_NAME == "safety_reports.portal_poll"


# ---- review-hardening: seen-set cap, mark-filed found=False, status precedence


def test_persist_seen_caps_to_max_seen(mocker):
    @contextmanager
    def _lock(_p):
        yield

    mocker.patch.object(portal_poll.state_io, "with_path_lock", _lock)
    captured: dict[str, Any] = {}
    mocker.patch.object(
        portal_poll.state_io, "atomic_write_json",
        side_effect=lambda path, data: captured.__setitem__("d", data),
    )
    big = {f"u{i}": {"status": "filed", "box_link": ""} for i in range(portal_poll.MAX_SEEN + 100)}
    portal_poll._persist_seen(big)
    written = captured["d"]
    assert len(written) == portal_poll.MAX_SEEN
    # kept the most-recent entries; dropped the oldest.
    assert f"u{portal_poll.MAX_SEEN + 99}" in written
    assert "u0" not in written


def test_mark_filed_found_false_logs_warn(_patch_all):
    _patch_all["get_pending"].return_value = [_row("u1")]
    _patch_all["mark_filed"].return_value = False
    _poll_inside_lock()
    warns = [
        c for c in _patch_all["log"].call_args_list
        if c.kwargs.get("error_code") == "portal_mark_filed_not_found"
    ]
    assert warns, "found=False from the Worker must WARN (not be silent)"


def test_per_row_exception_does_not_record_seen(_patch_all):
    _patch_all["get_pending"].return_value = [_row("u1")]
    _patch_all["process"].side_effect = RuntimeError("boom")
    _poll_inside_lock()
    persisted = _patch_all["persist_seen"].call_args.args[0]
    assert "u1" not in persisted  # a crashed row is NOT recorded → re-pull retries


def test_cycle_status_degraded_when_errors_and_reviewed(_patch_all):
    _patch_all["get_pending"].return_value = [_row("u1"), _row("u2")]
    _patch_all["process"].side_effect = [
        ProcessResult(status="review_queue", message_id="u1", correlation_id="c", box_link=None),
        RuntimeError("boom"),
    ]
    result = _poll_inside_lock()
    assert result.errors == 1 and result.reviewed == 1
    # errors (DEGRADED) takes precedence over reviewed (WARN).
    assert _patch_all["hb_row"].call_args.kwargs["status"] == "DEGRADED"
