"""Tests for the Phase-5 portal pull path in safety_reports/intake.py
(process_portal_submission). All Smartsheet/Box/active_jobs/render calls are mocked.

Live coverage is the deploy-gated end-to-end smoke (next session).
"""
from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from safety_reports import intake, week_sheet
from shared import box_client
from shared.smartsheet_client import SmartsheetError

BASE_SUB = {
    "submission_uuid": "u1",
    "job_id": "JOB-1",
    "form_code": "jha-v1",
    "work_date": "2026-06-05",
    "payload_json": '{"work_location": "Array A"}',
    "amends_uuid": None,
    "created_at": 1_717_600_000,
}

DEFINITION = {
    "form_code": "jha-v1",
    "parent_form_code": "jha",
    "form_name": "Job Hazard Analysis",
    "sections": [],
}


@pytest.fixture
def stub(mocker) -> dict[str, MagicMock]:
    """Mock every external boundary process_portal_submission touches."""
    job = SimpleNamespace(
        project_name="Bradley 1", is_active=True, active_status="Active"
    )
    s = {
        "get_job": mocker.patch.object(intake.active_jobs, "get_job", return_value=job),
        "ensure": mocker.patch.object(intake.week_sheet, "ensure_week_sheet", return_value=8001),
        "find": mocker.patch.object(intake.week_sheet, "find_submission_row", return_value=None),
        "write": mocker.patch.object(intake.week_sheet, "write_submission_row", return_value=555),
        "supersede": mocker.patch.object(intake.week_sheet, "supersede_row", return_value=True),
        "load_def": mocker.patch.object(intake.form_pdf, "load_definition", return_value=DEFINITION),
        "render": mocker.patch.object(intake.form_pdf, "render_submission_pdf", return_value=b"%PDF-1.4"),
        "incomplete": mocker.patch.object(intake.form_pdf, "incomplete_checklist_items", return_value=[]),
        "get_folder_id": mocker.patch.object(intake.project_routing, "get_folder_id", return_value="root1"),
        "resolve_sub": mocker.patch.object(intake, "_resolve_box_subfolder", return_value="leaf1"),
        "upload": mocker.patch.object(intake.box_client, "upload_bytes", return_value={"id": "f9", "name": "x", "size": 5}),
        "get_or_create": mocker.patch.object(intake.box_client, "get_or_create_folder", return_value="fb1"),
        "review": mocker.patch.object(intake.review_queue, "add"),
        "log": mocker.patch.object(intake.error_log, "log"),
    }
    return s


# ---- happy path ----------------------------------------------------------


def test_success_files_box_and_sheet_returns_processed(stub):
    result = intake.process_portal_submission(dict(BASE_SUB))
    assert result.status == "processed"
    assert result.box_link == "https://app.box.com/file/f9"
    # Wrote the per-submission row with the parsed date + Box link.
    kwargs = stub["write"].call_args.kwargs
    assert kwargs["submission_uuid"] == "u1"
    assert kwargs["work_date"] == date(2026, 6, 5)
    assert kwargs["box_link"] == "https://app.box.com/file/f9"
    assert kwargs["form_code"] == "jha-v1"
    stub["review"].assert_not_called()


def test_success_uploads_to_category_subfolder_named_by_date_and_type(stub):
    intake.process_portal_submission(dict(BASE_SUB))
    folder_id, name, content = (
        stub["upload"].call_args.args[0],
        stub["upload"].call_args.args[1],
        stub["upload"].call_args.args[2],
    )
    assert folder_id == "leaf1"  # the resolved JSAs category subfolder
    assert name == "2026-06-05-jha.pdf"  # <work_date>-<type>.pdf
    assert content == b"%PDF-1.4"


def test_box_fallback_when_category_subfolder_missing(stub):
    stub["resolve_sub"].return_value = None  # category subfolder not found
    result = intake.process_portal_submission(dict(BASE_SUB))
    assert result.status == "processed"
    # Filed into the auto-created ITS fallback folder instead.
    stub["get_or_create"].assert_called_once_with("root1", intake.PORTAL_BOX_FALLBACK_FOLDER)
    assert stub["upload"].call_args.args[0] == "fb1"
    notes = stub["write"].call_args.kwargs["notes"]
    assert "fallback" in notes


# ---- dedupe (re-pull) ----------------------------------------------------


def test_dedupe_already_filed_skips_refile_and_recovers_link(stub):
    stub["find"].return_value = {
        "_row_id": 7, week_sheet.COL_SUBMISSION_PDF: "https://app.box.com/file/old",
        "Row Type": week_sheet.ROW_TYPE_SUBMISSION,
    }
    result = intake.process_portal_submission(dict(BASE_SUB))
    assert result.status == "already_filed"
    assert result.box_link == "https://app.box.com/file/old"
    stub["write"].assert_not_called()
    stub["upload"].assert_not_called()


# ---- permanent refusals → review_queue (drain) ---------------------------


def test_unknown_job_routes_to_review(stub):
    stub["get_job"].return_value = None
    result = intake.process_portal_submission(dict(BASE_SUB))
    assert result.status == "review_queue"
    stub["review"].assert_called_once()
    assert stub["review"].call_args.kwargs["payload"]["reason"] == "job_not_found"
    stub["write"].assert_not_called()


def test_inactive_job_routes_to_review(stub):
    stub["get_job"].return_value = SimpleNamespace(
        project_name="Bradley 1", is_active=False, active_status="Archived"
    )
    result = intake.process_portal_submission(dict(BASE_SUB))
    assert result.status == "review_queue"
    assert stub["review"].call_args.kwargs["payload"]["reason"] == "job_inactive"


def test_malformed_work_date_routes_to_review_before_job_lookup(stub):
    sub = dict(BASE_SUB, work_date="not-a-date")
    result = intake.process_portal_submission(sub)
    assert result.status == "review_queue"
    assert stub["review"].call_args.kwargs["payload"]["reason"] == "malformed_work_date"
    stub["get_job"].assert_not_called()


def test_unknown_form_routes_to_review(stub):
    stub["load_def"].return_value = None
    result = intake.process_portal_submission(dict(BASE_SUB))
    assert result.status == "review_queue"
    assert stub["review"].call_args.kwargs["payload"]["reason"] == "unknown_form"
    stub["render"].assert_not_called()


def test_malformed_payload_routes_to_review(stub):
    sub = dict(BASE_SUB, payload_json="{not valid json")
    result = intake.process_portal_submission(sub)
    assert result.status == "review_queue"
    assert stub["review"].call_args.kwargs["payload"]["reason"] == "malformed_payload"


def test_payload_not_object_routes_to_review(stub):
    sub = dict(BASE_SUB, payload_json="[1, 2, 3]")  # valid JSON, not an object
    result = intake.process_portal_submission(sub)
    assert result.status == "review_queue"
    assert stub["review"].call_args.kwargs["payload"]["reason"] == "malformed_payload"


def test_unresolved_box_root_routes_to_review(stub):
    stub["get_folder_id"].return_value = ""  # no Box root for the project
    result = intake.process_portal_submission(dict(BASE_SUB))
    assert result.status == "review_queue"
    assert stub["review"].call_args.kwargs["payload"]["reason"] == "project_box_root_unresolved"


def test_missing_submission_uuid_routes_to_review(stub):
    sub = dict(BASE_SUB, submission_uuid="")
    result = intake.process_portal_submission(sub)
    assert result.status == "review_queue"
    assert stub["review"].call_args.kwargs["payload"]["reason"] == "missing_submission_uuid"


# ---- amend ---------------------------------------------------------------


def test_amend_supersedes_prior_row(stub):
    sub = dict(BASE_SUB, submission_uuid="u2", amends_uuid="u1")
    result = intake.process_portal_submission(sub)
    assert result.status == "processed"
    stub["supersede"].assert_called_once_with(8001, "u1", "u2")


def test_amend_missing_prior_still_files(stub):
    stub["supersede"].return_value = False  # prior not on the sheet
    sub = dict(BASE_SUB, submission_uuid="u2", amends_uuid="u1")
    result = intake.process_portal_submission(sub)
    assert result.status == "processed"  # Box keeps both; supersede pointer just absent
    # The missing-prior case WARNs — never silent (CLAUDE.md "never silent").
    amend_logs = [c for c in stub["log"].call_args_list if c.kwargs.get("error_code") == "portal_amend"]
    assert amend_logs and amend_logs[0].args[0] == intake.Severity.WARN


# ---- transient infra → error (NOT drained) -------------------------------


def test_transient_smartsheet_error_returns_error(stub):
    stub["ensure"].side_effect = SmartsheetError("503")
    result = intake.process_portal_submission(dict(BASE_SUB))
    assert result.status == "error"  # portal_poll will NOT mark-filed → re-pull


def test_transient_box_error_returns_error(stub):
    stub["upload"].side_effect = box_client.BoxRateLimitError("429")
    result = intake.process_portal_submission(dict(BASE_SUB))
    assert result.status == "error"


def test_permanent_box_error_routes_to_review(stub):
    stub["upload"].side_effect = box_client.BoxError("weird 400")
    result = intake.process_portal_submission(dict(BASE_SUB))
    assert result.status == "review_queue"
    assert stub["review"].call_args.kwargs["payload"]["reason"] == "box_error"


# ---- incomplete checklist is flagged, not silent -------------------------


def test_incomplete_checklist_tagged_in_notes_but_still_files(stub):
    stub["incomplete"].return_value = [("sec", "item1", "Item 1"), ("sec", "item2", "Item 2")]
    result = intake.process_portal_submission(dict(BASE_SUB))
    assert result.status == "processed"
    assert "[incomplete: 2 items]" in stub["write"].call_args.kwargs["notes"]


# ---- _file_portal_pdf conflict → suffix → recover (idempotent retry) -------


def test_file_portal_pdf_base_name_success(mocker):
    mocker.patch.object(intake.box_client, "upload_bytes",
                        return_value={"id": "f1", "name": "n", "size": 1})
    link = intake._file_portal_pdf("fld", "2026-06-05", "jha", "u1abcdef00", b"x")
    assert link == "https://app.box.com/file/f1"


def test_file_portal_pdf_conflict_then_suffix(mocker):
    up = mocker.patch.object(
        intake.box_client, "upload_bytes",
        side_effect=[box_client.BoxConflictError("dup"), {"id": "f2", "name": "n", "size": 1}],
    )
    link = intake._file_portal_pdf("fld", "2026-06-05", "jha", "u1abcdef00", b"x")
    assert link == "https://app.box.com/file/f2"
    assert up.call_args_list[1].args[1] == "2026-06-05-jha-u1abcdef.pdf"  # short-uuid suffix


def test_file_portal_pdf_suffix_conflict_recovers_existing_link(mocker):
    mocker.patch.object(
        intake.box_client, "upload_bytes",
        side_effect=[box_client.BoxConflictError("a"), box_client.BoxConflictError("b")],
    )
    mocker.patch.object(
        intake.box_client, "list_folder",
        return_value=[{"id": "r9", "name": "2026-06-05-jha-u1abcdef.pdf", "type": "file"}],
    )
    link = intake._file_portal_pdf("fld", "2026-06-05", "jha", "u1abcdef00", b"x")
    assert link == "https://app.box.com/file/r9"  # recovered the prior partial upload


def test_file_portal_pdf_suffix_conflict_no_recovery_reraises(mocker):
    mocker.patch.object(
        intake.box_client, "upload_bytes",
        side_effect=[box_client.BoxConflictError("a"), box_client.BoxConflictError("b")],
    )
    mocker.patch.object(intake.box_client, "list_folder", return_value=[])
    with pytest.raises(box_client.BoxConflictError):
        intake._file_portal_pdf("fld", "2026-06-05", "jha", "u1abcdef00", b"x")


# ---- _resolve_portal_box_folder ------------------------------------------


def test_resolve_box_folder_category_hit(mocker):
    mocker.patch.object(intake.project_routing, "get_folder_id", return_value="root1")
    mocker.patch.object(intake, "_resolve_box_subfolder", return_value="leaf1")
    fid, note = intake._resolve_portal_box_folder("Bradley 1", "jha")
    assert fid == "leaf1" and note.startswith("category:")


def test_resolve_box_folder_unknown_parent_uses_fallback(mocker):
    mocker.patch.object(intake.project_routing, "get_folder_id", return_value="root1")
    goc = mocker.patch.object(intake.box_client, "get_or_create_folder", return_value="fb1")
    fid, note = intake._resolve_portal_box_folder("Bradley 1", "unknown-parent")
    assert fid == "fb1" and "fallback" in note
    goc.assert_called_once_with("root1", intake.PORTAL_BOX_FALLBACK_FOLDER)


def test_resolve_box_folder_unresolved_root_returns_none(mocker):
    mocker.patch.object(intake.project_routing, "get_folder_id", return_value="")
    fid, note = intake._resolve_portal_box_folder("Bradley 1", "jha")
    assert fid is None and note == "project_box_root_unresolved"


# ---- _portal_review payload completeness ----------------------------------


def test_portal_review_constructs_complete_payload(mocker):
    add = mocker.patch.object(intake.review_queue, "add")
    mocker.patch.object(intake.error_log, "log")
    res = intake._portal_review(
        dict(BASE_SUB), machine_reason="unknown_form", summary="s",
        reason=intake.review_queue.ReviewReason.STRUCTURED_OUTPUT_EDGE, correlation_id="c",
    )
    assert res.status == "review_queue"
    payload = add.call_args.kwargs["payload"]
    for k in ("submission_uuid", "job_id", "form_code", "work_date", "amends_uuid", "reason", "payload_json"):
        assert k in payload
    assert payload["reason"] == "unknown_form"
    assert add.call_args.kwargs["source_file"] == "u1"
