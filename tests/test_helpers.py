"""Sanity tests for shared helpers.

Run with: pytest -q
"""
from __future__ import annotations

import shutil
import sys

import pytest

from shared.kill_switch import SystemState, check_system_state, require_active


def test_check_system_state_returns_enum(mocker):
    # check_system_state now reads from Smartsheet; mock the boundary so this
    # stays a unit test. Full fail-open and happy-path coverage lives in
    # tests/test_kill_switch.py.
    mocker.patch(
        "shared.kill_switch.smartsheet_client.get_setting", return_value="ACTIVE"
    )
    state = check_system_state()
    assert isinstance(state, SystemState)


def test_require_active_lets_through_when_active(mocker):
    mocker.patch("shared.kill_switch.check_system_state", return_value=SystemState.ACTIVE)

    @require_active
    def real_work():
        return "did the work"

    assert real_work() == "did the work"


def test_require_active_skips_when_paused(mocker, capsys):
    mocker.patch("shared.kill_switch.check_system_state", return_value=SystemState.PAUSED)

    @require_active
    def real_work():
        raise AssertionError("should not have been called")

    assert real_work() is None
    captured = capsys.readouterr()
    assert "PAUSED" in captured.out


@pytest.mark.skipif(
    sys.platform != "darwin" or shutil.which("security") is None,
    reason="macOS Keychain `security` CLI required",
)
def test_keychain_missing_secret_raises_friendly_error():
    from shared.keychain import KeychainError, get_secret

    with pytest.raises(KeychainError, match="security add-generic-password"):
        get_secret("DOES_NOT_EXIST_TEST_KEY_12345")
