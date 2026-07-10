"""Lock the po:v1 HMAC contract (shared/portal_hmac.py PO protocol) — the
cross-language vector.

The canonical string + HMAC must match the Worker (safety_portal/worker/po.ts
poCanonicalString + canonicalPoJson + hmacHex) byte-for-byte. The Worker-side vitest
(safety_portal/test/po.test.ts "po:v1 HMAC") locks the TS half by recomputing a live
queued row's hmac from the same canonical builders; THIS file locks the Python half
against a HAND-TRACED literal: `CANONICAL_JSON` below was written by walking
canonicalPoJson's insertion order and JSON.stringify's serialization rules by hand
(compact separators, null for D1 NULLs, ints bare, the ≤3dp qty double shortest-
roundtrip, no \\u escaping) — NOT by calling the builder under test. If either side's
canonical drifts, its own pin breaks in CI before a live mismatch can strand a PO.

The fixture mirrors the Worker test's draftBody()+generate: the same money values
(EXPECTED subtotal 125950 / tax 11336 / total 147286 — 900bp of 125950 is 11335.5,
pinning the .5-rounds-UP boundary) so the two suites pin the same numbers.
"""
from __future__ import annotations

import hashlib
import hmac as _hmac
from typing import Any

from shared import portal_hmac

SECRET = "po-test-secret"
PO_ID = 7
PO_NUMBER = "2026.001.2.0.0"

# The /pending row's canonical header fields (Worker key order is portal_hmac's
# PO_CANONICAL_HEADER_KEYS; dict order here is deliberately SCRAMBLED for one test —
# the builder must impose the canonical order, not inherit ours).
PO_ROW: dict[str, Any] = {
    "id": PO_ID,
    "status": "queued",           # non-canonical D1 columns ride along and are ignored
    "po_uuid": "u-x",
    "draft_version": 3,
    "po_number": PO_NUMBER,
    "job_no": "2026.001",
    "site_phase": 2,
    "supersede_seq": 0,
    "revision": 0,
    "vendor_key": "VEN-000001",
    "job_id": "JOB-000017",
    "job_name": "Sunrise Solar",
    "ship_to_name": "Evergreen Renewables LLC",
    "ship_to_address": "100 Array Rd",
    "ship_to_city": "Rockford",
    "ship_to_state": "IL",
    "ship_to_zip": "61101",
    "delivery_contact_name": "Dana Field",
    "delivery_contact_phone": "555-0100",
    "delivery_contact_email": "dana@example.com",
    "sow_text": "Supply and deliver racking components.",
    "delivery_instructions": "Call site lead ahead of delivery.",
    "payment_terms_text": "Net 30",
    "terms_profile_id": "standard_17",
    "terms_version": "1",
    "subtotal_cents": 125_950,
    "tax_mode": "auto",
    "tax_rate_bp": 900,
    "tax_cents": 11_336,
    "shipping_cents": 10_000,
    "total_cents": 147_286,
    "line_column_variant": "default",
    "supersedes_po_id": None,
    "approver_name": "Alex Approver",
    "approver_title": "Director of Procurement",
}

LINE_ITEMS: list[dict[str, Any]] = [
    {
        "position": 1, "part_number": "RK-100", "description": "Rail 100",
        "qty": 10, "unit": "ea", "unit_cost_cents": 12_345, "extended_cents": 123_450,
        "watts": None, "panels": None, "pallets": None, "price_per_watt_microcents": None,
    },
    {
        "position": 2, "part_number": "RK-200", "description": "Clamp kit",
        "qty": 2.5, "unit": "box", "unit_cost_cents": 1_000, "extended_cents": 2_500,
        "watts": None, "panels": None, "pallets": None, "price_per_watt_microcents": None,
    },
]

# HAND-TRACED from canonicalPoJson (po.ts) — see module docstring. One adjacent-
# literal string; every byte is load-bearing.
CANONICAL_JSON = (
    '{"po_number":"2026.001.2.0.0","job_no":"2026.001","site_phase":2,'
    '"supersede_seq":0,"revision":0,"vendor_key":"VEN-000001","job_id":"JOB-000017",'
    '"job_name":"Sunrise Solar","ship_to_name":"Evergreen Renewables LLC",'
    '"ship_to_address":"100 Array Rd","ship_to_city":"Rockford","ship_to_state":"IL",'
    '"ship_to_zip":"61101","delivery_contact_name":"Dana Field",'
    '"delivery_contact_phone":"555-0100","delivery_contact_email":"dana@example.com",'
    '"sow_text":"Supply and deliver racking components.",'
    '"delivery_instructions":"Call site lead ahead of delivery.",'
    '"payment_terms_text":"Net 30","terms_profile_id":"standard_17",'
    '"terms_version":"1","subtotal_cents":125950,"tax_mode":"auto","tax_rate_bp":900,'
    '"tax_cents":11336,"shipping_cents":10000,"total_cents":147286,'
    '"line_column_variant":"default","supersedes_po_id":null,'
    '"approver_name":"Alex Approver","approver_title":"Director of Procurement",'
    '"line_items":[{"position":1,"part_number":"RK-100","description":"Rail 100",'
    '"qty":10,"unit":"ea","unit_cost_cents":12345,"extended_cents":123450,'
    '"watts":null,"panels":null,"pallets":null,"price_per_watt_microcents":null},'
    '{"position":2,"part_number":"RK-200","description":"Clamp kit","qty":2.5,'
    '"unit":"box","unit_cost_cents":1000,"extended_cents":2500,"watts":null,'
    '"panels":null,"pallets":null,"price_per_watt_microcents":null}]}'
)

CANONICAL_STRING = f"po:v1\n{PO_ID}\n{PO_NUMBER}\n{CANONICAL_JSON}"

# Precomputed pin: HMAC-SHA256(SECRET, CANONICAL_STRING) — a hardcoded digest so a
# drift in EITHER the builder OR the independent recompute below is caught.
PINNED_HMAC = "a53b46f1a39e5216e241cde033dbf0e2fb9adb5eab5b57122dd5c82d755baab6"


def test_po_canonical_json_matches_hand_traced_literal() -> None:
    """The builder reproduces the hand-traced JSON.stringify output byte-for-byte —
    fixed key order (independent of input dict order), compact separators, null for
    None, bare ints, shortest-roundtrip qty double, non-canonical keys ignored."""
    assert portal_hmac.po_canonical_json(PO_ROW, LINE_ITEMS) == CANONICAL_JSON


def test_po_canonical_string_shape() -> None:
    built = portal_hmac.po_canonical_string(PO_ID, PO_NUMBER, CANONICAL_JSON)
    assert built == CANONICAL_STRING
    assert built.startswith("po:v1\n")


def test_sign_po_matches_pinned_and_independent_hmac() -> None:
    independent = _hmac.new(
        SECRET.encode(), CANONICAL_STRING.encode(), hashlib.sha256
    ).hexdigest()
    assert independent == PINNED_HMAC
    assert portal_hmac.sign_po(
        SECRET, po_id=PO_ID, po_number=PO_NUMBER, canonical_json=CANONICAL_JSON
    ) == PINNED_HMAC


def test_verify_po_round_trip_and_tamper() -> None:
    assert portal_hmac.verify_po(
        SECRET, PINNED_HMAC, po_id=PO_ID, po_number=PO_NUMBER, canonical_json=CANONICAL_JSON
    ) is True
    # Tampered total (the money-integrity point of the signature).
    tampered = dict(PO_ROW, total_cents=147_287)
    assert portal_hmac.verify_po(
        SECRET, PINNED_HMAC, po_id=PO_ID, po_number=PO_NUMBER,
        canonical_json=portal_hmac.po_canonical_json(tampered, LINE_ITEMS),
    ) is False
    # Replay under a different PO identity fails (id + number are bound).
    assert portal_hmac.verify_po(
        SECRET, PINNED_HMAC, po_id=8, po_number=PO_NUMBER, canonical_json=CANONICAL_JSON
    ) is False
    assert portal_hmac.verify_po(
        SECRET, PINNED_HMAC, po_id=PO_ID, po_number="2026.001.2.0.1",
        canonical_json=CANONICAL_JSON,
    ) is False
    # Wrong secret / absent signature.
    assert portal_hmac.verify_po(
        "other", PINNED_HMAC, po_id=PO_ID, po_number=PO_NUMBER, canonical_json=CANONICAL_JSON
    ) is False
    assert portal_hmac.verify_po(
        SECRET, None, po_id=PO_ID, po_number=PO_NUMBER, canonical_json=CANONICAL_JSON
    ) is False
    assert portal_hmac.verify_po(
        SECRET, "", po_id=PO_ID, po_number=PO_NUMBER, canonical_json=CANONICAL_JSON
    ) is False


def test_po_domain_separation_from_sibling_protocols() -> None:
    """An UNdomained signature over the same content — and every sibling protocol's
    domain — must not verify as po:v1 (cross-protocol confusion is structurally
    impossible)."""
    undomained = "\n".join([str(PO_ID), PO_NUMBER, CANONICAL_JSON])
    forged = _hmac.new(SECRET.encode(), undomained.encode(), hashlib.sha256).hexdigest()
    assert portal_hmac.verify_po(
        SECRET, forged, po_id=PO_ID, po_number=PO_NUMBER, canonical_json=CANONICAL_JSON
    ) is False
    for other_domain in (portal_hmac.ITEM_PHOTO_DOMAIN, portal_hmac.DAILY_PHOTO_DOMAIN):
        cross = "\n".join([other_domain, str(PO_ID), PO_NUMBER, CANONICAL_JSON])
        forged = _hmac.new(SECRET.encode(), cross.encode(), hashlib.sha256).hexdigest()
        assert portal_hmac.verify_po(
            SECRET, forged, po_id=PO_ID, po_number=PO_NUMBER, canonical_json=CANONICAL_JSON
        ) is False


def test_absent_canonical_key_serializes_as_null_and_fails_verify() -> None:
    """A row missing a canonical field serializes it as null (what a D1 NULL would
    be) — if the Worker signed a VALUE there, verification fails (fail-closed: the
    drift IS the signal)."""
    shrunk = {k: v for k, v in PO_ROW.items() if k != "approver_name"}
    assert '"approver_name":null' in portal_hmac.po_canonical_json(shrunk, LINE_ITEMS)
    assert portal_hmac.verify_po(
        SECRET, PINNED_HMAC, po_id=PO_ID, po_number=PO_NUMBER,
        canonical_json=portal_hmac.po_canonical_json(shrunk, LINE_ITEMS),
    ) is False


def test_nan_in_money_field_raises() -> None:
    """NaN is not JSON — allow_nan=False raises rather than minting a canonical the
    Worker could never have signed (the caller's per-row fence catches it)."""
    import pytest

    bad = dict(PO_ROW, subtotal_cents=float("nan"))
    with pytest.raises(ValueError):
        portal_hmac.po_canonical_json(bad, LINE_ITEMS)
