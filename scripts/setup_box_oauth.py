#!/usr/bin/env python3
"""One-time interactive setup for Box OAuth 2.0.

Runs the OAuth 2.0 Authorization Code Grant flow against the Box "ITS
sandbox" Custom App, then stores the resulting refresh token in macOS
Keychain under ITS_BOX_REFRESH_TOKEN. After this completes,
shared/box_client.py can authenticate to Box on every ITS run with no
human interaction.

USAGE
    python scripts/setup_box_oauth.py

PREREQUISITES (operator-side, one-time)

  1. Configure the Box Custom App for OAuth 2.0 User Authentication
     (Dev Console → ITS sandbox → Configuration → Authentication Method
     → "User Authentication (OAuth 2.0)").

  2. Add http://localhost:8000/callback to the app's "OAuth 2.0 Redirect
     URIs" (same Configuration tab → OAuth 2.0 Redirect URIs).

  3. Seed Keychain with the app's client ID + secret:
         security add-generic-password -a "$USER" -s ITS_BOX_CLIENT_ID -w "<id>" -U
         security add-generic-password -a "$USER" -s ITS_BOX_CLIENT_SECRET -w "<secret>" -U

  4. Run this script. A browser opens to the Box authorize URL; log in
     (if not already) and click "Grant access." The browser redirects
     back to a local server this script runs at http://localhost:8000.
     Script captures the auth code, exchanges it for tokens, stores the
     refresh token, prints the authenticated user's email for visual
     confirmation, and exits.

RECOVERY
  - If the redirect URI is rejected by Box: add
    http://localhost:8000/callback in Box Dev Console → ITS sandbox →
    Configuration → OAuth 2.0 Redirect URIs.
  - If the wrong user is authenticated (e.g., personal Box vs
    evergreenmirror): cancel, log out of Box in the browser, re-run.
    The /users/me confirmation at the end is the only protection
    against this.
  - If the refresh token is lost or revoked: simply re-run this
    script. Nothing else in ITS needs touching.

DO NOT RUN unattended — this script requires a human at the keyboard.
DO NOT seed ITS_BOX_REFRESH_TOKEN manually; this script writes it.
"""
from __future__ import annotations

import http.server
import json
import secrets
import socketserver
import sys
import urllib.parse
import webbrowser

import requests  # type: ignore[import-untyped]

from shared import keychain
from shared.box_client import (
    KC_CLIENT_ID,
    KC_CLIENT_SECRET,
    KC_REFRESH_TOKEN,
    OAUTH_AUTHORIZE_URL,
    OAUTH_TOKEN_URL,
)

REDIRECT_HOST = "localhost"
REDIRECT_PORT = 8000
REDIRECT_PATH = "/callback"
REDIRECT_URI = f"http://{REDIRECT_HOST}:{REDIRECT_PORT}{REDIRECT_PATH}"

# Box User Authentication grants scopes at the consent screen — no admin
# re-authorization needed (different from JWT). Scope set matches what the
# previous Custom App had configured. If the consent screen shows a smaller
# set than expected, check Application Scopes in Box Dev Console.
_REQUESTED_SCOPES = "root_readwrite manage_users"  # noqa — informational only


class _CallbackHandler(http.server.BaseHTTPRequestHandler):
    """Single-purpose HTTP handler — captures one OAuth callback then dies."""

    # Populated on the server instance by main() before serve_forever.
    expected_state: str = ""
    captured: dict[str, str] = {}

    def do_GET(self) -> None:  # noqa: N802 — stdlib API requires this name
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path != REDIRECT_PATH:
            self.send_response(404)
            self.end_headers()
            return

        query = urllib.parse.parse_qs(parsed.query)
        state = (query.get("state") or [""])[0]
        code = (query.get("code") or [""])[0]
        error = (query.get("error") or [""])[0]

        if error:
            self._respond_html(
                400,
                f"<h1>Box returned an error</h1><pre>{_escape(error)}</pre>"
                "<p>You can close this tab and re-run "
                "<code>setup_box_oauth.py</code>.</p>",
            )
            self.server.captured = {"error": error}  # type: ignore[attr-defined]
            return

        if not state or state != self.server.expected_state:  # type: ignore[attr-defined]
            self._respond_html(
                400,
                "<h1>State mismatch</h1>"
                "<p>The state parameter didn't match. This may indicate a "
                "stale callback from a previous attempt; close this tab and "
                "re-run.</p>",
            )
            self.server.captured = {"error": "state_mismatch"}  # type: ignore[attr-defined]
            return

        if not code:
            self._respond_html(
                400,
                "<h1>Missing code</h1>"
                "<p>Box redirected without an authorization code. "
                "Re-run the setup script.</p>",
            )
            self.server.captured = {"error": "missing_code"}  # type: ignore[attr-defined]
            return

        self._respond_html(
            200,
            "<h1>OAuth setup complete</h1>"
            "<p>You can close this tab. The setup script will print "
            "your authenticated user details in the terminal.</p>",
        )
        self.server.captured = {"code": code}  # type: ignore[attr-defined]

    def log_message(self, format: str, *args) -> None:  # noqa: A002 — stdlib API name
        # Silence default per-request access log to stderr. The script
        # already prints structured status to stdout.
        return

    def _respond_html(self, status: int, html: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(html.encode("utf-8"))


def _escape(s: str) -> str:
    """Minimal HTML escape for error strings displayed in the browser."""
    return (
        s.replace("&", "&amp;")
         .replace("<", "&lt;")
         .replace(">", "&gt;")
         .replace('"', "&quot;")
    )


def _capture_callback(expected_state: str) -> dict[str, str]:
    """Run a single-request HTTP server on the redirect port; return captured params."""
    handler = _CallbackHandler
    with socketserver.TCPServer((REDIRECT_HOST, REDIRECT_PORT), handler) as httpd:
        httpd.expected_state = expected_state  # type: ignore[attr-defined]
        httpd.captured = {}  # type: ignore[attr-defined]
        # Serve until the callback handler stuffs something into `captured`.
        # handle_request() processes exactly one connection then returns;
        # if the user double-fires (e.g., browser preload), we loop until
        # captured is populated.
        while not getattr(httpd, "captured", {}):
            httpd.handle_request()
        return httpd.captured  # type: ignore[attr-defined]


def _exchange_code_for_tokens(
    code: str, client_id: str, client_secret: str,
) -> dict[str, str]:
    """POST /oauth2/token with authorization_code grant. Returns full JSON body."""
    response = requests.post(
        OAUTH_TOKEN_URL,
        data={
            "grant_type": "authorization_code",
            "code": code,
            "client_id": client_id,
            "client_secret": client_secret,
        },
        timeout=30,
    )
    if response.status_code != 200:
        raise SystemExit(
            f"Token exchange failed (HTTP {response.status_code}): "
            f"{response.text[:300]}"
        )
    return response.json()


def _fetch_authenticated_user(access_token: str) -> dict[str, str]:
    """GET /users/me with the new access token. Used for visual confirmation."""
    response = requests.get(
        "https://api.box.com/2.0/users/me",
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=30,
    )
    if response.status_code != 200:
        raise SystemExit(
            f"/users/me lookup failed (HTTP {response.status_code}): "
            f"{response.text[:300]}"
        )
    return response.json()


def main() -> None:
    print("ITS Box OAuth setup")
    print("=" * 60)

    print("\n[1/5] Reading client credentials from Keychain...")
    try:
        client_id = keychain.get_secret(KC_CLIENT_ID)
        client_secret = keychain.get_secret(KC_CLIENT_SECRET)
    except keychain.KeychainError as e:
        print(f"      ERROR: {e}")
        print(
            "      Seed both ITS_BOX_CLIENT_ID and ITS_BOX_CLIENT_SECRET in "
            "Keychain first.\n"
            "      See the docstring at the top of this script."
        )
        sys.exit(1)
    print("      OK")

    print("\n[2/5] Opening browser to Box authorize URL...")
    state = secrets.token_urlsafe(32)
    authorize_params = urllib.parse.urlencode({
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": REDIRECT_URI,
        "state": state,
    })
    authorize_url = f"{OAUTH_AUTHORIZE_URL}?{authorize_params}"
    print(f"      URL: {authorize_url}")
    webbrowser.open(authorize_url)

    print(
        f"\n[3/5] Waiting for Box to redirect to {REDIRECT_URI} ...\n"
        "      If the redirect URI is rejected, add\n"
        f"      {REDIRECT_URI}\n"
        "      to your Box Custom App's OAuth 2.0 Redirect URIs and retry."
    )
    captured = _capture_callback(state)
    if "error" in captured:
        print(f"      ERROR: {captured['error']}")
        sys.exit(1)
    code = captured["code"]
    print("      OK: authorization code captured")

    print("\n[4/5] Exchanging code for tokens at api.box.com/oauth2/token ...")
    tokens = _exchange_code_for_tokens(code, client_id, client_secret)
    access_token = tokens.get("access_token")
    refresh_token = tokens.get("refresh_token")
    if not access_token or not refresh_token:
        print(f"      ERROR: token response missing fields: {json.dumps(tokens)[:300]}")
        sys.exit(1)
    keychain.set_secret(KC_REFRESH_TOKEN, refresh_token)
    rt_redacted = f"<redacted, {len(refresh_token)} chars>"
    print(f"      OK: refresh token persisted to Keychain as {KC_REFRESH_TOKEN!r} "
          f"({rt_redacted})")

    print("\n[5/5] Visual confirmation via /users/me ...")
    user = _fetch_authenticated_user(access_token)
    name = user.get("name", "<unknown>")
    login = user.get("login", "<unknown>")
    print(f"      Authenticated user: {name} <{login}>")

    print("\n" + "=" * 60)
    print("Setup complete. ITS can now authenticate to Box.")
    print("Verify the user above is correct. If not, re-run after logging "
          "out of the wrong account.")


if __name__ == "__main__":
    main()
