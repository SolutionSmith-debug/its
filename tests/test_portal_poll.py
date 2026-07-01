"""Unit tests for safety_reports/portal_poll.py.

All external services mocked. Exercises the verify → dispatch → receipt cycle,
the fail-closed credential gate, the HMAC-reject path, and the seen-set
fast-paths. Structure mirrors tests/test_weekly_send_poll.py.
"""
from __future__ import annotations

import json
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


def _processed(
    uuid: str = "u1", link: str = "https://app.box.com/file/f9", file_id: str = "f9"
) -> ProcessResult:
    return ProcessResult(
        status="processed", message_id=uuid, correlation_id="c",
        box_link=link, box_file_id=file_id,
    )


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
        "mark_rejected": mocker.patch.object(
            portal_poll.portal_client, "mark_rejected", return_value=True
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
        # A4 unfiled-backlog marker — mocked so existing cycle tests neither touch real
        # state nor assert on it; dedicated tests below exercise the real helper on a tmp path.
        "backlog": mocker.patch.object(portal_poll, "_record_pending_backlog"),
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
        # PR-4 request-driven PDF cache servicing pass: default to NO pending PDF
        # requests so the existing cycle tests neither hit Box nor POST any chunk.
        "get_pdf_requests": mocker.patch.object(
            portal_poll.portal_client, "get_pdf_requests", return_value=[]
        ),
        "upload_filed_pdf": mocker.patch.object(
            portal_poll.portal_client, "upload_filed_pdf",
            return_value={"ok": True, "ready": True, "stored": True, "received": 1},
        ),
        "download_file": mocker.patch.object(
            portal_poll.box_client, "download_file", return_value=b"%PDF-1.4 bytes"
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
        box_file_id="f9",  # PR-4: the structural id threaded to the receipt
    )
    # Recorded as filed in the seen-set for a future lost-receipt re-post.
    persisted = _patch_all["persist_seen"].call_args.args[0]
    assert persisted["u1"] == {
        "status": "filed", "box_link": "https://app.box.com/file/f9", "box_file_id": "f9",
    }


def test_review_queue_result_is_drained_and_counted(_patch_all):
    _patch_all["get_pending"].return_value = [_row("u1")]
    _patch_all["process"].return_value = ProcessResult(
        status="review_queue", message_id="u1", correlation_id="c", box_link=None
    )
    result = _poll_inside_lock()
    assert result.reviewed == 1 and result.filed == 0
    # Drained with an empty link (the Review Queue entry is the durable record);
    # no box_file_id on the review path → box_file_id=None.
    _patch_all["mark_filed"].assert_called_once_with(
        "https://portal.example.com", "bearer", submission_uuid="u1", box_link="",
        box_file_id=None,
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
    _patch_all["mark_rejected"].assert_called_once()  # M4 (PR-4): flipped box_verified=-1 (stops the forever re-pull)
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
        "u1": {
            "status": "filed", "box_link": "https://app.box.com/file/keep",
            "box_file_id": "keep",
        }
    }
    result = _poll_inside_lock()
    assert result.remarked == 1 and result.filed == 0
    _patch_all["verify"].assert_not_called()     # already verified before
    _patch_all["process"].assert_not_called()    # NOT re-filed
    # PR-4: the stored box_file_id rides the lost-receipt re-post too.
    _patch_all["mark_filed"].assert_called_once_with(
        "https://portal.example.com", "bearer",
        submission_uuid="u1", box_link="https://app.box.com/file/keep",
        box_file_id="keep",
    )


def test_seen_filed_repost_recovers_box_file_id_from_link_when_absent(_patch_all):
    # A seen-set record written BEFORE PR-4 has no box_file_id — recover it from the
    # stored link (digits after /file/) so the cache handle survives the re-post.
    _patch_all["get_pending"].return_value = [_row("u1")]
    _patch_all["load_seen"].return_value = {
        "u1": {"status": "filed", "box_link": "https://app.box.com/file/12345"}
    }
    _poll_inside_lock()
    assert _patch_all["mark_filed"].call_args.kwargs["box_file_id"] == "12345"


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


def test_transient_circuit_open_warns_and_skips_without_paging(_patch_all):
    # base URL unreadable because the Smartsheet circuit is OPEN → CREDS_TRANSIENT, NOT None.
    _patch_all["creds"].return_value = portal_poll.CREDS_TRANSIENT
    result = _poll_inside_lock()
    assert result.halted_transient is True
    assert result.halted_no_creds is False
    _patch_all["get_pending"].assert_not_called()  # FAIL-CLOSED: no poll
    # transient → WARN heartbeat (NOT ERROR), and NO watchdog marker (a SUSTAINED outage still
    # surfaces via the Check-C staleness floor — same as the no-creds path).
    assert _patch_all["hb_row"].call_args.kwargs["status"] == "WARN"
    _patch_all["wd"].assert_not_called()
    # It WARNs (portal_creds_transient) and must NOT fire the misconfig CRITICAL.
    assert any(
        c.args and c.args[0] == portal_poll.Severity.WARN
        and c.kwargs.get("error_code") == "portal_creds_transient"
        for c in _patch_all["log"].call_args_list
    )
    assert not any(
        c.kwargs.get("error_code") == "portal_creds_missing"
        for c in _patch_all["log"].call_args_list
    )


# ---- _resolve_credentials 3-state (transient vs genuinely-absent) ----------


def test_resolve_credentials_circuit_open_returns_transient(mocker):
    # A Smartsheet circuit-open on the base-URL read is TRANSIENT — never confused with a misconfig.
    mocker.patch.object(
        portal_poll.smartsheet_client, "get_setting",
        side_effect=portal_poll.smartsheet_client.SmartsheetCircuitOpenError("open"),
    )
    assert portal_poll._resolve_credentials() is portal_poll.CREDS_TRANSIENT


def test_resolve_credentials_missing_row_returns_none(mocker):
    # base-URL row genuinely absent (NotFound) → None (misconfig), NOT transient.
    mocker.patch.object(
        portal_poll.smartsheet_client, "get_setting",
        side_effect=portal_poll.smartsheet_client.SmartsheetNotFoundError("nope"),
    )
    mocker.patch.object(portal_poll.keychain, "get_secret", return_value="x")
    assert portal_poll._resolve_credentials() is None


def test_resolve_credentials_missing_keychain_returns_none(mocker):
    # base URL fine, but the Keychain bearer/secret are absent → None (misconfig), NOT transient.
    mocker.patch.object(
        portal_poll.smartsheet_client, "get_setting", return_value="https://portal.example.com",
    )
    mocker.patch.object(
        portal_poll.keychain, "get_secret",
        side_effect=portal_poll.keychain.KeychainError("missing"),
    )
    assert portal_poll._resolve_credentials() is None


def test_resolve_credentials_all_present_returns_creds(mocker):
    mocker.patch.object(
        portal_poll.smartsheet_client, "get_setting", return_value="https://portal.example.com",
    )
    mocker.patch.object(portal_poll.keychain, "get_secret", return_value="secret-val")
    creds = portal_poll._resolve_credentials()
    assert isinstance(creds, portal_poll._PortalCreds)
    assert creds.base_url == "https://portal.example.com"


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


# ---- PR-4 request-driven PDF cache servicing pass ------------------------


def _pdf_req(uuid="u1", file_id="f9"):
    return {
        "submission_uuid": uuid, "box_file_id": file_id,
        "form_code": "jha-v1", "work_date": "2026-06-05",
    }


def test_pdf_service_downloads_chunks_and_uploads(_patch_all):
    _patch_all["get_pdf_requests"].return_value = [_pdf_req("u1", "f9")]
    # 1.5 chunks worth of bytes → exactly 2 chunks at PDF_CHUNK_BYTES.
    pdf = b"A" * (portal_poll.PDF_CHUNK_BYTES + 10)
    _patch_all["download_file"].return_value = pdf

    result = _poll_inside_lock()

    assert result.pdf_serviced == 1
    _patch_all["download_file"].assert_called_once_with("f9")
    calls = _patch_all["upload_filed_pdf"].call_args_list
    assert len(calls) == 2  # chunked across 2 POSTs
    assert [c.kwargs["chunk_index"] for c in calls] == [0, 1]
    assert all(c.kwargs["chunk_total"] == 2 for c in calls)
    assert all(c.kwargs["submission_uuid"] == "u1" for c in calls)
    # chunk_b64 round-trips back to the original PDF bytes (never logged raw).
    import base64 as _b64
    reassembled = b"".join(_b64.b64decode(c.kwargs["chunk_b64"]) for c in calls)
    assert reassembled == pdf


def test_pdf_service_single_chunk_for_small_pdf(_patch_all):
    _patch_all["get_pdf_requests"].return_value = [_pdf_req("u1", "f9")]
    _patch_all["download_file"].return_value = b"%PDF tiny"
    result = _poll_inside_lock()
    assert result.pdf_serviced == 1
    calls = _patch_all["upload_filed_pdf"].call_args_list
    assert len(calls) == 1 and calls[0].kwargs["chunk_total"] == 1


def test_pdf_service_empty_box_file_is_skipped_not_uploaded(_patch_all):
    # A zero-byte filed PDF is a DATA error → WARN skip, NEVER an empty-chunk upload
    # (the Worker would 400 it). The request stays unready for re-file.
    _patch_all["get_pdf_requests"].return_value = [_pdf_req("u1", "f9")]
    _patch_all["download_file"].return_value = b""
    result = _poll_inside_lock()
    assert result.pdf_serviced == 0
    _patch_all["upload_filed_pdf"].assert_not_called()
    codes = [c.kwargs.get("error_code") for c in _patch_all["log"].call_args_list]
    assert "portal_pdf_empty_file" in codes


def test_pdf_service_per_item_fence_one_bad_item_does_not_abort(_patch_all):
    _patch_all["get_pdf_requests"].return_value = [
        _pdf_req("u1", "f1"), _pdf_req("u2", "f2"), _pdf_req("u3", "f3"),
    ]
    # The middle item's Box download blows up; the other two still service.
    _patch_all["download_file"].side_effect = [b"one", RuntimeError("box boom"), b"three"]
    result = _poll_inside_lock()
    assert result.pdf_serviced == 2  # u1 + u3, not u2
    codes = [c.kwargs.get("error_code") for c in _patch_all["log"].call_args_list]
    assert "portal_pdf_request_item_failed" in codes


def test_pdf_service_skips_row_missing_box_file_id(_patch_all):
    _patch_all["get_pdf_requests"].return_value = [
        {"submission_uuid": "u1", "box_file_id": "", "form_code": "x", "work_date": "d"},
    ]
    result = _poll_inside_lock()
    assert result.pdf_serviced == 0
    _patch_all["download_file"].assert_not_called()
    codes = [c.kwargs.get("error_code") for c in _patch_all["log"].call_args_list]
    assert "portal_pdf_request_malformed" in codes


def test_pdf_service_failure_is_best_effort_and_does_not_block_intake(_patch_all):
    # A filed submission + a failing PDF-request pull: the cycle still completes, the
    # intake drain stats are intact, and the failure is a WARN (not an intake error).
    _patch_all["get_pending"].return_value = [_row("u1")]
    _patch_all["get_pdf_requests"].side_effect = (
        portal_poll.portal_client.PortalTransportError("500")
    )
    result = _poll_inside_lock()
    assert result.filed == 1          # intake drain unaffected
    assert result.errors == 0         # the pass failure is NOT an intake error
    assert result.pdf_serviced == 0
    codes = [c.kwargs.get("error_code") for c in _patch_all["log"].call_args_list]
    assert "portal_pdf_service_failed" in codes


def test_pdf_service_no_requests_is_noop(_patch_all):
    _patch_all["get_pdf_requests"].return_value = []
    result = _poll_inside_lock()
    assert result.pdf_serviced == 0
    _patch_all["download_file"].assert_not_called()
    _patch_all["upload_filed_pdf"].assert_not_called()


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


# ---- A4: _record_pending_backlog stuck-backlog marker --------------------


def _read_backlog(path):
    return json.loads(path.read_text())


def test_record_pending_backlog_saturated_no_drain_latches(monkeypatch, tmp_path):
    """A saturated page (>= PENDING_LIMIT) that drained nothing latches high_since_utc."""
    p = tmp_path / "backlog.json"
    monkeypatch.setattr(portal_poll, "PENDING_BACKLOG_STATE_PATH", p)
    portal_poll._record_pending_backlog(portal_poll.PENDING_LIMIT, 0)
    data = _read_backlog(p)
    assert data["count"] == portal_poll.PENDING_LIMIT
    assert data["drained"] == 0
    assert data["high_since_utc"] is not None


def test_record_pending_backlog_progress_clears_latch(monkeypatch, tmp_path):
    """Any drain progress clears the latch even on a full page."""
    p = tmp_path / "backlog.json"
    monkeypatch.setattr(portal_poll, "PENDING_BACKLOG_STATE_PATH", p)
    portal_poll._record_pending_backlog(portal_poll.PENDING_LIMIT, 3)
    assert _read_backlog(p)["high_since_utc"] is None


def test_record_pending_backlog_unsaturated_clears_latch(monkeypatch, tmp_path):
    """A page below the cap is not a stuck backlog regardless of drain."""
    p = tmp_path / "backlog.json"
    monkeypatch.setattr(portal_poll, "PENDING_BACKLOG_STATE_PATH", p)
    portal_poll._record_pending_backlog(portal_poll.PENDING_LIMIT - 1, 0)
    assert _read_backlog(p)["high_since_utc"] is None


def test_record_pending_backlog_latch_persists_across_cycles(monkeypatch, tmp_path):
    """A sustained stuck backlog keeps the FIRST high_since_utc across cycles."""
    p = tmp_path / "backlog.json"
    monkeypatch.setattr(portal_poll, "PENDING_BACKLOG_STATE_PATH", p)
    portal_poll._record_pending_backlog(portal_poll.PENDING_LIMIT, 0)
    first = _read_backlog(p)["high_since_utc"]
    portal_poll._record_pending_backlog(portal_poll.PENDING_LIMIT, 0)
    second = _read_backlog(p)
    assert second["high_since_utc"] == first  # latch not reset
    assert second["last_scan_utc"] >= first   # last_scan advances


def test_record_pending_backlog_relatches_after_recovery(monkeypatch, tmp_path):
    """Stuck → recovered (latch clears) → stuck again sets a NEW latch."""
    p = tmp_path / "backlog.json"
    monkeypatch.setattr(portal_poll, "PENDING_BACKLOG_STATE_PATH", p)
    portal_poll._record_pending_backlog(portal_poll.PENDING_LIMIT, 0)
    portal_poll._record_pending_backlog(portal_poll.PENDING_LIMIT, 5)  # recovered → clears
    assert _read_backlog(p)["high_since_utc"] is None
    portal_poll._record_pending_backlog(portal_poll.PENDING_LIMIT, 0)  # stuck again
    assert _read_backlog(p)["high_since_utc"] is not None


def test_record_pending_backlog_failsoft_on_write_error(monkeypatch, tmp_path, mocker):
    """A marker write error is swallowed (WARN-logged), never raised into the drain."""
    p = tmp_path / "backlog.json"
    monkeypatch.setattr(portal_poll, "PENDING_BACKLOG_STATE_PATH", p)
    mocker.patch.object(portal_poll.state_io, "atomic_write_json", side_effect=OSError("disk full"))
    log = mocker.patch.object(portal_poll.error_log, "log")
    portal_poll._record_pending_backlog(portal_poll.PENDING_LIMIT, 0)  # must not raise
    assert log.called
