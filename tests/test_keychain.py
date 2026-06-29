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

from shared import keychain
from shared.keychain import (
    KEYCHAIN_CLI_TIMEOUT,
    KeychainError,
    KeychainLockedError,
    get_secret,
    set_secret,
)


@pytest.fixture(autouse=True)
def _isolate_state_io(mocker):
    """A3: keep the new keychain write-lock off the real filesystem in unit tests.

    `set_secret` now wraps `subprocess.run` in `state_io.with_path_lock`; no-op it
    so tests never flock a real ~/its/state sidecar, and stub `error_log.log` so
    the fail-open WARN path never attempts a real ITS_Errors write.
    """
    mocker.patch("shared.keychain.state_io.with_path_lock")
    mocker.patch("shared.error_log.log")
    # Task #8: default the controlling-TTY check to False (the daemon/headless
    # write path) so set_secret tests are deterministic regardless of whether
    # pytest runs under a terminal; the interactive (argv) path is exercised
    # explicitly in the TTY-trap tests below.
    mocker.patch("shared.keychain._has_controlling_tty", return_value=False)


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


# ---- A2: timeout + locked-keychain (host resilience) ----------------------


def test_get_secret_passes_timeout_to_subprocess(mocker):
    """A2: the `security` CLI call is bounded by KEYCHAIN_CLI_TIMEOUT so a hung
    keychain interaction can't block a daemon indefinitely."""
    mock_run = mocker.patch(
        "shared.keychain.subprocess.run", return_value=MagicMock(stdout="x\n")
    )
    get_secret("ITS_TEST_KEY")
    assert mock_run.call_args.kwargs["timeout"] == KEYCHAIN_CLI_TIMEOUT


def test_security_cli_timeout_raises_keychain_error(mocker):
    """A `security` CLI that exceeds the timeout surfaces a clear, non-leaking
    KeychainError (not an indefinite hang)."""
    mocker.patch(
        "shared.keychain.subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd="security", timeout=KEYCHAIN_CLI_TIMEOUT),
    )
    with pytest.raises(KeychainError, match="timed out"):
        get_secret("ITS_TEST_KEY")


def test_locked_keychain_raises_keychain_locked_error(mocker):
    """A2: a LOCKED keychain (interaction not allowed) raises the distinct
    KeychainLockedError, not a misleading 'entry not found' — so a daemon fails
    loud with an actionable signal after a reboot before the keychain is unlocked."""
    mocker.patch(
        "shared.keychain.subprocess.run",
        side_effect=subprocess.CalledProcessError(
            returncode=36,
            cmd="security",
            output="",
            stderr="SecKeychainSearchCopyNext: The user interaction is not allowed.",
        ),
    )
    with pytest.raises(KeychainLockedError, match="LOCKED"):
        get_secret("ITS_TEST_KEY")


def test_set_secret_passes_timeout_and_locked_detection(mocker):
    """set_secret is bounded too, and a locked keychain on WRITE raises
    KeychainLockedError without leaking the value."""
    mocker.patch("shared.keychain.getpass.getuser", return_value="u")
    mock_run = mocker.patch("shared.keychain.subprocess.run")
    set_secret("ITS_TEST_KEY", "v")
    assert mock_run.call_args.kwargs["timeout"] == KEYCHAIN_CLI_TIMEOUT

    mocker.patch(
        "shared.keychain.subprocess.run",
        side_effect=subprocess.CalledProcessError(
            returncode=36, cmd="security", output="",
            stderr="errSecInteractionNotAllowed (-25308)",
        ),
    )
    with pytest.raises(KeychainLockedError) as exc:
        set_secret("ITS_TEST_KEY", "super-secret-value")
    assert "super-secret-value" not in str(exc.value)


# ---- set_secret -----------------------------------------------------------


def test_set_secret_calls_security_with_update_flag(mocker):
    """The `-U` flag makes add-generic-password idempotent (update if
    exists, otherwise create). Without it, a second call on the same
    service raises SecKeychainItemCreate errSecDuplicateItem (-25299).
    Box OAuth's refresh-token rotation hits the same key on every API
    call — must not error on the second rotation.

    F04: `-w` is now a bare flag (value supplied on stdin), so the secret
    value must NOT appear anywhere in the argv list."""
    mock_run = mocker.patch("shared.keychain.subprocess.run")
    mocker.patch("shared.keychain.getpass.getuser", return_value="someuser")

    set_secret("ITS_TEST_KEY", "secret-value")

    cmd = mock_run.call_args.args[0]
    assert cmd[0] == "security"
    assert cmd[1] == "add-generic-password"
    assert cmd[cmd.index("-a") + 1] == "someuser"
    assert cmd[cmd.index("-s") + 1] == "ITS_TEST_KEY"
    # F04: the secret is NOT in argv (it's on stdin). `-w` MUST be the last
    # option — that's what makes `security` read from stdin instead of argv.
    # `-U` MUST precede `-w`; placed after it, the CLI swallows `-U` as the
    # password value (verified live). So `-U` is present and earlier than `-w`.
    assert "secret-value" not in cmd
    assert cmd[-1] == "-w"
    assert "-U" in cmd  # idempotent update preserved
    assert cmd.index("-U") < cmd.index("-w")


def test_set_secret_passes_value_on_stdin_not_argv(mocker):
    """F04: the secret reaches `security` via stdin (input=value, text=True),
    never as a `-w VALUE` argv element — so it is invisible to `ps` /
    `/proc/<pid>/cmdline` / EDR argv capture. Preserves the existing
    capture_output / check kwargs."""
    mock_run = mocker.patch("shared.keychain.subprocess.run")
    mocker.patch("shared.keychain.getpass.getuser", return_value="someuser")

    set_secret("ITS_TEST_KEY", "secret-value")

    kwargs = mock_run.call_args.kwargs
    # Value fed twice: the `-w` prompt reads password + retype, one line each.
    assert kwargs["input"] == "secret-value\nsecret-value\n"
    assert kwargs["text"] is True
    assert kwargs["capture_output"] is True
    assert kwargs["check"] is True


def test_set_secret_explicit_account_overrides_default(mocker):
    mock_run = mocker.patch("shared.keychain.subprocess.run")
    mocker.patch("shared.keychain.getpass.getuser", return_value="someuser")

    set_secret("ITS_TEST_KEY", "v", account="service-account")

    cmd = mock_run.call_args.args[0]
    assert cmd[cmd.index("-a") + 1] == "service-account"


def test_set_secret_does_not_leak_value_into_error_message(mocker):
    """If the CLI fails, the exception must NOT contain the secret value —
    error messages can land in logs / tracebacks / triple-fire alert
    bodies."""
    mocker.patch(
        "shared.keychain.subprocess.run",
        side_effect=subprocess.CalledProcessError(
            returncode=1, cmd="security",
            output="", stderr="errSecAuthFailed",
        ),
    )

    secret = "very-sensitive-refresh-token-value"
    with pytest.raises(KeychainError) as exc:
        set_secret("ITS_TEST_KEY", secret)
    # Service name + stderr should appear; value MUST NOT.
    assert "ITS_TEST_KEY" in str(exc.value)
    assert "errSecAuthFailed" in str(exc.value)
    assert secret not in str(exc.value)


def test_set_secret_missing_security_cli_raises_keychain_error(mocker):
    mocker.patch("shared.keychain.subprocess.run", side_effect=FileNotFoundError())

    with pytest.raises(KeychainError, match="macOS-only"):
        set_secret("ITS_TEST_KEY", "x")


# ---- A3: cross-process write-lock -----------------------------------------


def test_set_secret_acquires_write_lock(mocker):
    """A3: the write is serialized under the cross-process Keychain write-lock."""
    lock = mocker.patch("shared.keychain.state_io.with_path_lock")
    mocker.patch("shared.keychain.subprocess.run")
    mocker.patch("shared.keychain.getpass.getuser", return_value="u")

    set_secret("ITS_TEST_KEY", "v")

    lock.assert_called_once_with(keychain._KEYCHAIN_WRITE_LOCK_ANCHOR)


def test_set_secret_lock_timeout_fails_open(mocker):
    """A3: a write-lock timeout still performs the write (fail-open) — a missed
    secret rotation is worse than a lost lock."""
    mocker.patch(
        "shared.keychain.state_io.with_path_lock",
        side_effect=keychain.state_io.StateLockTimeoutError("locked"),
    )
    run = mocker.patch("shared.keychain.subprocess.run")
    mocker.patch("shared.keychain.getpass.getuser", return_value="u")

    set_secret("ITS_TEST_KEY", "v")

    run.assert_called_once()  # the write happened despite the lock timeout


# ---- Task #8: TTY-trap fix (interactive vs daemon write form) --------------


def test_set_secret_interactive_uses_argv_not_stdin(mocker):
    """With a controlling TTY (interactive operator run), the value is passed as
    `-w VALUE` on argv — NOT via stdin — so `security` never prompts /dev/tty (the
    TTY-trap that corrupted ITS_BOX_REFRESH_TOKEN during the A3 smoke).
    PROVE-IT-BITES: the value is on argv and `input` is None (no prompt-able
    stdin to mis-read)."""
    mocker.patch("shared.keychain._has_controlling_tty", return_value=True)
    mock_run = mocker.patch("shared.keychain.subprocess.run")
    mocker.patch("shared.keychain.getpass.getuser", return_value="someuser")

    set_secret("ITS_TEST_KEY", "secret-value")

    cmd = mock_run.call_args.args[0]
    assert cmd[cmd.index("-w") + 1] == "secret-value"  # value on argv, -w not last
    assert "-U" in cmd and cmd.index("-U") < cmd.index("-w")
    assert mock_run.call_args.kwargs["input"] is None  # no stdin prompt fed
    assert mock_run.call_args.kwargs["timeout"] == KEYCHAIN_CLI_TIMEOUT


def test_set_secret_daemon_keeps_value_off_argv(mocker):
    """With NO controlling TTY (launchd daemon — the frequent, F04-sensitive
    path), the write is UNCHANGED: value on stdin twice, never on argv, `-w`
    last. This guards against an argv-leak regression on the daemon path."""
    mocker.patch("shared.keychain._has_controlling_tty", return_value=False)
    mock_run = mocker.patch("shared.keychain.subprocess.run")
    mocker.patch("shared.keychain.getpass.getuser", return_value="someuser")

    set_secret("ITS_TEST_KEY", "secret-value")

    cmd = mock_run.call_args.args[0]
    assert cmd[-1] == "-w"
    assert "secret-value" not in cmd
    assert mock_run.call_args.kwargs["input"] == "secret-value\nsecret-value\n"


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
