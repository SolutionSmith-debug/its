"""Microsoft Graph SDK wrapper for Outlook (mail), Teams, Calendar.

Uses MSAL for OAuth client-credentials flow against an Entra ID app registration. The app's
client ID and secret come from Keychain.

Awaiting Microsoft 365 admin access + app registration (Phase 1 open question).
Stubbed import-safe.
"""
from __future__ import annotations

from typing import Any

_client: Any | None = None


def get_client():
    global _client
    if _client is None:
        # TODO: uncomment once Entra app registration is complete.
        # import msal
        # tenant_id = keychain.get_secret("ITS_MS_TENANT_ID")
        # client_id = keychain.get_secret("ITS_MS_CLIENT_ID")
        # client_secret = keychain.get_secret("ITS_MS_CLIENT_SECRET")
        # _client = msal.ConfidentialClientApplication(
        #     client_id,
        #     authority=f"https://login.microsoftonline.com/{tenant_id}",
        #     client_credential=client_secret,
        # )
        raise NotImplementedError(
            "Microsoft Graph client not yet wired. "
            "Awaiting Entra ID app registration. Required Keychain entries: "
            "ITS_MS_TENANT_ID, ITS_MS_CLIENT_ID, ITS_MS_CLIENT_SECRET."
        )
    return _client


def send_mail(*, to: list[str], subject: str, body: str, from_mailbox: str):
    """Send mail via Graph using the configured app."""
    raise NotImplementedError


def list_inbox(*, mailbox: str, since: str | None = None) -> list[dict[str, Any]]:
    """List inbox messages."""
    raise NotImplementedError
