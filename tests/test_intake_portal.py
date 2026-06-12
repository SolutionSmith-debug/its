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
        "attach": mocker.patch.object(intake.smartsheet_client, "attach_pdf_to_row", return_value=42),
        "supersede": mocker.patch.object(intake.week_sheet, "supersede_row", return_value=True),
        "load_def": mocker.patch.object(intake.form_pdf, "load_definition", return_value=DEFINITION),
        "render": mocker.patch.object(intake.form_pdf, "render_submission_pdf", return_value=b"%PDF-1.4"),
        "incomplete": mocker.patch.object(intake.form_pdf, "incomplete_checklist_items", return_value=[]),
        "box_root": mocker.patch.object(intake, "_portal_box_root", return_value=""),  # gated OFF → legacy
        "get_folder_id": mocker.patch.object(intake.project_routing, "get_folder_id", return_value="root1"),
        "resolve_sub": mocker.patch.object(intake, "_resolve_box_subfolder", return_value="leaf1"),
        "upload": mocker.patch.object(intake.box_client, "upload_bytes", return_value={"id": "f9", "name": "x", "size": 5}),
        "get_or_create": mocker.patch.object(intake.box_client, "get_or_create_folder", return_value="fb1"),
        "review": mocker.patch.object(intake.review_queue, "add"),
        "log": mocker.patch.object(intake.error_log, "log"),
        "clamav": mocker.patch.object(intake, "_photo_clamav_enabled", return_value=False),
    }
    return s


# ---- happy path ----------------------------------------------------------


def test_success_files_box_and_sheet_returns_processed(stub):
    result = intake.process_portal_submission(dict(BASE_SUB))
    assert result.status == "processed"
    assert result.box_link == "https://app.box.com/file/f9"
    # PR-4: the structural Box file id rides on the receipt (id in hand from upload).
    assert result.box_file_id == "f9"
    # Wrote the per-submission row with the parsed date + Box link.
    kwargs = stub["write"].call_args.kwargs
    assert kwargs["submission_uuid"] == "u1"
    assert kwargs["work_date"] == date(2026, 6, 5)
    assert kwargs["box_link"] == "https://app.box.com/file/f9"
    assert kwargs["form_code"] == "jha-v1"
    stub["review"].assert_not_called()


def test_success_attaches_rendered_pdf_to_submission_row(stub):
    intake.process_portal_submission(dict(BASE_SUB))
    stub["attach"].assert_called_once()
    sheet_id, row_id, filename, pdf_bytes = stub["attach"].call_args.args
    assert sheet_id == 8001 and row_id == 555   # the week sheet + the submission row id
    assert filename.endswith(".pdf")
    assert pdf_bytes == b"%PDF-1.4"             # the rendered bytes, inline on the row


def test_attach_failure_does_not_fail_filing(stub):
    # The inline attachment is supplementary (Box is the SoR) — a failure is a WARN,
    # never a filing failure.
    stub["attach"].side_effect = SmartsheetError("attach boom")
    result = intake.process_portal_submission(dict(BASE_SUB))
    assert result.status == "processed"


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


# ---- Box mirror tree (PR-K) ----------------------------------------------


def test_mirror_tree_files_into_root_job_week_when_configured(stub):
    stub["box_root"].return_value = "ROOT9"  # mirror tree ON
    stub["get_or_create"].side_effect = ["job9", "week9"]  # ROOT→job, job→week
    result = intake.process_portal_submission(dict(BASE_SUB))
    assert result.status == "processed"
    calls = stub["get_or_create"].call_args_list
    assert calls[0].args == ("ROOT9", "Bradley 1")  # ROOT → per-job folder
    assert calls[1].args == ("job9", "week of 2026-05-30")  # job → per-week folder
    assert stub["upload"].call_args.args[0] == "week9"  # PDF filed into the week folder
    assert "[box:mirror_tree]" in stub["write"].call_args.kwargs["notes"]
    # Legacy category path bypassed entirely.
    stub["get_folder_id"].assert_not_called()
    stub["resolve_sub"].assert_not_called()


def test_mirror_tree_new_job_does_not_strand(stub):
    # Headline fix: with the root configured, a brand-new job (no project_routing
    # entry) self-provisions in Box — NEVER strands with project_box_root_unresolved.
    stub["box_root"].return_value = "ROOT9"
    stub["get_folder_id"].return_value = ""  # new job: no legacy routing
    stub["get_or_create"].side_effect = ["jobN", "weekN"]
    result = intake.process_portal_submission(dict(BASE_SUB))
    assert result.status == "processed"
    stub["review"].assert_not_called()


# ---- dedupe (re-pull) ----------------------------------------------------


def test_dedupe_already_filed_skips_refile_and_recovers_link(stub):
    stub["find"].return_value = {
        "_row_id": 7, week_sheet.COL_SUBMISSION_PDF: "https://app.box.com/file/old",
        "Row Type": week_sheet.ROW_TYPE_SUBMISSION,
    }
    result = intake.process_portal_submission(dict(BASE_SUB))
    assert result.status == "already_filed"
    assert result.box_link == "https://app.box.com/file/old"
    # PR-4: id derived from the recovered link (split on /file/) → no structural id stored.
    assert result.box_file_id == "old"
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
    link, file_id = intake._file_portal_pdf("fld", "2026-06-05", "jha", "u1abcdef00", b"x")
    assert link == "https://app.box.com/file/f1"
    assert file_id == "f1"  # PR-4: the structural id rides alongside the link


def test_file_portal_pdf_conflict_then_suffix(mocker):
    up = mocker.patch.object(
        intake.box_client, "upload_bytes",
        side_effect=[box_client.BoxConflictError("dup"), {"id": "f2", "name": "n", "size": 1}],
    )
    link, file_id = intake._file_portal_pdf("fld", "2026-06-05", "jha", "u1abcdef00", b"x")
    assert link == "https://app.box.com/file/f2"
    assert file_id == "f2"
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
    link, file_id = intake._file_portal_pdf("fld", "2026-06-05", "jha", "u1abcdef00", b"x")
    assert link == "https://app.box.com/file/r9"  # recovered the prior partial upload
    assert file_id == "r9"  # the recovered file's id rides too


def test_file_portal_pdf_suffix_conflict_no_recovery_reraises(mocker):
    mocker.patch.object(
        intake.box_client, "upload_bytes",
        side_effect=[box_client.BoxConflictError("a"), box_client.BoxConflictError("b")],
    )
    mocker.patch.object(intake.box_client, "list_folder", return_value=[])
    with pytest.raises(box_client.BoxConflictError):
        intake._file_portal_pdf("fld", "2026-06-05", "jha", "u1abcdef00", b"x")


# ---- _box_file_id_from_link (already_filed id recovery) -------------------


@pytest.mark.parametrize(
    "link,expected",
    [
        ("https://app.box.com/file/12345", "12345"),
        ("https://app.box.com/file/old", "old"),
        ("", None),
        ("https://app.box.com/folder/9", None),  # no /file/ segment
        ("https://app.box.com/file/", None),     # trailing slash, empty id
    ],
)
def test_box_file_id_from_link(link, expected):
    assert intake._box_file_id_from_link(link) == expected


# ---- _resolve_portal_box_folder ------------------------------------------


def test_resolve_box_folder_category_hit(mocker):
    mocker.patch.object(intake, "_portal_box_root", return_value="")  # legacy path
    mocker.patch.object(intake.project_routing, "get_folder_id", return_value="root1")
    mocker.patch.object(intake, "_resolve_box_subfolder", return_value="leaf1")
    fid, note = intake._resolve_portal_box_folder("Bradley 1", "jha", date(2026, 6, 5))
    assert fid == "leaf1" and note.startswith("category:")


def test_resolve_box_folder_unknown_parent_uses_fallback(mocker):
    mocker.patch.object(intake, "_portal_box_root", return_value="")
    mocker.patch.object(intake.project_routing, "get_folder_id", return_value="root1")
    goc = mocker.patch.object(intake.box_client, "get_or_create_folder", return_value="fb1")
    fid, note = intake._resolve_portal_box_folder("Bradley 1", "unknown-parent", date(2026, 6, 5))
    assert fid == "fb1" and "fallback" in note
    goc.assert_called_once_with("root1", intake.PORTAL_BOX_FALLBACK_FOLDER)


def test_resolve_box_folder_unresolved_root_returns_none(mocker):
    mocker.patch.object(intake, "_portal_box_root", return_value="")
    mocker.patch.object(intake.project_routing, "get_folder_id", return_value="")
    fid, note = intake._resolve_portal_box_folder("Bradley 1", "jha", date(2026, 6, 5))
    assert fid is None and note == "project_box_root_unresolved"


def test_resolve_box_folder_mirror_tree_when_root_set(mocker):
    mocker.patch.object(intake, "_portal_box_root", return_value="ROOT9")
    goc = mocker.patch.object(
        intake.box_client, "get_or_create_folder", side_effect=["jobX", "weekX"]
    )
    fid, note = intake._resolve_portal_box_folder("A/B Site", "jha", date(2026, 6, 5))
    assert fid == "weekX" and note == "mirror_tree"
    assert goc.call_args_list[0].args == ("ROOT9", "A-B Site")  # sanitized job folder
    assert goc.call_args_list[1].args == ("jobX", "week of 2026-05-30")


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


# ---- job-orphan routing → Orphaned Reports (Part C) ----------------------


@pytest.fixture
def orphan_on(stub, mocker):
    """Activate Part C: SHEET_ORPHANED_REPORTS set + a portal Box root + add_rows mocked."""
    mocker.patch.object(intake.sheet_ids, "SHEET_ORPHANED_REPORTS", 7777)
    stub["box_root"].return_value = "boxroot1"
    stub["add_rows"] = mocker.patch.object(
        intake.smartsheet_client, "add_rows", return_value=[111]
    )
    return stub


def test_unknown_job_routes_to_orphaned_reports_when_enabled(orphan_on):
    orphan_on["get_job"].return_value = None
    result = intake.process_portal_submission(dict(BASE_SUB))
    # Rendered + filed to the Orphaned Reports Box folder + a sheet row written; NOT the queue.
    orphan_on["render"].assert_called_once()
    orphan_on["add_rows"].assert_called_once()
    assert orphan_on["add_rows"].call_args.args[0] == 7777  # SHEET_ORPHANED_REPORTS
    row = orphan_on["add_rows"].call_args.args[1][0]
    assert row["Reason"] == "job_not_found" and row["Status"] == "Pending"
    assert row["Box Link"] and row["Submission UUID"] == "u1"
    orphan_on["review"].assert_not_called()
    assert result.status == "review_queue" and result.box_link  # drains (filed)


def test_inactive_job_routes_to_orphaned_reports_when_enabled(orphan_on):
    orphan_on["get_job"].return_value = SimpleNamespace(
        project_name="P", is_active=False, active_status="Inactive"
    )
    intake.process_portal_submission(dict(BASE_SUB))
    orphan_on["add_rows"].assert_called_once()
    assert orphan_on["add_rows"].call_args.args[1][0]["Reason"] == "job_inactive"
    orphan_on["review"].assert_not_called()


def test_orphan_falls_back_to_review_when_disabled(stub, mocker):
    # Part C OFF → the generic Review Queue (pre-Part-C). EXPLICITLY pin SHEET_ORPHANED_REPORTS
    # to 0 — never rely on the module default, which the operator FLIPS at activation (a stale
    # coupling to that default red-CI'd the activation PR #235).
    mocker.patch.object(intake.sheet_ids, "SHEET_ORPHANED_REPORTS", 0)
    stub["get_job"].return_value = None
    stub["box_root"].return_value = "boxroot1"  # box root set, but sheet id 0 → OFF
    result = intake.process_portal_submission(dict(BASE_SUB))
    stub["review"].assert_called_once()
    assert result.status == "review_queue"


def test_empty_job_id_stays_in_review_not_orphan(orphan_on):
    # no_job_id is NOT a job-orphan (brief C3 split) → Review Queue even when Part C is ON.
    result = intake.process_portal_submission(dict(BASE_SUB, job_id=""))
    orphan_on["review"].assert_called_once()
    orphan_on["add_rows"].assert_not_called()
    assert result.status == "review_queue"


def test_orphan_unrenderable_form_falls_back_to_review(orphan_on):
    # A structurally-bad submission (unknown form) is not a clean orphan → Review Queue.
    orphan_on["get_job"].return_value = None
    orphan_on["load_def"].return_value = None
    intake.process_portal_submission(dict(BASE_SUB))
    orphan_on["review"].assert_called_once()
    orphan_on["add_rows"].assert_not_called()


# ==========================================================================
# §34 portal photo screening (PR-2)
# ==========================================================================
import base64  # noqa: E402 — grouped with the photo tests for locality
import io  # noqa: E402

from safety_reports import photo_screen  # noqa: E402

# A form definition with a header-level photo field.
PHOTO_DEFINITION = {
    "form_code": "jha-v1",
    "parent_form_code": "jha",
    "form_name": "Job Hazard Analysis",
    "sections": [
        {
            "type": "header",
            "fields": [
                {"key": "work_location", "input": "text", "label": "Location"},
                {"key": "site_photos", "input": "photo", "label": "Site Photos", "max_count": 4},
            ],
        }
    ],
}


def _jpeg_b64(size=(48, 36), color=(10, 110, 60)) -> str:
    from PIL import Image as _PILImage

    buf = io.BytesIO()
    _PILImage.new("RGB", size, color).save(buf, format="JPEG")
    return base64.b64encode(buf.getvalue()).decode()


def _photo_obj(data: str, name="front.jpg", taken_at="2026-06-12T09:30:00", gps="34.0,-118.2"):
    return {"data": data, "name": name, "taken_at": taken_at, "gps": gps}


def _payload(photos: list[dict]) -> str:
    import json

    return json.dumps({"work_location": "Array A", "site_photos": photos})


# ---- clean photo: files + embeds + uploads originals ---------------------
def test_clean_photo_files_embeds_and_uploads(stub, mocker):
    stub["load_def"].return_value = PHOTO_DEFINITION
    new_ver = mocker.patch.object(
        intake.box_client, "upload_bytes_or_new_version", return_value={"id": "p1"}
    )
    # Let the REAL renderer run so we exercise the photo-grid embed end-to-end.
    real_render = mocker.patch.object(
        intake.form_pdf, "render_submission_pdf", wraps=intake.form_pdf.render_submission_pdf
    )
    sub = dict(BASE_SUB, payload_json=_payload([_photo_obj(_jpeg_b64())]))
    result = intake.process_portal_submission(sub)

    assert result.status == "processed"
    # render received the SCREENED photos (a re-encoded JPEG + caption), not raw base64.
    rendered = real_render.call_args.args[1]
    screened = rendered["screened_photos"]
    assert len(screened) == 1
    caption, jpeg = screened[0]
    assert "front.jpg" in caption and jpeg.startswith(b"\xff\xd8\xff")
    # Box originals filed under ITS Photos/<uuid>/ via version-on-conflict.
    new_ver.assert_called_once()
    folder_arg, name_arg, bytes_arg = new_ver.call_args.args
    assert name_arg == "01.jpg" and bytes_arg.startswith(b"\xff\xd8\xff")
    stub["review"].assert_not_called()


def test_clean_photo_creates_its_photos_subtree(stub, mocker):
    stub["load_def"].return_value = PHOTO_DEFINITION
    mocker.patch.object(intake.box_client, "upload_bytes_or_new_version", return_value={"id": "p1"})
    intake.process_portal_submission(dict(BASE_SUB, payload_json=_payload([_photo_obj(_jpeg_b64())])))
    folder_names = [c.args[1] for c in stub["get_or_create"].call_args_list]
    assert "ITS Photos" in folder_names
    assert "u1" in folder_names  # the per-submission subfolder


# ---- malicious photo: refused, paged, not filed --------------------------
def test_malicious_photo_refused_paged_not_filed(stub, mocker):
    stub["load_def"].return_value = PHOTO_DEFINITION
    # A solid PNG whose dimensions exceed the decompression-bomb cap (small encoded size).
    buf = io.BytesIO()
    from PIL import Image as _PILImage

    _PILImage.new("RGB", (5001, 5001), (1, 1, 1)).save(buf, format="PNG")
    bomb_b64 = base64.b64encode(buf.getvalue()).decode()
    sub = dict(BASE_SUB, payload_json=_payload([_photo_obj(bomb_b64)]), actor_username="pm.jones")
    result = intake.process_portal_submission(sub)

    assert result.status == "review_queue"
    assert result.notes == "reason=photo_malicious"
    # Refused BEFORE render/file: neither the renderer nor the week-sheet writer ran.
    stub["render"].assert_not_called()
    stub["write"].assert_not_called()
    # Review Queue row: security-flagged, CRITICAL, SECURITY_TRIGGER.
    kw = stub["review"].call_args.kwargs
    assert kw["security_flag"] is True
    assert kw["severity"] is intake.Severity.CRITICAL
    assert kw["reason"] is intake.review_queue.ReviewReason.SECURITY_TRIGGER
    assert "pm.jones" in kw["summary"] and "DISABLE" in kw["summary"]
    # CRITICAL page fired naming the account for operator disable.
    crit = [c for c in stub["log"].call_args_list if c.args[0] is intake.Severity.CRITICAL]
    assert crit and "disable this portal account" in crit[0].args[2]


# ---- suspicious photo: refused to review, NOT paged ----------------------
def test_suspicious_photo_routed_to_review_not_paged(stub):
    stub["load_def"].return_value = PHOTO_DEFINITION
    # Wrong magic (GIF) → suspicious (L1 magic_mismatch).
    bad = base64.b64encode(b"GIF89a" + b"\x00" * 64).decode()
    result = intake.process_portal_submission(dict(BASE_SUB, payload_json=_payload([_photo_obj(bad)])))

    assert result.status == "review_queue"
    assert result.notes == "reason=photo_suspicious"
    kw = stub["review"].call_args.kwargs
    assert kw["security_flag"] is True
    assert kw["severity"] is intake.Severity.WARN          # suspicious does NOT page
    assert kw["reason"] is intake.review_queue.ReviewReason.SECURITY_TRIGGER
    crit = [c for c in stub["log"].call_args_list if c.args[0] is intake.Severity.CRITICAL]
    assert not crit
    stub["write"].assert_not_called()


def test_undecodable_base64_is_suspicious(stub):
    stub["load_def"].return_value = PHOTO_DEFINITION
    sub = dict(BASE_SUB, payload_json=_payload([_photo_obj("!!! not base64 !!!")]))
    result = intake.process_portal_submission(sub)
    assert result.status == "review_queue"
    assert result.notes == "reason=photo_suspicious"


# ---- best-effort Box upload (never sinks the filed submission) -----------
def test_photo_box_upload_failure_is_best_effort(stub, mocker):
    stub["load_def"].return_value = PHOTO_DEFINITION
    mocker.patch.object(
        intake.box_client, "upload_bytes_or_new_version",
        side_effect=box_client.BoxError("boom"),
    )
    result = intake.process_portal_submission(dict(BASE_SUB, payload_json=_payload([_photo_obj(_jpeg_b64())])))
    assert result.status == "processed"  # the PDF-of-record already filed; photo upload WARNs
    warns = [c for c in stub["log"].call_args_list
             if c.kwargs.get("error_code") == "portal_photo_upload_failed"]
    assert warns


# ---- no photo field: unchanged behavior ----------------------------------
def test_no_photo_field_files_normally(stub):
    # The default DEFINITION has no photo field → screened_photos empty, no Box photo tree.
    result = intake.process_portal_submission(dict(BASE_SUB))
    assert result.status == "processed"
    assert stub["render"].call_args.args[1]["screened_photos"] == []


# ---- _screen_portal_photos refuses a submission past the per-submission cap ----
def test_screen_over_cap_refuses_whole_submission(mocker):
    mocker.patch.object(intake, "_photo_clamav_enabled", return_value=False)
    review = mocker.patch.object(intake.review_queue, "add")
    mocker.patch.object(intake.error_log, "log")
    # Three photo fields × 3 photos = 9 > MAX_PHOTOS_PER_SUBMISSION (8). A submission past
    # the Worker's cap can only arrive by bypassing the Worker → refuse the whole thing.
    img = _jpeg_b64()
    definition = {"sections": [{"type": "header", "fields": [
        {"key": "a", "input": "photo", "label": "A", "max_count": 4},
        {"key": "b", "input": "photo", "label": "B", "max_count": 4},
        {"key": "c", "input": "photo", "label": "C", "max_count": 4},
    ]}]}
    values = {k: [_photo_obj(img) for _ in range(3)] for k in ("a", "b", "c")}
    refusal, screened = intake._screen_portal_photos(
        definition, values, dict(BASE_SUB), correlation_id="t"
    )
    assert refusal is not None
    assert refusal.status == "review_queue"
    assert refusal.notes == "reason=photo_suspicious"
    assert screened == []
    kw = review.call_args.kwargs
    assert kw["security_flag"] is True
    assert "over_submission_cap" in kw["payload"]["detail"]


def test_screen_at_cap_is_accepted(mocker):
    # Exactly 8 photos (2 fields × 4) is allowed — no refusal.
    mocker.patch.object(intake, "_photo_clamav_enabled", return_value=False)
    img = _jpeg_b64()
    definition = {"sections": [{"type": "header", "fields": [
        {"key": "a", "input": "photo", "label": "A", "max_count": 4},
        {"key": "b", "input": "photo", "label": "B", "max_count": 4},
    ]}]}
    values = {k: [_photo_obj(img) for _ in range(4)] for k in ("a", "b")}
    refusal, screened = intake._screen_portal_photos(
        definition, values, dict(BASE_SUB), correlation_id="t"
    )
    assert refusal is None
    assert len(screened) == photo_screen.MAX_PHOTOS_PER_SUBMISSION
