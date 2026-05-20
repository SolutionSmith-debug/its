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
        value: The secret value to persist. Passed via `-w` argv, NOT stdin;
            the value never lands in shell history because Python invokes
            `security` directly via subprocess without a shell.
        account: Optional account; defaults to the current user.

    Raises:
        KeychainError: If the `security` CLI is unavailable or the write fails.
    """
    account = account or getpass.getuser()
    try:
        subprocess.run(
            [
                "security", "add-generic-password",
                "-a", account,
                "-s", service,
                "-w", value,
                "-U",
            ],
            check=True,
            capture_output=True,
            text=True,
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
