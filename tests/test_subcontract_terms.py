"""Subcontract terms-library integrity (SC-S2) — the seeded 27-article body artifact + the
manifest-derived picklist. Pure filesystem, no network. Mirrors tests/test_po_terms.py's controls:
manifest↔file↔sha256↔tokens parity, and the picklist vocabulary DERIVED from the manifest.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from shared import picklist_validation

_TERMS_DIR = Path(__file__).resolve().parents[1] / "subcontracts" / "terms"
_TOKEN_RE = re.compile(r"\{\{([a-z_]+)\}\}")


def _manifest() -> dict:
    return json.loads((_TERMS_DIR / "manifest.json").read_text(encoding="utf-8"))


def test_manifest_declares_expected_profiles():
    m = _manifest()
    assert m["manifest_version"] == 1
    assert set(m["profiles"]) == {"standard_subcontract", "negotiated_msa"}


def test_standard_body_file_hash_and_tokens_match_manifest():
    """The immutability contract: the pinned sha256 covers the RAW file bytes, and the declared
    tokens are EXACTLY the {{tokens}} the file actually carries (STRICT both ways)."""
    v = _manifest()["profiles"]["standard_subcontract"]["versions"]["v1"]
    raw = (_TERMS_DIR / v["file"]).read_bytes()
    assert hashlib.sha256(raw).hexdigest() == v["sha256"], "body file drifted from its sha256 pin"
    file_tokens = sorted(set(_TOKEN_RE.findall(raw.decode("utf-8"))))
    assert file_tokens == sorted(v["tokens"]), "manifest token list != the file's actual {{tokens}}"


def test_standard_body_carries_no_residual_specimen_data():
    """The corpus-seed drift check: the tokenized body must not leak the ESS/Danville specimen."""
    raw = (_TERMS_DIR / "standard_subcontract_v1.md").read_text(encoding="utf-8")
    for leak in ("Danville", "Coastal Carolina", "274,018", "Fairfax", "ESS LLC"):
        assert leak not in raw, f"residual specimen data leaked: {leak!r}"


def test_standard_body_is_legal_review_pending_until_operator_clears():
    """Seeded pending: the render-side Layer-A gate fences it until the operator makes it current
    (the legal attestation). A smoke requires that one-click make-current first."""
    v = _manifest()["profiles"]["standard_subcontract"]["versions"]["v1"]
    assert v["legal_review"] == "pending"


def test_picklist_is_derived_from_the_subcontract_manifest():
    """The vendor-terms manifest-derivation pattern, for subcontracts: the ITS_Subcontractors
    'Default Terms Profile' vocabulary IS the manifest profile ids; reserved ids stay out."""
    m = _manifest()
    assert set(m["profiles"]) == set(picklist_validation._SUBCONTRACTOR_TERMS_PROFILE_VALUES)
    for reserved in m.get("reserved_profile_ids", {}):
        assert reserved not in picklist_validation._SUBCONTRACTOR_TERMS_PROFILE_VALUES


def test_picklist_derivation_falls_back_when_manifest_unreadable(tmp_path):
    missing = tmp_path / "nope.json"
    assert picklist_validation._derive_subcontractor_terms_profile_values(missing) == frozenset(
        {"standard_subcontract", "negotiated_msa"}
    )
