#!/usr/bin/env python3
"""Operator CLI for Safety Portal user provisioning + revocation (Phase 7, brief §4).

Purpose
    Operator-run command line over the Worker's bearer-gated `/api/internal/admin/*`
    routes — provision / reset-password / disable / enable / list portal users:
        add-user <username>        provision (prompts for password)
        reset-password <username>  re-hash an existing user's password (prompts)
        disable-user <username>    lock out (revocation, effective next request)
        enable-user <username>     restore access
        list-users                 usernames + disabled flag (no hashes)
    Run `python -m safety_reports.portal_admin <subcommand> ...`. Usernames are
    `lastname.firstname` (lowercased); the Worker validates the format.

Invariants
    - Operator-run, NOT a daemon (no kill-switch / @its_error_log decorator).
    - Passwords are NEVER stored, echoed, or logged here: typed via getpass (no
      echo, confirmed twice) and sent over the bearer-gated channel; the BACKEND
      bcrypt-hashes (cost 10). (Invariant 1 + Invariant 2 posture.)
    - All HTTP routes through `shared.portal_client.admin_request` (F02-allowlisted
      control-plane to OUR OWN Worker); this module imports no network library and
      performs NO external send.
    - The admin bearer (Keychain `ITS_PORTAL_ADMIN_TOKEN`) is DISTINCT from the
      poller's `ITS_PORTAL_INTERNAL_TOKEN` (privilege separation).

Failure modes
    - Missing creds (no `ITS_PORTAL_ADMIN_TOKEN` Keychain entry / no
      `safety_reports.portal.worker_base_url` ITS_Config row) → exit 2 (loud, never
      silent). A wrong/missing admin bearer → the Worker 401s → `PortalAuthError`.
    - A semantic refusal (409 exists / 404 not-found / 400 invalid) prints `FAIL: …`
      and exits 1 — the CLI never silently succeeds on bad state.

Consumers
    Operator-invoked only (no daemon, no launchd plist). Reads `shared.keychain`,
    `shared.smartsheet_client` (ITS_Config), and `shared.portal_client`.
"""
from __future__ import annotations

import argparse
import getpass
import sys

from shared import keychain, portal_client, smartsheet_client

KC_ADMIN_TOKEN = "ITS_PORTAL_ADMIN_TOKEN"  # noqa: S105 — Keychain entry NAME, not a secret
CFG_WORKER_BASE_URL = "safety_reports.portal.worker_base_url"
WORKSTREAM = "safety_reports"
MIN_PASSWORD_LEN = 8


def _fail(msg: str) -> None:
    print(f"FAIL: {msg}", file=sys.stderr)
    sys.exit(1)


def _resolve_creds() -> tuple[str, str]:
    """Worker base URL (ITS_Config) + admin bearer (Keychain). Exits 2 if either absent."""
    try:
        base_url = smartsheet_client.get_setting(CFG_WORKER_BASE_URL, workstream=WORKSTREAM)
    except smartsheet_client.SmartsheetError as exc:
        print(f"FAIL: cannot read {CFG_WORKER_BASE_URL} from ITS_Config: {exc!r}", file=sys.stderr)
        sys.exit(2)
    try:
        token = keychain.get_secret(KC_ADMIN_TOKEN)
    except keychain.KeychainError as exc:
        print(f"FAIL: cannot read Keychain {KC_ADMIN_TOKEN}: {exc!r}", file=sys.stderr)
        sys.exit(2)
    if not base_url or not token:
        print(
            f"FAIL: {CFG_WORKER_BASE_URL} (ITS_Config) and {KC_ADMIN_TOKEN} (Keychain) "
            "must both be set",
            file=sys.stderr,
        )
        sys.exit(2)
    return base_url, token


def _prompt_new_password() -> str:
    """Prompt for a password twice (getpass — never echoed); validate match + length."""
    p1 = getpass.getpass("New password: ")
    p2 = getpass.getpass("Confirm password: ")
    if p1 != p2:
        _fail("passwords do not match")
    if len(p1) < MIN_PASSWORD_LEN:
        _fail(f"password too short (min {MIN_PASSWORD_LEN})")
    return p1


def cmd_add_user(base_url: str, token: str, username: str) -> None:
    password = _prompt_new_password()
    status, data = portal_client.admin_request(
        base_url, token, "POST", "/api/internal/admin/users",
        json_body={"username": username, "password": password},
    )
    if status == 201:
        print(f"OK: created user {data.get('username', username)!r}")
    elif status == 409:
        _fail(f"user {username!r} already exists (use reset-password)")
    elif status == 400:
        _fail(
            f"rejected ({data.get('error')}); username must be lastname.firstname, "
            f"password ≥ {MIN_PASSWORD_LEN} chars"
        )
    else:
        _fail(f"unexpected status {status}: {data}")


def cmd_reset_password(base_url: str, token: str, username: str) -> None:
    password = _prompt_new_password()
    status, data = portal_client.admin_request(
        base_url, token, "POST", "/api/internal/admin/users/reset",
        json_body={"username": username, "password": password},
    )
    if status == 200:
        print(f"OK: reset password for {username!r}")
    elif status == 404:
        _fail(f"user {username!r} not found (use add-user)")
    else:
        _fail(f"unexpected status {status}: {data}")


def cmd_set_disabled(base_url: str, token: str, username: str, *, disable: bool) -> None:
    verb = "disable" if disable else "enable"
    status, data = portal_client.admin_request(
        base_url, token, "POST", f"/api/internal/admin/users/{verb}",
        json_body={"username": username},
    )
    if status == 200:
        print(f"OK: {verb}d user {username!r}")
    elif status == 404:
        _fail(f"user {username!r} not found")
    else:
        _fail(f"unexpected status {status}: {data}")


def cmd_list_users(base_url: str, token: str) -> None:
    status, data = portal_client.admin_request(
        base_url, token, "GET", "/api/internal/admin/users"
    )
    if status != 200:
        _fail(f"unexpected status {status}: {data}")
    users = data.get("users") or []
    if not users:
        print("(no users)")
        return
    for u in users:
        flag = "DISABLED" if u.get("disabled") else "active"
        print(f"  {str(u.get('username')):<32} {flag}")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="portal_admin",
        description="Safety Portal user provisioning (operator; Phase 7).",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name in ("add-user", "reset-password", "disable-user", "enable-user"):
        sub.add_parser(name).add_argument("username")
    sub.add_parser("list-users")
    args = parser.parse_args(argv)

    base_url, token = _resolve_creds()
    if args.cmd == "add-user":
        cmd_add_user(base_url, token, args.username)
    elif args.cmd == "reset-password":
        cmd_reset_password(base_url, token, args.username)
    elif args.cmd == "disable-user":
        cmd_set_disabled(base_url, token, args.username, disable=True)
    elif args.cmd == "enable-user":
        cmd_set_disabled(base_url, token, args.username, disable=False)
    elif args.cmd == "list-users":
        cmd_list_users(base_url, token)


if __name__ == "__main__":
    main()
