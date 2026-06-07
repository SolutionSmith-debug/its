"""Deploy-gated integration test for the Phase-7 admin routes + session revocation.

Exercises the full revocation flow against a LIVE deployed Worker: provision a
throwaway user → login (cookie) → /api/session OK → disable → the SAME session is
now 401 (the revocation requireSession reads). SKIPS until the Worker is deployed
with the admin routes AND the operator has set ITS_PORTAL_ADMIN_TOKEN — i.e. it
runs at the activation session, not in CI.

Config:
    ITS_PORTAL_WORKER_BASE_URL (env)      — the deployed Worker origin
    ITS_PORTAL_ADMIN_TOKEN     (Keychain) — the operator-only admin bearer

Residue: leaves the throwaway user `zzadmintest.user` DISABLED (no delete-user
endpoint by design). Harmless + idempotent — a re-run resets + re-enables it.
"""
from __future__ import annotations

import os
import secrets

import pytest
import requests  # type: ignore[import-untyped]

from shared import keychain, portal_client

pytestmark = pytest.mark.integration

ADMIN_USER = "zzadmintest.user"


@pytest.fixture
def _ctx() -> tuple[str, str]:
    base_url = os.environ.get("ITS_PORTAL_WORKER_BASE_URL", "").strip()
    if not base_url:
        pytest.skip("ITS_PORTAL_WORKER_BASE_URL not set (Worker not deployed)")
    try:
        admin_token = keychain.get_secret("ITS_PORTAL_ADMIN_TOKEN")
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"ITS_PORTAL_ADMIN_TOKEN unavailable: {e!r}")
    if not admin_token:
        pytest.skip("ITS_PORTAL_ADMIN_TOKEN returned empty")
    return base_url.rstrip("/"), admin_token


def test_admin_provision_and_revocation_round_trip(_ctx):
    base_url, admin_token = _ctx
    password = "zz-admintest-" + secrets.token_hex(6)

    # Provision (idempotent across runs: 409 → reset to the new password instead).
    status, _ = portal_client.admin_request(
        base_url, admin_token, "POST", "/api/internal/admin/users",
        json_body={"username": ADMIN_USER, "password": password},
    )
    if status == 409:
        status, _ = portal_client.admin_request(
            base_url, admin_token, "POST", "/api/internal/admin/users/reset",
            json_body={"username": ADMIN_USER, "password": password},
        )
        assert status == 200
    else:
        assert status == 201

    # Ensure enabled before the login leg.
    portal_client.admin_request(
        base_url, admin_token, "POST", "/api/internal/admin/users/enable",
        json_body={"username": ADMIN_USER},
    )

    # Login → an authenticated session cookie; /api/session confirms it's valid.
    # Direct requests.Session (not portal_client) here: this is the stateful
    # cookie-session flow admin_request can't model, and tests/ is F02-exempt (the
    # network-allowlist walk covers safety_reports/ + shared/, never tests/).
    sess = requests.Session()
    r = sess.post(f"{base_url}/api/login", json={"username": ADMIN_USER, "password": password}, timeout=30)
    assert r.status_code == 200, r.text
    assert sess.get(f"{base_url}/api/session", timeout=30).status_code == 200

    # Disable → the SAME cookie is now rejected (revocation, effective immediately).
    status, _ = portal_client.admin_request(
        base_url, admin_token, "POST", "/api/internal/admin/users/disable",
        json_body={"username": ADMIN_USER},
    )
    assert status == 200
    assert sess.get(f"{base_url}/api/session", timeout=30).status_code == 401

    # The list reflects the disabled flag (no hashes in the payload).
    status, data = portal_client.admin_request(
        base_url, admin_token, "GET", "/api/internal/admin/users"
    )
    assert status == 200
    assert any(u["username"] == ADMIN_USER and u["disabled"] for u in data["users"])


def test_admin_routes_reject_the_internal_token(_ctx):
    """Privilege separation: the poller's internal token must NOT work on admin routes."""
    base_url, _admin = _ctx
    try:
        internal = keychain.get_secret("ITS_PORTAL_INTERNAL_TOKEN")
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"ITS_PORTAL_INTERNAL_TOKEN unavailable: {e!r}")
    if not internal:
        pytest.skip("ITS_PORTAL_INTERNAL_TOKEN returned empty")
    with pytest.raises(portal_client.PortalAuthError):
        portal_client.admin_request(base_url, internal, "GET", "/api/internal/admin/users")
