"""Live-API integration test for shared/portal_client.py (Op Stds §30).

DEPLOY-GATED: needs a reachable Safety Portal Worker + the internal bearer token.
Until the Cloudflare deploy (the NEXT session), this SKIPS — it exists so the
deploy session has the SDK-vs-Live round-trip ready. Default `pytest -q` skips it
(integration marker); CI never runs it.

Config:
    ITS_PORTAL_WORKER_BASE_URL  (env)      — the Worker origin, e.g. https://portal…workers.dev
    ITS_PORTAL_INTERNAL_TOKEN   (Keychain) — the bearer mirroring PORTAL_INTERNAL_API_TOKEN

Safety: `get_pending` is read-only; `mark_filed` is called ONLY for a
guaranteed-nonexistent UUID (found=False) so it never drains a real submission.
`push_jobs` pushes the CURRENT ITS_Active_Jobs set verbatim — the same idempotent
full-replace reconcile `portal_poll` runs each cycle, converging the dropdown to
Smartsheet, never a destructive subset.
"""
from __future__ import annotations

import os

import pytest

from shared import active_jobs, keychain, portal_client

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


def test_push_jobs_full_set_reconcile_round_trip(_portal):
    base_url, token = _portal
    # Push the CURRENT ITS_Active_Jobs set — exactly what portal_poll does each
    # cycle (idempotent full-replace reconcile, NOT a destructive subset push: the
    # dropdown converges to Smartsheet). Skips if the sheet read is empty (the
    # Worker rejects an empty push by design — it would otherwise wipe the dropdown).
    jobs = active_jobs.list_all_jobs()
    if not jobs:
        pytest.skip("ITS_Active_Jobs read returned no rows")
    payload = [
        {"job_id": j.job_id, "project_name": j.project_name, "active": 1 if j.is_active else 0}
        for j in jobs
    ]
    out = portal_client.push_jobs(base_url, token, payload)
    assert out.get("ok") is True
    assert out.get("upserted") == len(payload)
    assert "deactivated" in out
