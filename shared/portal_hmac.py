"""Portal-submission HMAC — the Python verify side of the Phase-5 pull-model trust boundary.

Purpose
-------
    Mirror the Cloudflare Worker's signing (safety_portal/worker/index.ts —
    canonicalPayload + hmacHex) on the Python side. The Worker signs each submission
    at /api/submit; the portal_poll daemon (Phase 5) verifies it before intake files
    the submission.

Invariants
----------
    * The canonical payload + HMAC-SHA256-hex MUST match the Worker byte-for-byte:
          canonical = submission_uuid \\n job_id \\n form_code \\n work_date \\n payload_json
          hmac      = HMAC-SHA256(secret, canonical).hexdigest()   (lowercase)
      `payload_json` is the EXACT JSON string the Worker stored — used verbatim,
      NEVER re-serialized (re-serialization would change the bytes and break verify).
    * Constant-time compare (hmac.compare_digest) — no timing oracle.

Failure modes
-------------
    `verify` returns False (never raises) on any mismatch — wrong secret, tampered
    field, or absent/empty signature. A False result is the downgrade defense: the
    caller (portal_poll) rejects + flags the submission and does NOT file it.

Consumers
---------
    safety_reports/portal_poll.py (Phase 5) — verifies every pulled submission before
    handing it to intake. The secret is the macOS Keychain `ITS_PORTAL_HMAC_SECRET`,
    mirroring the Worker's HMAC_PAYLOAD_SECRET.

Item-photo protocol (G1 Slice 2)
--------------------------------
    The checklist item-photo queue (`item_photos`, migration 0036) is signed by the
    Worker with the SAME key + MAC (HMAC-SHA256 → lowercase hex) over a DIFFERENT,
    domain-separated canonical string (fieldops_checklist.ts `itemPhotoCanonical`):

        canonical = "item_photo:v1" \\n <item_state_id (decimal)> \\n <photo_json>

    * The `"item_photo:v1"` literal domain-separates this protocol from submission
      HMACs (a submission canonical starts with its uuid — cross-protocol signature
      confusion is structurally impossible) and versions the string.
    * `item_state_id` binds the photo to its item (a valid signed photo cannot be
      replayed onto a different item without failing verification).
    * `photo_json` is the EXACT stored JSON string ({data,name,taken_at,gps,
      uploaded_by}) — used VERBATIM, never re-serialized, exactly like payload_json.

    `verify_item_photo` is the Mac-side recompute portal_poll's `_service_item_photos`
    pass runs before any byte is screened or filed (the downgrade defense, mirroring
    the submission drain).

Daily-photo protocol (DR-photo-pool Slice 2)
--------------------------------------------
    The daily-report additional-photo POOL (`daily_photo_pool`, migration 0037) is
    signed by the Worker with the SAME key + MAC over its own domain-separated
    canonical string (fieldops_daily_photos.ts `dailyPhotoCanonical`):

        canonical = "daily_photo:v1" \\n <job_id> \\n <work_date> \\n <photo_json>

    * The `"daily_photo:v1"` literal domain-separates this protocol from submission
      HMACs (uuid-first) AND item-photo HMACs ("item_photo:v1") — cross-protocol
      signature confusion is structurally impossible — and versions the string.
    * `job_id` + `work_date` bind the photo to its day (a valid signed photo cannot
      be replayed onto a different job or date without failing verification). The
      pool row id can't participate — it doesn't exist until the INSERT the
      signature rides in.
    * `photo_json` is the EXACT stored JSON string ({data,name,taken_at,gps,
      uploaded_by}) — used VERBATIM, never re-serialized, exactly like payload_json.

    `verify_daily_photo` is the Mac-side recompute portal_poll's
    `_service_daily_photos` pass runs before any byte is screened or filed.
"""
from __future__ import annotations

import hashlib
import hmac as _hmac

# The item-photo protocol's domain-separation literal — MUST match the Worker's
# itemPhotoCanonical (safety_portal/worker/fieldops_checklist.ts) byte-for-byte.
ITEM_PHOTO_DOMAIN = "item_photo:v1"

# The daily-photo protocol's domain-separation literal — MUST match the Worker's
# dailyPhotoCanonical (safety_portal/worker/fieldops_daily_photos.ts) byte-for-byte.
DAILY_PHOTO_DOMAIN = "daily_photo:v1"


def canonical_payload(
    *, submission_uuid: str, job_id: str, form_code: str, work_date: str, payload_json: str
) -> str:
    """The exact string the Worker signs (order + ``\\n`` separator are load-bearing)."""
    return "\n".join([submission_uuid, job_id, form_code, work_date, payload_json])


def sign(
    secret: str, *, submission_uuid: str, job_id: str, form_code: str, work_date: str, payload_json: str
) -> str:
    """HMAC-SHA256(secret, canonical) → lowercase hex — identical to the Worker's hmacHex."""
    msg = canonical_payload(
        submission_uuid=submission_uuid, job_id=job_id, form_code=form_code,
        work_date=work_date, payload_json=payload_json,
    ).encode("utf-8")
    return _hmac.new(secret.encode("utf-8"), msg, hashlib.sha256).hexdigest()


def verify(
    secret: str, provided_hmac: str | None, *,
    submission_uuid: str, job_id: str, form_code: str, work_date: str, payload_json: str,
) -> bool:
    """True iff `provided_hmac` matches the recomputed signature. Never raises (False on any mismatch)."""
    expected = sign(
        secret, submission_uuid=submission_uuid, job_id=job_id, form_code=form_code,
        work_date=work_date, payload_json=payload_json,
    )
    return _hmac.compare_digest(expected, provided_hmac or "")


# ---- Item-photo protocol (G1 Slice 2 — see module docstring) ---------------------


def item_photo_canonical(*, item_state_id: int, photo_json: str) -> str:
    """The exact string the Worker signs for one checklist item photo
    (fieldops_checklist.ts itemPhotoCanonical — order + ``\\n`` separator load-bearing)."""
    return "\n".join([ITEM_PHOTO_DOMAIN, str(item_state_id), photo_json])


def sign_item_photo(secret: str, *, item_state_id: int, photo_json: str) -> str:
    """HMAC-SHA256(secret, item-photo canonical) → lowercase hex — identical to the
    Worker's hmacHex over itemPhotoCanonical. `photo_json` is used VERBATIM."""
    msg = item_photo_canonical(item_state_id=item_state_id, photo_json=photo_json).encode("utf-8")
    return _hmac.new(secret.encode("utf-8"), msg, hashlib.sha256).hexdigest()


def verify_item_photo(
    secret: str, provided_hmac: str | None, *, item_state_id: int, photo_json: str
) -> bool:
    """True iff `provided_hmac` matches the recomputed item-photo signature.
    Constant-time; never raises (False on any mismatch — the caller refuses the row)."""
    expected = sign_item_photo(secret, item_state_id=item_state_id, photo_json=photo_json)
    return _hmac.compare_digest(expected, provided_hmac or "")


# ---- Daily-photo protocol (DR-photo-pool Slice 2 — see module docstring) ----------


def daily_photo_canonical(*, job_id: str, work_date: str, photo_json: str) -> str:
    """The exact string the Worker signs for one daily-pool photo
    (fieldops_daily_photos.ts dailyPhotoCanonical — order + ``\\n`` separator
    load-bearing). job_id + work_date bind the photo to its day."""
    return "\n".join([DAILY_PHOTO_DOMAIN, job_id, work_date, photo_json])


def sign_daily_photo(secret: str, *, job_id: str, work_date: str, photo_json: str) -> str:
    """HMAC-SHA256(secret, daily-photo canonical) → lowercase hex — identical to the
    Worker's hmacHex over dailyPhotoCanonical. `photo_json` is used VERBATIM."""
    msg = daily_photo_canonical(
        job_id=job_id, work_date=work_date, photo_json=photo_json
    ).encode("utf-8")
    return _hmac.new(secret.encode("utf-8"), msg, hashlib.sha256).hexdigest()


def verify_daily_photo(
    secret: str, provided_hmac: str | None, *, job_id: str, work_date: str, photo_json: str
) -> bool:
    """True iff `provided_hmac` matches the recomputed daily-photo signature.
    Constant-time; never raises (False on any mismatch — the caller refuses the row)."""
    expected = sign_daily_photo(
        secret, job_id=job_id, work_date=work_date, photo_json=photo_json
    )
    return _hmac.compare_digest(expected, provided_hmac or "")
