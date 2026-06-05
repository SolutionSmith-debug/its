"""Live-API integration test for shared/portal_client.py (Op Stds §30).

DEPLOY-GATED: needs a reachable Safety Portal Worker + the internal bearer token.
Until the Cloudflare deploy (the NEXT session), this SKIPS — it exists so the
deploy session has the SDK-vs-Live round-trip ready. Default `pytest -q` skips it
(integration marker); CI never runs it.

Config:
    ITS_PORTAL_WORKER_BASE_URL  (env)      — the Worker origin, e.g. https://portal…workers.dev
    ITS_PORTAL_INTERNAL_TOKEN   (Keychain) — the bearer mirroring PORTAL_INTERNAL_API_TOKEN

Safety: this test is READ-MOSTLY. `get_pending` is read-only. `mark_filed` is
called ONLY for a guaranteed-nonexistent UUID (expects found=False) so it can
never drain a real pending submission.
"""
from __future__ import annotations

import os

import pytest

from shared import keychain, portal_client

pytestmark = pytest.mark.integration


@pytest.fixture
def _portal():
    base_url = os.environ.get("ITS_PORTAL_WORKER_BASE_URL", "").strip()
    if not base_url:
        pytest.skip("ITS_PORTAL_WORKER_BASE_URL not set (Worker not deployed yet)")
    try:
        token = keychain.get_secret("ITS_PORTAL_INTERNAL_TOKEN")
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"ITS_PORTAL_INTERNAL_TOKEN unavailable: {e!r}")
    if not token:
        pytest.skip("ITS_PORTAL_INTERNAL_TOKEN returned empty")
    return base_url, token


def test_get_pending_returns_a_list(_portal):
    base_url, token = _portal
    pending = portal_client.get_pending(base_url, token, limit=5)
    assert isinstance(pending, list)
    # If anything is queued, every row carries the contract fields.
    for row in pending:
        assert "submission_uuid" in row and "hmac" in row and "payload_json" in row


def test_mark_filed_nonexistent_uuid_is_safe_noop(_portal):
    base_url, token = _portal
    # A UUID that cannot exist → the Worker reports found=False, drains nothing.
    found = portal_client.mark_filed(
        base_url, token,
        submission_uuid="int-nonexistent-00000000",
        box_link="https://app.box.com/file/int-test",
    )
    assert found is False
