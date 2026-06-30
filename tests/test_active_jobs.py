"""Unit tests for shared.active_jobs (ITS_Active_Jobs Job-ID lookup)."""
from __future__ import annotations

from typing import Any

import pytest

from shared import active_jobs, smartsheet_client


def _row(job_id: str, project: str, active: str = "Active", row_id: int = 1, **extra: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "Job ID": job_id,
        "Project Name": project,
        "Job Slug": extra.get("slug", ""),
        "Address": extra.get("address", ""),
        "Stakeholder Name": extra.get("stakeholder_name", ""),
        "Stakeholder Email": extra.get("stakeholder_email", ""),
        "Stakeholder Phone": extra.get("stakeholder_phone", ""),
        "Safety Reports Contact Email": extra.get("contact", ""),
        "Safety Reports Contact Name": extra.get("contact_name", ""),
        "CC 1": extra.get("cc1", ""),
        "CC 2": extra.get("cc2", ""),
        "CC 3": extra.get("cc3", ""),
        "CC 4": extra.get("cc4", ""),
        "CC 5": extra.get("cc5", ""),
        "Active": active,
        "_row_id": row_id,
    }
    return base


@pytest.fixture(autouse=True)
def _clear_cache():
    active_jobs.invalidate_cache()
    yield
    active_jobs.invalidate_cache()


@pytest.fixture
def patch_rows(monkeypatch):
    calls = {"n": 0}

    def _install(rows: list[dict[str, Any]]):
        def fake_get_rows(sheet_id: int):
            calls["n"] += 1
            return rows
        monkeypatch.setattr(smartsheet_client, "get_rows", fake_get_rows)
        return calls

    return _install


def test_get_job_resolves_by_autonumber_job_id(patch_rows):
    patch_rows([
        _row("JOB-0001", "Bradley 1", slug="bradley-1", row_id=11,
             contact="safety@bradley.example"),
        _row("JOB-0002", "Brimfield 1", slug="brimfield-1", row_id=12),
    ])
    job = active_jobs.get_job("JOB-0001")
    assert job is not None
    assert job.project_name == "Bradley 1"
    assert job.safety_reports_contact_email == "safety@bradley.example"
    assert job.is_active
    assert job.row_id == 11


def test_contact_name_and_cc_emails_projected(patch_rows):
    patch_rows([_row("JOB-0001", "Bradley 1", contact="to@x.com",
                     contact_name="Pat PM", cc1="a@x.com", cc2="b@x.com")])
    job = active_jobs.get_job("JOB-0001")
    assert job.safety_reports_contact_name == "Pat PM"
    assert job.cc_emails == ("a@x.com", "b@x.com")


# ── P4: progress-config parameterization (parameterize-not-clone) ─────────────


def _progress_row(job_id: str, project: str, active: str = "Active", row_id: int = 1,
                  **extra: Any) -> dict[str, Any]:
    """A row as it appears on ITS_Active_Jobs_Progress — the PROGRESS contact columns are
    DISTINCT from the safety ones (a real progress sheet has both, populated independently)."""
    base = _row(job_id, project, active, row_id, **extra)
    base["Progress Reports Contact Email"] = extra.get("p_contact", "")
    base["Progress Reports Contact Name"] = extra.get("p_contact_name", "")
    return base


def test_progress_config_reads_progress_contact_columns(patch_rows):
    patch_rows([_progress_row("JOB-0001", "Bradley 1",
                              p_contact="progress@bradley.example", p_contact_name="Dana PM")])
    jobs = active_jobs.list_active_jobs(active_jobs.PROGRESS_ACTIVE_JOBS_CONFIG)
    assert len(jobs) == 1
    assert jobs[0].reports_contact_email == "progress@bradley.example"
    assert jobs[0].reports_contact_name == "Dana PM"


def test_progress_config_ignores_the_safety_contact_column(monkeypatch):
    # A row carrying BOTH contacts — the progress reader must (a) ROUTE to the progress sheet
    # AND (b) pick the PROGRESS contact, never safety's. Asserting the sheet_id passed to
    # get_rows proves the routing half; a bug that read the safety sheet regardless of config
    # would fail here. This is the "can't get mixed up" guarantee at the recipient-SOURCE layer.
    row = _progress_row("JOB-0001", "Bradley 1",
                        contact="safety@x.example", p_contact="progress@x.example")
    seen: list[int] = []

    def fake_get_rows(sheet_id: int) -> list[dict[str, Any]]:
        seen.append(sheet_id)
        return [row]

    monkeypatch.setattr(smartsheet_client, "get_rows", fake_get_rows)
    job = active_jobs.get_job("JOB-0001", active_jobs.PROGRESS_ACTIVE_JOBS_CONFIG)
    assert job is not None
    assert seen == [active_jobs.PROGRESS_ACTIVE_JOBS_CONFIG.sheet_id]  # ROUTING proven
    assert job.reports_contact_email == "progress@x.example"          # COLUMN proven
    assert job.reports_contact_email != "safety@x.example"


def test_safety_config_ignores_the_progress_contact_column(monkeypatch):
    # The mirror guarantee: the safety reader (default config) ROUTES to the safety sheet and
    # never picks up the progress contact.
    row = _progress_row("JOB-0001", "Bradley 1",
                        contact="safety@x.example", p_contact="progress@x.example")
    seen: list[int] = []

    def fake_get_rows(sheet_id: int) -> list[dict[str, Any]]:
        seen.append(sheet_id)
        return [row]

    monkeypatch.setattr(smartsheet_client, "get_rows", fake_get_rows)
    job = active_jobs.get_job("JOB-0001")  # safety default
    assert job is not None
    assert seen == [active_jobs.SAFETY_ACTIVE_JOBS_CONFIG.sheet_id]  # ROUTING proven
    assert job.reports_contact_email == "safety@x.example"           # COLUMN proven


def test_reports_contact_aliases_mirror_the_field(patch_rows):
    patch_rows([_row("JOB-0001", "Bradley 1", contact="to@x.com", contact_name="Pat PM")])
    job = active_jobs.get_job("JOB-0001")  # safety default
    assert job is not None
    assert job.reports_contact_email == job.safety_reports_contact_email == "to@x.com"
    assert job.reports_contact_name == job.safety_reports_contact_name == "Pat PM"


def test_safety_and_progress_caches_are_isolated(monkeypatch):
    # Distinct sheet_ids → distinct cache slots → a safety read and a progress read never
    # share state (return DISTINCT rows per sheet so any leak is visible), and each sheet is
    # read exactly once then served from its own cache.
    safety_rows = [_row("JOB-S", "Safety Job", contact="s@x.com")]
    progress_rows = [_progress_row("JOB-P", "Progress Job", p_contact="p@x.com")]
    calls = {"safety": 0, "progress": 0}

    def fake_get_rows(sheet_id: int) -> list[dict[str, Any]]:
        if sheet_id == active_jobs.SAFETY_ACTIVE_JOBS_CONFIG.sheet_id:
            calls["safety"] += 1
            return safety_rows
        if sheet_id == active_jobs.PROGRESS_ACTIVE_JOBS_CONFIG.sheet_id:
            calls["progress"] += 1
            return progress_rows
        return []

    monkeypatch.setattr(smartsheet_client, "get_rows", fake_get_rows)
    s = active_jobs.list_active_jobs()  # safety default
    p = active_jobs.list_active_jobs(active_jobs.PROGRESS_ACTIVE_JOBS_CONFIG)
    assert [j.job_id for j in s] == ["JOB-S"]
    assert [j.job_id for j in p] == ["JOB-P"]
    assert p[0].reports_contact_email == "p@x.com"
    assert calls == {"safety": 1, "progress": 1}
    # Repeat reads are served from each sheet's own cache — no extra fetches, no cross-fill.
    active_jobs.list_active_jobs()
    active_jobs.list_active_jobs(active_jobs.PROGRESS_ACTIVE_JOBS_CONFIG)
    assert calls == {"safety": 1, "progress": 1}


def test_cc_slot_splits_comma_separated_and_dedups_case_insensitively(patch_rows):
    patch_rows([_row("JOB-0001", "Bradley 1",
                     cc1="a@x.com, b@x.com", cc2="A@X.com", cc3="c@x.com")])
    # comma-split flatten; case-insensitive de-dup (first spelling wins); order preserved
    assert active_jobs.get_job("JOB-0001").cc_emails == ("a@x.com", "b@x.com", "c@x.com")


def test_cc_malformed_entries_skipped_and_warned(patch_rows, caplog):
    import logging
    patch_rows([_row("JOB-0001", "Bradley 1", cc1="good@x.com, not-an-email, also bad@x")])
    with caplog.at_level(logging.WARNING):
        job = active_jobs.get_job("JOB-0001")
    assert job.cc_emails == ("good@x.com",)  # the two malformed entries dropped
    assert sum("malformed CC" in r.message for r in caplog.records) == 2  # both announced


def test_no_cc_or_contact_name_yields_empty(patch_rows):
    patch_rows([_row("JOB-0001", "Bradley 1")])
    job = active_jobs.get_job("JOB-0001")
    assert job.cc_emails == ()
    assert job.safety_reports_contact_name == ""


def test_get_job_unknown_returns_none(patch_rows):
    patch_rows([_row("JOB-0001", "Bradley 1")])
    assert active_jobs.get_job("JOB-9999") is None


def test_get_job_returns_inactive_job_so_caller_can_distinguish(patch_rows):
    patch_rows([_row("JOB-0003", "Huntley", active="Inactive", row_id=33)])
    job = active_jobs.get_job("JOB-0003")
    assert job is not None
    assert job.active_status == "Inactive"
    assert not job.is_active


def test_get_job_strips_and_handles_blank_query(patch_rows):
    patch_rows([_row("JOB-0001", "Bradley 1")])
    assert active_jobs.get_job("  JOB-0001  ").project_name == "Bradley 1"
    assert active_jobs.get_job("") is None
    assert active_jobs.get_job("   ") is None


def test_rows_missing_job_id_or_project_are_skipped(patch_rows):
    patch_rows([
        _row("", "No Job ID", row_id=1),
        _row("JOB-0004", "", row_id=2),
        {"Job ID": "JOB-0005", "Project Name": "No Row Id"},  # missing _row_id
        _row("JOB-0006", "Rockford", row_id=6),
    ])
    assert active_jobs.get_job("JOB-0006").project_name == "Rockford"
    assert active_jobs.get_job("JOB-0004") is None
    assert active_jobs.get_job("JOB-0005") is None


def test_numeric_phone_cell_coerces_to_string(patch_rows):
    patch_rows([_row("JOB-0001", "Bradley 1", stakeholder_phone=None)])
    # Smartsheet may hand back a phone typed as a number.
    rows = [_row("JOB-0007", "Brimfield 2", row_id=7)]
    rows[0]["Stakeholder Phone"] = 5095551234
    patch_rows(rows)
    job = active_jobs.get_job("JOB-0007")
    assert job.stakeholder_phone == "5095551234"


def test_list_active_jobs_filters_to_active_only(patch_rows):
    patch_rows([
        _row("JOB-0001", "Bradley 1", active="Active", row_id=1),
        _row("JOB-0002", "Brimfield 1", active="Inactive", row_id=2),
        _row("JOB-0003", "Huntley", active="Archived", row_id=3),
        _row("JOB-0004", "Rockford", active="Active", row_id=4),
        _row("JOB-0005", "Half Row", active="", row_id=5),  # blank status → not Active
    ])
    names = {j.project_name for j in active_jobs.list_active_jobs()}
    assert names == {"Bradley 1", "Rockford"}


def test_read_failure_surfaces_empty_and_warns(monkeypatch, caplog):
    def boom(sheet_id: int):
        raise smartsheet_client.SmartsheetNotFoundError("sheet gone")
    monkeypatch.setattr(smartsheet_client, "get_rows", boom)
    import logging
    with caplog.at_level(logging.WARNING):
        assert active_jobs.get_job("JOB-0001") is None
        assert active_jobs.list_active_jobs() == []
    assert any("read failed" in r.message for r in caplog.records)


def test_ttl_cache_avoids_repeat_reads(patch_rows):
    calls = patch_rows([_row("JOB-0001", "Bradley 1")])
    active_jobs.get_job("JOB-0001")
    active_jobs.get_job("JOB-0001")
    active_jobs.list_active_jobs()
    assert calls["n"] == 1  # one fetch served all three within the TTL window
    active_jobs.invalidate_cache()
    active_jobs.get_job("JOB-0001")
    assert calls["n"] == 2
