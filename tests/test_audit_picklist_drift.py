"""Tests for scripts/audit_picklist_drift.py — registry/sheet comparison logic.

`scripts/` is not a Python package; we use the same sys.path-insert
pattern as tests/test_watchdog.py so `import audit_picklist_drift`
resolves the script as a top-level module. Importing as
`from scripts import audit_picklist_drift` would make mypy see the
file under two module names and fail CI's strict `mypy .` gate.

All Smartsheet reads are mocked. The audit logic itself (per-sheet
comparison + finding categorization) is pure and trivially testable.

Run with: pytest -q tests/test_audit_picklist_drift.py
"""
from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import audit_picklist_drift  # noqa: E402  — sys.path-driven import


def _live(title: str, ctype: str, options: list[str] | None = None) -> dict:
    return {"id": 1, "title": title, "type": ctype, "options": options or []}


# ---- audit_one_sheet -----------------------------------------------------


def test_audit_finds_zero_drift_when_picklist_matches_registry(mocker):
    """Happy path: column is PICKLIST and options match the registry exactly."""
    mocker.patch(
        "scripts.audit_picklist_drift.smartsheet_client.list_columns_with_options",
        return_value=[_live("Severity", "PICKLIST", ["INFO", "WARN", "ERROR", "CRITICAL"])],
    )
    findings = audit_picklist_drift.audit_one_sheet(
        sheet_id=1,
        column_registry={
            "Severity": frozenset({"INFO", "WARN", "ERROR", "CRITICAL"}),
        },
    )
    assert findings == []


def test_audit_flags_text_number_as_pending_conversion(mocker):
    """Column is TEXT_NUMBER → operator UI conversion pending."""
    mocker.patch(
        "scripts.audit_picklist_drift.smartsheet_client.list_columns_with_options",
        return_value=[_live("Severity", "TEXT_NUMBER")],
    )
    findings = audit_picklist_drift.audit_one_sheet(
        sheet_id=1,
        column_registry={"Severity": frozenset({"INFO", "WARN"})},
    )
    assert len(findings) == 1
    assert "TEXT_NUMBER" in findings[0]
    assert "PICKLIST" in findings[0]


def test_audit_flags_allowed_set_mismatch(mocker):
    """Column is PICKLIST but its allowed set differs from the registry."""
    mocker.patch(
        "scripts.audit_picklist_drift.smartsheet_client.list_columns_with_options",
        return_value=[_live("Severity", "PICKLIST", ["INFO", "WARN", "STRAY"])],
    )
    findings = audit_picklist_drift.audit_one_sheet(
        sheet_id=1,
        column_registry={
            "Severity": frozenset({"INFO", "WARN", "ERROR", "CRITICAL"}),
        },
    )
    assert len(findings) == 1
    assert "allowed-set mismatch" in findings[0]
    assert "STRAY" in findings[0]
    assert "ERROR" in findings[0] or "CRITICAL" in findings[0]


def test_audit_flags_missing_column_in_live_sheet(mocker):
    """Registry has a column that doesn't exist on the live sheet."""
    mocker.patch(
        "scripts.audit_picklist_drift.smartsheet_client.list_columns_with_options",
        return_value=[_live("OtherColumn", "PICKLIST", ["X"])],
    )
    findings = audit_picklist_drift.audit_one_sheet(
        sheet_id=1,
        column_registry={"Severity": frozenset({"INFO"})},
    )
    assert len(findings) == 1
    assert "NOT PRESENT" in findings[0]


def test_audit_records_unreadable_sheet_as_finding(mocker):
    """Smartsheet error during sheet read → single finding, doesn't crash audit."""
    from shared.smartsheet_client import SmartsheetError
    mocker.patch(
        "scripts.audit_picklist_drift.smartsheet_client.list_columns_with_options",
        side_effect=SmartsheetError("HTTP 503"),
    )
    findings = audit_picklist_drift.audit_one_sheet(
        sheet_id=1,
        column_registry={"Severity": frozenset({"INFO"})},
    )
    assert len(findings) == 1
    assert "unreadable" in findings[0]
    assert "SmartsheetError" in findings[0]


def test_audit_accepts_multi_picklist_as_compliant(mocker):
    """MULTI_PICKLIST is also a valid hardened type (not just PICKLIST)."""
    mocker.patch(
        "scripts.audit_picklist_drift.smartsheet_client.list_columns_with_options",
        return_value=[_live("Tags", "MULTI_PICKLIST", ["a", "b"])],
    )
    findings = audit_picklist_drift.audit_one_sheet(
        sheet_id=1,
        column_registry={"Tags": frozenset({"a", "b"})},
    )
    assert findings == []


# ---- audit() aggregation ------------------------------------------------


def test_audit_aggregates_findings_across_registered_sheets(mocker):
    """audit() walks REGISTRY and collects findings from each sheet."""
    # Stub the registry to a known minimal shape.
    mocker.patch.object(
        audit_picklist_drift.picklist_validation,
        "REGISTRY",
        {
            1: {"Severity": frozenset({"INFO"})},
            2: {"Workstream": frozenset({"safety_reports"})},
            3: {},  # empty entry — skipped
        },
    )
    # Two distinct findings — one per sheet.
    side_effects = [
        [_live("Severity", "TEXT_NUMBER")],  # sheet 1 finding
        [_live("Workstream", "PICKLIST", ["wrong_value"])],  # sheet 2 finding
    ]
    mocker.patch(
        "scripts.audit_picklist_drift.smartsheet_client.list_columns_with_options",
        side_effect=side_effects,
    )
    findings = audit_picklist_drift.audit()
    assert len(findings) == 2


def test_audit_skips_empty_registry_entries(mocker):
    """Per-project entries with empty column registries (placeholder shells) are skipped."""
    mocker.patch.object(
        audit_picklist_drift.picklist_validation,
        "REGISTRY",
        {1: {}, 2: {}, 3: {}},
    )
    mocker.patch(
        "scripts.audit_picklist_drift.smartsheet_client.list_columns_with_options",
    )
    findings = audit_picklist_drift.audit()
    assert findings == []


# ---- apply_reconcile (Phase 3b --apply) ---------------------------------


def _stub_registry(mocker, registry):
    mocker.patch.object(
        audit_picklist_drift.picklist_validation, "REGISTRY", registry,
    )


def _patch_ensure(mocker, *, added=(), applied=False, side_effect=None):
    from types import SimpleNamespace
    kwargs = {}
    if side_effect is not None:
        kwargs["side_effect"] = side_effect
    else:
        kwargs["return_value"] = SimpleNamespace(added=tuple(added), applied=applied)
    return mocker.patch(
        "scripts.audit_picklist_drift.smartsheet_client.ensure_picklist_options",
        **kwargs,
    )


def test_apply_reconcile_dry_run_counts_and_passes_dry_run_true(mocker):
    _stub_registry(mocker, {1: {"Reason": frozenset({"a", "b", "c"})}})
    ensure = _patch_ensure(mocker, added=("b", "c"), applied=False)

    changed, added, skipped = audit_picklist_drift.apply_reconcile(commit=False)

    assert (changed, added, skipped) == (1, 2, [])
    # Sorted values + dry_run=True on a preview.
    ensure.assert_called_once_with(1, "Reason", ["a", "b", "c"], dry_run=True)


def test_apply_reconcile_commit_passes_dry_run_false(mocker):
    _stub_registry(mocker, {1: {"Reason": frozenset({"a"})}})
    ensure = _patch_ensure(mocker, added=("a",), applied=True)

    audit_picklist_drift.apply_reconcile(commit=True)

    assert ensure.call_args.kwargs["dry_run"] is False


def test_apply_reconcile_noop_when_nothing_added(mocker):
    _stub_registry(mocker, {1: {"Reason": frozenset({"a"})}})
    _patch_ensure(mocker, added=(), applied=False)

    assert audit_picklist_drift.apply_reconcile(commit=True) == (0, 0, [])


def test_apply_reconcile_skips_missing_column_value_error(mocker):
    _stub_registry(mocker, {1: {"Absent": frozenset({"x"})}})
    _patch_ensure(
        mocker, side_effect=ValueError("column 'Absent' not found"),
    )

    changed, added, skipped = audit_picklist_drift.apply_reconcile(commit=True)

    assert (changed, added) == (0, 0)
    assert len(skipped) == 1
    assert "Absent" in skipped[0]


def test_apply_reconcile_skips_empty_registry_entries(mocker):
    _stub_registry(mocker, {1: {}, 2: {}})
    ensure = _patch_ensure(mocker, added=("x",))

    assert audit_picklist_drift.apply_reconcile(commit=True) == (0, 0, [])
    ensure.assert_not_called()


# ---- CLI flag validation -------------------------------------------------


def test_parse_args_commit_without_apply_errors():
    import pytest
    with pytest.raises(SystemExit):
        audit_picklist_drift._parse_args(["--commit"])


def test_parse_args_apply_defaults_to_preview():
    args = audit_picklist_drift._parse_args(["--apply"])
    assert args.apply is True
    assert args.commit is False
