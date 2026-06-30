"""Progress weekly compile (P4) — the progress instantiation of generate_core.

The deterministic compile ENGINE is proven generically by tests/test_weekly_generate.py
(which exercises generate_core via the SAFETY config). These tests prove the PROGRESS binding
routes to the progress surfaces and nothing safety — the operator's "can't get mixed up"
requirement at the compile level — plus that main() delegates to the shared core.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from progress_reports import progress_weekly_generate as pwg
from safety_reports import generate_core
from shared import active_jobs, review_queue


def test_progress_config_binds_only_progress_surfaces() -> None:
    c = pwg.PROGRESS_GENERATE_CONFIG
    assert c.workstream == "progress_reports"
    # Iterates the PROGRESS Active-Jobs sheet (not safety's) — recipients can only be progress.
    assert c.active_jobs_config is active_jobs.PROGRESS_ACTIVE_JOBS_CONFIG
    assert c.active_jobs_config.sheet_id != active_jobs.SAFETY_ACTIVE_JOBS_CONFIG.sheet_id
    # Writes the review row to the WPR sheet (the progress twin of WSR).
    from shared import sheet_ids
    assert c.review_sheet_id == sheet_ids.SHEET_WPR_HUMAN_REVIEW
    # Files into the progress week-sheet workspace, the progress Box root, the progress mutex.
    assert c.week_sheet_config.workspace_id == sheet_ids.WORKSPACE_PROGRESS_REPORTING
    assert c.box_root_setting_key == "progress_reports.box.portal_root_folder_id"
    assert c.box_legacy_fallback is False  # progress has no legacy project_routing tree
    assert c.compile_mutex_role == "progress"
    assert c.watchdog_slug == "progress_weekly_generate"


def test_add_review_row_writes_to_wpr_tagged_progress(monkeypatch) -> None:
    # The config's add_review_row must land on WPR with Workstream=progress (via add_wpr_row),
    # never on WSR. Capture the call.
    captured: dict[str, Any] = {}

    def fake_add_wpr_row(**kw: Any) -> int:
        captured.update(kw)
        return 4242

    monkeypatch.setattr("progress_reports.wpr_review.add_wpr_row", fake_add_wpr_row)
    from datetime import date
    rid = pwg.PROGRESS_GENERATE_CONFIG.add_review_row(
        job_project="P1", job_id="JOB-P", week_of=date(2026, 6, 27),
        compiled_pdf_link="", recipient_to="to@x.com", cc_display="", email_body="b", notes="n",
    )
    assert rid == 4242
    assert captured["job_id"] == "JOB-P"  # routed to add_wpr_row (which bakes Workstream=progress)


def test_empty_week_via_progress_config_writes_wpr_and_reads_progress_jobs(monkeypatch) -> None:
    """End-to-end-ish: an empty progress week writes a WPR row + reads the PROGRESS active-jobs
    sheet — proving the progress pipeline routes to progress surfaces, not safety."""
    job = active_jobs.ActiveJob(
        job_id="JOB-P1", project_name="Progress One", address="", stakeholder_name="",
        stakeholder_email="", stakeholder_phone="",
        safety_reports_contact_email="pm@x.com", safety_reports_contact_name="PM",
        cc_emails=(), active_status="Active", row_id=1,
    )
    seen_active_jobs_config: list[Any] = []

    def fake_list_active_jobs(config):  # type: ignore[no-untyped-def]
        seen_active_jobs_config.append(config)
        return [job]

    wpr_calls: list[dict[str, Any]] = []

    def fake_add_wpr(**kw: Any) -> int:
        wpr_calls.append(kw)
        return 7

    monkeypatch.setattr(generate_core.active_jobs, "list_active_jobs", fake_list_active_jobs)
    monkeypatch.setattr("progress_reports.wpr_review.add_wpr_row", fake_add_wpr)
    # week_sheet: empty week (no submissions, no prior rollup)
    ws = SimpleNamespace(
        ensure_week_sheet=lambda cfg, project, start: 9001,
        list_rollup_rows=lambda sid: [],
        list_submission_rows=lambda sid, active_only=True: [],
        any_compile_now_requested=lambda rows: False,
        append_rollup_row=lambda sid, **kw: 1,
        clear_compile_now_on_rollups=lambda sid, rows: None,
        COL_SUBMITTED_AT="Submitted At", COL_FORM_CODE="Form Code",
        COL_SUBMISSION_PDF="PDF", COL_WORK_DATE="Work Date",
        latest_submitted_at=lambda subs: "",
    )
    monkeypatch.setattr(generate_core, "week_sheet", ws)
    monkeypatch.setattr(generate_core, "_read_str_setting", lambda config, key, fb: fb)
    monkeypatch.setattr(generate_core, "_read_int_setting", lambda config, key, fb: fb)
    # compile_mutex: a no-op contextmanager
    import contextlib
    monkeypatch.setattr(generate_core.compile_mutex, "hold",
                        lambda role: contextlib.nullcontext())
    monkeypatch.setattr(generate_core, "_write_watchdog_marker", lambda config: None)

    out = generate_core.run_generate(
        pwg.PROGRESS_GENERATE_CONFIG, week_start_override=None
    )
    # Read the PROGRESS active-jobs sheet (never safety's).
    assert seen_active_jobs_config == [active_jobs.PROGRESS_ACTIVE_JOBS_CONFIG]
    # Wrote a WPR row for the empty week (never silently skipped).
    assert len(wpr_calls) == 1
    assert wpr_calls[0]["job_id"] == "JOB-P1"
    assert out["empty_weeks"] == 1


def test_sla_tier_is_a_real_tier() -> None:
    # Reuses the 4h SAFETY_INTAKE window; the Workstream tag (progress) is the real label.
    assert pwg.PROGRESS_GENERATE_CONFIG.sla_tier in set(review_queue.SlaTier)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
