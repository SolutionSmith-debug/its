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

Purchase-order protocol (PO S4)
-------------------------------
    A generated PO (`purchase_orders`, migration 0043) is signed by the Worker at
    /api/po/drafts/:id/generate with the SAME key + MAC over its own
    domain-separated canonical string (safety_portal/worker/po.ts
    `poCanonicalString` + `canonicalPoJson`):

        canonical = "po:v1" \\n <po_id (decimal)> \\n <po_number> \\n <canonical_json>

    * The `"po:v1"` literal domain-separates this protocol from submission HMACs
      (uuid-first), item-photo HMACs ("item_photo:v1"), and daily-photo HMACs
      ("daily_photo:v1") — cross-protocol signature confusion is structurally
      impossible — and versions the string.
    * `po_id` + `po_number` bind the signature to one allocated PO identity (a
      signed body cannot be replayed under a different PO number or D1 row).
    * `canonical_json` is UNLIKE payload_json: the Worker does NOT store it — it
      is REBUILT on both sides from the row's fields in a FIXED key order
      (`canonicalPoJson`'s insertion order, mirrored by `PO_CANONICAL_HEADER_KEYS`
      + `PO_CANONICAL_LINE_KEYS` below) and serialized with JSON.stringify
      semantics (`json.dumps(..., ensure_ascii=False, separators=(",", ":"))`).
      Byte-matching therefore depends on (a) the key order, (b) compact
      separators, (c) raw (non-\\u-escaped) non-ASCII, and (d) values passing
      through VERBATIM from the Worker's JSON response — the wire values were
      serialized by JS once already, so a parse→re-dump round-trip in Python
      reproduces the exact number/string forms (ints stay ints; the ≤3dp qty
      double's shortest-roundtrip repr agrees across JS/Python). The
      cross-language vector is pinned in tests/test_portal_hmac_po.py.

    `verify_po` is the Mac-side recompute `po_materials.po_poll`'s drafts pass
    runs before any render/Box-file/mark-filed (the downgrade defense, mirroring
    the submission drain).
"""
from __future__ import annotations

import hashlib
import hmac as _hmac
import json
from collections.abc import Mapping, Sequence
from typing import Any

# The item-photo protocol's domain-separation literal — MUST match the Worker's
# itemPhotoCanonical (safety_portal/worker/fieldops_checklist.ts) byte-for-byte.
ITEM_PHOTO_DOMAIN = "item_photo:v1"

# The daily-photo protocol's domain-separation literal — MUST match the Worker's
# dailyPhotoCanonical (safety_portal/worker/fieldops_daily_photos.ts) byte-for-byte.
DAILY_PHOTO_DOMAIN = "daily_photo:v1"

# The purchase-order protocol's domain-separation literal — MUST match the Worker's
# PO_HMAC_DOMAIN (safety_portal/worker/po.ts) byte-for-byte.
PO_DOMAIN = "po:v1"

# The FIXED header-field order of the Worker's canonicalPoJson (po.ts) — insertion
# order is the serialization order on both sides; any reorder breaks every signature.
PO_CANONICAL_HEADER_KEYS: tuple[str, ...] = (
    "po_number",
    "job_no",
    "site_phase",
    "supersede_seq",
    "revision",
    "vendor_key",
    "job_id",
    "job_name",
    "ship_to_name",
    "ship_to_address",
    "ship_to_city",
    "ship_to_state",
    "ship_to_zip",
    "delivery_contact_name",
    "delivery_contact_phone",
    "delivery_contact_email",
    "sow_text",
    "delivery_instructions",
    "payment_terms_text",
    "terms_profile_id",
    "terms_version",
    "subtotal_cents",
    "tax_mode",
    "tax_rate_bp",
    "tax_cents",
    "shipping_cents",
    "total_cents",
    "line_column_variant",
    "supersedes_po_id",
    "approver_name",
    "approver_title",
)

# The FIXED per-line key order of canonicalPoJson's line_items mapper (po.ts).
PO_CANONICAL_LINE_KEYS: tuple[str, ...] = (
    "position",
    "part_number",
    "description",
    "qty",
    "unit",
    "unit_cost_cents",
    "extended_cents",
    "watts",
    "panels",
    "pallets",
    "price_per_watt_microcents",
)


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


# ---- Purchase-order protocol (PO S4 — see module docstring) ------------------------


def po_canonical_json(po: Mapping[str, Any], line_items: Sequence[Mapping[str, Any]]) -> str:
    """Rebuild the Worker's canonicalPoJson byte-for-byte from a /pending row.

    Values pass through VERBATIM (no coercion): the row arrived as JSON the Worker
    serialized, so `json.dumps` over the parsed values reproduces the exact bytes —
    `ensure_ascii=False` (JSON.stringify never \\u-escapes non-ASCII), compact
    separators, and the FIXED key orders above. A key absent from the row serializes
    as null exactly like a D1 NULL would; if that differs from what the Worker signed,
    the verify simply fails (fail-closed — the drift IS the signal, never a file).

    `allow_nan=False`: NaN/Infinity are not JSON — a row carrying one is malformed
    transport and the resulting ValueError surfaces to the caller's per-row fence
    rather than minting a canonical string the Worker could never have signed.
    """
    obj: dict[str, Any] = {key: po.get(key) for key in PO_CANONICAL_HEADER_KEYS}
    obj["line_items"] = [
        {key: line.get(key) for key in PO_CANONICAL_LINE_KEYS} for line in line_items
    ]
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def po_canonical_string(po_id: int, po_number: str, canonical_json: str) -> str:
    """The exact string the Worker signs for one generated PO
    (po.ts poCanonicalString — order + ``\\n`` separator load-bearing).
    po_id + po_number bind the signature to one allocated PO identity."""
    return "\n".join([PO_DOMAIN, str(po_id), po_number, canonical_json])


def sign_po(secret: str, *, po_id: int, po_number: str, canonical_json: str) -> str:
    """HMAC-SHA256(secret, PO canonical) → lowercase hex — identical to the Worker's
    hmacHex over poCanonicalString. `canonical_json` comes from `po_canonical_json`."""
    msg = po_canonical_string(po_id, po_number, canonical_json).encode("utf-8")
    return _hmac.new(secret.encode("utf-8"), msg, hashlib.sha256).hexdigest()


def verify_po(
    secret: str, provided_hmac: str | None, *, po_id: int, po_number: str, canonical_json: str
) -> bool:
    """True iff `provided_hmac` matches the recomputed PO signature.
    Constant-time; never raises (False on any mismatch — the caller refuses the row:
    one-shot flag + CRITICAL, never rendered, never filed, never marked)."""
    expected = sign_po(secret, po_id=po_id, po_number=po_number, canonical_json=canonical_json)
    return _hmac.compare_digest(expected, provided_hmac or "")
