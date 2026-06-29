"""Tests for shared/picklist_validation.py — registry + validate_cell + validate_row.

Run with: pytest -q tests/test_picklist_validation.py
"""
from __future__ import annotations

import pytest

from shared import picklist_validation, sheet_ids
from shared.error_log import Severity
from shared.picklist_validation import (
    REGISTRY,
    PicklistViolationError,
    validate_cell,
    validate_row,
)
from shared.quarantine import QuarantineReason
from shared.review_queue import ReviewReason

# ---- validate_cell happy path + pass-through -----------------------------


def test_validate_cell_registered_with_allowed_value_passes_through():
    # ITS_Errors.Severity allows INFO/WARN/ERROR/CRITICAL
    validate_cell(sheet_ids.SHEET_ERRORS, "Severity", "WARN")


def test_wsr_workstream_column_accepts_safety_rejects_others():
    # P1b cross-workstream send guard: the WSR Workstream column is gated to {safety}.
    validate_cell(sheet_ids.SHEET_WSR_HUMAN_REVIEW, "Workstream", "safety")
    for bad in ("progress", "safety_reports", "Safety"):
        with pytest.raises(PicklistViolationError):
            validate_cell(sheet_ids.SHEET_WSR_HUMAN_REVIEW, "Workstream", bad)


def test_validate_cell_registered_with_disallowed_value_raises():
    with pytest.raises(PicklistViolationError) as exc:
        validate_cell(sheet_ids.SHEET_ERRORS, "Severity", "BOGUS")
    # Error metadata preserved
    assert exc.value.sheet_id == sheet_ids.SHEET_ERRORS
    assert exc.value.column == "Severity"
    assert exc.value.value == "BOGUS"
    assert "BOGUS" in str(exc.value)
    assert "Severity" in str(exc.value)


def test_validate_cell_unregistered_sheet_passes_through():
    # arbitrary sheet ID not in REGISTRY → no error
    validate_cell(9999999999, "Anything", "bogus_value")


def test_validate_cell_registered_sheet_unregistered_column_passes_through():
    validate_cell(sheet_ids.SHEET_ERRORS, "FreeFormColumn", "anything")


def test_validate_cell_none_passes_through():
    """Blank cells are intentional; pass-through."""
    validate_cell(sheet_ids.SHEET_ERRORS, "Severity", None)


def test_validate_cell_bool_passes_through():
    """CHECKBOX columns are type-enforced; bool values bypass picklist check."""
    validate_cell(sheet_ids.SHEET_REVIEW_QUEUE, "Status", True)


# ---- validate_row aggregation -------------------------------------------


def test_validate_row_all_valid_passes():
    row = {
        "Severity": "INFO",
        "Workstream": "safety_reports",
        "Source File": "test.eml",  # unregistered column passes through
    }
    validate_row(sheet_ids.SHEET_ERRORS, row)


def test_validate_row_raises_on_first_invalid_cell():
    row = {
        "Severity": "WARN",   # ok
        "Workstream": "bogus",  # NOT ok
    }
    with pytest.raises(PicklistViolationError) as exc:
        validate_row(sheet_ids.SHEET_ERRORS, row)
    assert exc.value.column == "Workstream"


def test_validate_row_skips_underscore_meta_keys():
    """_row_id and other _-prefixed meta keys are not Smartsheet columns."""
    row = {
        "_row_id": 12345,
        "_extra_meta": "garbage",
        "Severity": "INFO",
    }
    validate_row(sheet_ids.SHEET_ERRORS, row)


def test_validate_row_unregistered_sheet_passes_through():
    """Every cell is unregistered → no validation, regardless of value content."""
    row = {"AnyColumn": "any_value", "Other": 12345}
    validate_row(9999999999, row)


# ---- REGISTRY composition ------------------------------------------------


def test_registry_severity_matches_enum():
    """REGISTRY composes from Severity StrEnum directly."""
    assert (
        REGISTRY[sheet_ids.SHEET_ERRORS]["Severity"]
        == frozenset(s.value for s in Severity)
    )


def test_registry_review_reason_includes_pr72_additions():
    """The 3 PR #72 ReviewReason values are in the registry."""
    review_reasons = REGISTRY[sheet_ids.SHEET_REVIEW_QUEUE]["Reason"]
    assert "header-soft-fail-trusted" in review_reasons
    assert "sender-pending-verification" in review_reasons
    assert "project-out-of-scope" in review_reasons


def test_registry_quarantine_workstream_uses_other_not_global():
    """ITS_Quarantine.Workstream picklist uses `other` as catch-all, NOT `global`."""
    workstreams = REGISTRY[sheet_ids.SHEET_QUARANTINE]["Workstream"]
    assert "other" in workstreams
    assert "global" not in workstreams


def test_registry_trusted_contacts_conditional_on_real_sheet_id():
    """SHEET_TRUSTED_CONTACTS=0 placeholder is skipped to avoid spurious violations."""
    if sheet_ids.SHEET_TRUSTED_CONTACTS == 0:
        # Skipped — placeholder not yet replaced by operator
        assert sheet_ids.SHEET_TRUSTED_CONTACTS not in REGISTRY
    else:
        # Operator pasted the real ID; registry picked it up
        assert sheet_ids.SHEET_TRUSTED_CONTACTS in REGISTRY


def test_registry_wpr_send_status_matches_pr68_picklist():
    """Send Status: PENDING / SENT / FAILED / HELD per PR #68 schema-drift finding."""
    expected = {"PENDING", "SENT", "FAILED", "HELD"}
    assert REGISTRY[sheet_ids.SHEET_WPR_PENDING_REVIEW]["Send Status"] == expected


def test_registry_wsr_send_status_includes_sending():
    """WSR's Send Status adds the SENDING write-ahead marker (PR #247) to the WPR set. Without
    it, weekly_send's pre-send SENDING write raises PicklistViolationError and the send is
    blocked (the weekly_send_poll DEGRADED regression this fixes); WPR keeps the narrower set."""
    allowed = REGISTRY[sheet_ids.SHEET_WSR_HUMAN_REVIEW]["Send Status"]
    assert allowed == {"PENDING", "SENT", "FAILED", "HELD", "SENDING"}
    # Regression guard: the value that was being rejected on the live send path.
    from shared import picklist_validation
    picklist_validation.validate_cell(sheet_ids.SHEET_WSR_HUMAN_REVIEW, "Send Status", "SENDING")


def test_every_wsr_send_status_writer_constant_is_registered():
    """META-TEST — recurrence guard for the #247->#253 SENDING-omission (forensic
    class #1: mocks-pass-but-live-fails).

    The test above pins the EXPECTED literal set; this one closes the drift gap it
    cannot: it DERIVES the writer-side source of truth — every ``STATUS_*`` string
    constant in ``safety_reports/wsr_review.py`` (what the pipeline can actually
    write) — and asserts each is registered in the REGISTRY that gates every
    ``update_rows``. A future ``STATUS_FOO = "FOO"`` added without a matching REGISTRY
    entry (exactly what #247 did with SENDING) makes the live send path raise
    ``PicklistViolationError`` and fail-closes ALL sends — invisible to mocks, caught
    here statically instead.
    """
    from safety_reports import wsr_review

    writer_values = {
        v for n, v in vars(wsr_review).items()
        if n.startswith("STATUS_") and isinstance(v, str)
    }
    assert writer_values, "no STATUS_* constants found in wsr_review — test wiring broke"

    registered = REGISTRY[sheet_ids.SHEET_WSR_HUMAN_REVIEW]["Send Status"]
    missing = writer_values - set(registered)
    assert not missing, (
        f"wsr_review Send Status constant(s) {sorted(missing)} are NOT in "
        f"REGISTRY[SHEET_WSR_HUMAN_REVIEW]['Send Status'] ({sorted(registered)}). "
        "Register them in shared/picklist_validation.py in the SAME PR as the writer — "
        "an unregistered value makes update_rows raise PicklistViolationError and blocks "
        "the live send path (the #247->#253 SENDING regression)."
    )


# ---- Error formatting + integer-cast safety ------------------------------


def test_picklist_violation_message_includes_sorted_allowed_set():
    """Error message is deterministic regardless of frozenset iteration order."""
    err = PicklistViolationError(
        sheet_id=1, column="x", value="bad", allowed=frozenset({"c", "a", "b"}),
    )
    assert "['a', 'b', 'c']" in str(err)


def test_validate_cell_numeric_value_stringified_then_compared():
    """A numeric 0 written into a string-enum column should raise.

    Smartsheet's API accepts non-string cell values for picklist columns
    silently in some shapes; we coerce to str before compare so a stray
    `0` or `1` doesn't slip through as a valid string value.
    """
    with pytest.raises(PicklistViolationError):
        validate_cell(sheet_ids.SHEET_ERRORS, "Severity", 0)


# ---- Module surface ------------------------------------------------------


def test_module_exports_expected_surface():
    expected = {
        "PicklistViolationError", "REGISTRY",
        "validate_cell", "validate_row",
    }
    assert expected.issubset(set(picklist_validation.__all__))


def test_quarantine_reason_pr72_values_introspectable():
    """The QuarantineReason enum re-export is available for diagnostics."""
    assert picklist_validation.QuarantineReason is QuarantineReason


def test_review_reason_pr72_values_introspectable():
    assert picklist_validation.ReviewReason is ReviewReason
