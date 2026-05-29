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
import subprocess


class KeychainError(RuntimeError):
    """Raised when a Keychain entry is missing or inaccessible."""


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
        )
        return result.stdout.rstrip("\n")
    except FileNotFoundError as e:
        raise KeychainError(
            "macOS `security` CLI not found. This module is macOS-only."
        ) from e
    except subprocess.CalledProcessError as e:
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
    # `security add-generic-password` reads the password from stdin only when
    # `-w` is the LAST option (`security add-generic-password -h`: "Specify -w
    # as the last option to be prompted"). It then issues a password + retype
    # confirmation prompt and reads one line per prompt, so the value is fed
    # twice. `-U` (update-in-place) MUST precede `-w`; placed after `-w` it gets
    # swallowed as the password value — verified live, the stored secret became
    # the literal "-U". Feeding the value twice is robust whether the CLI
    # prompts once or twice (a single-prompt build reads the first line and
    # ignores the rest). Reference: audit F04.
    try:
        subprocess.run(
            [
                "security", "add-generic-password",
                "-U",
                "-a", account,
                "-s", service,
                "-w",  # MUST be last — value read from stdin, never argv
            ],
            check=True,
            capture_output=True,
            text=True,
            input=f"{value}\n{value}\n",  # password + retype; never in argv/ps
        )
    except FileNotFoundError as e:
        raise KeychainError(
            "macOS `security` CLI not found. This module is macOS-only."
        ) from e
    except subprocess.CalledProcessError as e:
        # stderr surfaces the actual reason (e.g., permission denied, locked
        # keychain). Don't include `value` in the message — that would leak
        # the secret into logs.
        raise KeychainError(
            f"Keychain write failed for service={service!r}, account={account!r}: "
            f"{e.stderr.strip() or 'no detail'}"
        ) from e
