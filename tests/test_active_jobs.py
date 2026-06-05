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
    assert job.job_slug == "bradley-1"
    assert job.safety_reports_contact_email == "safety@bradley.example"
    assert job.is_active
    assert job.row_id == 11


def test_contact_name_and_cc_emails_projected(patch_rows):
    patch_rows([_row("JOB-0001", "Bradley 1", contact="to@x.com",
                     contact_name="Pat PM", cc1="a@x.com", cc2="b@x.com")])
    job = active_jobs.get_job("JOB-0001")
    assert job.safety_reports_contact_name == "Pat PM"
    assert job.cc_emails == ("a@x.com", "b@x.com")


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
