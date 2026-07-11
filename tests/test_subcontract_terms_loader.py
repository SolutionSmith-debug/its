"""Tests for subcontracts/terms.py — the render-time loader + the Layer-A legal gate. The gate is
tested on TMP fixtures (a pending vs cleared version), never the live seed's state (that would be the
self-defeating live-content landmine the S2 review removed)."""
from __future__ import annotations

import hashlib
import json

import pytest

from subcontracts import terms
from subcontracts.terms import TermsError

_BODY = "SUBCONTRACT AGREEMENT\n\nby and between {{contractor_entity}} and {{subcontractor_entity}}.\n"


def _seed(tmp_path, legal_review: str):
    """Seed a tmp subcontracts/terms with one library version at the given legal_review."""
    tdir = tmp_path / "terms"
    tdir.mkdir()
    raw = _BODY.encode("utf-8")
    (tdir / "standard_subcontract_v1.md").write_bytes(raw)
    manifest = {
        "manifest_version": 1,
        "profiles": {
            "standard_subcontract": {
                "kind": "library", "label": "Body", "current_version": "v1",
                "versions": {"v1": {"file": "standard_subcontract_v1.md",
                                    "sha256": hashlib.sha256(raw).hexdigest(),
                                    "tokens": ["contractor_entity", "subcontractor_entity"],
                                    "legal_review": legal_review}},
            },
            "negotiated_msa": {"kind": "attach", "label": "MSA", "render_line": "UNDER THE MSA."},
        },
    }
    (tdir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return tdir


def test_layer_a_gate_fences_a_pending_version(tmp_path, monkeypatch):
    monkeypatch.setattr(terms, "TERMS_DIR", _seed(tmp_path, "pending"))
    with pytest.raises(TermsError, match="NOT cleared"):
        terms.load_terms_text("standard_subcontract")
    with pytest.raises(TermsError, match="NOT cleared"):
        terms.required_tokens("standard_subcontract")


def test_cleared_version_loads_sha_verified_and_header_stripped(tmp_path, monkeypatch):
    tdir = _seed(tmp_path, "cleared")
    monkeypatch.setattr(terms, "TERMS_DIR", tdir)
    text = terms.load_terms_text("standard_subcontract")
    assert "{{contractor_entity}}" in text
    assert terms.required_tokens("standard_subcontract") == ["contractor_entity", "subcontractor_entity"]


def test_hash_mismatch_refuses(tmp_path, monkeypatch):
    tdir = _seed(tmp_path, "cleared")
    (tdir / "standard_subcontract_v1.md").write_text(_BODY + "TAMPERED", encoding="utf-8")
    monkeypatch.setattr(terms, "TERMS_DIR", tdir)
    with pytest.raises(TermsError, match="HASH MISMATCH"):
        terms.load_terms_text("standard_subcontract")


def test_substitute_tokens_is_strict_on_missing():
    with pytest.raises(TermsError, match="unfilled token"):
        terms.substitute_tokens("hi {{contractor_entity}}", {})
    out = terms.substitute_tokens("hi {{contractor_entity}}", {"contractor_entity": "Evergreen"})
    assert out == "hi Evergreen"


def test_attach_render_line(tmp_path, monkeypatch):
    monkeypatch.setattr(terms, "TERMS_DIR", _seed(tmp_path, "cleared"))
    assert terms.render_line("negotiated_msa") == "UNDER THE MSA."
    with pytest.raises(TermsError, match="not attach"):
        terms.render_line("standard_subcontract")


def test_config_loaders_shape_only_on_live_files():
    # Shape assertions only (required keys present) — NEVER the live values (HOUSE_REFLEXES §5).
    c = terms.load_contractor_config()
    assert {"entity", "address_lines", "phone", "signature_entity", "prime_contractor_default"} <= set(c)
    p = terms.load_payment_terms_config()
    assert isinstance(p["retainage_bp"], int) and not isinstance(p["retainage_bp"], bool)
