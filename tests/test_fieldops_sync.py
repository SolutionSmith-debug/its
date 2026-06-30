"""Unit tests for field_ops.fieldops_sync — the dual-sheet job up-sync daemon.

Fully mocked (no live Smartsheet / Worker): the data-plane seams (portal_client pending +
mark-mirrored, active_jobs_writer.upsert_job, review_queue.add) and the heartbeat / watchdog
seams are patched. Covers: sync_enabled gate OFF→noop, the dirty-job happy path (dual
find-or-create + per-sheet mark-mirrored, OK heartbeat + marker), the per-job permanent fence
(→ Review Queue, WARN), the partial-failure self-heal (safety committed + progress transient
→ only safety mark-mirrored, job left dirty, DEGRADED), the fail-closed no-creds halt, and a
malformed (no job_id) row skip.
"""
from __future__ import annotations

from typing import Any

import pytest

from field_ops import fieldops_sync
from shared import active_jobs_writer, smartsheet_client


def _job(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "job_id": "acme-solar-01",
        "project_name": "Acme Solar 01",
        "lifecycle": "active",
        "address": "1 Main St",
        "stakeholder_name": "Sam",
        "stakeholder_email": "sam@acme.example",
        "stakeholder_phone": "5551234",
        "safety_contact_name": "Pat",
        "safety_contact_email": "pat@acme.example",
        "safety_cc": ["a@x.com"],
        "progress_contact_name": "Riley",
        "progress_contact_email": "riley@acme.example",
        "progress_cc": ["c@x.com"],
        "mirror_version": 3,
    }
    base.update(over)
    return base


def _upsert_ok(config, job):
    """upsert_job side-effect: distinct (row_id, canonical) per sheet config."""
    if config is active_jobs_writer.SAFETY_WRITE_CONFIG:
        return (111, "JOB-0007")
    return (222, "JOB-PRG-0007")


@pytest.fixture
def _patch(mocker):
    return {
        "pending": mocker.patch(
            "field_ops.fieldops_sync.portal_client.get_fieldops_pending_jobs",
            return_value=[],
        ),
        "mark": mocker.patch(
            "field_ops.fieldops_sync.portal_client.mark_fieldops_jobs_mirrored",
            return_value={"ok": True, "updated": 1},
        ),
        "upsert": mocker.patch("field_ops.fieldops_sync.active_jobs_writer.upsert_job"),
        "review": mocker.patch("field_ops.fieldops_sync.review_queue.add", return_value=1),
        "creds": mocker.patch(
            "field_ops.fieldops_sync._resolve_credentials",
            return_value=("https://safety.example", "tok"),
        ),
        "hb": mocker.patch("field_ops.fieldops_sync._write_heartbeat", return_value=None),
        "hb_row": mocker.patch("field_ops.fieldops_sync._write_heartbeat_row", return_value=None),
        "marker": mocker.patch("field_ops.fieldops_sync._write_watchdog_marker", return_value=None),
        "log": mocker.patch("field_ops.fieldops_sync.error_log.log", return_value=None),
        "circuit": mocker.patch("field_ops.fieldops_sync.circuit_breaker.is_open", return_value=False),
    }


# ---- sync_enabled gate ----------------------------------------------------


def test_sync_disabled_is_noop(mocker, _patch):
    mocker.patch("field_ops.fieldops_sync._sync_enabled", return_value=False)
    assert fieldops_sync.sync_once() == 0
    _patch["pending"].assert_not_called()
    _patch["upsert"].assert_not_called()
    _patch["hb"].assert_not_called()


# ---- dirty-job happy path -------------------------------------------------


def test_dirty_job_mirrors_both_sheets_per_sheet_commit(_patch):
    _patch["pending"].return_value = [_job()]
    _patch["upsert"].side_effect = _upsert_ok

    stats = fieldops_sync._sync_inside_lock()

    assert stats.mirrored == 1
    assert stats.errors == 0 and stats.reviewed == 0
    # find-or-create on BOTH sheets
    assert _patch["upsert"].call_count == 2
    # per-sheet mark-mirrored: safety first, then progress (the version-vector commit order)
    assert _patch["mark"].call_count == 2
    safety_updates = _patch["mark"].call_args_list[0].args[2]
    assert safety_updates[0]["sheet"] == "safety"
    assert safety_updates[0]["row_id"] == 111
    assert safety_updates[0]["mirrored_version"] == 3
    assert safety_updates[0]["canonical_job_id"] == "JOB-0007"
    progress_updates = _patch["mark"].call_args_list[1].args[2]
    assert progress_updates[0]["sheet"] == "progress"
    assert progress_updates[0]["row_id"] == 222
    assert "canonical_job_id" not in progress_updates[0]  # canonical is safety-only
    # heartbeat OK + watchdog marker
    _patch["hb"].assert_called_once()
    assert _patch["hb_row"].call_args.kwargs["status"] == "OK"
    _patch["marker"].assert_called_once()


# ---- per-job permanent fence → Review Queue -------------------------------


def test_permanent_failure_routes_to_review_queue(_patch):
    _patch["pending"].return_value = [_job()]
    _patch["upsert"].side_effect = smartsheet_client.SmartsheetValidationError("HTTP 400 reject")

    stats = fieldops_sync._sync_inside_lock()

    assert stats.reviewed == 1
    assert stats.mirrored == 0
    _patch["review"].assert_called_once()
    assert _patch["review"].call_args.kwargs["workstream"] == "progress_reports"
    # permanent failure on the safety upsert → nothing was ever mark-mirrored
    _patch["mark"].assert_not_called()
    assert _patch["hb_row"].call_args.kwargs["status"] == "WARN"
    _patch["marker"].assert_called_once()


# ---- partial-failure self-heal -------------------------------------------


def test_partial_failure_advances_only_safety_and_leaves_dirty(_patch):
    _patch["pending"].return_value = [_job()]

    def _upsert(config, job):
        if config is active_jobs_writer.SAFETY_WRITE_CONFIG:
            return (111, "JOB-0007")
        raise smartsheet_client.SmartsheetError("transient progress write")

    _patch["upsert"].side_effect = _upsert

    stats = fieldops_sync._sync_inside_lock()

    assert stats.errors == 1
    assert stats.mirrored == 0
    assert stats.reviewed == 0
    # ONLY the safety watermark was committed; progress was never mark-mirrored (job dirty).
    assert _patch["mark"].call_count == 1
    only = _patch["mark"].call_args_list[0].args[2]
    assert only[0]["sheet"] == "safety"
    _patch["review"].assert_not_called()
    assert _patch["hb_row"].call_args.kwargs["status"] == "DEGRADED"
    _patch["marker"].assert_called_once()


# ---- fail-closed credentials ----------------------------------------------


def test_fail_closed_when_credentials_missing(_patch):
    _patch["creds"].return_value = None

    stats = fieldops_sync._sync_inside_lock()

    assert stats.halted_no_creds is True
    _patch["pending"].assert_not_called()
    _patch["hb"].assert_called_once()
    assert _patch["hb_row"].call_args.kwargs["status"] == "ERROR"
    # No watchdog marker on a no-creds halt — let Check C go stale (mirror portal_poll).
    _patch["marker"].assert_not_called()


def test_pending_auth_error_pages_and_writes_error_heartbeat(_patch):
    _patch["pending"].side_effect = fieldops_sync.portal_client.PortalAuthError("401")

    stats = fieldops_sync._sync_inside_lock()

    assert stats.errors == 1
    _patch["upsert"].assert_not_called()
    assert _patch["hb_row"].call_args.kwargs["status"] == "ERROR"
    _patch["marker"].assert_not_called()


# ---- malformed row --------------------------------------------------------


def test_row_missing_job_id_is_skipped(_patch):
    _patch["pending"].return_value = [{"project_name": "no id"}]

    stats = fieldops_sync._sync_inside_lock()

    assert stats.errors == 1
    _patch["upsert"].assert_not_called()
    _patch["mark"].assert_not_called()
