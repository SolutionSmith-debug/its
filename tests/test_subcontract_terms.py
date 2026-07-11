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


def test_manifest_contains_the_seeded_profiles():
    # SUBSET, not equality: the operator can create_profile (add more) via the config editor, so an
    # exact-set pin would RED the moment a profile is added and strand the actuation PR (HOUSE_REFLEXES
    # §5 / PR-511). Assert the seeded profiles are PRESENT + the map is non-empty.
    m = _manifest()
    assert m["manifest_version"] == 1
    assert isinstance(m["profiles"], dict) and m["profiles"]
    assert {"standard_subcontract", "negotiated_msa"} <= set(m["profiles"])


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


# NOTE: no `legal_review == "pending"` assertion here. That flips to "cleared" the instant the
# operator does make-current on the body (the intended first smoke step), so pinning it live would be
# a self-defeating landmine (HOUSE_REFLEXES §5). The seed's ships-pending property is a one-time fact,
# not a permanent invariant; add_version's pending-default is covered in test_config_apply.py on a tmp
# fixture. The Layer-A render gate itself is enforced + tested where the subcontracts renderer lands (S3).


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
