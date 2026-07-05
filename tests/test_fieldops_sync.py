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
from shared import active_jobs_writer, sheet_ids, smartsheet_client


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
        # P7 hours pass seams — hours_enabled defaults OFF so EVERY existing job-mirror test is
        # byte-identical (the pass is skipped); the hours tests below flip it on. All hours I/O is
        # mocked so no test touches live Smartsheet / the Worker.
        "hours_enabled": mocker.patch(
            "field_ops.fieldops_sync._hours_enabled", return_value=False
        ),
        "hours_pending": mocker.patch(
            "field_ops.fieldops_sync.portal_client.get_fieldops_pending_hours", return_value=[]
        ),
        "hours_mark": mocker.patch(
            "field_ops.fieldops_sync.portal_client.mark_fieldops_hours_mirrored",
            return_value={"ok": True, "updated": 1},
        ),
        "ensure_sheet": mocker.patch(
            "field_ops.fieldops_sync.hours_log.ensure_hours_log_sheet", return_value=999
        ),
        "upsert_entry": mocker.patch(
            "field_ops.fieldops_sync.hours_log.upsert_entry_row", return_value=1
        ),
        "supersede_entry": mocker.patch(
            "field_ops.fieldops_sync.hours_log.supersede_entry_row", return_value=True
        ),
        "row_cap": mocker.patch(
            "field_ops.fieldops_sync.hours_log.check_row_cap", return_value=None
        ),
        # §51 archive-on-closure seams — default to "an Hours Log exists" so the archived-job
        # tests exercise the move; the guard/idempotency tests below flip these to None.
        "arc_folder": mocker.patch(
            "field_ops.fieldops_sync.smartsheet_client.find_folder_by_name_in_workspace",
            return_value=4242,
        ),
        "arc_sheet": mocker.patch(
            "field_ops.fieldops_sync.smartsheet_client.find_sheet_by_name_in_folder",
            return_value=888,
        ),
        "arc_move": mocker.patch(
            "field_ops.fieldops_sync.smartsheet_client.move_sheet_to_folder",
            return_value=None,
        ),
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


def test_malformed_mirror_version_warns_and_coerces_to_zero(_patch):
    # Never-silent: a missing/malformed mirror_version (a Worker payload defect) coerces to 0
    # — which would leave the job permanently dirty — so it must WARN, not silently coerce.
    _patch["pending"].return_value = [_job(mirror_version="not-an-int")]
    _patch["upsert"].side_effect = _upsert_ok
    fieldops_sync._sync_inside_lock()
    assert any(
        c.kwargs.get("error_code") == "fieldops_mirror_version_malformed"
        for c in _patch["log"].call_args_list
    )
    # the coerced 0 is what's sent to the Worker watermark (vector stays consistent).
    assert _patch["mark"].call_args_list[0].args[2][0]["mirrored_version"] == 0


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
    # the ticket records the partial state: failed on the safety sheet, nothing mirrored yet.
    payload = _patch["review"].call_args.kwargs["payload"]
    assert payload["failed_sheet"] == "safety"
    assert payload["safety_mirrored"] is False
    assert _patch["hb_row"].call_args.kwargs["status"] == "WARN"
    _patch["marker"].assert_called_once()


def test_permanent_failure_on_progress_records_safety_already_mirrored(_patch):
    # Safety mirrors fine; the PROGRESS upsert permanently fails → the Review ticket must record
    # that safety is already live and ONLY progress failed (the operator's remediation differs).
    _patch["pending"].return_value = [_job()]

    def _upsert(config, job):
        if config is active_jobs_writer.SAFETY_WRITE_CONFIG:
            return (111, "JOB-0007")
        raise smartsheet_client.SmartsheetValidationError("HTTP 400 progress reject")

    _patch["upsert"].side_effect = _upsert

    stats = fieldops_sync._sync_inside_lock()

    assert stats.reviewed == 1 and stats.mirrored == 0
    # safety WAS committed (mark-mirrored once) before the progress upsert failed
    assert _patch["mark"].call_count == 1
    assert _patch["mark"].call_args_list[0].args[2][0]["sheet"] == "safety"
    payload = _patch["review"].call_args.kwargs["payload"]
    assert payload["failed_sheet"] == "progress"
    assert payload["safety_mirrored"] is True


def test_mark_mirrored_401_pages_critical_not_transient(_patch):
    # A 401 on the mark-mirrored write-back (bad/rotated field-ops bearer) is NOT transient — it
    # must page CRITICAL, not fall into the self-healing PortalTransportError bucket (its parent).
    _patch["pending"].return_value = [_job()]
    _patch["upsert"].side_effect = _upsert_ok
    _patch["mark"].side_effect = fieldops_sync.portal_client.PortalAuthError("401")

    stats = fieldops_sync._sync_inside_lock()

    assert stats.errors == 1
    assert stats.mirrored == 0 and stats.reviewed == 0
    _patch["upsert"].assert_called()  # the sheet write ran before the mark-mirrored 401
    assert any(
        c.args and c.args[0] == fieldops_sync.Severity.CRITICAL
        and c.kwargs.get("error_code") == "fieldops_mark_mirrored_unauthorized"
        for c in _patch["log"].call_args_list
    )
    # and it is NOT mis-classified as the self-healing transient error
    assert not any(
        c.kwargs.get("error_code") == "fieldops_job_transient"
        for c in _patch["log"].call_args_list
    )


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


# ---- shared base-URL key is read under its owning workstream --------------


def test_worker_base_url_read_under_safety_reports_workstream(mocker):
    # The Worker base-URL key is SHARED with portal_poll and OWNED by safety_reports; reading
    # it under field_ops would force a duplicate ITS_Config row that can silently diverge from
    # the canonical safety_reports row. This proves the control bites: the base-URL key is
    # requested under workstream="safety_reports", while a field_ops-scoped key (the
    # sync_enabled gate) is still read under workstream="field_ops".
    def _get_setting(key, *, workstream):
        if key == fieldops_sync.CFG_WORKER_BASE_URL:
            return "https://safety.example"
        if key == fieldops_sync.CFG_SYNC_ENABLED:
            return "true"
        return None

    get_setting = mocker.patch(
        "field_ops.fieldops_sync.smartsheet_client.get_setting", side_effect=_get_setting
    )
    mocker.patch(
        "field_ops.fieldops_sync.keychain.get_secret", return_value="bearer-tok"
    )

    creds = fieldops_sync._resolve_credentials()
    assert creds == ("https://safety.example", "bearer-tok")

    base_url_calls = [
        c for c in get_setting.call_args_list if c.args[0] == fieldops_sync.CFG_WORKER_BASE_URL
    ]
    assert base_url_calls, "expected the Worker base-URL key to be read"
    assert all(c.kwargs["workstream"] == "safety_reports" for c in base_url_calls)

    # A field_ops-owned key stays under field_ops (byte-identical default behavior).
    assert fieldops_sync._sync_enabled() is True
    sync_calls = [
        c for c in get_setting.call_args_list if c.args[0] == fieldops_sync.CFG_SYNC_ENABLED
    ]
    assert sync_calls, "expected the sync_enabled gate to be read"
    assert all(c.kwargs["workstream"] == "field_ops" for c in sync_calls)


# ---- P7 hours pass (Track 2, Slice 1) ------------------------------------


def _hours_entry(uuid: str = "T1", job_id: str = "JOB-1", **over: Any) -> dict[str, Any]:
    e: dict[str, Any] = {
        "uuid": uuid,
        "job_id": job_id,
        "project_name": "Job One",
        "work_started_at": 1751000000,
        "work_ended_at": 1751028800,
        "hours": 8,
        "notes": "poured footings",
        "amends_uuid": None,
        "created_at": 1751030000,
        "personnel_name": "Alice Crew",
    }
    e.update(over)
    return e


def test_hours_pass_off_by_default(_patch):
    # hours_enabled defaults False (fixture) → the pass never touches the Worker or the sheets.
    _patch["hours_pending"].return_value = [_hours_entry()]
    stats = fieldops_sync._sync_inside_lock()
    _patch["hours_pending"].assert_not_called()
    _patch["ensure_sheet"].assert_not_called()
    assert stats.hours_mirrored == 0 and stats.hours_errors == 0 and stats.hours_reviewed == 0


def test_hours_pass_mirrors_and_marks(_patch):
    _patch["hours_enabled"].return_value = True
    _patch["hours_pending"].return_value = [_hours_entry("T1"), _hours_entry("T2")]
    stats = fieldops_sync._sync_inside_lock()
    assert stats.hours_mirrored == 2 and stats.hours_errors == 0 and stats.hours_reviewed == 0
    _patch["ensure_sheet"].assert_called_once_with("Job One")  # one sheet for the one job
    assert _patch["upsert_entry"].call_count == 2               # one upsert per entry
    # commit point LAST — one mark-mirrored batch of the succeeded uuids
    _patch["hours_mark"].assert_called_once()
    assert _patch["hours_mark"].call_args.args[2] == ["T1", "T2"]
    assert _patch["hb_row"].call_args.kwargs["status"] == "OK"
    assert _patch["hb_row"].call_args.kwargs["items_processed"] == 2
    _patch["row_cap"].assert_called_once()  # §51 row-cap watchdog runs once per job-sheet


def test_hours_amend_supersedes_prior(_patch):
    _patch["hours_enabled"].return_value = True
    _patch["hours_pending"].return_value = [_hours_entry("T2", amends_uuid="T1")]
    fieldops_sync._sync_inside_lock()
    _patch["supersede_entry"].assert_called_once_with(999, "T1", "T2")


def test_hours_amend_prior_missing_warns_but_still_marks(_patch):
    _patch["hours_enabled"].return_value = True
    _patch["hours_pending"].return_value = [_hours_entry("T2", amends_uuid="T1")]
    _patch["supersede_entry"].return_value = False  # prior not on the sheet yet (out-of-order)
    stats = fieldops_sync._sync_inside_lock()
    assert stats.hours_mirrored == 1  # the amend's own row still filed + marked
    assert any(
        c.kwargs.get("error_code") == "fieldops_hours_amend_prior_missing"
        for c in _patch["log"].call_args_list
    )


def test_hours_permanent_failure_routes_review_and_not_marked(_patch):
    _patch["hours_enabled"].return_value = True
    _patch["hours_pending"].return_value = [_hours_entry("T1")]
    _patch["upsert_entry"].side_effect = smartsheet_client.SmartsheetValidationError("HTTP 400")
    stats = fieldops_sync._sync_inside_lock()
    assert stats.hours_reviewed == 1 and stats.hours_mirrored == 0
    _patch["review"].assert_called_once()
    assert _patch["review"].call_args.kwargs["workstream"] == "progress_reports"
    _patch["hours_mark"].assert_not_called()  # nothing succeeded → no mark-mirrored
    assert _patch["hb_row"].call_args.kwargs["status"] == "WARN"


def test_hours_per_entry_fence_one_bad_entry_does_not_block_others(_patch):
    _patch["hours_enabled"].return_value = True
    _patch["hours_pending"].return_value = [_hours_entry("T1"), _hours_entry("T2")]
    _patch["upsert_entry"].side_effect = [smartsheet_client.SmartsheetError("boom"), 5]
    stats = fieldops_sync._sync_inside_lock()
    assert stats.hours_errors == 1 and stats.hours_mirrored == 1  # T2 still mirrored
    assert _patch["hours_mark"].call_args.args[2] == ["T2"]        # only the succeeded uuid marked


def test_hours_pending_fetch_failure_leaves_unmarked_but_cycle_completes(_patch):
    _patch["hours_enabled"].return_value = True
    _patch["hours_pending"].side_effect = fieldops_sync.portal_client.PortalTransportError("down")
    stats = fieldops_sync._sync_inside_lock()
    assert stats.hours_errors == 1 and stats.hours_mirrored == 0
    _patch["hours_mark"].assert_not_called()
    # the hours failure NEVER aborts the cycle — heartbeat + marker still run.
    _patch["hb"].assert_called_once()
    _patch["marker"].assert_called_once()


def test_hours_malformed_entry_is_skipped_never_silent(_patch):
    _patch["hours_enabled"].return_value = True
    _patch["hours_pending"].return_value = [_hours_entry("T1", project_name="")]  # unfoldabe
    stats = fieldops_sync._sync_inside_lock()
    assert stats.hours_mirrored == 0
    _patch["ensure_sheet"].assert_not_called()
    assert any(
        c.kwargs.get("error_code") == "fieldops_hours_row_malformed"
        for c in _patch["log"].call_args_list
    )


# ---- §51 archive-on-closure ----------------------------------------------


def test_archived_job_moves_hours_log_to_closed_projects(_patch):
    # An ARCHIVED (closed) job whose Hours Log exists → the standing tracker is MOVED into
    # the Archive workspace's Closed-Projects folder, and the job still counts as mirrored.
    _patch["pending"].return_value = [_job(lifecycle="archived")]
    _patch["upsert"].side_effect = _upsert_ok

    stats = fieldops_sync._sync_inside_lock()

    assert stats.mirrored == 1 and stats.errors == 0 and stats.reviewed == 0
    _patch["arc_folder"].assert_called_once()  # source per-job folder resolved (no create)
    _patch["arc_sheet"].assert_called_once()   # Hours Log resolved in that folder (no create)
    _patch["arc_move"].assert_called_once()
    move_args = _patch["arc_move"].call_args.args
    assert move_args[0] == 888  # the resolved Hours Log sheet id
    assert move_args[1] == sheet_ids.FOLDER_ARCHIVE_CLOSED_PROJECTS


def test_active_job_does_not_archive(_patch):
    # Proves the guard bites: a NON-archived (active) job must never touch the archive path.
    # (If the `lifecycle == "archived"` guard were removed, arc_move would fire here.)
    _patch["pending"].return_value = [_job(lifecycle="active")]
    _patch["upsert"].side_effect = _upsert_ok

    stats = fieldops_sync._sync_inside_lock()

    assert stats.mirrored == 1
    _patch["arc_folder"].assert_not_called()
    _patch["arc_sheet"].assert_not_called()
    _patch["arc_move"].assert_not_called()


def test_archived_job_no_folder_is_noop_no_error(_patch):
    # No per-job folder found → nothing was ever created → no move, no error.
    _patch["pending"].return_value = [_job(lifecycle="archived")]
    _patch["upsert"].side_effect = _upsert_ok
    _patch["arc_folder"].return_value = None

    stats = fieldops_sync._sync_inside_lock()

    assert stats.mirrored == 1 and stats.errors == 0
    _patch["arc_sheet"].assert_not_called()
    _patch["arc_move"].assert_not_called()
    assert not any(
        c.kwargs.get("error_code") == "fieldops_archive_on_closure_failed"
        for c in _patch["log"].call_args_list
    )


def test_archived_job_no_hours_log_is_noop_no_error(_patch):
    # Folder exists but no Hours Log sheet in it → already moved (or never existed) → no move.
    # This is the natural idempotency: once moved out of the source folder it isn't found again.
    _patch["pending"].return_value = [_job(lifecycle="archived")]
    _patch["upsert"].side_effect = _upsert_ok
    _patch["arc_sheet"].return_value = None

    stats = fieldops_sync._sync_inside_lock()

    assert stats.mirrored == 1 and stats.errors == 0
    _patch["arc_move"].assert_not_called()
    assert not any(
        c.kwargs.get("error_code") == "fieldops_archive_on_closure_failed"
        for c in _patch["log"].call_args_list
    )


def test_archive_move_failure_warns_but_never_fails_the_mirror(_patch):
    # A move failure must NEVER fail the mirror (the job is already mirrored + mark-synced):
    # WARN is logged, no exception propagates, and the job still counts as mirrored.
    _patch["pending"].return_value = [_job(lifecycle="archived")]
    _patch["upsert"].side_effect = _upsert_ok
    _patch["arc_move"].side_effect = smartsheet_client.SmartsheetError("move boom")

    stats = fieldops_sync._sync_inside_lock()

    assert stats.mirrored == 1  # the mirror still counts despite the failed archive move
    assert stats.errors == 0 and stats.reviewed == 0
    warn = [
        c for c in _patch["log"].call_args_list
        if c.kwargs.get("error_code") == "fieldops_archive_on_closure_failed"
    ]
    assert warn, "expected a WARN with error_code=fieldops_archive_on_closure_failed"
    assert warn[0].args[0] == fieldops_sync.Severity.WARN
    # heartbeat is still OK — the archive move is best-effort, not part of the mirror result
    assert _patch["hb_row"].call_args.kwargs["status"] == "OK"
