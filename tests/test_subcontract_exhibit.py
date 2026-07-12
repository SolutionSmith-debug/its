"""Tests for subcontracts/exhibit.py — the Exhibit A skeleton + per-trade Article II loader. Integrity
behaviour (sha-mismatch) is tested on a TMP fixture; the live-file assertions are SHAPE/round-trip only
(every trade resolves, electrical trades share, tokens present) — never pinned to the corpus content
(HOUSE_REFLEXES §5)."""
from __future__ import annotations

import hashlib
import json

import pytest

from subcontracts import exhibit
from subcontracts.exhibit import ExhibitError

_SKELETON = "Exhibit A\n\nby {{contractor_entity}} and {{subcontractor_entity}}.\n\n{{article_ii}}\n"
_ART2 = "Civil:\nC0.1 - do the work.\n"


def _seed(tmp_path):
    """Seed a tmp exhibit dir with one skeleton + one trade template, sha-pinned, and return it."""
    edir = tmp_path / "exhibit"
    (edir / "art2").mkdir(parents=True)
    skel_raw = _SKELETON.encode("utf-8")
    art2_raw = _ART2.encode("utf-8")
    (edir / "skeleton.md").write_bytes(skel_raw)
    (edir / "art2" / "civil.md").write_bytes(art2_raw)
    manifest = {
        "manifest_version": 1,
        "skeleton": {
            "file": "skeleton.md",
            "sha256": hashlib.sha256(skel_raw).hexdigest(),
            "tokens": ["contractor_entity", "subcontractor_entity", "article_ii"],
        },
        "trade_templates": {
            "civil": {"file": "art2/civil.md", "sha256": hashlib.sha256(art2_raw).hexdigest()},
        },
        "trade_map": {"Civil": "civil"},
    }
    (edir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return edir


# --- live manifest / file assertions (shape + round-trip, no content pins) ---

def test_manifest_loads_and_shape_checks():
    manifest = exhibit.load_manifest()
    assert manifest["manifest_version"] == 1
    assert isinstance(manifest["skeleton"], dict)
    assert isinstance(manifest["trade_templates"], dict) and manifest["trade_templates"]
    assert isinstance(manifest["trade_map"], dict) and manifest["trade_map"]


def test_skeleton_loads_sha_verified_and_carries_article_ii_marker():
    text = exhibit.load_skeleton()
    assert "{{article_ii}}" in text
    assert "{{project_name}}" in text


def test_required_tokens_matches_skeleton_declaration():
    tokens = exhibit.required_tokens()
    assert "article_ii" in tokens
    assert set(tokens) == set(exhibit.load_manifest()["skeleton"]["tokens"])


def test_every_trade_in_map_resolves_to_a_real_art2_file():
    trade_map = exhibit.load_manifest()["trade_map"]
    for trade in trade_map:
        key = exhibit.template_key_for_trade(trade)
        assert key in exhibit.load_manifest()["trade_templates"]
        body = exhibit.load_trade_art2(trade)  # sha-verified read; raises on any drift
        assert isinstance(body, str) and body.strip()


def test_three_electrical_trades_share_the_electrical_template():
    for trade in ("AC Electrical", "MV Electrical", "DC Electrical"):
        assert exhibit.template_key_for_trade(trade) == "electrical"
    bodies = {exhibit.load_trade_art2(t) for t in ("AC Electrical", "MV Electrical", "DC Electrical")}
    assert len(bodies) == 1  # identical text, one distinct value


def test_unknown_trade_raises():
    with pytest.raises(ExhibitError, match="unknown subcontract trade"):
        exhibit.template_key_for_trade("Underwater Basket Weaving")
    with pytest.raises(ExhibitError, match="unknown subcontract trade"):
        exhibit.load_trade_art2("Underwater Basket Weaving")


# --- integrity on a tmp fixture ---

def test_skeleton_hash_mismatch_refuses(tmp_path, monkeypatch):
    edir = _seed(tmp_path)
    (edir / "skeleton.md").write_text(_SKELETON + "TAMPERED", encoding="utf-8")
    monkeypatch.setattr(exhibit, "EXHIBIT_DIR", edir)
    with pytest.raises(ExhibitError, match="HASH MISMATCH"):
        exhibit.load_skeleton()


def test_trade_template_hash_mismatch_refuses(tmp_path, monkeypatch):
    edir = _seed(tmp_path)
    (edir / "art2" / "civil.md").write_text(_ART2 + "TAMPERED", encoding="utf-8")
    monkeypatch.setattr(exhibit, "EXHIBIT_DIR", edir)
    with pytest.raises(ExhibitError, match="HASH MISMATCH"):
        exhibit.load_trade_art2("Civil")


def test_seeded_tmp_loads_clean(tmp_path, monkeypatch):
    monkeypatch.setattr(exhibit, "EXHIBIT_DIR", _seed(tmp_path))
    assert "{{article_ii}}" in exhibit.load_skeleton()
    assert exhibit.load_trade_art2("Civil").startswith("Civil:")


# --- substitute_tokens strictness ---

def test_substitute_tokens_is_strict_on_missing_and_blank():
    with pytest.raises(ExhibitError, match="unfilled token"):
        exhibit.substitute_tokens("hi {{contractor_entity}}", {})
    with pytest.raises(ExhibitError, match="unfilled token"):
        exhibit.substitute_tokens("hi {{contractor_entity}}", {"contractor_entity": "  "})
    out = exhibit.substitute_tokens("hi {{contractor_entity}}", {"contractor_entity": "Evergreen"})
    assert out == "hi Evergreen"


def test_substitute_tokens_treats_article_ii_as_required_present():
    text = "scope: {{article_ii}}"
    with pytest.raises(ExhibitError, match="unfilled token"):
        exhibit.substitute_tokens(text, {})
    assert exhibit.substitute_tokens(text, {"article_ii": "THE WORK BODY"}) == "scope: THE WORK BODY"
