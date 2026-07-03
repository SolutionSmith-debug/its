#!/usr/bin/env python3
"""Operator CLI for Safety Portal user provisioning + revocation (Phase 7, brief §4).

Purpose
    Operator-run command line over the Worker's bearer-gated `/api/internal/admin/*`
    routes — provision / reset-password / disable / enable / set-role / list portal
    users:
        add-user <username> [--role submitter|manager|admin]
                                   provision (prompts for password); default submitter
        reset-password <username>  re-hash an existing user's password (prompts)
        disable-user <username>    lock out (revocation, effective next request)
        enable-user <username>     restore access
        set-role <username> <role> change role (submitter|manager|admin) — BREAK-GLASS for the
                                   in-app admin dashboard (e.g. restore an admin the UI
                                   demoted); deliberately NOT last-admin-guarded so it
                                   is a recovery path OUT of a zero-admin lockout
        list-users                 usernames + role + disabled flag (no hashes)
    Run `python -m safety_reports.portal_admin <subcommand> ...`. Usernames are
    `lastname.firstname` (lowercased); the Worker validates the format. The two
    Phase-1 admins are bootstrapped with `add-user <name> --role admin`.

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


def cmd_add_user(base_url: str, token: str, username: str, role: str = "submitter") -> None:
    password = _prompt_new_password()
    status, data = portal_client.admin_request(
        base_url, token, "POST", "/api/internal/admin/users",
        json_body={"username": username, "password": password, "role": role},
    )
    if status == 201:
        print(f"OK: created user {username!r} (role={role})")
    elif status == 409:
        _fail(f"user {username!r} already exists (use reset-password)")
    elif status == 400:
        _fail(
            f"rejected: username must be lastname.firstname (lowercased), "
            f"password ≥ {MIN_PASSWORD_LEN} chars, role submitter|manager|admin"
        )
    else:
        _fail(f"unexpected status {status}")


def cmd_set_role(base_url: str, token: str, username: str, role: str) -> None:
    status, data = portal_client.admin_request(
        base_url, token, "POST", "/api/internal/admin/users/role",
        json_body={"username": username, "role": role},
    )
    if status == 200:
        print(f"OK: set {username!r} role={role}")
    elif status == 404:
        _fail(f"user {username!r} not found (use add-user)")
    elif status == 400:
        _fail("rejected: role must be submitter|manager|admin, username lastname.firstname")
    else:
        _fail(f"unexpected status {status}")


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
        _fail(f"unexpected status {status}")


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
        _fail(f"unexpected status {status}")


def cmd_list_users(base_url: str, token: str) -> None:
    status, data = portal_client.admin_request(
        base_url, token, "GET", "/api/internal/admin/users"
    )
    if status != 200:
        _fail(f"unexpected status {status}")
    users = data.get("users") or []
    if not users:
        print("(no users)")
        return
    for u in users:
        flag = "DISABLED" if u.get("disabled") else "active"
        role = str(u.get("role") or "submitter")
        print(f"  {str(u.get('username')):<32} {role:<10} {flag}")


def cmd_purge_job(base_url: str, token: str, job_id: str) -> None:
    """Hard-delete a job + ALL its D1 rows (submissions, filed-PDF cache, pdf_requests,
    per-job daily requirements + expected materials).

    For clearing a test / decommissioned job that the daemon /api/internal/sync can't: sync
    refuses an empty set (so a transient empty ITS_Active_Jobs read can't wipe the dropdown),
    which leaves a fully-removed job lingering active=1. D1 is a transport cache — Box + the
    week sheet keep the durable record; this only clears the local copy. Idempotent: an
    unknown job_id reports "nothing purged".
    """
    status, data = portal_client.admin_request(
        base_url, token, "POST", "/api/internal/admin/purge-job",
        json_body={"job_id": job_id},
    )
    if status != 200 or not data.get("ok"):
        _fail(f"purge-job failed (HTTP {status})")  # status only — never log the raw response body
    if not data.get("found"):
        print(f"no job {job_id} in D1 — nothing purged")
        return
    # Coerce the counts to plain ints (they ARE integers in the response): hardens the output
    # and keeps the response dict out of the log line (clear-text-logging hygiene).
    job = int(data.get("job_deleted") or 0)
    subs = int(data.get("submissions") or 0)
    chunks = int(data.get("pdfChunks") or 0)
    reqs = int(data.get("pdfRequests") or 0)
    # Slice 1 (R3-F4): the Worker cascade also purges the two per-job content tables.
    dreqs = int(data.get("requirements") or 0)
    mats = int(data.get("expectedMaterials") or 0)
    print(
        f"purged {job_id}: job={job} submissions={subs} pdf_chunks={chunks} "
        f"pdf_requests={reqs} daily_requirements={dreqs} expected_materials={mats}"
    )


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="portal_admin",
        description="Safety Portal user provisioning (operator; Phase 7).",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_add = sub.add_parser("add-user")
    p_add.add_argument("username")
    p_add.add_argument("--role", choices=("submitter", "manager", "admin"), default="submitter")
    for name in ("reset-password", "disable-user", "enable-user"):
        sub.add_parser(name).add_argument("username")
    p_role = sub.add_parser("set-role")
    p_role.add_argument("username")
    p_role.add_argument("role", choices=("submitter", "manager", "admin"))
    sub.add_parser("list-users")
    sub.add_parser("purge-job").add_argument("job_id")
    args = parser.parse_args(argv)

    base_url, token = _resolve_creds()
    if args.cmd == "add-user":
        cmd_add_user(base_url, token, args.username, args.role)
    elif args.cmd == "reset-password":
        cmd_reset_password(base_url, token, args.username)
    elif args.cmd == "disable-user":
        cmd_set_disabled(base_url, token, args.username, disable=True)
    elif args.cmd == "enable-user":
        cmd_set_disabled(base_url, token, args.username, disable=False)
    elif args.cmd == "set-role":
        cmd_set_role(base_url, token, args.username, args.role)
    elif args.cmd == "list-users":
        cmd_list_users(base_url, token)
    elif args.cmd == "purge-job":
        cmd_purge_job(base_url, token, args.job_id)


if __name__ == "__main__":
    main()
