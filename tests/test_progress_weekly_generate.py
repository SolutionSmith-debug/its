"""Progress weekly compile (P4) — the progress instantiation of generate_core.

The deterministic compile ENGINE is proven generically by tests/test_weekly_generate.py
(which exercises generate_core via the SAFETY config). These tests prove the PROGRESS binding
routes to the progress surfaces and nothing safety — the operator's "can't get mixed up"
requirement at the compile level — plus that main() delegates to the shared core.
"""
from __future__ import annotations

from datetime import date, datetime, time
from types import SimpleNamespace
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from progress_reports import progress_weekly_generate as pwg
from safety_reports import generate_core
from shared import active_jobs, review_queue, safety_week


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


# ── P6 rollup-numbers page (progress-only hook) ───────────────────────────────────
def _active_job(job_id: str = "JOB-P", project_name: str = "Prog One") -> active_jobs.ActiveJob:
    return active_jobs.ActiveJob(
        job_id=job_id, project_name=project_name, address="", stakeholder_name="",
        stakeholder_email="", stakeholder_phone="",
        safety_reports_contact_email="pm@x.com", safety_reports_contact_name="PM",
        cc_emails=(), active_status="Active", row_id=1,
    )


def test_progress_config_binds_the_rollup_provider() -> None:
    # Progress binds the closure; safety binds nothing (byte-identical, §14 — pinned separately
    # in tests/test_generate_core.py::test_safety_config_binds_no_rollup_provider).
    assert pwg.PROGRESS_GENERATE_CONFIG.rollup_page_provider is pwg._rollup_page_provider


def test_week_epoch_window_is_pacific_midnight_seven_days() -> None:
    wk = safety_week.week_bounds(date(2026, 6, 5))  # Sat 2026-05-30 → Fri 2026-06-05, no DST edge
    frm, to = pwg._week_epoch_window(wk)
    assert to - frm == 7 * 86400  # exactly one Pacific week (no DST transition in this window)
    expected_from = int(datetime.combine(
        date(2026, 5, 30), time.min, tzinfo=ZoneInfo("America/Los_Angeles")).timestamp())
    assert frm == expected_from  # from = the Saturday's Pacific midnight (inclusive)


def test_resolve_creds_present(monkeypatch) -> None:
    monkeypatch.setattr(pwg.smartsheet_client, "get_setting", lambda key, workstream: "  https://w  ")
    monkeypatch.setattr(pwg.keychain, "get_secret", lambda name: "tok")
    assert pwg._resolve_rollup_creds() == ("https://w", "tok")  # base_url trimmed


def test_resolve_creds_none_when_base_url_empty(monkeypatch) -> None:
    monkeypatch.setattr(pwg.smartsheet_client, "get_setting", lambda key, workstream: "")
    monkeypatch.setattr(pwg.keychain, "get_secret", lambda name: "tok")
    assert pwg._resolve_rollup_creds() is None  # fail-closed: no base_url → no rollup


def test_resolve_creds_none_on_setting_not_found(monkeypatch) -> None:
    def boom(key, workstream):  # type: ignore[no-untyped-def]
        raise pwg.smartsheet_client.SmartsheetNotFoundError("no row")
    monkeypatch.setattr(pwg.smartsheet_client, "get_setting", boom)
    monkeypatch.setattr(pwg.keychain, "get_secret", lambda name: "tok")
    assert pwg._resolve_rollup_creds() is None


def test_resolve_creds_none_when_bearer_missing(monkeypatch) -> None:
    monkeypatch.setattr(pwg.smartsheet_client, "get_setting", lambda key, workstream: "https://w")

    def boom(name):  # type: ignore[no-untyped-def]
        raise pwg.keychain.KeychainError("no keychain entry")
    monkeypatch.setattr(pwg.keychain, "get_secret", boom)
    assert pwg._resolve_rollup_creds() is None  # fail-closed: no bearer → no rollup


def test_rollup_provider_returns_none_when_creds_unset(monkeypatch) -> None:
    # Unwired (pre-cutover) progress workstream → quiet no-op (no page), NOT an error.
    monkeypatch.setattr(pwg, "_resolve_rollup_creds", lambda: None)
    job = _active_job()
    assert pwg._rollup_page_provider(job, safety_week.week_bounds(date(2026, 6, 5))) is None


def test_rollup_provider_fetches_and_renders(monkeypatch) -> None:
    monkeypatch.setattr(pwg, "_resolve_rollup_creds", lambda: ("https://w", "tok"))
    captured: dict[str, Any] = {}

    def fake_get(base_url, bearer, *, job_id, week_from, week_to):  # type: ignore[no-untyped-def]
        captured.update(base_url=base_url, bearer=bearer, job_id=job_id,
                        week_from=week_from, week_to=week_to)
        return {"labor_hours": 8, "equipment": [], "open_tasks": 2}
    monkeypatch.setattr(pwg.portal_client, "get_progress_rollup", fake_get)
    monkeypatch.setattr(pwg.form_pdf, "render_progress_rollup",
                        lambda proj, label, numbers: b"%PDF-roll")
    job = _active_job()
    out = pwg._rollup_page_provider(job, safety_week.week_bounds(date(2026, 6, 5)))
    assert out == b"%PDF-roll"
    assert captured["job_id"] == "JOB-P"
    assert captured["base_url"] == "https://w" and captured["bearer"] == "tok"
    assert captured["week_to"] - captured["week_from"] == 7 * 86400  # the Sat→Fri epoch window


def test_rollup_provider_transport_error_propagates(monkeypatch) -> None:
    # A wired-but-broken rollup RAISES so generate_core's fence WARNs (never silent).
    monkeypatch.setattr(pwg, "_resolve_rollup_creds", lambda: ("https://w", "tok"))

    def boom(*a, **k):  # type: ignore[no-untyped-def]
        raise pwg.portal_client.PortalTransportError("worker 500")
    monkeypatch.setattr(pwg.portal_client, "get_progress_rollup", boom)
    job = _active_job()
    with pytest.raises(pwg.portal_client.PortalTransportError):
        pwg._rollup_page_provider(job, safety_week.week_bounds(date(2026, 6, 5)))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
