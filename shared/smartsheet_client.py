"""Smartsheet SDK wrapper.

Lazy-loads the API token from Keychain. Use this for every read/write to Smartsheet rather
than instantiating the SDK directly — gives us a single place to add retry, rate-limiting,
and logging later.

Awaiting sandbox Smartsheet provisioning — Daniel Stephens completing as of 2026-05-14.
Stubbed import-safe so modules can import this without exploding before credentials land.
"""
from __future__ import annotations

from typing import Any

_client: Any | None = None


def get_client():
    global _client
    if _client is None:
        # TODO: uncomment once Keychain entry exists.
        # import smartsheet
        # token = keychain.get_secret("ITS_SMARTSHEET_TOKEN")
        # _client = smartsheet.Smartsheet(token)
        # _client.errors_as_exceptions(True)
        raise NotImplementedError(
            "Smartsheet client not yet wired. "
            "Add ITS_SMARTSHEET_TOKEN to Keychain, then enable in shared/smartsheet_client.py."
        )
    return _client


def get_sheet(sheet_id: int):
    """Fetch a sheet by ID."""
    return get_client().Sheets.get_sheet(sheet_id)


def add_row(sheet_id: int, cells: list[dict[str, Any]]):
    """Append a row to a sheet. `cells` is a list of {column_id, value} dicts."""
    # TODO: implement once smartsheet SDK is enabled.
    raise NotImplementedError
