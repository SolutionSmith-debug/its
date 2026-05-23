"""Tests for scripts/audit_picklist_drift.py — registry/sheet comparison logic.

All Smartsheet reads are mocked. The audit logic itself (per-sheet
comparison + finding categorization) is pure and trivially testable.

Run with: pytest -q tests/test_audit_picklist_drift.py
"""
from __future__ import annotations

from scripts import audit_picklist_drift


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
