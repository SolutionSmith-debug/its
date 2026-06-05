"""Tests for safety_reports/weekly_generate.py — the Phase-5 DETERMINISTIC compile.

All Smartsheet/Box/active_jobs calls are mocked. The legacy LLM-path tests were
retired with the Anthropic core (Phase-5 rewrite). Live coverage is the deploy-gated
tests/test_weekly_generate_integration.py.
"""
from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from safety_reports import week_sheet, weekly_generate

ANCHOR = date(2026, 6, 5)  # Friday → Sat→Fri week 2026-05-30 … 2026-06-05


def _job(**kw):
    base = dict(
        project_name="Bradley 1", job_id="JOB-1",
        safety_reports_contact_email="pm@evergreenmirror.com",
        safety_reports_contact_name="Dana PM",
        cc_emails=("a@x.com", "b@x.com"), is_active=True, active_status="Active",
    )
    base.update(kw)
    return SimpleNamespace(**base)


def _sub(form_code="jha-v1", link="https://app.box.com/file/11", submitted="2026-06-05T08:00:00-07:00"):
    return {
        week_sheet.COL_FORM_CODE: form_code,
        week_sheet.COL_SUBMISSION_PDF: link,
        week_sheet.COL_SUBMITTED_AT: submitted,
        week_sheet.COL_ROW_TYPE: week_sheet.ROW_TYPE_SUBMISSION,
        week_sheet.COL_STATUS: week_sheet.STATUS_ACTIVE,
    }


@pytest.fixture
def stub(mocker) -> dict[str, MagicMock]:
    return {
        "list_jobs": mocker.patch.object(weekly_generate.active_jobs, "list_active_jobs", return_value=[_job()]),
        "ensure": mocker.patch.object(weekly_generate.week_sheet, "ensure_week_sheet", return_value=8001),
        "rollup": mocker.patch.object(weekly_generate.week_sheet, "get_rollup_row", return_value=None),
        "subs": mocker.patch.object(weekly_generate.week_sheet, "list_submission_rows", return_value=[_sub()]),
        "upsert_rollup": mocker.patch.object(weekly_generate.week_sheet, "upsert_rollup_row", return_value=99),
        "download": mocker.patch.object(weekly_generate.box_client, "download_file", return_value=b"%PDF-1.4 one"),
        "merge": mocker.patch.object(weekly_generate.form_pdf, "merge_pdfs", return_value=b"%PDF-merged"),
        "get_root": mocker.patch.object(weekly_generate.project_routing, "get_folder_id", return_value="root1"),
        "mkfolder": mocker.patch.object(weekly_generate.box_client, "get_or_create_folder", return_value="wk1"),
        "upload": mocker.patch.object(weekly_generate.box_client, "upload_bytes", return_value={"id": "pkt9", "name": "x", "size": 9}),
        "wsr": mocker.patch.object(weekly_generate.wsr_review, "upsert_row", return_value=(123, True)),
        "evergreen": mocker.patch.object(weekly_generate, "_read_str_setting", return_value="the office"),
        "review": mocker.patch.object(weekly_generate.review_queue, "add"),
        "marker": mocker.patch.object(weekly_generate, "_write_watchdog_marker"),
        "log": mocker.patch.object(weekly_generate.error_log, "log"),
    }


# ---- pure helpers --------------------------------------------------------


@pytest.mark.parametrize("link,expected", [
    ("https://app.box.com/file/123", "123"),
    ("https://app.box.com/file/9?x=1", "9"),
    ("", None),
    ("not a link", None),
    ("https://app.box.com/folder/5", None),
])
def test_box_file_id(link, expected):
    assert weekly_generate._box_file_id(link) == expected


def test_its_week_folder_name_and_packet_name():
    wk = weekly_generate.safety_week.week_bounds(ANCHOR)
    assert weekly_generate._its_week_folder_name(wk) == "ITS Week of 2026-05-30 to 2026-06-05"
    assert weekly_generate._packet_filename("Bradley 1", wk).startswith(
        "Weekly Safety Report — Bradley 1 — 2026-05-30 to 2026-06-05"
    )


def test_recipient_display():
    to, cc = weekly_generate._recipient_display(_job())
    assert to == "pm@evergreenmirror.com"
    assert cc == "a@x.com, b@x.com"


# ---- happy compile -------------------------------------------------------


def test_compile_merges_files_and_dual_writes(stub):
    out = weekly_generate._run_pipeline(week_start_override=ANCHOR)
    assert out["packets_compiled"] == 1 and out["wsr_written"] == 1
    assert out["week_start"] == "2026-05-30" and out["week_end"] == "2026-06-05"
    stub["merge"].assert_called_once()
    stub["mkfolder"].assert_called_once_with("root1", "ITS Week of 2026-05-30 to 2026-06-05")
    stub["upload"].assert_called_once()
    assert stub["upsert_rollup"].call_args.kwargs["packet_link"] == "https://app.box.com/file/pkt9"
    assert stub["wsr"].call_args.kwargs["compiled_pdf_link"] == "https://app.box.com/file/pkt9"
    assert stub["wsr"].call_args.kwargs["job_id"] == "JOB-1"
    assert stub["wsr"].call_args.kwargs["recipient_to"] == "pm@evergreenmirror.com"


def test_two_submissions_merged_in_order(stub):
    stub["subs"].return_value = [
        _sub("jha-v1", "https://app.box.com/file/11"),
        _sub("toolbox-talk-ppe-v1", "https://app.box.com/file/22"),
    ]
    stub["download"].side_effect = [b"%PDF-A", b"%PDF-B"]
    weekly_generate._run_pipeline(week_start_override=ANCHOR)
    assert stub["merge"].call_args.args[0] == [b"%PDF-A", b"%PDF-B"]


# ---- skip-no-change + compile-now ----------------------------------------


def test_skip_when_already_compiled_no_new_docs(stub):
    stub["rollup"].return_value = {
        "_row_id": 5, week_sheet.COL_ROW_TYPE: week_sheet.ROW_TYPE_ROLLUP,
        week_sheet.COL_SUBMITTED_AT: "2026-06-05T09:00:00-07:00",
        week_sheet.COL_COMPILE_NOW: False,
    }
    out = weekly_generate._run_pipeline(week_start_override=ANCHOR)
    assert out["skipped_no_change"] == 1 and out["packets_compiled"] == 0
    stub["merge"].assert_not_called()
    stub["upload"].assert_not_called()


def test_compile_now_forces_recompile(stub):
    stub["rollup"].return_value = {
        "_row_id": 5, week_sheet.COL_ROW_TYPE: week_sheet.ROW_TYPE_ROLLUP,
        week_sheet.COL_SUBMITTED_AT: "2026-06-05T09:00:00-07:00",
        week_sheet.COL_COMPILE_NOW: True,
    }
    out = weekly_generate._run_pipeline(week_start_override=ANCHOR)
    assert out["packets_compiled"] == 1
    assert stub["upsert_rollup"].call_args.kwargs["existing_rollup_row_id"] == 5


def test_new_doc_since_compile_triggers_recompile(stub):
    stub["rollup"].return_value = {
        "_row_id": 5, week_sheet.COL_ROW_TYPE: week_sheet.ROW_TYPE_ROLLUP,
        week_sheet.COL_SUBMITTED_AT: "2026-06-04T09:00:00-07:00",
        week_sheet.COL_COMPILE_NOW: False,
    }
    out = weekly_generate._run_pipeline(week_start_override=ANCHOR)
    assert out["packets_compiled"] == 1


# ---- empty week ----------------------------------------------------------


def test_empty_week_still_writes_rollup_and_wsr(stub):
    stub["subs"].return_value = []
    out = weekly_generate._run_pipeline(week_start_override=ANCHOR)
    assert out["empty_weeks"] == 1
    stub["merge"].assert_not_called()
    stub["upsert_rollup"].assert_called_once()
    stub["wsr"].assert_called_once()
    assert stub["wsr"].call_args.kwargs["compiled_pdf_link"] == ""


# ---- download failures ---------------------------------------------------


def test_partial_download_failure_still_compiles_available(stub):
    stub["subs"].return_value = [
        _sub("jha-v1", "https://app.box.com/file/11"),
        _sub("toolbox-talk-ppe-v1", "https://app.box.com/file/22"),
    ]
    stub["download"].side_effect = [b"%PDF-A", weekly_generate.box_client.BoxError("404")]
    out = weekly_generate._run_pipeline(week_start_override=ANCHOR)
    assert out["packets_compiled"] == 1 and out["download_errors"] == 1
    assert stub["merge"].call_args.args[0] == [b"%PDF-A"]


def test_all_downloads_fail_writes_wsr_without_packet(stub):
    stub["subs"].return_value = [_sub("jha-v1", "https://app.box.com/file/11")]
    stub["download"].side_effect = weekly_generate.box_client.BoxError("404")
    out = weekly_generate._run_pipeline(week_start_override=ANCHOR)
    stub["merge"].assert_not_called()
    stub["wsr"].assert_called_once()
    assert stub["wsr"].call_args.kwargs["compiled_pdf_link"] == ""
    assert out["packets_compiled"] == 0  # all-downloads-failed is NOT a real packet
    assert out["wsr_written"] == 1       # …but the WSR row IS still written


# ---- blank Submitted At must NOT silently skip (critical) -----------------


def test_blank_submitted_at_forces_recompile_not_skip(stub):
    # A prior compile exists; a submission row carries a BLANK Submitted At →
    # we cannot prove "no new docs", so we MUST recompile (never silently skip).
    stub["rollup"].return_value = {
        "_row_id": 5, week_sheet.COL_ROW_TYPE: week_sheet.ROW_TYPE_ROLLUP,
        week_sheet.COL_SUBMITTED_AT: "2026-06-05T09:00:00-07:00",
        week_sheet.COL_COMPILE_NOW: False,
    }
    stub["subs"].return_value = [_sub("jha-v1", "https://app.box.com/file/11", submitted="")]
    out = weekly_generate._run_pipeline(week_start_override=ANCHOR)
    assert out["packets_compiled"] == 1 and out["skipped_no_change"] == 0
    warns = [c for c in stub["log"].call_args_list
             if c.kwargs.get("error_code") == "weekly_generate.missing_submitted_at"]
    assert warns, "a blank Submitted At with submissions present must WARN, not skip silently"


# ---- missing-contact flagged on CREATE, not on UPDATE (#8) ----------------


def test_missing_to_contact_not_flagged_on_update(stub):
    stub["list_jobs"].return_value = [_job(safety_reports_contact_email="")]
    stub["wsr"].return_value = (123, False)  # existing WSR row (update, not create)
    weekly_generate._run_pipeline(week_start_override=ANCHOR)
    stub["review"].assert_not_called()  # only CREATE flags a missing TO contact


def test_submission_with_no_box_link_excluded(stub):
    stub["subs"].return_value = [_sub("jha-v1", link="")]
    out = weekly_generate._run_pipeline(week_start_override=ANCHOR)
    assert out["download_errors"] == 1
    stub["download"].assert_not_called()


# ---- WSR missing-contact + per-job fence ---------------------------------


def test_missing_to_contact_on_create_flags_review(stub):
    stub["list_jobs"].return_value = [_job(safety_reports_contact_email="")]
    weekly_generate._run_pipeline(week_start_override=ANCHOR)
    stub["review"].assert_called_once()


def test_no_review_when_to_present(stub):
    weekly_generate._run_pipeline(week_start_override=ANCHOR)
    stub["review"].assert_not_called()


def test_per_job_fence_routes_failure_to_review_and_continues(stub):
    from shared.smartsheet_client import SmartsheetError
    stub["list_jobs"].return_value = [_job(project_name="Bradley 1", job_id="JOB-1"),
                                       _job(project_name="Huntley", job_id="JOB-2")]
    stub["ensure"].side_effect = [SmartsheetError("boom"), 8002]
    out = weekly_generate._run_pipeline(week_start_override=ANCHOR)
    assert "Bradley 1" in out["errors_per_job"]
    assert out["packets_compiled"] == 1  # JOB-2 still compiled
    stub["review"].assert_called()


def test_unresolved_box_root_surfaces_to_review(stub):
    stub["get_root"].return_value = ""
    out = weekly_generate._run_pipeline(week_start_override=ANCHOR)
    assert out["errors_per_job"]
    stub["review"].assert_called()


# ---- watchdog marker always written --------------------------------------


def test_watchdog_marker_written(stub):
    weekly_generate._run_pipeline(week_start_override=ANCHOR)
    stub["marker"].assert_called_once()
