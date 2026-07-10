"""Tests for po_materials/terms.py — manifest↔file parity, hash-pinned releases,
strict token substitution, config validation, and the picklist-vocabulary parity (S3).

The S3 controls-that-bite:
  * sha256 hash pins — a drifted/edited version file must REFUSE to load (the
    immutability contract: pinned drafts render identically forever).
  * manifest↔directory parity BOTH ways — every declared file exists; every file in
    terms/ is declared (an orphaned _v2 file someone forgot to register is a silent
    render-divergence waiting to happen).
  * profile ids == the ITS_Vendors "Default Terms Profile" picklist vocabulary
    (shared/picklist_validation) — a profile the picklist offers but the library
    can't load (or vice versa) breaks the S4 render path.
  * strict tokens — a PO must never render with an unfilled contract blank.

Pure filesystem tests — no Smartsheet, no network.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from po_materials import terms
from shared import picklist_validation

# ---- Manifest shape + parity ----------------------------------------------


def test_manifest_loads_and_declares_expected_profiles():
    manifest = terms.load_manifest()
    assert set(manifest["profiles"]) == {"standard_17", "chint_vendor", "negotiated_gtc"}


def test_profile_ids_match_vendors_picklist_vocabulary():
    """The terms-library ids ARE the ITS_Vendors 'Default Terms Profile' options —
    one vocabulary, parity-pinned in both directions. Reserved ids stay OUT of the
    picklist until built."""
    manifest = terms.load_manifest()
    assert set(manifest["profiles"]) == set(picklist_validation._VENDOR_TERMS_PROFILE_VALUES)
    for reserved in manifest.get("reserved_profile_ids", {}):
        assert reserved not in picklist_validation._VENDOR_TERMS_PROFILE_VALUES


def test_every_declared_version_file_exists_and_hash_matches():
    for profile_id, profile in terms.list_profiles().items():
        if profile["kind"] != "library":
            continue
        for version in profile["versions"]:
            text = terms.load_terms_text(profile_id, version)  # hash-verifies internally
            assert text.strip(), f"{profile_id} v{version} is empty"


def test_no_orphan_files_in_terms_dir():
    """Every .md in terms/ must be a declared version file — an unregistered file is
    a silent render-divergence hazard."""
    declared = {
        entry["file"]
        for profile in terms.list_profiles().values()
        if profile["kind"] == "library"
        for entry in profile["versions"].values()
    }
    on_disk = {p.name for p in terms.TERMS_DIR.glob("*.md")}
    assert on_disk == declared


def test_current_version_is_a_declared_version():
    for profile_id, profile in terms.list_profiles().items():
        if profile["kind"] != "library":
            continue
        assert str(profile["current_version"]) in profile["versions"], profile_id


def test_every_current_version_is_legally_cleared():
    """Layer B invariant: every library profile's CURRENT version must be legal_review 'cleared'.
    A manifest that advances current_version onto a pending/rejected version would fence EVERY PO on
    that profile (Layer A) — this pins the manifest so that mis-bump fails CI, not live rendering."""
    for profile_id, profile in terms.list_profiles().items():
        if profile["kind"] != "library":
            continue
        cur = str(profile["current_version"])
        entry = profile["versions"][cur]
        assert entry.get("legal_review") == "cleared", f"{profile_id} current v{cur} not cleared"


def test_pending_version_refuses_to_load(tmp_path, monkeypatch):
    """Layer A prove-the-control-bites: a version whose legal_review != 'cleared' must NOT render —
    it raises (fencing that PO) rather than silently emitting un-reviewed contract language. Mirrors
    the hash-mismatch tamper test, on a copied tree so the live manifest is untouched."""
    src = terms.TERMS_DIR
    work = tmp_path / "terms"
    work.mkdir()
    for p in src.iterdir():
        (work / p.name).write_bytes(p.read_bytes())
    manifest = json.loads((work / "manifest.json").read_text())
    manifest["profiles"]["standard_17"]["versions"]["1"]["legal_review"] = "pending"
    (work / "manifest.json").write_text(json.dumps(manifest))
    monkeypatch.setattr(terms, "TERMS_DIR", work)
    with pytest.raises(terms.TermsError, match="NOT cleared"):
        terms.load_terms_text("standard_17")
    # required_tokens shares the same _version_entry choke point → also fenced.
    with pytest.raises(terms.TermsError, match="NOT cleared"):
        terms.required_tokens("standard_17")


def test_hash_mismatch_refuses_to_load(tmp_path, monkeypatch):
    """The immutability contract's teeth: a tampered version file must raise, never
    silently render different words onto a pinned draft."""
    src = terms.TERMS_DIR
    work = tmp_path / "terms"
    work.mkdir()
    for p in src.iterdir():
        (work / p.name).write_bytes(p.read_bytes())
    tampered = work / "chint_vendor_v1.md"
    tampered.write_text(tampered.read_text() + "\n9. A quietly added clause\n")
    monkeypatch.setattr(terms, "TERMS_DIR", work)
    with pytest.raises(terms.TermsError, match="HASH MISMATCH"):
        terms.load_terms_text("chint_vendor")


def test_attach_kind_refuses_text_load_and_carries_render_line():
    profile = terms.get_profile("negotiated_gtc")
    assert profile["kind"] == "attach"
    assert profile["render_line"].startswith("THIS PURCHASE ORDER IS SUBJECT")
    with pytest.raises(terms.TermsError, match="attach"):
        terms.load_terms_text("negotiated_gtc")


def test_unknown_profile_and_version_raise():
    with pytest.raises(terms.TermsError, match="unknown terms profile"):
        terms.get_profile("field_21")  # reserved, not built
    with pytest.raises(terms.TermsError, match="no version"):
        terms.load_terms_text("standard_17", "99")


# ---- Transcription content pins -------------------------------------------


def test_standard_17_contains_the_clause_landmarks_and_tokens():
    text = terms.load_terms_text("standard_17")
    for landmark in (
        "ADDITIONAL INSTRUCTIONS:",
        "TERMS AND CONDITIONS:",
        "DEFINITION OF PURCHASER",
        "1.a DEFINITION OF SELLER",
        "11. FAILURE OF PERFOMANCE AND REMEDIES",  # typo preserved VERBATIM by design
        "15. OPTIONAL CANCELLATION",
        "17. MISCELLANEOUS",
    ):
        assert landmark in text, landmark
    # The two sanctioned deviations: entity + seller blank are tokens, so the raw
    # template entity never leaks onto a rendered PO. The provenance header comment
    # (which legitimately MENTIONS "E.S.S. LLC") must be stripped by the loader —
    # renderable text starts at ADDITIONAL INSTRUCTIONS.
    assert "{{purchaser_entity}}" in text and "{{seller_name}}" in text
    assert "E.S.S. LLC" not in text
    assert "<!--" not in text
    assert text.startswith("ADDITIONAL INSTRUCTIONS:")
    assert terms.required_tokens("standard_17") == ["purchaser_entity", "seller_name"]


def test_chint_vendor_contains_the_eight_bullets_and_no_tokens():
    text = terms.load_terms_text("chint_vendor")
    for landmark in (
        "1. Prices are FOB Shipping Point",
        "3. CPS Standard Terms and Conditions apply",
        "8. Local sales tax and installation not included",
    ):
        assert landmark in text, landmark
    assert terms.required_tokens("chint_vendor") == []
    assert "{{" not in text


# ---- Token substitution -----------------------------------------------------


def test_substitute_tokens_happy_path():
    out = terms.substitute_tokens(
        "shall mean {{purchaser_entity}} and {{seller_name}}",
        {"purchaser_entity": "Evergreen Renewables LLC", "seller_name": "Rexel", "extra": "x"},
    )
    assert out == "shall mean Evergreen Renewables LLC and Rexel"


def test_substitute_tokens_missing_or_blank_raises():
    with pytest.raises(terms.TermsError, match="unfilled token"):
        terms.substitute_tokens("mean {{seller_name}}", {})
    with pytest.raises(terms.TermsError, match="unfilled token"):
        terms.substitute_tokens("mean {{seller_name}}", {"seller_name": "   "})


def test_standard_17_renders_clean_with_tokens_filled():
    purchaser = terms.load_purchaser_config()
    rendered = terms.substitute_tokens(
        terms.load_terms_text("standard_17"),
        {"purchaser_entity": purchaser["entity"], "seller_name": "Chint Power Systems (CPS)"},
    )
    assert "{{" not in rendered
    assert purchaser["entity"] in rendered  # single-source the needle we fed the token, not a literal


# ---- Config validation ------------------------------------------------------


def test_purchaser_config_carries_the_d5_identity():
    # SHAPE, not pinned values — the purchaser identity is operator-editable via the §50 config
    # editor (entity/address/phone/routing all mutate + bump config_version). Pinning the live
    # values here would red-light the editor's auto-merge-on-green the instant the operator edits
    # them (the config-editor merge-blocker; HOUSE REFLEXES §5). Assert structure instead.
    config = terms.load_purchaser_config()
    assert isinstance(config["entity"], str) and config["entity"].strip()
    assert isinstance(config["address_lines"], list) and config["address_lines"]
    assert all(isinstance(line, str) and line.strip() for line in config["address_lines"])
    assert isinstance(config["phone"], str) and config["phone"].strip()
    routing = config["invoice_routing"]
    assert "@" in routing["to"]
    assert isinstance(routing["cc"], list)
    assert all(isinstance(addr, str) and "@" in addr for addr in routing["cc"])


def test_tax_config_is_integer_basis_points():
    # Assert the integer-bp SHAPE + state_names parity, never the exact table — the rate VALUES are
    # operator-editable via the §50 tax editor (HOUSE REFLEXES §5).
    config = terms.load_tax_config()
    assert config["rates_bp"], "rates_bp must be non-empty"
    # int, never bool, in range — the money-path invariant (matches config_apply's validation).
    assert all(
        isinstance(v, int) and not isinstance(v, bool) and 0 <= v <= 10_000
        for v in config["rates_bp"].values()
    )
    assert set(config["state_names"]) == set(config["rates_bp"])


def test_tax_config_rejects_float_rates(tmp_path, monkeypatch):
    """No floats in the money path — a 9.0 in the table must refuse to load."""
    work = tmp_path / "config"
    work.mkdir()
    (work / "purchaser.json").write_bytes((terms.CONFIG_DIR / "purchaser.json").read_bytes())
    (work / "tax.json").write_text(
        '{"config_version": 1, "rates_bp": {"IL": 9.0}, "state_names": {"IL": "Illinois"}}'
    )
    monkeypatch.setattr(terms, "CONFIG_DIR", work)
    with pytest.raises(terms.TermsError, match="INTEGER"):
        terms.load_tax_config()


def test_loader_module_is_import_pure():
    """No network/Smartsheet/state imports — safe to import anywhere (the F02 walk
    now covers po_materials/, this is the local double-check)."""
    import ast
    src = (Path(terms.__file__)).read_text()
    tree = ast.parse(src)
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    forbidden = {"requests", "smartsheet", "urllib", "http", "socket", "shared"}
    assert not (imported & forbidden), imported & forbidden
