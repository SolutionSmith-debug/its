"""Unit tests for safety_reports/weekly_generate.py.

All external services mocked. Tests call `_run_pipeline` directly to
bypass the @require_active + @its_error_log decorator stack. Capability
gating is exercised separately by tests/test_capability_gating.py.

Structure mirrors tests/test_intake.py — pipeline-stage tests for the
pure helpers, then end-to-end tests through `_run_pipeline` with the
full mock stack.
"""
from __future__ import annotations

import ast
import json
from datetime import UTC, date, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from safety_reports import weekly_generate
from safety_reports.week_folder import WeekScaffold
from safety_reports.weekly_generate import (
    GENERATE_WPR_TOOL_NAME,
    GenerationResult,
    ProjectInputs,
    RunSummary,
    _check_anomalies,
    _compose_notes,
    _handle_standard_project,
    _handle_zero_data_week,
    _project_tool_use,
    _resolve_existing_wpr_row,
    _row_in_week,
    _run_pipeline,
    iter_active_projects,
)
from shared import review_queue
from shared.scheduling import ReviewerChain, ReviewerSlot
from shared.smartsheet_client import SmartsheetError, SmartsheetNotFoundError

# ---- Fixtures / helpers --------------------------------------------------


def _build_tool_use_response(input_dict: dict[str, Any]) -> SimpleNamespace:
    """Build a fake Anthropic response with one matching tool_use block."""
    block = SimpleNamespace(
        type="tool_use",
        name=GENERATE_WPR_TOOL_NAME,
        input=input_dict,
    )
    return SimpleNamespace(content=[block])


VALID_TOOL_INPUT: dict[str, Any] = {
    "draft_body": (
        "Bradley 1\n[REVIEWER TO FILL — Location]\nEvergreen Renewables Weekly "
        "Progress Record\n...full draft body would go here..."
    ),
    "confidence": 0.92,
    "incident_counts": {
        "lost_time_accidents": 0,
        "lost_work_days": 0,
        "job_transfer_or_restriction": 0,
        "near_misses": 0,
        "other_recordable_cases": 0,
        "first_aid_cases": 0,
    },
    "safety_topics_covered": ["PPE", "Trip Hazards", "Hydration"],
    "narrative_summary": "Crew completed module install on Block C this week.",
    "anomaly_flags": [],
    "data_completeness": "complete",
}


def _chain_with(emails: list[str]) -> ReviewerChain:
    """Build a ReviewerChain with the given email list as positional slots."""
    slots = tuple(
        ReviewerSlot(email=e, joins_at_offset_hours=4 * i)
        for i, e in enumerate(emails)
    )
    return ReviewerChain(workstream="safety_reports", on_date=date(2026, 5, 22), slots=slots)


def _scaffold(daily_sheet_id: int = 111, rollup_sheet_id: int = 222) -> WeekScaffold:
    return WeekScaffold(
        folder_id=999,
        daily_reports_sheet_id=daily_sheet_id,
        weekly_rollup_sheet_id=rollup_sheet_id,
    )


def _project_inputs(
    project_name: str = "Bradley 1",
    daily_rows: list[dict[str, Any]] | None = None,
    rollup_rows: list[dict[str, Any]] | None = None,
) -> ProjectInputs:
    return ProjectInputs(
        project_name=project_name,
        folder_id=999,
        week_start=date(2026, 5, 18),
        week_end=date(2026, 5, 24),
        daily_reports_sheet_id=111,
        weekly_rollup_sheet_id=222,
        daily_reports_rows=daily_rows or [],
        weekly_rollup_rows=rollup_rows or [],
    )


@pytest.fixture
def _patch_all(mocker):
    """Default mock surface — every external call short-circuited.

    Tests override individual mocks via the returned dict. Default config
    reads return safe fallbacks; default Smartsheet reads return empty
    lists; default chain has one reviewer (Teala) so the empty-chain
    abort path doesn't fire unless a test wants it.
    """
    mocks = {
        "resolve_chain": mocker.patch(
            "safety_reports.weekly_generate.scheduling.resolve_chain",
            return_value=_chain_with(["teala@example.com"]),
        ),
        "get_setting": mocker.patch(
            "safety_reports.weekly_generate.smartsheet_client.get_setting",
            side_effect=SmartsheetNotFoundError("default test stub"),
        ),
        "get_rows": mocker.patch(
            "safety_reports.weekly_generate.smartsheet_client.get_rows",
            return_value=[],
        ),
        "add_rows": mocker.patch(
            "safety_reports.weekly_generate.smartsheet_client.add_rows",
            return_value=[12345],
        ),
        "update_rows": mocker.patch(
            "safety_reports.weekly_generate.smartsheet_client.update_rows",
            return_value=None,
        ),
        "ensure_folder": mocker.patch(
            "safety_reports.weekly_generate.ensure_current_week_folder",
            return_value=_scaffold(),
        ),
        "anthropic_call": mocker.patch(
            "safety_reports.weekly_generate.anthropic_client.call",
            return_value=_build_tool_use_response(VALID_TOOL_INPUT),
        ),
        "review_queue_add": mocker.patch(
            "safety_reports.weekly_generate.review_queue.add",
            return_value=99999,
        ),
        "error_log": mocker.patch(
            "safety_reports.weekly_generate.error_log.log",
            return_value=None,
        ),
        "marker": mocker.patch(
            "safety_reports.weekly_generate._write_watchdog_marker",
            return_value=None,
        ),
        # Restrict iteration to a single project for end-to-end tests so
        # we don't have to thread 6 projects' worth of mocks. Tests that
        # care about multi-project iteration override this.
        "iter_projects": mocker.patch(
            "safety_reports.weekly_generate.iter_active_projects",
            return_value=[(999, "Bradley 1")],
        ),
    }
    return mocks


# ---- Target-week resolution + helpers ------------------------------------


def test_iter_active_projects_returns_sorted_pairs():
    """Order is stable so smoke output is reproducible."""
    pairs = iter_active_projects()
    folder_ids = [p[0] for p in pairs]
    assert folder_ids == sorted(folder_ids)
    assert len(pairs) == 6  # six configured projects per PROJECT_NAME_BY_FOLDER_ID


def test_row_in_week_iso_string_inside_range():
    row = {"Report Date": "2026-05-20"}
    assert _row_in_week(row, date(2026, 5, 18), date(2026, 5, 24)) is True


def test_row_in_week_iso_string_outside_range():
    row = {"Report Date": "2026-05-10"}
    assert _row_in_week(row, date(2026, 5, 18), date(2026, 5, 24)) is False


def test_row_in_week_date_object_inside_range():
    row = {"Report Date": date(2026, 5, 19)}
    assert _row_in_week(row, date(2026, 5, 18), date(2026, 5, 24)) is True


def test_row_in_week_unparseable_excluded():
    row = {"Report Date": "not-a-date"}
    assert _row_in_week(row, date(2026, 5, 18), date(2026, 5, 24)) is False


def test_row_in_week_missing_field_excluded():
    row: dict[str, Any] = {}
    assert _row_in_week(row, date(2026, 5, 18), date(2026, 5, 24)) is False


# ---- _compose_notes ------------------------------------------------------


def test_compose_notes_with_no_tags_emits_timestamp_only():
    ts = datetime(2026, 5, 22, 14, 0, 0, tzinfo=UTC)
    notes = _compose_notes([], ts)
    assert notes == "generated=2026-05-22T14:00:00+00:00"


def test_compose_notes_joins_tags_with_timestamp():
    ts = datetime(2026, 5, 22, 14, 0, 0, tzinfo=UTC)
    notes = _compose_notes(["[ZERO_DATA_WEEK]", "[NO_RECIPIENTS]"], ts)
    assert notes.startswith("[ZERO_DATA_WEEK] [NO_RECIPIENTS]")
    assert "generated=2026-05-22T14:00:00+00:00" in notes


# ---- _project_tool_use ---------------------------------------------------


def test_project_tool_use_happy_path():
    response = _build_tool_use_response(VALID_TOOL_INPUT)
    result = _project_tool_use(response)
    assert result is not None
    assert result.confidence == pytest.approx(0.92)
    assert result.safety_topics_covered == ["PPE", "Trip Hazards", "Hydration"]
    assert result.data_completeness == "complete"


def test_project_tool_use_returns_none_when_no_tool_use():
    response = SimpleNamespace(content=[SimpleNamespace(type="text", text="ignored")])
    assert _project_tool_use(response) is None


def test_project_tool_use_returns_none_on_missing_required_field():
    bad = {k: v for k, v in VALID_TOOL_INPUT.items() if k != "draft_body"}
    response = _build_tool_use_response(bad)
    assert _project_tool_use(response) is None


# ---- _check_anomalies ----------------------------------------------------


def _generation_result(**overrides: Any) -> GenerationResult:
    base = dict(VALID_TOOL_INPUT)
    base.update(overrides)
    return GenerationResult(
        draft_body=str(base["draft_body"]),
        confidence=float(base["confidence"]),
        incident_counts={k: int(v) for k, v in base["incident_counts"].items()},
        safety_topics_covered=list(base["safety_topics_covered"]),
        narrative_summary=str(base["narrative_summary"]),
        anomaly_flags=list(base["anomaly_flags"]),
        data_completeness=str(base["data_completeness"]),
    )


def test_check_anomalies_clean_returns_no_trigger():
    result = _generation_result()
    signals = _check_anomalies(result, result.anomaly_flags)
    assert signals.security_trigger is False
    assert signals.reasons == []


def test_check_anomalies_self_reported_injection_triggers():
    result = _generation_result(anomaly_flags=["apparent_injection_attempt"])
    signals = _check_anomalies(result, result.anomaly_flags)
    assert signals.security_trigger is True
    assert "apparent_injection_attempt" in signals.reasons


def test_check_anomalies_low_severity_self_report_does_not_trigger():
    # `inconsistent_dates` is a model-surfaced anomaly but not a security
    # trigger — security_trigger only fires for explicit injection sentinels.
    result = _generation_result(anomaly_flags=["inconsistent_dates"])
    signals = _check_anomalies(result, result.anomaly_flags)
    assert signals.security_trigger is False


# ---- _resolve_existing_wpr_row -------------------------------------------


def test_resolve_existing_wpr_row_missing(_patch_all):
    _patch_all["get_rows"].return_value = []
    row_id, approved = _resolve_existing_wpr_row("Bradley 1", date(2026, 5, 18))
    assert row_id is None
    assert approved is False


def test_resolve_existing_wpr_row_unapproved(_patch_all):
    _patch_all["get_rows"].return_value = [
        {"_row_id": 42, "Job": "Bradley 1", "Week": "2026-05-18", "Approved for Send": False}
    ]
    row_id, approved = _resolve_existing_wpr_row("Bradley 1", date(2026, 5, 18))
    assert row_id == 42
    assert approved is False


def test_resolve_existing_wpr_row_approved(_patch_all):
    _patch_all["get_rows"].return_value = [
        {"_row_id": 42, "Job": "Bradley 1", "Week": "2026-05-18", "Approved for Send": True}
    ]
    row_id, approved = _resolve_existing_wpr_row("Bradley 1", date(2026, 5, 18))
    assert row_id == 42
    assert approved is True


# ---- _handle_zero_data_week ----------------------------------------------


def test_handle_zero_data_week_writes_placeholder_row(_patch_all):
    summary = RunSummary()
    _handle_zero_data_week(
        inputs=_project_inputs(),
        existing_row_id=None,
        recipients=["customer@example.com"],
        correlation_id="abc123",
        summary=summary,
    )
    _patch_all["add_rows"].assert_called_once()
    _patch_all["anthropic_call"].assert_not_called()
    assert summary.drafts_written == 1
    assert summary.drafts_zero_data == 1
    # Inspect the add_rows payload
    rows_arg = _patch_all["add_rows"].call_args[0][1]
    assert rows_arg[0]["Notes"].startswith("[ZERO_DATA_WEEK]")
    assert "No reports submitted" in rows_arg[0]["Draft Body"]
    assert rows_arg[0]["Approved for Send"] is False


def test_handle_zero_data_week_missing_recipients_tags_and_queues(_patch_all):
    summary = RunSummary()
    _handle_zero_data_week(
        inputs=_project_inputs(),
        existing_row_id=None,
        recipients=[],
        correlation_id="abc123",
        summary=summary,
    )
    _patch_all["review_queue_add"].assert_called_once()
    rows_arg = _patch_all["add_rows"].call_args[0][1]
    assert "[ZERO_DATA_WEEK]" in rows_arg[0]["Notes"]
    assert "[NO_RECIPIENTS]" in rows_arg[0]["Notes"]
    assert summary.review_queue_entries == 1


# ---- _handle_standard_project --------------------------------------------


def test_handle_standard_project_writes_draft_row(_patch_all):
    summary = RunSummary()
    inputs = _project_inputs(
        daily_rows=[{"Report Date": "2026-05-20", "Category": "Daily JHA"}]
    )
    _handle_standard_project(
        inputs=inputs,
        existing_row_id=None,
        recipients=["customer@example.com"],
        threshold=0.85,
        model="claude-sonnet-4-6",
        correlation_id="abc123",
        summary=summary,
    )
    _patch_all["add_rows"].assert_called_once()
    rows_arg = _patch_all["add_rows"].call_args[0][1]
    assert rows_arg[0]["Approved for Send"] is False
    assert rows_arg[0]["Job"] == "Bradley 1"
    assert summary.drafts_written == 1
    assert summary.anthropic_calls == 1
    assert summary.review_queue_entries == 0  # confidence high, no security trigger


def test_handle_standard_project_low_confidence_dual_writes(_patch_all):
    """Low confidence writes WPR row AND a parallel Review Queue row."""
    _patch_all["anthropic_call"].return_value = _build_tool_use_response(
        dict(VALID_TOOL_INPUT, confidence=0.50)
    )
    summary = RunSummary()
    _handle_standard_project(
        inputs=_project_inputs(
            daily_rows=[{"Report Date": "2026-05-20"}]
        ),
        existing_row_id=None,
        recipients=["customer@example.com"],
        threshold=0.85,
        model="m",
        correlation_id="abc123",
        summary=summary,
    )
    _patch_all["add_rows"].assert_called_once()
    _patch_all["review_queue_add"].assert_called_once()
    rows_arg = _patch_all["add_rows"].call_args[0][1]
    assert "[LOW_CONFIDENCE: 0.50]" in rows_arg[0]["Notes"]
    rq_kwargs = _patch_all["review_queue_add"].call_args.kwargs
    assert rq_kwargs["reason"] == review_queue.ReviewReason.LOW_CONFIDENCE_EXTRACTION
    assert summary.review_queue_entries == 1


def test_handle_standard_project_security_trigger_dual_writes(_patch_all):
    _patch_all["anthropic_call"].return_value = _build_tool_use_response(
        dict(VALID_TOOL_INPUT, anomaly_flags=["apparent_injection_attempt"])
    )
    summary = RunSummary()
    _handle_standard_project(
        inputs=_project_inputs(daily_rows=[{"Report Date": "2026-05-20"}]),
        existing_row_id=None,
        recipients=["customer@example.com"],
        threshold=0.85,
        model="m",
        correlation_id="abc123",
        summary=summary,
    )
    _patch_all["add_rows"].assert_called_once()
    _patch_all["review_queue_add"].assert_called_once()
    rows_arg = _patch_all["add_rows"].call_args[0][1]
    assert "[SECURITY_TRIGGER]" in rows_arg[0]["Notes"]
    rq_kwargs = _patch_all["review_queue_add"].call_args.kwargs
    assert rq_kwargs["reason"] == review_queue.ReviewReason.SECURITY_TRIGGER
    assert rq_kwargs["security_flag"] is True


def test_handle_standard_project_missing_recipients_writes_with_empty_field(_patch_all):
    summary = RunSummary()
    _handle_standard_project(
        inputs=_project_inputs(daily_rows=[{"Report Date": "2026-05-20"}]),
        existing_row_id=None,
        recipients=[],
        threshold=0.85,
        model="m",
        correlation_id="abc123",
        summary=summary,
    )
    _patch_all["add_rows"].assert_called_once()
    _patch_all["review_queue_add"].assert_called_once()
    rows_arg = _patch_all["add_rows"].call_args[0][1]
    assert rows_arg[0]["Recipients"] == "[]"
    assert "[NO_RECIPIENTS]" in rows_arg[0]["Notes"]
    rq_kwargs = _patch_all["review_queue_add"].call_args.kwargs
    assert rq_kwargs["reason"] == review_queue.ReviewReason.OTHER


def test_handle_standard_project_no_tool_use_routes_to_review_queue_no_wpr_row(_patch_all):
    _patch_all["anthropic_call"].return_value = SimpleNamespace(
        content=[SimpleNamespace(type="text", text="model emitted prose")]
    )
    summary = RunSummary()
    _handle_standard_project(
        inputs=_project_inputs(daily_rows=[{"Report Date": "2026-05-20"}]),
        existing_row_id=None,
        recipients=["customer@example.com"],
        threshold=0.85,
        model="m",
        correlation_id="abc123",
        summary=summary,
    )
    _patch_all["add_rows"].assert_not_called()
    _patch_all["update_rows"].assert_not_called()
    _patch_all["review_queue_add"].assert_called_once()
    assert summary.drafts_written == 0


def test_handle_standard_project_unapproved_row_updates_not_adds(_patch_all):
    summary = RunSummary()
    _handle_standard_project(
        inputs=_project_inputs(daily_rows=[{"Report Date": "2026-05-20"}]),
        existing_row_id=42,
        recipients=["customer@example.com"],
        threshold=0.85,
        model="m",
        correlation_id="abc123",
        summary=summary,
    )
    _patch_all["update_rows"].assert_called_once()
    _patch_all["add_rows"].assert_not_called()
    updates = _patch_all["update_rows"].call_args[0][1]
    # The update payload must NOT touch the approval columns.
    assert "Approved for Send" not in updates[0]
    assert "Approved By" not in updates[0]
    assert "Approved At" not in updates[0]
    assert updates[0]["_row_id"] == 42


# ---- _run_pipeline end-to-end --------------------------------------------


def test_run_pipeline_empty_chain_aborts_without_writes(_patch_all):
    _patch_all["resolve_chain"].return_value = _chain_with([])  # everyone out
    result = _run_pipeline(week_start_override=date(2026, 5, 18))
    assert result["aborted_empty_chain"] is True
    _patch_all["anthropic_call"].assert_not_called()
    _patch_all["add_rows"].assert_not_called()
    _patch_all["update_rows"].assert_not_called()
    # error_log.log fired CRITICAL for the empty-chain case
    severities = [c.args[0] for c in _patch_all["error_log"].call_args_list]
    assert any(sev.name == "CRITICAL" for sev in severities)


def test_run_pipeline_skips_approved_row(_patch_all):
    """Existing approved WPR row → skip the project entirely."""
    _patch_all["get_rows"].return_value = [
        {"_row_id": 42, "Job": "Bradley 1", "Week": "2026-05-18", "Approved for Send": True}
    ]
    result = _run_pipeline(week_start_override=date(2026, 5, 18))
    assert result["drafts_skipped_approved"] == 1
    assert result["drafts_written"] == 0
    _patch_all["add_rows"].assert_not_called()
    _patch_all["update_rows"].assert_not_called()
    _patch_all["anthropic_call"].assert_not_called()


def test_run_pipeline_zero_data_writes_placeholder(_patch_all):
    # get_rows returns [] for both Daily Reports + Weekly Rollup + WPR lookup
    _patch_all["get_rows"].return_value = []
    result = _run_pipeline(week_start_override=date(2026, 5, 18))
    assert result["drafts_zero_data"] == 1
    assert result["drafts_written"] == 1
    _patch_all["anthropic_call"].assert_not_called()
    _patch_all["add_rows"].assert_called_once()


def test_run_pipeline_standard_path_writes_draft(_patch_all):
    # Different return values per call: WPR lookup empty, Daily Reports has rows,
    # Weekly Rollup empty.
    call_count = {"n": 0}

    def get_rows_side_effect(sheet_id, **_kwargs):
        call_count["n"] += 1
        # Sequence of calls inside the run: ensure_folder mocked → returns scaffold;
        # then get_rows(daily_sheet_id), get_rows(weekly_sheet_id), then
        # get_rows(SHEET_WPR_PENDING_REVIEW) for the existing-row check.
        # Order in code: daily, rollup, then WPR. So calls 1 + 2 are data, call 3 is WPR.
        if call_count["n"] == 1:
            return [{"Report Date": "2026-05-20", "Category": "Daily JHA"}]
        return []

    _patch_all["get_rows"].side_effect = get_rows_side_effect
    result = _run_pipeline(week_start_override=date(2026, 5, 18))
    assert result["drafts_written"] == 1
    assert result["drafts_zero_data"] == 0
    assert result["anthropic_calls"] == 1
    _patch_all["add_rows"].assert_called_once()


def test_run_pipeline_writes_watchdog_marker(_patch_all):
    """Marker file gets written on completion."""
    _run_pipeline(week_start_override=date(2026, 5, 18))
    _patch_all["marker"].assert_called_once()


# ---- Capability gating belt-and-suspenders -------------------------------


def test_weekly_generate_imports_no_send_or_graph_modules():
    """Static AST scan confirms weekly_generate.py has none of the forbidden
    substrings in any import path. The parametrized test in
    test_capability_gating.py is the canonical enforcement; this redundant
    check lives alongside the unit tests so a refactor that adds a
    forbidden import surfaces in this test file too.
    """
    repo_root = Path(__file__).resolve().parent.parent
    source = (repo_root / "safety_reports" / "weekly_generate.py").read_text()
    tree = ast.parse(source)
    seen: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                seen.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                seen.add(node.module)
                for alias in node.names:
                    seen.add(f"{node.module}.{alias.name}")

    forbidden = ["graph_client", "send_mail", "resend", "smtplib", "email.mime"]
    for needle in forbidden:
        offenders = [name for name in seen if needle in name]
        assert offenders == [], (
            f"weekly_generate.py imports contain forbidden substring "
            f"{needle!r}: {offenders}"
        )


# ---- Prompt + schema files load correctly --------------------------------


def test_prompt_file_strips_yaml_frontmatter():
    """`_load_prompt` returns the body, not the front-matter metadata."""
    body = weekly_generate._load_prompt()
    assert "---" not in body[:5]
    assert "[REVIEWER TO FILL]" in body  # sentinel survives strip


def test_schema_file_loads_and_projects_to_tool_shape():
    tool_schema = weekly_generate._load_tool_schema()
    assert tool_schema["name"] == GENERATE_WPR_TOOL_NAME
    assert "input_schema" in tool_schema
    required = tool_schema["input_schema"]["required"]
    for field_name in (
        "draft_body",
        "confidence",
        "incident_counts",
        "safety_topics_covered",
        "narrative_summary",
        "anomaly_flags",
        "data_completeness",
    ):
        assert field_name in required


# ---- Schema-version enforcement (F20) ------------------------------------
#
# `_load_tool_schema` validates the schema file's `version` key against
# `_EXPECTED_SCHEMA_VERSION` and raises on mismatch/missing — a fail-LOUD
# guard against silently loading a drifted contract (Op Stds §42). These
# tests point the loader at a tmp fixture via monkeypatch so the real
# schemas/safety_weekly_generate.json is never mutated.


def test_incident_count_fields_carry_numeric_bounds():
    """F21: every incident-count integer field in the REAL schema carries both
    minimum and maximum, so a prompt-injected absurd count can't pass extraction
    silently; and the schema `version` stays in lockstep with the consuming
    constant (so a future schema edit without a version bump is caught here)."""
    schema = json.loads(weekly_generate._SCHEMA_PATH.read_text())
    counts = schema["input_schema"]["properties"]["incident_counts"]["properties"]
    assert counts, "no incident-count fields found"
    for field, spec in counts.items():
        assert "minimum" in spec, f"{field} missing minimum"
        assert "maximum" in spec, f"{field} missing maximum (F21)"
    assert schema["version"] == weekly_generate._EXPECTED_SCHEMA_VERSION


_MINIMAL_SCHEMA_BODY: dict[str, Any] = {
    "name": GENERATE_WPR_TOOL_NAME,
    "description": "fixture schema for version-enforcement tests",
    "input_schema": {"type": "object", "properties": {}, "required": []},
}


def _write_fixture_schema(tmp_path: Path, payload: dict[str, Any]) -> Path:
    """Write a fixture schema JSON to tmp and return its path."""
    fixture = tmp_path / "fixture_schema.json"
    fixture.write_text(json.dumps(payload))
    return fixture


def test_load_tool_schema_accepts_matching_version(tmp_path, monkeypatch):
    """Happy path: a fixture whose version matches the expected constant
    loads and projects to the tool shape; the version key is consumed for
    validation, not projected through."""
    payload = {
        "version": weekly_generate._EXPECTED_SCHEMA_VERSION,
        **_MINIMAL_SCHEMA_BODY,
    }
    monkeypatch.setattr(
        weekly_generate, "_SCHEMA_PATH", _write_fixture_schema(tmp_path, payload)
    )
    tool_schema = weekly_generate._load_tool_schema()
    assert tool_schema["name"] == GENERATE_WPR_TOOL_NAME
    assert tool_schema["input_schema"] == _MINIMAL_SCHEMA_BODY["input_schema"]
    assert "version" not in tool_schema


def test_load_tool_schema_rejects_version_mismatch(tmp_path, monkeypatch):
    """A version differing from the expected constant raises — never loads
    a drifted contract silently."""
    drifted = f"{weekly_generate._EXPECTED_SCHEMA_VERSION}-drift"
    payload = {"version": drifted, **_MINIMAL_SCHEMA_BODY}
    monkeypatch.setattr(
        weekly_generate, "_SCHEMA_PATH", _write_fixture_schema(tmp_path, payload)
    )
    with pytest.raises(ValueError, match="schema version"):
        weekly_generate._load_tool_schema()


def test_load_tool_schema_rejects_missing_version(tmp_path, monkeypatch):
    """A schema with no `version` key raises — missing is treated as drift,
    not a silent pass."""
    monkeypatch.setattr(
        weekly_generate,
        "_SCHEMA_PATH",
        _write_fixture_schema(tmp_path, dict(_MINIMAL_SCHEMA_BODY)),
    )
    with pytest.raises(ValueError, match="schema version"):
        weekly_generate._load_tool_schema()


def test_run_pipeline_aborts_on_schema_drift(_patch_all, tmp_path, monkeypatch):
    """A drifted schema version aborts the WHOLE run pre-flight, before the
    project loop. The ValueError is raised outside the per-project fence, so
    it propagates up to @its_error_log (→ CRITICAL) rather than degrading to
    a per-project GENERATION_FAILED placeholder. This asserts the loud-abort
    intent: never reach iteration, never call Anthropic, never write a row."""
    drifted = f"{weekly_generate._EXPECTED_SCHEMA_VERSION}-drift"
    payload = {"version": drifted, **_MINIMAL_SCHEMA_BODY}
    monkeypatch.setattr(
        weekly_generate, "_SCHEMA_PATH", _write_fixture_schema(tmp_path, payload)
    )
    with pytest.raises(ValueError, match="schema version"):
        _run_pipeline(week_start_override=date(2026, 5, 18))
    _patch_all["iter_projects"].assert_not_called()
    _patch_all["anthropic_call"].assert_not_called()
    _patch_all["add_rows"].assert_not_called()
    _patch_all["update_rows"].assert_not_called()


# ---- Recipients lookup ---------------------------------------------------


def test_read_recipients_for_missing_returns_empty(_patch_all):
    _patch_all["get_setting"].side_effect = SmartsheetNotFoundError("missing")
    assert weekly_generate._read_recipients_for("Bradley 1") == []


def test_read_recipients_for_present_returns_list(_patch_all):
    _patch_all["get_setting"].side_effect = None
    _patch_all["get_setting"].return_value = json.dumps(
        ["customer@example.com", "ops@example.com"]
    )
    result = weekly_generate._read_recipients_for("Bradley 1")
    assert result == ["customer@example.com", "ops@example.com"]


def test_read_recipients_for_slug_lowercase_underscore_conversion(_patch_all):
    _patch_all["get_setting"].side_effect = None
    _patch_all["get_setting"].return_value = "[]"
    weekly_generate._read_recipients_for("Bradley 1")
    # The first positional arg is the config key string
    call_args = _patch_all["get_setting"].call_args
    assert call_args.args[0] == "safety_reports.recipients.bradley_1"


# ---- Single-shot retry + GENERATION_FAILED placeholder -------------------


def test_transient_404_retried_succeeds(_patch_all, mocker):
    """First call raises SmartsheetNotFoundError; retry call succeeds.

    Verifies: retries_attempted incremented exactly once, drafts_failed
    stays 0 (real draft wrote), no placeholder add_rows on
    WPR_Pending_Review, INFO log entry with the retry error_code.
    """
    # Patch sleep so the test doesn't actually wait.
    mocker.patch("safety_reports.weekly_generate.time.sleep", return_value=None)
    # First call raises 404, second call returns scaffold successfully.
    real_scaffold = _scaffold()
    _patch_all["ensure_folder"].side_effect = [
        SmartsheetNotFoundError("HTTP 404 (code 1006): Not Found"),
        real_scaffold,
    ]
    result = _run_pipeline(week_start_override=date(2026, 5, 18))
    assert result["retries_attempted"] == 1
    assert result["drafts_failed"] == 0
    assert result["drafts_written"] == 1
    assert result["drafts_zero_data"] == 1  # ZERO_DATA path inside retry success
    # Find the retry INFO log row
    log_codes = [
        kwargs.get("error_code")
        for call in _patch_all["error_log"].call_args_list
        for kwargs in [call.kwargs]
    ]
    assert "weekly_generate.transient_404_retry" in log_codes


def test_persistent_404_writes_failure_placeholder(_patch_all, mocker):
    """Both calls raise SmartsheetNotFoundError → placeholder write fires."""
    mocker.patch("safety_reports.weekly_generate.time.sleep", return_value=None)
    _patch_all["ensure_folder"].side_effect = SmartsheetNotFoundError(
        "HTTP 404 (code 1006): Not Found"
    )
    result = _run_pipeline(week_start_override=date(2026, 5, 18))
    assert result["retries_attempted"] == 1  # only ONE retry per project
    assert result["drafts_failed"] == 1
    assert result["drafts_written"] == 1  # the placeholder counts as a write
    # Inspect the placeholder add_rows call
    _patch_all["add_rows"].assert_called_once()
    rows_arg = _patch_all["add_rows"].call_args[0][1]
    assert "[GENERATION_FAILED: SmartsheetNotFoundError]" in rows_arg[0]["Notes"]
    assert rows_arg[0]["Draft Body"].startswith(
        "[GENERATION_FAILED — pipeline error processing"
    )
    assert rows_arg[0]["Recipients"] == ""
    assert rows_arg[0]["Approved for Send"] is False


def test_non_404_smartsheet_error_does_not_retry(_patch_all, mocker):
    """A generic SmartsheetError (not the NotFound subclass) skips retry."""
    mocker.patch("safety_reports.weekly_generate.time.sleep", return_value=None)
    _patch_all["ensure_folder"].side_effect = SmartsheetError("HTTP 500: server error")
    result = _run_pipeline(week_start_override=date(2026, 5, 18))
    assert result["retries_attempted"] == 0  # NO retry on non-404
    assert result["drafts_failed"] == 1
    assert result["drafts_written"] == 1  # placeholder still wrote
    rows_arg = _patch_all["add_rows"].call_args[0][1]
    assert "[GENERATION_FAILED: SmartsheetError]" in rows_arg[0]["Notes"]


def test_generic_exception_writes_placeholder_without_retry(_patch_all, mocker):
    """A non-Smartsheet exception triggers the broad fence; no retry."""
    mocker.patch("safety_reports.weekly_generate.time.sleep", return_value=None)
    _patch_all["ensure_folder"].side_effect = RuntimeError("boom")
    result = _run_pipeline(week_start_override=date(2026, 5, 18))
    assert result["retries_attempted"] == 0
    assert result["drafts_failed"] == 1
    rows_arg = _patch_all["add_rows"].call_args[0][1]
    assert "[GENERATION_FAILED: RuntimeError]" in rows_arg[0]["Notes"]


def test_placeholder_respects_existing_unapproved_row(_patch_all, mocker):
    """Existing unapproved row → update Notes only; do NOT add a new row."""
    mocker.patch("safety_reports.weekly_generate.time.sleep", return_value=None)
    # Force the per-project work to fail with a generic SmartsheetError.
    _patch_all["ensure_folder"].side_effect = SmartsheetError("HTTP 500")
    # The placeholder helper does its own get_rows lookup; return an
    # existing unapproved row for the failing (Job, Week).
    _patch_all["get_rows"].return_value = [
        {
            "_row_id": 7777,
            "Job": "Bradley 1",
            "Week": "2026-05-18",
            "Approved for Send": False,
            "Notes": "[LOW_CONFIDENCE: 0.50] generated=2026-05-22T14:00:00+00:00",
        }
    ]
    result = _run_pipeline(week_start_override=date(2026, 5, 18))
    assert result["drafts_failed"] == 1
    assert result["drafts_written"] == 1
    # No add_rows for the failing project (existing row → update path).
    _patch_all["add_rows"].assert_not_called()
    # update_rows called with appended Notes, not Draft Body touched.
    _patch_all["update_rows"].assert_called_once()
    updates = _patch_all["update_rows"].call_args[0][1]
    assert updates[0]["_row_id"] == 7777
    assert "[GENERATION_FAILED: SmartsheetError]" in updates[0]["Notes"]
    assert "[LOW_CONFIDENCE: 0.50]" in updates[0]["Notes"]  # prior tag preserved
    assert "Draft Body" not in updates[0]  # body untouched


def test_placeholder_respects_existing_approved_row(_patch_all, mocker):
    """Existing approved row → log + skip; do not write or update anything."""
    mocker.patch("safety_reports.weekly_generate.time.sleep", return_value=None)
    _patch_all["ensure_folder"].side_effect = SmartsheetError("HTTP 500")
    _patch_all["get_rows"].return_value = [
        {
            "_row_id": 8888,
            "Job": "Bradley 1",
            "Week": "2026-05-18",
            "Approved for Send": True,
            "Notes": "generated=2026-05-22T14:00:00+00:00",
        }
    ]
    result = _run_pipeline(week_start_override=date(2026, 5, 18))
    # drafts_failed still incremented (the project failed) but no row written.
    assert result["drafts_failed"] == 1
    assert result["drafts_written"] == 0
    _patch_all["add_rows"].assert_not_called()
    _patch_all["update_rows"].assert_not_called()
    # INFO log for the approved-skip.
    log_codes = [
        kwargs.get("error_code")
        for call in _patch_all["error_log"].call_args_list
        for kwargs in [call.kwargs]
    ]
    assert "weekly_generate.placeholder_skipped_approved" in log_codes


def test_placeholder_write_failure_does_not_crash_run(_patch_all, mocker):
    """When the placeholder add_rows itself raises, log and continue."""
    mocker.patch("safety_reports.weekly_generate.time.sleep", return_value=None)
    _patch_all["ensure_folder"].side_effect = SmartsheetError("HTTP 500")
    # No existing row, so the placeholder helper takes the add_rows path —
    # which we make fail.
    _patch_all["add_rows"].side_effect = SmartsheetError(
        "WPR_Pending_Review unreachable"
    )
    # Should NOT raise. Result returns cleanly.
    result = _run_pipeline(week_start_override=date(2026, 5, 18))
    assert result["drafts_failed"] == 1  # we tried; counter still increments
    # Two ITS_Errors entries: original failure + placeholder-write failure.
    log_codes = [
        kwargs.get("error_code")
        for call in _patch_all["error_log"].call_args_list
        for kwargs in [call.kwargs]
    ]
    assert "smartsheet_error" in log_codes
    assert "weekly_generate.placeholder_write_failed" in log_codes
