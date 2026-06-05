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
"""
from __future__ import annotations

import hashlib
import hmac as _hmac


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
