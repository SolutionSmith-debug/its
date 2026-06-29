"""macOS Keychain credential access.

All ITS credentials live in macOS Keychain — Anthropic key, Smartsheet token, Box JWT,
Microsoft Graph credentials. Never in env files. Never committed.

Add a secret manually from terminal:
    security add-generic-password -a "$USER" -s "ITS_ANTHROPIC_KEY" -w
    # ^ -w with no value prompts for the secret without it landing in shell history

Read it back:
    security find-generic-password -a "$USER" -s "ITS_ANTHROPIC_KEY" -w
"""
from __future__ import annotations

import getpass
import os
import subprocess
from pathlib import Path

from . import state_io


class KeychainError(RuntimeError):
    """Raised when a Keychain entry is missing or inaccessible."""


class KeychainLockedError(KeychainError):
    """The `security` CLI could not access the keychain because it is LOCKED.

    Distinct from a genuinely missing entry — common after a reboot before the
    login keychain is unlocked (eval A2 `host-keychain-locked-after-reboot`). A
    daemon should fail LOUD with this recognizable signal (and the operator
    unlocks the keychain), not blindly retry or report a misleading
    "entry not found".
    """


# `security` is a fast LOCAL CLI; a multi-second wait means it is hung or blocked
# on a locked-keychain interaction prompt. Bound it so a daemon never hangs
# indefinitely (eval A2 `host-daemon-no-timeout`). 10s is generous for a local call.
KEYCHAIN_CLI_TIMEOUT = 10

# A3: anchor for the cross-process Keychain-WRITE lock. Multiple ITS daemons can
# rotate the same secret (notably the Box refresh token) within one window; an
# un-serialized write race can persist a stale value. `with_path_lock` flocks
# "{anchor}.lock" → ~/its/state/keychain_write.lock. The write is FAIL-OPEN: a
# lock-acquire timeout writes anyway (a missed rotation is worse than a lost lock).
_KEYCHAIN_WRITE_LOCK_ANCHOR = Path.home() / "its" / "state" / "keychain_write"

# Substrings in `security` stderr that indicate a LOCKED keychain
# (errSecInteractionNotAllowed, -25308) rather than a missing item.
_LOCKED_INDICATORS = (
    "interaction is not allowed",
    "interactionnotallowed",
    "-25308",
    "errsecinteractionnotallowed",
)


def _looks_locked(stderr: str) -> bool:
    """True if `security` stderr indicates a locked keychain (vs. a missing item)."""
    low = (stderr or "").lower()
    return any(ind in low for ind in _LOCKED_INDICATORS)


def _has_controlling_tty() -> bool:
    """True if the process has a controlling terminal.

    Directly tests what `security ... -w` would do on write: when a controlling
    /dev/tty exists it PROMPTS there (ignoring piped stdin — the TTY-trap); when
    none exists (a launchd daemon) it reads stdin. `set_secret` uses this to pick
    the write form (see `_do_write`).
    """
    try:
        fd = os.open("/dev/tty", os.O_RDONLY)
    except OSError:
        return False
    os.close(fd)
    return True


def get_secret(service: str, account: str | None = None) -> str:
    """Read a generic-password Keychain entry.

    Args:
        service: The service name (e.g., 'ITS_ANTHROPIC_KEY').
        account: Optional account; defaults to the current user.

    Returns:
        The secret value as a string.

    Raises:
        KeychainError: If the entry does not exist or cannot be read.
    """
    account = account or getpass.getuser()
    try:
        result = subprocess.run(
            ["security", "find-generic-password", "-a", account, "-s", service, "-w"],
            check=True,
            capture_output=True,
            text=True,
            timeout=KEYCHAIN_CLI_TIMEOUT,
        )
        return result.stdout.rstrip("\n")
    except FileNotFoundError as e:
        raise KeychainError(
            "macOS `security` CLI not found. This module is macOS-only."
        ) from e
    except subprocess.TimeoutExpired as e:
        raise KeychainError(
            f"`security` CLI timed out after {KEYCHAIN_CLI_TIMEOUT}s reading "
            f"service={service!r} (keychain locked or hung?). Unlock the login "
            f"keychain (`security unlock-keychain`) and retry."
        ) from e
    except subprocess.CalledProcessError as e:
        if _looks_locked(e.stderr or ""):
            raise KeychainLockedError(
                f"Keychain LOCKED — cannot read service={service!r}: "
                f"{(e.stderr or '').strip() or 'interaction not allowed'}. Unlock "
                f"the login keychain (`security unlock-keychain`) — common after a "
                f"reboot before the keychain is unlocked."
            ) from e
        raise KeychainError(
            f"Keychain entry not found: service={service!r}, account={account!r}. "
            f"Add it with: security add-generic-password -a \"$USER\" -s \"{service}\" -w"
        ) from e


def set_secret(service: str, value: str, account: str | None = None) -> None:
    """Write or overwrite a generic-password Keychain entry.

    Uses `security add-generic-password -U` so an existing entry with the same
    service+account is updated in place rather than rejected. Required by any
    flow that rotates a secret programmatically (e.g., Box OAuth refresh-token
    rotation — see `shared/box_client.py`'s store_tokens callback).

    Args:
        service: The service name (e.g., 'ITS_BOX_REFRESH_TOKEN').
        value: The secret value to persist. Supplied to `security` on stdin
            (`-w` is the last option, with no `-w VALUE` argv element), so the
            secret is not visible to other local processes via `ps` /
            `/proc/<pid>/cmdline` / EDR argv capture. Must be a single-line
            value — the CLI's `-w` prompt is line-based (all ITS secrets are
            single-line API keys / OAuth tokens). Reference: audit F04.
        account: Optional account; defaults to the current user.

    Raises:
        KeychainError: If the `security` CLI is unavailable or the write fails.
    """
    account = account or getpass.getuser()

    def _do_write() -> None:
        # §42 — the macOS Keychain TTY-trap (task #8). `security add-generic-password
        # -w` (bare, as the LAST option) reads the password from the controlling
        # terminal (/dev/tty) when one is present, IGNORING piped stdin. Under
        # launchd (no controlling TTY) it consumes stdin — so daemons work and the
        # value never touches argv/ps (audit F04). But an INTERACTIVE operator run
        # (e.g. a manual Box OAuth refresh-token rotation) then prompts the operator
        # for "password data" they don't have, and any keystroke corrupts the secret
        # (this corrupted ITS_BOX_REFRESH_TOKEN during the A3 smoke).
        #
        # Fix — detect the controlling TTY and split the write form:
        #   * NO TTY (daemon/headless — the frequent, F04-sensitive path): UNCHANGED.
        #     `-w` last + value fed on stdin twice (password + retype); never on argv.
        #   * TTY present (rare interactive operator run): pass the value as `-w VALUE`
        #     on argv so `security` never prompts. TRADEOFF: the secret is briefly
        #     visible to `ps` / argv capture — accepted ONLY here because it is the
        #     operator's own at-keyboard machine and the alternative is the
        #     token-corrupting prompt; the unattended daemon path keeps the value off
        #     argv. (`-U` update-in-place MUST precede `-w`; placed after `-w` it is
        #     swallowed as the password value — verified live. audit F04, task #8.)
        base = ["security", "add-generic-password", "-U", "-a", account, "-s", service]
        if _has_controlling_tty():
            args = [*base, "-w", value]  # interactive: value on argv, no /dev/tty prompt
            input_data: str | None = None
        else:
            args = [*base, "-w"]  # daemon: -w last, value read from stdin (off argv/ps)
            input_data = f"{value}\n{value}\n"  # password + retype; never in argv/ps
        try:
            subprocess.run(
                args,
                check=True,
                capture_output=True,
                text=True,
                input=input_data,
                timeout=KEYCHAIN_CLI_TIMEOUT,
            )
        except FileNotFoundError as e:
            raise KeychainError(
                "macOS `security` CLI not found. This module is macOS-only."
            ) from e
        except subprocess.TimeoutExpired as e:
            # Never include `value` — a timeout message must not leak the secret.
            raise KeychainError(
                f"`security` CLI timed out after {KEYCHAIN_CLI_TIMEOUT}s writing "
                f"service={service!r} (keychain locked or hung?). Unlock the login "
                f"keychain (`security unlock-keychain`) and retry."
            ) from e
        except subprocess.CalledProcessError as e:
            # stderr surfaces the actual reason (e.g., permission denied, locked
            # keychain). Don't include `value` in the message — that would leak
            # the secret into logs.
            if _looks_locked(e.stderr or ""):
                raise KeychainLockedError(
                    f"Keychain LOCKED — cannot write service={service!r}: "
                    f"{(e.stderr or '').strip() or 'interaction not allowed'}. Unlock "
                    f"the login keychain (`security unlock-keychain`)."
                ) from e
            raise KeychainError(
                f"Keychain write failed for service={service!r}, account={account!r}: "
                f"{e.stderr.strip() or 'no detail'}"
            ) from e

    # A3 §42: serialize the write across processes (fail-open). A lock-acquire
    # timeout writes UNLOCKED rather than skipping — a missed secret rotation is
    # worse than a lost lock. The `security`-CLI failure modes raised inside
    # `_do_write` propagate untouched (the lock only handles its own timeout).
    try:
        with state_io.with_path_lock(_KEYCHAIN_WRITE_LOCK_ANCHOR):
            _do_write()
    except state_io.StateLockTimeoutError:
        from .error_log import Severity, log
        log(
            Severity.WARN,
            "shared.keychain",
            f"Keychain write-lock timeout for service={service!r} — writing "
            f"UNLOCKED (fail-open).",
            error_code="keychain_write_lock_timeout",
        )
        _do_write()
