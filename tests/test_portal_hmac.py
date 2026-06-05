"""Lock the portal-submission HMAC contract (shared/portal_hmac.py).

The canonical format + HMAC must match the Worker (safety_portal/worker/index.ts)
byte-for-byte; the live wrangler-dev cross-language check proves the actual match,
this locks the Python side so a canonical-format change is caught in CI.
"""
from __future__ import annotations

import hashlib
import hmac as _hmac

from shared import portal_hmac

SECRET = "test-secret-xyz"
FIELDS = {
    "submission_uuid": "u-1",
    "job_id": "JOB-000001",
    "form_code": "jha-v1",
    "work_date": "2026-06-03",
    "payload_json": '{"a":1}',
}
CANONICAL = 'u-1\nJOB-000001\njha-v1\n2026-06-03\n{"a":1}'


def test_canonical_format_is_stable() -> None:
    assert portal_hmac.canonical_payload(**FIELDS) == CANONICAL


def test_sign_matches_independent_hmac() -> None:
    expected = _hmac.new(SECRET.encode(), CANONICAL.encode(), hashlib.sha256).hexdigest()
    assert portal_hmac.sign(SECRET, **FIELDS) == expected


def test_verify_accepts_correct_signature() -> None:
    assert portal_hmac.verify(SECRET, portal_hmac.sign(SECRET, **FIELDS), **FIELDS) is True


def test_verify_rejects_tampered_field() -> None:
    sig = portal_hmac.sign(SECRET, **FIELDS)
    assert portal_hmac.verify(SECRET, sig, **{**FIELDS, "payload_json": '{"a":2}'}) is False


def test_verify_rejects_wrong_secret() -> None:
    assert portal_hmac.verify("other-secret", portal_hmac.sign(SECRET, **FIELDS), **FIELDS) is False


def test_verify_rejects_empty_or_none() -> None:
    assert portal_hmac.verify(SECRET, None, **FIELDS) is False
    assert portal_hmac.verify(SECRET, "", **FIELDS) is False
