"""Integration of picklist validation into smartsheet_client write paths.

Tests verify that `add_rows` and `update_rows` call `validate_row` BEFORE
any Smartsheet API request. The API call itself is mocked so the assertion
is "validation fired and raised first; API was never invoked."

Run with: pytest -q tests/test_smartsheet_client_picklist_integration.py
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from shared import sheet_ids, smartsheet_client
from shared.picklist_validation import PicklistViolationError


@pytest.fixture
def mock_client(mocker):
    """Mock the entire Smartsheet client + column-map resolution."""
    fake_client = MagicMock()
    add_result = MagicMock()
    add_result.result = [MagicMock(id=1001)]
    fake_client.Sheets.add_rows.return_value = add_result
    fake_client.Sheets.update_rows.return_value = None
    mocker.patch(
        "shared.smartsheet_client.get_client", return_value=fake_client,
    )
    # Stub column resolution so _resolve_cells doesn't try to hit the API.
    mocker.patch(
        "shared.smartsheet_client._column_map",
        return_value={"Severity": 1, "Workstream": 2, "Send Status": 3},
    )
    return fake_client


# ---- add_rows ------------------------------------------------------------


def test_add_rows_rejects_invalid_picklist_value_before_api(mock_client):
    """Invalid Severity value → PicklistViolationError BEFORE any add_rows API call."""
    with pytest.raises(PicklistViolationError):
        smartsheet_client.add_rows(
            sheet_ids.SHEET_ERRORS,
            [{"Severity": "BOGUS"}],
        )
    mock_client.Sheets.add_rows.assert_not_called()


def test_add_rows_accepts_valid_picklist_value(mock_client):
    """All-valid row passes validation and reaches the API."""
    new_ids = smartsheet_client.add_rows(
        sheet_ids.SHEET_ERRORS,
        [{"Severity": "WARN", "Workstream": "safety_reports"}],
    )
    assert new_ids == [1001]
    mock_client.Sheets.add_rows.assert_called_once()


def test_add_rows_unregistered_sheet_skips_validation(mock_client):
    """An unregistered sheet_id passes through validation entirely."""
    # 9999999999 is not in REGISTRY; any cell value is allowed.
    smartsheet_client.add_rows(
        9999999999,
        [{"Severity": "completely_unchecked"}],
    )
    mock_client.Sheets.add_rows.assert_called_once()


def test_add_rows_first_invalid_row_blocks_entire_batch(mock_client):
    """Validation fires per-row; first violation blocks the API call entirely."""
    with pytest.raises(PicklistViolationError):
        smartsheet_client.add_rows(
            sheet_ids.SHEET_ERRORS,
            [
                {"Severity": "INFO"},  # valid
                {"Severity": "INVALID"},  # invalid
            ],
        )
    mock_client.Sheets.add_rows.assert_not_called()


# ---- update_rows ---------------------------------------------------------


def test_update_rows_rejects_invalid_picklist_value_before_api(mock_client):
    with pytest.raises(PicklistViolationError):
        smartsheet_client.update_rows(
            sheet_ids.SHEET_ERRORS,
            [{"_row_id": 5050, "Severity": "BOGUS"}],
        )
    mock_client.Sheets.update_rows.assert_not_called()


def test_update_rows_skips_row_id_meta_key_during_validation(mock_client):
    """_row_id doesn't trigger validation (it's the row identifier, not a column).

    Note: `_resolve_cells` itself only filters `_row_id`, not arbitrary
    `_`-prefixed keys — adding other meta keys would fail at column-map
    lookup. The picklist-validation `_`-prefix skip is defensive for
    future meta-key conventions; this test pins the live behavior on
    the one meta key actually carried (`_row_id`).
    """
    smartsheet_client.update_rows(
        sheet_ids.SHEET_ERRORS,
        [{"_row_id": 5050, "Severity": "INFO"}],
    )
    mock_client.Sheets.update_rows.assert_called_once()


# ---- Backward compat: empty inputs ---------------------------------------


def test_add_rows_empty_input_no_validation_no_api(mock_client):
    result = smartsheet_client.add_rows(sheet_ids.SHEET_ERRORS, [])
    assert result == []
    mock_client.Sheets.add_rows.assert_not_called()


def test_update_rows_empty_input_no_validation_no_api(mock_client):
    smartsheet_client.update_rows(sheet_ids.SHEET_ERRORS, [])
    mock_client.Sheets.update_rows.assert_not_called()
