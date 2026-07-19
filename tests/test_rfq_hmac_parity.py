"""Golden-vector parity tests for the rfq:v1 RFQ HMAC protocol (ADR-0004 R2 / PR-C).

The pinned contract (mirrors safety_portal/worker/rfq.ts canonicalRfqJson +
rfqCanonicalString EXACTLY): the Worker signs each composed RFQ over the
domain-separated canonical string

    "rfq:v1" \\n str(rfq_id) \\n rfq_number \\n canonical_json

where canonical_json is REBUILT on both sides from the row's fields in the FIXED
key order RFQ_CANONICAL_HEADER_KEYS (rfq_number, job_no, job_name, the ship_to_*
quintet, the delivery_contact_* trio, scope_text, due_date) + a `line_items` nest
of RFQ_CANONICAL_LINE_KEYS (position, part_number, description, qty, unit,
line_note) + a SORTED `vendor_keys` array LAST, serialized with JSON.stringify
semantics (compact separators, raw non-ASCII) — the po:v1 recompute-from-fields
pattern, deliberately NOT a stored verbatim string (the rebuild signature-covers
the exact served fields the Mac renders from; see the shared/portal_hmac.py module
docstring). PRICE-FREE: no money key exists anywhere in the canonical.

Every expected signature here is computed IN THE TEST from the pinned canonical
string with stdlib hmac/hashlib/json — independent of the implementation under
test, so a drifted canonical (reordered fields, wrong separator, wrong domain,
re-serialization drift, missing utf-8 encode) fails against the golden math, not
against itself.

Run with: pytest -q tests/test_rfq_hmac_parity.py
"""
from __future__ import annotations

import hashlib
import hmac as _hmac
import json
from typing import Any

import pytest

from shared import portal_hmac

SECRET = "rfq-parity-test-secret"

RFQ_DOMAIN = "rfq:v1"

# The pinned key orders — duplicated HERE from the contract (not imported) so a
# reorder in the implementation fails against the golden math.
HEADER_KEYS = (
    "rfq_number", "job_no", "job_name",
    "ship_to_name", "ship_to_address", "ship_to_city", "ship_to_state", "ship_to_zip",
    "delivery_contact_name", "delivery_contact_phone", "delivery_contact_email",
    "scope_text", "due_date",
)
LINE_KEYS = ("position", "part_number", "description", "qty", "unit", "line_note")

# One fixed golden vector (realistic shape: two vendors, a non-ASCII scope char,
# a ≤3dp decimal qty — the JS/Python shortest-roundtrip agreement class).
RFQ: dict[str, Any] = {
    "id": 7,
    "rfq_number": "RFQ-2026.001-003",
    "job_no": "2026.001",
    "job_name": "Sunrise Solar",
    "ship_to_name": "Sunrise Solar Laydown",
    "ship_to_address": "100 Array Rd",
    "ship_to_city": "Rockford",
    "ship_to_state": "IL",
    "ship_to_zip": "61101",
    "delivery_contact_name": "Dana Field",
    "delivery_contact_phone": "815-555-0101",
    "delivery_contact_email": "dana@evergreen.example",
    "scope_text": "Racking + módules — supply only.",
    "due_date": "2026-08-14",
}
VENDOR_KEYS = ["VEN-000007", "VEN-000001"]  # deliberately UNSORTED (the sort is signed)
LINES: list[dict[str, Any]] = [
    {"position": 1, "part_number": "RK-100", "description": "Rail 100",
     "qty": 10, "unit": "EA", "line_note": "black anodized"},
    {"position": 2, "part_number": "", "description": "Clamp kit",
     "qty": 2.5, "unit": "BOX", "line_note": ""},
]


def _golden_canonical_json(
    rfq: dict[str, Any], lines: list[dict[str, Any]], vendor_keys: list[str]
) -> str:
    obj: dict[str, Any] = {k: rfq.get(k) for k in HEADER_KEYS}
    obj["line_items"] = [{k: ln.get(k) for k in LINE_KEYS} for ln in lines]
    obj["vendor_keys"] = sorted(vendor_keys)
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def _golden_canonical_string(rfq_id: int, rfq_number: str, canonical_json: str) -> str:
    return "\n".join([RFQ_DOMAIN, str(rfq_id), rfq_number, canonical_json])


def _hmac_hex(secret: str, message: str) -> str:
    return _hmac.new(secret.encode("utf-8"), message.encode("utf-8"), hashlib.sha256).hexdigest()


def _golden_sig() -> str:
    cj = _golden_canonical_json(RFQ, LINES, VENDOR_KEYS)
    return _hmac_hex(SECRET, _golden_canonical_string(7, RFQ["rfq_number"], cj))


# ---- canonical-json parity ---------------------------------------------------------


def test_rfq_canonical_json_matches_golden_math():
    assert portal_hmac.rfq_canonical_json(RFQ, LINES, VENDOR_KEYS) == \
        _golden_canonical_json(RFQ, LINES, VENDOR_KEYS)


def test_rfq_canonical_json_sorts_vendor_keys():
    """rfq.ts sorts vendor_keys at signing (read-order independence) — the Python
    rebuild must sort identically, and vendor_keys nests LAST."""
    got = portal_hmac.rfq_canonical_json(RFQ, LINES, ["VEN-000007", "VEN-000001"])
    assert got == portal_hmac.rfq_canonical_json(RFQ, LINES, ["VEN-000001", "VEN-000007"])
    assert got.endswith('"vendor_keys":["VEN-000001","VEN-000007"]}')


def test_rfq_canonical_json_is_compact_and_raw_unicode():
    """JSON.stringify semantics: compact separators, non-ASCII NOT \\u-escaped."""
    got = portal_hmac.rfq_canonical_json(RFQ, LINES, VENDOR_KEYS)
    assert ": " not in got and ", " not in got  # compact separators (no fixture text has either)
    assert "módules" in got  # raw non-ASCII, never \\u00f3
    assert "\\u" not in got


def test_rfq_canonical_json_is_price_free():
    """PROVE-THE-CONTRACT: no money key can appear in the canonical — the header
    and line key tuples contain no *_cents / price / cost key, so even a served
    row smuggling one serializes WITHOUT it (it simply isn't picked)."""
    smuggled_rfq = {**RFQ, "total_cents": 999_999}
    smuggled_lines = [{**LINES[0], "unit_cost_cents": 12345}]
    got = portal_hmac.rfq_canonical_json(smuggled_rfq, smuggled_lines, VENDOR_KEYS)
    assert "cents" not in got and "price" not in got and "cost" not in got
    for key in (*portal_hmac.RFQ_CANONICAL_HEADER_KEYS, *portal_hmac.RFQ_CANONICAL_LINE_KEYS):
        assert "cents" not in key and "price" not in key and "cost" not in key


def test_rfq_canonical_json_missing_key_serializes_null():
    """A key absent from the row serializes null (the D1-NULL equivalence)."""
    partial = {k: v for k, v in RFQ.items() if k != "due_date"}
    assert '"due_date":null' in portal_hmac.rfq_canonical_json(partial, LINES, VENDOR_KEYS)


def test_rfq_canonical_json_rejects_nan():
    with pytest.raises(ValueError):
        portal_hmac.rfq_canonical_json(RFQ, [{**LINES[0], "qty": float("nan")}], VENDOR_KEYS)


# ---- canonical-string parity -------------------------------------------------------


def test_rfq_canonical_builds_the_exact_pinned_string():
    cj = portal_hmac.rfq_canonical_json(RFQ, LINES, VENDOR_KEYS)
    assert portal_hmac.rfq_canonical(7, RFQ["rfq_number"], cj) == \
        _golden_canonical_string(7, RFQ["rfq_number"], cj)


def test_rfq_canonical_pinned_literal_prefix():
    """Fully-literal domain/id/number prefix — immune to a shared helper bug."""
    got = portal_hmac.rfq_canonical(7, "RFQ-2026.001-003", "{}")
    assert got == "rfq:v1\n7\nRFQ-2026.001-003\n{}"


# ---- accept path -------------------------------------------------------------------


def test_verify_accepts_the_golden_vector():
    cj = portal_hmac.rfq_canonical_json(RFQ, LINES, VENDOR_KEYS)
    assert portal_hmac.verify_rfq(
        SECRET, _golden_sig(), rfq_id=7, rfq_number=RFQ["rfq_number"], canonical_json=cj,
    ) is True


# ---- reject paths (each mutation MUST break verification) --------------------------


@pytest.mark.parametrize("mutate", [
    lambda r, ln, vk: ({**r, "rfq_number": "RFQ-2026.001-004"}, ln, vk),  # replayed number
    lambda r, ln, vk: (r, ln, ["VEN-000001"]),                        # dropped vendor
    lambda r, ln, vk: (r, ln, ["VEN-000001", "VEN-666666"]),          # swapped vendor
    lambda r, ln, vk: (r, ln, [*vk, "VEN-666666"]),                   # appended vendor
    lambda r, ln, vk: ({**r, "due_date": "2026-09-14"}, ln, vk),      # shifted due date
    lambda r, ln, vk: ({**r, "scope_text": "different scope"}, ln, vk),  # edited scope
    lambda r, ln, vk: ({**r, "ship_to_address": "666 Elsewhere"}, ln, vk),  # edited ship-to
    lambda r, ln, vk: (r, [{**ln[0], "qty": 100}, ln[1]], vk),        # edited line qty
    lambda r, ln, vk: (r, ln[:1], vk),                                # dropped line
])
def test_verify_rejects_any_field_mutation(mutate):
    mutated_rfq, mutated_lines, mutated_vk = mutate(RFQ, LINES, VENDOR_KEYS)
    cj = portal_hmac.rfq_canonical_json(mutated_rfq, mutated_lines, mutated_vk)
    assert portal_hmac.verify_rfq(
        SECRET, _golden_sig(), rfq_id=7,
        rfq_number=str(mutated_rfq.get("rfq_number")), canonical_json=cj,
    ) is False


def test_verify_rejects_replayed_rfq_id():
    cj = portal_hmac.rfq_canonical_json(RFQ, LINES, VENDOR_KEYS)
    assert portal_hmac.verify_rfq(
        SECRET, _golden_sig(), rfq_id=8, rfq_number=RFQ["rfq_number"], canonical_json=cj,
    ) is False


def test_verify_rejects_wrong_secret_and_absent_sig():
    cj = portal_hmac.rfq_canonical_json(RFQ, LINES, VENDOR_KEYS)
    assert portal_hmac.verify_rfq(
        "other-secret", _golden_sig(), rfq_id=7,
        rfq_number=RFQ["rfq_number"], canonical_json=cj,
    ) is False
    for absent in (None, ""):
        assert portal_hmac.verify_rfq(
            SECRET, absent, rfq_id=7, rfq_number=RFQ["rfq_number"], canonical_json=cj,
        ) is False


# ---- domain separation -------------------------------------------------------------


def test_rfq_domain_is_the_pinned_literal_and_unique():
    assert portal_hmac.RFQ_DOMAIN == "rfq:v1"
    siblings = {
        portal_hmac.ITEM_PHOTO_DOMAIN, portal_hmac.DAILY_PHOTO_DOMAIN,
        portal_hmac.PO_DOMAIN, portal_hmac.SUB_DOMAIN,
        portal_hmac.PO_ATTACH_DOMAIN, portal_hmac.EST_DOMAIN,
    }
    assert portal_hmac.RFQ_DOMAIN not in siblings
    assert len(siblings) == 6  # no sibling collided either


def test_rfq_signature_never_verifies_under_sibling_domains():
    """Cross-protocol confusion is structurally impossible: an rfq:v1 signature
    over structurally-similar fields fails est:v1, po-att:v1, AND po:v1 verifies
    (and vice versa — the domains differ, so the MACs differ)."""
    cj = portal_hmac.rfq_canonical_json(RFQ, LINES, VENDOR_KEYS)
    rfq_sig = portal_hmac.sign_rfq(
        SECRET, rfq_id=7, rfq_number=RFQ["rfq_number"], canonical_json=cj,
    )
    # po:v1 over the same (id, number, json) triple — only the domain differs.
    assert portal_hmac.verify_po(
        SECRET, rfq_sig, po_id=7, po_number=RFQ["rfq_number"], canonical_json=cj,
    ) is False
    # And a po:v1 signature never verifies as rfq:v1.
    po_sig = portal_hmac.sign_po(
        SECRET, po_id=7, po_number=RFQ["rfq_number"], canonical_json=cj,
    )
    assert portal_hmac.verify_rfq(
        SECRET, po_sig, rfq_id=7, rfq_number=RFQ["rfq_number"], canonical_json=cj,
    ) is False
    # est:v1 (a different field shape entirely) with the same secret also never
    # collides — sanity-pin one concrete cross-check.
    est_sig = portal_hmac.sign_po_estimate(
        SECRET, est_uuid="u-1", job_no=RFQ["job_no"], filename="q.pdf",
        declared_mime="application/pdf", size_bytes=1, sha256="00" * 32,
    )
    assert portal_hmac.verify_rfq(
        SECRET, est_sig, rfq_id=7, rfq_number=RFQ["rfq_number"], canonical_json=cj,
    ) is False
