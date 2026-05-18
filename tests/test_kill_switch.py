"""Tests for shared/kill_switch.py.

All Smartsheet reads are mocked at `smartsheet_client.get_setting`. `error_log.log`
is mocked so tests can assert exactly which fail-open branch fired without writing
to the real ~/its/logs/ directory.

Run with: pytest -q tests/test_kill_switch.py
"""
from __future__ import annotations

import pytest

from shared import kill_switch
from shared.error_log import Severity
from shared.kill_switch import SystemState
from shared.smartsheet_client import (
    SmartsheetAuthError,
    SmartsheetError,
    SmartsheetNotFoundError,
)


@pytest.fixture
def mock_log(mocker):
    return mocker.patch("shared.kill_switch.log")


@pytest.fixture
def mock_get_setting(mocker):
    return mocker.patch("shared.kill_switch.smartsheet_client.get_setting")


# ---- Happy paths ---------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("ACTIVE", SystemState.ACTIVE),
        ("PAUSED", SystemState.PAUSED),
        ("MAINTENANCE", SystemState.MAINTENANCE),
    ],
)
def test_check_system_state_returns_enum_for_valid_value(
    mock_get_setting, mock_log, raw, expected
):
    mock_get_setting.return_value = raw

    assert kill_switch.check_system_state() is expected
    mock_get_setting.assert_called_once_with("system.state", workstream="global")
    mock_log.assert_not_called()


# ---- Fail-open: Smartsheet unreachable -----------------------------------


def test_check_system_state_smartsheet_error_returns_active_and_logs(
    mock_get_setting, mock_log
):
    mock_get_setting.side_effect = SmartsheetError("boom")

    assert kill_switch.check_system_state() is SystemState.ACTIVE
    mock_log.assert_called_once()
    severity, script, message = mock_log.call_args.args
    assert severity is Severity.WARN
    assert script == "shared.kill_switch"
    assert "read failed" in message
    assert "defaulting to ACTIVE" in message


def test_check_system_state_smartsheet_error_child_class_also_caught(
    mock_get_setting, mock_log
):
    # Anything in the SmartsheetError hierarchy except SmartsheetNotFoundError
    # routes to the "read failed" branch. Distinguishability matters because
    # the morning scan reads the message string.
    mock_get_setting.side_effect = SmartsheetAuthError("token rotated")

    assert kill_switch.check_system_state() is SystemState.ACTIVE
    (_, _, message) = mock_log.call_args.args
    assert "read failed" in message
    assert "row missing" not in message


# ---- Fail-open: row missing ----------------------------------------------


def test_check_system_state_row_missing_returns_active_and_logs(
    mock_get_setting, mock_log
):
    mock_get_setting.side_effect = SmartsheetNotFoundError("no such row")

    assert kill_switch.check_system_state() is SystemState.ACTIVE
    mock_log.assert_called_once()
    severity, script, message = mock_log.call_args.args
    assert severity is Severity.WARN
    assert script == "shared.kill_switch"
    assert "row missing" in message
    assert "defaulting to ACTIVE" in message


# ---- Fail-open: invalid value --------------------------------------------


@pytest.mark.parametrize("bad", ["active", "ON", "", "DISABLED", None])
def test_check_system_state_invalid_value_returns_active_and_logs(
    mock_get_setting, mock_log, bad
):
    mock_get_setting.return_value = bad

    assert kill_switch.check_system_state() is SystemState.ACTIVE
    mock_log.assert_called_once()
    severity, script, message = mock_log.call_args.args
    assert severity is Severity.WARN
    assert script == "shared.kill_switch"
    assert "invalid value" in message
    assert "defaulting to ACTIVE" in message


# ---- require_active decorator --------------------------------------------


def test_require_active_runs_fn_when_active(mocker):
    mocker.patch(
        "shared.kill_switch.check_system_state", return_value=SystemState.ACTIVE
    )

    @kill_switch.require_active
    def main():
        return "ran"

    assert main() == "ran"


def test_require_active_skips_fn_when_paused(mocker, capsys):
    mocker.patch(
        "shared.kill_switch.check_system_state", return_value=SystemState.PAUSED
    )

    @kill_switch.require_active
    def main():
        raise AssertionError("should not run")

    assert main() is None
    assert "system_state=PAUSED" in capsys.readouterr().out
