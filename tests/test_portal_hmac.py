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


# ---- Item-photo protocol (G1 Slice 2) -------------------------------------
# Mirrors the Worker's itemPhotoCanonical (fieldops_checklist.ts); the Worker-side
# vitest (fieldops-item-photo.test.ts) locks the TS half against the SAME canonical
# string + secret, so a drift on either side breaks its CI.

PHOTO_JSON = '{"data":"QUJD","name":"site.jpg","taken_at":"","gps":"","uploaded_by":"sub.sam"}'
ITEM_CANONICAL = f"item_photo:v1\n7\n{PHOTO_JSON}"


def test_item_photo_canonical_format_is_stable() -> None:
    assert (
        portal_hmac.item_photo_canonical(item_state_id=7, photo_json=PHOTO_JSON)
        == ITEM_CANONICAL
    )


def test_item_photo_sign_matches_independent_hmac() -> None:
    expected = _hmac.new(SECRET.encode(), ITEM_CANONICAL.encode(), hashlib.sha256).hexdigest()
    assert (
        portal_hmac.sign_item_photo(SECRET, item_state_id=7, photo_json=PHOTO_JSON)
        == expected
    )


def test_item_photo_verify_round_trip_and_tamper() -> None:
    sig = portal_hmac.sign_item_photo(SECRET, item_state_id=7, photo_json=PHOTO_JSON)
    assert portal_hmac.verify_item_photo(
        SECRET, sig, item_state_id=7, photo_json=PHOTO_JSON
    ) is True
    # Replay onto a DIFFERENT item state fails (item binding).
    assert portal_hmac.verify_item_photo(
        SECRET, sig, item_state_id=8, photo_json=PHOTO_JSON
    ) is False
    # Tampered photo_json fails.
    assert portal_hmac.verify_item_photo(
        SECRET, sig, item_state_id=7, photo_json=PHOTO_JSON.replace("QUJD", "WFla")
    ) is False
    # Absent/empty signature fails, never raises.
    assert portal_hmac.verify_item_photo(
        SECRET, None, item_state_id=7, photo_json=PHOTO_JSON
    ) is False
    assert portal_hmac.verify_item_photo(
        SECRET, "", item_state_id=7, photo_json=PHOTO_JSON
    ) is False


def test_item_photo_domain_separation_from_submission_protocol() -> None:
    # A signature minted by ONE protocol can never verify under the other, even over
    # byte-identical field material — the "item_photo:v1" literal domain-separates.
    sub_sig = portal_hmac.sign(
        SECRET, submission_uuid="item_photo:v1", job_id="7",
        form_code=PHOTO_JSON, work_date="", payload_json="",
    )
    assert portal_hmac.verify_item_photo(
        SECRET, sub_sig, item_state_id=7, photo_json=PHOTO_JSON
    ) is False
