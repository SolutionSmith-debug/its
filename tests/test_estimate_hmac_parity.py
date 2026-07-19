"""Golden-vector parity tests for the est:v1 vendor-estimate HMAC protocol.

The pinned contract (ADR-0004 Lane 1 / PR-A): the Worker signs each uploaded
vendor estimate at upload time over the domain-separated canonical string

    "est:v1" \\n est_uuid \\n job_no \\n filename \\n declared_mime
             \\n str(size_bytes) \\n sha256_hex

with HMAC-SHA256 → lowercase hex (the portal HMAC secret — Worker env
HMAC_PAYLOAD_SECRET / Keychain ITS_PORTAL_HMAC_SECRET). The Mac side
(`shared.portal_hmac.est_canonical` + `verify_po_estimate`) recomputes it
constant-time before a single byte is trusted — the po-att:v1 pattern
(`po_attachment_canonical` / `verify_po_attachment`) with estimate identity
(`est_uuid` + `job_no`) in place of attachment identity (`att_uuid` + `po_id`).

Every expected signature here is computed IN THE TEST from the pinned canonical
string with stdlib hmac/hashlib — independent of the implementation under test,
so a drifted canonical (reordered fields, wrong separator, wrong domain, missing
utf-8 encode) fails against the golden math, not against itself.

Run with: pytest -q tests/test_estimate_hmac_parity.py
"""
from __future__ import annotations

import hashlib
import hmac as _hmac
from typing import Any

from shared import portal_hmac

SECRET = "est-parity-test-secret"

# One fixed golden vector (realistic corpus-shaped values, incl. a parenthesized
# revision-chain filename — the "(2)/(3)" class the ADR corpus survey found).
FIELDS: dict[str, Any] = {
    "est_uuid": "6f0a2d1c-9b7e-4c33-8f6d-2a51e0c4b911",
    "job_no": "2026.001",
    "filename": "Platt Quote 4471 (2).pdf",
    "declared_mime": "application/pdf",
    "size_bytes": 48_213,
    "sha256": "9c56cc51b374c3ba189210d5b6d4bf57790d351c96c47c02190ecf1e430635ab",
}

# A second vector with a non-ASCII filename — pins the utf-8 encode of the
# canonical string (a latin-1 or ascii-errors encode would diverge here).
FIELDS_UNICODE: dict[str, Any] = {
    **FIELDS,
    "est_uuid": "0d9e51aa-1111-4c33-8f6d-2a51e0c4b922",
    "filename": "Terratech — Devis n°7 (naïve).pdf",
}

EST_DOMAIN = "est:v1"
PO_ATT_DOMAIN = "po-att:v1"


def _canonical(domain: str, f: dict[str, Any]) -> str:
    """The pinned wire string, built HERE from the contract — not via portal_hmac."""
    return "\n".join([
        domain,
        f["est_uuid"],
        f["job_no"],
        f["filename"],
        f["declared_mime"],
        str(f["size_bytes"]),
        f["sha256"],
    ])


def _hmac_hex(secret: str, message: str) -> str:
    return _hmac.new(secret.encode("utf-8"), message.encode("utf-8"), hashlib.sha256).hexdigest()


def _golden_sig(f: dict[str, Any]) -> str:
    return _hmac_hex(SECRET, _canonical(EST_DOMAIN, f))


# ---- canonical-string parity -------------------------------------------------------


def test_est_canonical_builds_the_exact_pinned_string():
    assert portal_hmac.est_canonical(**FIELDS) == _canonical(EST_DOMAIN, FIELDS)


def test_est_canonical_pinned_literal():
    """Fully-literal golden string — immune to a bug shared by test helper and impl."""
    expected = (
        "est:v1\n"
        "6f0a2d1c-9b7e-4c33-8f6d-2a51e0c4b911\n"
        "2026.001\n"
        "Platt Quote 4471 (2).pdf\n"
        "application/pdf\n"
        "48213\n"
        "9c56cc51b374c3ba189210d5b6d4bf57790d351c96c47c02190ecf1e430635ab"
    )
    assert portal_hmac.est_canonical(**FIELDS) == expected


def test_est_canonical_size_bytes_is_decimal_str():
    """size_bytes rides as str(int) — no float repr, no padding."""
    got = portal_hmac.est_canonical(**{**FIELDS, "size_bytes": 7})
    assert "\n7\n" in got


# ---- accept path -------------------------------------------------------------------


def test_verify_accepts_the_golden_vector():
    assert portal_hmac.verify_po_estimate(SECRET, _golden_sig(FIELDS), **FIELDS) is True


def test_verify_accepts_unicode_filename_vector():
    assert (
        portal_hmac.verify_po_estimate(SECRET, _golden_sig(FIELDS_UNICODE), **FIELDS_UNICODE)
        is True
    )


# ---- reject paths (each mutation MUST break verification) --------------------------


def test_flipped_byte_in_sha256_rejected():
    """The signature covers the content digest — one flipped hex nibble kills it."""
    sig = _golden_sig(FIELDS)
    bad_sha = ("a" if FIELDS["sha256"][0] != "a" else "b") + FIELDS["sha256"][1:]
    assert bad_sha != FIELDS["sha256"]
    tampered = {**FIELDS, "sha256": bad_sha}
    assert portal_hmac.verify_po_estimate(SECRET, sig, **tampered) is False


def test_signature_over_different_filename_rejected():
    """Simulated field swap/tamper: a signature minted for one filename must not
    verify against another (rename-after-signing, the po-att posture)."""
    other = {**FIELDS, "filename": "renamed-after-signing.pdf"}
    sig_for_other = _golden_sig(other)
    assert portal_hmac.verify_po_estimate(SECRET, sig_for_other, **FIELDS) is False


def test_swapped_field_order_rejected():
    """est_uuid and job_no exchanged between slots — same bytes, wrong positions.
    Proves the canonical is position-bound, not a bag of values."""
    swapped = {**FIELDS, "est_uuid": FIELDS["job_no"], "job_no": FIELDS["est_uuid"]}
    sig_for_swapped = _hmac_hex(SECRET, _canonical(EST_DOMAIN, swapped))
    assert portal_hmac.verify_po_estimate(SECRET, sig_for_swapped, **FIELDS) is False


def test_po_att_domain_signature_never_verifies_as_estimate():
    """DOMAIN-SEPARATION PROOF: an HMAC minted under the po-att:v1 domain over the
    IDENTICAL field tail must not verify under est:v1 — cross-protocol signature
    confusion is structurally impossible (the ADR Invariant-2 requirement)."""
    po_att_sig = _hmac_hex(SECRET, _canonical(PO_ATT_DOMAIN, FIELDS))
    assert po_att_sig != _golden_sig(FIELDS)  # sanity: the domains really diverge
    assert portal_hmac.verify_po_estimate(SECRET, po_att_sig, **FIELDS) is False


def test_real_po_attachment_signature_never_verifies_as_estimate():
    """Same proof via the LIVE po-att:v1 signer (not a hand-built string): sign a
    PO attachment sharing uuid/filename/mime/size/sha, then try it as an estimate."""
    att_sig = portal_hmac.sign_po_attachment(
        SECRET,
        att_uuid=FIELDS["est_uuid"],
        po_id=7,
        filename=FIELDS["filename"],
        declared_mime=FIELDS["declared_mime"],
        size_bytes=FIELDS["size_bytes"],
        sha256=FIELDS["sha256"],
    )
    assert portal_hmac.verify_po_estimate(SECRET, att_sig, **FIELDS) is False


def test_wrong_secret_rejected():
    sig = _golden_sig(FIELDS)
    assert portal_hmac.verify_po_estimate("other-secret", sig, **FIELDS) is False


def test_absent_or_empty_signature_rejected_without_raising():
    """The verify contract: never raises — False on any mismatch incl. None/empty
    (the fail-closed downgrade defense the daemon relies on)."""
    assert portal_hmac.verify_po_estimate(SECRET, None, **FIELDS) is False
    assert portal_hmac.verify_po_estimate(SECRET, "", **FIELDS) is False
    assert portal_hmac.verify_po_estimate(SECRET, "not-hex-at-all", **FIELDS) is False
