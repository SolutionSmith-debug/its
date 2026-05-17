#!/usr/bin/env python3
"""Smoke test for ITS Microsoft Graph integration.

OPERATIONAL — makes REAL network calls and sends REAL email.
Sandbox-only: mailbox addresses hardcoded to evergreenmirror.com.

Re-run after:
  - Client secret rotation
  - Entra app re-registration or scope changes
  - Application Access Policy modifications
  - Live-tenant cutover (after duplicating with customer-domain mailboxes)

Parameterization of mailbox addresses deferred until Customer 2 onboarding.

Verifies the full chain end-to-end:
  1. Keychain credentials are readable
  2. MSAL client-credentials auth against Entra ID app registration works
  3. Mail.Read scope works (lists inbox of safety@)
  4. Application Access Policy enforcement (persona mailbox correctly blocked)
  5. Mail.Send scope works (sends test mail from safety@ to seths@)

Run after Entra app registration, Keychain seeding, and Application Access Policy.
Re-run after any client secret rotation or scope change.
"""
from __future__ import annotations

import os
import subprocess
import sys

try:
    import msal
    import requests
except ImportError as e:
    print(f"❌ Missing dependency: {e.name}. Run: pip3 install --user msal requests")
    sys.exit(1)


def keychain_get(service: str) -> str:
    """Read a secret from macOS Keychain. Account = current user."""
    try:
        result = subprocess.run(
            ["security", "find-generic-password", "-a", os.environ["USER"], "-s", service, "-w"],
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout.strip()
    except subprocess.CalledProcessError:
        print(f"❌ Keychain entry not found: {service}")
        print(f"   Re-run: security add-generic-password -a \"$USER\" -s \"{service}\" -w 'VALUE' -U")
        sys.exit(1)


def get_access_token() -> str:
    tenant_id = keychain_get("ITS_MS_TENANT_ID")
    client_id = keychain_get("ITS_MS_CLIENT_ID")
    client_secret = keychain_get("ITS_MS_CLIENT_SECRET")

    app = msal.ConfidentialClientApplication(
        client_id,
        authority=f"https://login.microsoftonline.com/{tenant_id}",
        client_credential=client_secret,
    )

    result = app.acquire_token_for_client(scopes=["https://graph.microsoft.com/.default"])

    if "access_token" not in result:
        err = result.get("error", "?")
        desc = result.get("error_description", "?")
        print(f"❌ Token acquisition failed: {err}")
        print(f"   {desc}")
        sys.exit(1)

    return result["access_token"]


def list_inbox(token: str, mailbox: str) -> tuple[int, str]:
    """Returns (http_status, message)."""
    url = (
        f"https://graph.microsoft.com/v1.0/users/{mailbox}/mailFolders/Inbox/messages"
        "?$top=5&$select=subject,from,receivedDateTime"
    )
    r = requests.get(url, headers={"Authorization": f"Bearer {token}"})
    if r.status_code == 200:
        return r.status_code, f"OK: {len(r.json().get('value', []))} message(s)"
    try:
        msg = r.json().get("error", {}).get("message", "unknown")
    except Exception:
        msg = r.text[:200]
    return r.status_code, f"HTTP {r.status_code}: {msg}"


def send_mail(token: str, from_mailbox: str, to: str, subject: str, body: str) -> tuple[bool, str]:
    url = f"https://graph.microsoft.com/v1.0/users/{from_mailbox}/sendMail"
    payload = {
        "message": {
            "subject": subject,
            "body": {"contentType": "Text", "content": body},
            "toRecipients": [{"emailAddress": {"address": to}}],
        },
        "saveToSentItems": True,
    }
    r = requests.post(
        url,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json=payload,
    )
    if r.status_code == 202:
        return True, "OK: 202 Accepted"
    try:
        msg = r.json().get("error", {}).get("message", "unknown")
    except Exception:
        msg = r.text[:200]
    return False, f"HTTP {r.status_code}: {msg}"


def main() -> None:
    print("ITS Microsoft Graph smoke test")
    print("=" * 60)

    print("\n[1/4] Acquiring access token (Keychain → MSAL → Entra)...")
    token = get_access_token()
    print(f"      ✅ Token acquired ({len(token)} chars)")

    print("\n[2/4] Reading inbox of safety@ (Mail.Read + policy = Granted)...")
    status, msg = list_inbox(token, "safety@evergreenmirror.com")
    if status == 200:
        print(f"      ✅ {msg}")
    else:
        print(f"      ❌ {msg}")
        if status == 403:
            print("      → Application Access Policy may still be propagating (up to 30 min).")
            print("        Wait 10 min and re-run. If still 403, check policy with:")
            print("        Test-ApplicationAccessPolicy -Identity safety@evergreenmirror.com -AppId <appid>")
        sys.exit(1)

    print("\n[3/4] Verifying persona mailbox is BLOCKED (jacobs@ = Denied)...")
    status, msg = list_inbox(token, "jacobs@evergreenmirror.com")
    if status == 403:
        print(f"      ✅ Correctly denied: {msg}")
    elif status == 200:
        print(f"      ❌ SECURITY ISSUE: jacobs@ should be denied but returned: {msg}")
        print("      → Application Access Policy is not enforcing. Investigate before proceeding.")
        sys.exit(1)
    else:
        print(f"      ⚠️  Unexpected response: {msg}")
        print("      → May be propagation lag. Re-run in 10 min.")

    print("\n[4/4] Sending test mail from safety@ to seths@ (Mail.Send)...")
    ok, msg = send_mail(
        token,
        from_mailbox="safety@evergreenmirror.com",
        to="seths@evergreenmirror.com",
        subject="ITS Graph smoke test — automated",
        body=(
            "This message was sent by smoke_test_graph.py via Microsoft Graph using\n"
            "client-credentials flow against the ITS-sandbox Entra app.\n\n"
            "If you see this, the full chain (Keychain → MSAL → Entra → Graph → Send-As) works."
        ),
    )
    if ok:
        print(f"      ✅ {msg}")
    else:
        print(f"      ❌ {msg}")
        sys.exit(1)

    print("\n" + "=" * 60)
    print("✅ All checks passed. graph_client.py can now be wired up.")
    print("   Check seths@ inbox for the test message (From: safety@evergreenmirror.com).")


if __name__ == "__main__":
    main()
