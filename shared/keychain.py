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
