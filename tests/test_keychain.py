"""Tests for shared/keychain.py.

Most tests mock subprocess.run so they don't depend on a real Keychain entry
and run on any platform. One macOS-only integration test exercises the real
`security` CLI against a known-missing entry to confirm the end-to-end error
path still looks right.

Run with: pytest -q tests/test_keychain.py
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from unittest.mock import MagicMock

import pytest

from shared.keychain import KeychainError, get_secret

# ---- Happy path ------------------------------------------------------------

def test_returns_secret_value(mocker):
    mocker.patch(
        "shared.keychain.subprocess.run",
        return_value=MagicMock(stdout="my-secret-value\n"),
    )
    assert get_secret("ITS_TEST_KEY") == "my-secret-value"


def test_strips_only_trailing_newline(mocker):
    # The `security ... -w` CLI always appends one trailing newline. The helper
    # must strip that one newline and nothing more — internal whitespace or
    # mid-value newlines (rare but legal in opaque secrets) must survive.
    mocker.patch(
        "shared.keychain.subprocess.run",
        return_value=MagicMock(stdout="line-one\nline-two\n"),
    )
    assert get_secret("ITS_TEST_KEY") == "line-one\nline-two"


# ---- Account argument ------------------------------------------------------

def test_defaults_account_to_current_user(mocker):
    mock_run = mocker.patch(
        "shared.keychain.subprocess.run",
        return_value=MagicMock(stdout="x\n"),
    )
    mocker.patch("shared.keychain.getpass.getuser", return_value="someuser")

    get_secret("ITS_TEST_KEY")

    cmd = mock_run.call_args.args[0]
    assert cmd[cmd.index("-a") + 1] == "someuser"
    assert cmd[cmd.index("-s") + 1] == "ITS_TEST_KEY"


def test_explicit_account_overrides_default(mocker):
    mock_run = mocker.patch(
        "shared.keychain.subprocess.run",
        return_value=MagicMock(stdout="x\n"),
    )
    mocker.patch("shared.keychain.getpass.getuser", return_value="someuser")

    get_secret("ITS_TEST_KEY", account="service-account")

    cmd = mock_run.call_args.args[0]
    assert cmd[cmd.index("-a") + 1] == "service-account"


# ---- Error paths -----------------------------------------------------------

def test_missing_entry_raises_keychain_error_with_service_name(mocker):
    mocker.patch(
        "shared.keychain.subprocess.run",
        side_effect=subprocess.CalledProcessError(returncode=44, cmd="security"),
    )

    # The error message should name the service so the human knows what to add.
    with pytest.raises(KeychainError, match="ITS_TEST_KEY"):
        get_secret("ITS_TEST_KEY")


def test_missing_entry_error_includes_add_command(mocker):
    mocker.patch(
        "shared.keychain.subprocess.run",
        side_effect=subprocess.CalledProcessError(returncode=44, cmd="security"),
    )

    # The helpful "here's how to fix it" hint must be in the message.
    with pytest.raises(KeychainError, match="security add-generic-password"):
        get_secret("ITS_TEST_KEY")


def test_missing_security_cli_raises_keychain_error(mocker):
    mocker.patch("shared.keychain.subprocess.run", side_effect=FileNotFoundError())

    # On non-macOS, the `security` CLI is absent — should raise KeychainError,
    # not FileNotFoundError, so callers only have one exception type to handle.
    with pytest.raises(KeychainError, match="macOS-only"):
        get_secret("ITS_TEST_KEY")


# ---- Real CLI integration (macOS only) ------------------------------------

@pytest.mark.skipif(
    sys.platform != "darwin" or shutil.which("security") is None,
    reason="macOS Keychain `security` CLI required",
)
def test_real_missing_secret_raises_friendly_error():
    # End-to-end check against the real `security` CLI. Mocked tests above cover
    # branches; this confirms the integration still works against a known-missing
    # entry. No setup/teardown — the key intentionally does not exist.
    with pytest.raises(KeychainError, match="security add-generic-password"):
        get_secret("DOES_NOT_EXIST_TEST_KEY_12345")
