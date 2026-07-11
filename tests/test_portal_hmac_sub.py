"""Lock the sub:v1 HMAC contract (shared/portal_hmac.py subcontract protocol) — the
cross-language vector.

The canonical string + HMAC must match the Worker (safety_portal/worker/subcontract.ts
subCanonicalString + canonicalSubJson + hmacHex) byte-for-byte. The Worker-side vitest
(safety_portal/test/subcontract.test.ts "sub:v1 HMAC") locks the TS half by recomputing
a live queued row's hmac from the same canonical builders; THIS file locks the Python
half against a HAND-TRACED literal: `CANONICAL_JSON` below was written by walking
canonicalSubJson's insertion order and JSON.stringify's serialization rules by hand
(compact separators, null for D1 NULLs, ints bare, the <=3dp qty double shortest-
roundtrip, no \\u escaping) — NOT by calling the builder under test. If either side's
canonical drifts, its own pin breaks in CI before a live mismatch can strand a
subcontract.

The fixture mirrors the Worker test's draftBody()+generate: the SOV sums to the
contract price (123450 + 2500 == 125950 == contract_price_cents), pinning the
SOV-sums-to-price generate gate.
"""
from __future__ import annotations

import hashlib
import hmac as _hmac
from typing import Any

from shared import portal_hmac

SECRET = "sub-test-secret"
SC_ID = 7
SC_NUMBER = "2026.001.2.0.0"

# The /pending row's canonical header fields (Worker key order is portal_hmac's
# SUB_CANONICAL_HEADER_KEYS; dict order here is deliberately SCRAMBLED for one test —
# the builder must impose the canonical order, not inherit ours).
SUB_ROW: dict[str, Any] = {
    "id": SC_ID,
    "status": "queued",           # non-canonical D1 columns ride along and are ignored
    "sc_uuid": "u-x",
    "draft_version": 3,
    "sc_number": SC_NUMBER,
    "job_no": "2026.001",
    "site_phase": 2,
    "supersede_seq": 0,
    "revision": 0,
    "sub_key": "SUB-000001",
    "trade": "Electrical",
    "job_id": "JOB-000017",
    "job_name": "Sunrise Solar",
    "project_name": "Sunrise Solar Farm",
    "owner_entity": "Sunrise Solar SPV LLC",
    "prime_contractor": "Evergreen Renewables LLC",
    "site_name": "Sunrise Array",
    "site_address": "100 Array Rd, Rockford, IL",
    "governing_law_state": "VA",
    "exhibit_a_template_id": "exhibit_a_standard",
    "exhibit_a_template_version": "1",
    "exhibit_a_work_text": "Furnish all labor and materials for the electrical scope.",
    "scope_summary": "Electrical installation",
    "price_basis": "fixed",
    "contract_price_cents": 125_950,
    "retainage_bp": 1_000,
    "subtotal_cents": 125_950,
    "start_date": "2026-02-01",
    "completion_date": "2026-06-30",
    "terms_profile_id": "standard_subcontract",
    "terms_version": "1",
    "template_family": "long_form",
    "supersedes_sc_id": None,
    "approver_name": "Alex Approver",
    "approver_title": "Director of Construction",
}

SOV_LINES: list[dict[str, Any]] = [
    {
        "position": 1, "item_number": "L-100", "description": "Electrical labor",
        "qty": 1, "unit": "ls", "unit_price_cents": 123_450, "extended_cents": 123_450,
    },
    {
        "position": 2, "item_number": "M-200", "description": "Conduit",
        "qty": 2.5, "unit": "box", "unit_price_cents": 1_000, "extended_cents": 2_500,
    },
]

# HAND-TRACED from canonicalSubJson (subcontract.ts) — see module docstring. One
# adjacent-literal string; every byte is load-bearing.
CANONICAL_JSON = (
    '{"sc_number":"2026.001.2.0.0","job_no":"2026.001","site_phase":2,'
    '"supersede_seq":0,"revision":0,"sub_key":"SUB-000001","trade":"Electrical",'
    '"job_id":"JOB-000017","job_name":"Sunrise Solar",'
    '"project_name":"Sunrise Solar Farm","owner_entity":"Sunrise Solar SPV LLC",'
    '"prime_contractor":"Evergreen Renewables LLC","site_name":"Sunrise Array",'
    '"site_address":"100 Array Rd, Rockford, IL","governing_law_state":"VA",'
    '"exhibit_a_template_id":"exhibit_a_standard","exhibit_a_template_version":"1",'
    '"exhibit_a_work_text":"Furnish all labor and materials for the electrical scope.",'
    '"scope_summary":"Electrical installation","price_basis":"fixed",'
    '"contract_price_cents":125950,"retainage_bp":1000,"subtotal_cents":125950,'
    '"start_date":"2026-02-01","completion_date":"2026-06-30",'
    '"terms_profile_id":"standard_subcontract","terms_version":"1",'
    '"template_family":"long_form","supersedes_sc_id":null,'
    '"approver_name":"Alex Approver","approver_title":"Director of Construction",'
    '"sov_lines":[{"position":1,"item_number":"L-100","description":"Electrical labor",'
    '"qty":1,"unit":"ls","unit_price_cents":123450,"extended_cents":123450},'
    '{"position":2,"item_number":"M-200","description":"Conduit","qty":2.5,'
    '"unit":"box","unit_price_cents":1000,"extended_cents":2500}]}'
)

CANONICAL_STRING = f"sub:v1\n{SC_ID}\n{SC_NUMBER}\n{CANONICAL_JSON}"

# Precomputed pin: HMAC-SHA256(SECRET, CANONICAL_STRING) — a hardcoded digest so a
# drift in EITHER the builder OR the independent recompute below is caught.
PINNED_HMAC = "007bdaf2a023e8c6269d58c4de5ea4b9a3261441332d21a360d44a3e4da0413f"


def test_sub_canonical_json_matches_hand_traced_literal() -> None:
    """The builder reproduces the hand-traced JSON.stringify output byte-for-byte —
    fixed key order (independent of input dict order), compact separators, null for
    None, bare ints, shortest-roundtrip qty double, non-canonical keys ignored."""
    assert portal_hmac.sub_canonical_json(SUB_ROW, SOV_LINES) == CANONICAL_JSON


def test_sub_canonical_string_shape() -> None:
    built = portal_hmac.sub_canonical_string(SC_ID, SC_NUMBER, CANONICAL_JSON)
    assert built == CANONICAL_STRING
    assert built.startswith("sub:v1\n")


def test_sign_sub_matches_pinned_and_independent_hmac() -> None:
    independent = _hmac.new(
        SECRET.encode(), CANONICAL_STRING.encode(), hashlib.sha256
    ).hexdigest()
    assert independent == PINNED_HMAC
    assert portal_hmac.sign_sub(
        SECRET, sc_id=SC_ID, sc_number=SC_NUMBER, canonical_json=CANONICAL_JSON
    ) == PINNED_HMAC


def test_verify_sub_round_trip_and_tamper() -> None:
    assert portal_hmac.verify_sub(
        SECRET, PINNED_HMAC, sc_id=SC_ID, sc_number=SC_NUMBER, canonical_json=CANONICAL_JSON
    ) is True
    # Tampered contract price (the money-integrity point of the signature).
    tampered = dict(SUB_ROW, contract_price_cents=125_951)
    assert portal_hmac.verify_sub(
        SECRET, PINNED_HMAC, sc_id=SC_ID, sc_number=SC_NUMBER,
        canonical_json=portal_hmac.sub_canonical_json(tampered, SOV_LINES),
    ) is False
    # Replay under a different subcontract identity fails (id + number are bound).
    assert portal_hmac.verify_sub(
        SECRET, PINNED_HMAC, sc_id=8, sc_number=SC_NUMBER, canonical_json=CANONICAL_JSON
    ) is False
    assert portal_hmac.verify_sub(
        SECRET, PINNED_HMAC, sc_id=SC_ID, sc_number="2026.001.2.0.1",
        canonical_json=CANONICAL_JSON,
    ) is False
    # Wrong secret / absent signature.
    assert portal_hmac.verify_sub(
        "other", PINNED_HMAC, sc_id=SC_ID, sc_number=SC_NUMBER, canonical_json=CANONICAL_JSON
    ) is False
    assert portal_hmac.verify_sub(
        SECRET, None, sc_id=SC_ID, sc_number=SC_NUMBER, canonical_json=CANONICAL_JSON
    ) is False
    assert portal_hmac.verify_sub(
        SECRET, "", sc_id=SC_ID, sc_number=SC_NUMBER, canonical_json=CANONICAL_JSON
    ) is False


def test_sub_domain_separation_from_sibling_protocols() -> None:
    """An UNdomained signature over the same content — and every sibling protocol's
    domain — must not verify as sub:v1 (cross-protocol confusion is structurally
    impossible)."""
    undomained = "\n".join([str(SC_ID), SC_NUMBER, CANONICAL_JSON])
    forged = _hmac.new(SECRET.encode(), undomained.encode(), hashlib.sha256).hexdigest()
    assert portal_hmac.verify_sub(
        SECRET, forged, sc_id=SC_ID, sc_number=SC_NUMBER, canonical_json=CANONICAL_JSON
    ) is False
    for other_domain in (
        portal_hmac.ITEM_PHOTO_DOMAIN,
        portal_hmac.DAILY_PHOTO_DOMAIN,
        portal_hmac.PO_DOMAIN,
    ):
        cross = "\n".join([other_domain, str(SC_ID), SC_NUMBER, CANONICAL_JSON])
        forged = _hmac.new(SECRET.encode(), cross.encode(), hashlib.sha256).hexdigest()
        assert portal_hmac.verify_sub(
            SECRET, forged, sc_id=SC_ID, sc_number=SC_NUMBER, canonical_json=CANONICAL_JSON
        ) is False


def test_absent_canonical_key_serializes_as_null_and_fails_verify() -> None:
    """A row missing a canonical field serializes it as null (what a D1 NULL would
    be) — if the Worker signed a VALUE there, verification fails (fail-closed: the
    drift IS the signal)."""
    shrunk = {k: v for k, v in SUB_ROW.items() if k != "approver_name"}
    assert '"approver_name":null' in portal_hmac.sub_canonical_json(shrunk, SOV_LINES)
    assert portal_hmac.verify_sub(
        SECRET, PINNED_HMAC, sc_id=SC_ID, sc_number=SC_NUMBER,
        canonical_json=portal_hmac.sub_canonical_json(shrunk, SOV_LINES),
    ) is False


def test_nan_in_money_field_raises() -> None:
    """NaN is not JSON — allow_nan=False raises rather than minting a canonical the
    Worker could never have signed (the caller's per-row fence catches it)."""
    import pytest

    bad = dict(SUB_ROW, subtotal_cents=float("nan"))
    with pytest.raises(ValueError):
        portal_hmac.sub_canonical_json(bad, SOV_LINES)


# ── Cross-language vector (matched pair with safety_portal/test/subcontract_hmac_xlang.test.ts) ──
# The SAME fixture + expected bytes asserted on the TS side. Editing either file without the other
# RED-lights here — this is the byte-for-byte Worker↔daemon contract, the one place a drift makes every
# signature fail closed. Fixture spans all 31 header keys incl. a null, a float qty, and non-ASCII text.
_XL_SUB = {
    "sc_number": "2026.001.A.0.1", "job_no": "2026.001", "site_phase": 0, "supersede_seq": 0, "revision": 1,
    "sub_key": "SUB-000042", "trade": "AC Electrical", "job_id": "JOB-1", "job_name": "Kendall Solar",
    "project_name": "Kendall Solar Project", "owner_entity": "Kendall Solar, LLC",
    "prime_contractor": "Evergreen Renewables of Virginia LLC", "site_name": "Kendall Site",
    "site_address": "123 Solar Rd, Süd", "governing_law_state": "OR", "exhibit_a_template_id": "electrical",
    "exhibit_a_template_version": "v1", "exhibit_a_work_text": "The Work: AC électrical", "scope_summary": "AC scope",
    "price_basis": "fixed", "contract_price_cents": 27401850, "retainage_bp": 1000, "subtotal_cents": 27401850,
    "start_date": "2026-08-01", "completion_date": "2026-12-31", "terms_profile_id": "standard_subcontract",
    "terms_version": "v1", "template_family": "long_form", "supersedes_sc_id": None, "approver_name": "Jane Doe",
    "approver_title": "PM",
}
_XL_SOV = [
    {"position": 1, "item_number": "1", "description": "AC électrical", "qty": 1, "unit": "LS", "unit_price_cents": 27401850, "extended_cents": 27401850},
    {"position": 2, "item_number": "2", "description": "extra", "qty": 2.5, "unit": "EA", "unit_price_cents": 400, "extended_cents": 1000},
]
_XL_CANONICAL = (
    '{"sc_number":"2026.001.A.0.1","job_no":"2026.001","site_phase":0,"supersede_seq":0,"revision":1,'
    '"sub_key":"SUB-000042","trade":"AC Electrical","job_id":"JOB-1","job_name":"Kendall Solar",'
    '"project_name":"Kendall Solar Project","owner_entity":"Kendall Solar, LLC",'
    '"prime_contractor":"Evergreen Renewables of Virginia LLC","site_name":"Kendall Site",'
    '"site_address":"123 Solar Rd, Süd","governing_law_state":"OR","exhibit_a_template_id":"electrical",'
    '"exhibit_a_template_version":"v1","exhibit_a_work_text":"The Work: AC électrical","scope_summary":"AC scope",'
    '"price_basis":"fixed","contract_price_cents":27401850,"retainage_bp":1000,"subtotal_cents":27401850,'
    '"start_date":"2026-08-01","completion_date":"2026-12-31","terms_profile_id":"standard_subcontract",'
    '"terms_version":"v1","template_family":"long_form","supersedes_sc_id":null,"approver_name":"Jane Doe",'
    '"approver_title":"PM","sov_lines":[{"position":1,"item_number":"1","description":"AC électrical","qty":1,'
    '"unit":"LS","unit_price_cents":27401850,"extended_cents":27401850},{"position":2,"item_number":"2",'
    '"description":"extra","qty":2.5,"unit":"EA","unit_price_cents":400,"extended_cents":1000}]}'
)
_XL_HMAC = "3587af83478a542ef770da4c1661bf10dd2a96c3112550b217fa21e4e20bd87d"


def test_xlang_vector_matches_ts_side() -> None:
    assert portal_hmac.sub_canonical_json(_XL_SUB, _XL_SOV) == _XL_CANONICAL
    assert portal_hmac.sign_sub("test-secret-xyz", sc_id=42, sc_number="2026.001.A.0.1",
                                canonical_json=_XL_CANONICAL) == _XL_HMAC
