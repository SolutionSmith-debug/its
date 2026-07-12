"""Tests for subcontracts/subcontract_generate.py — the deterministic record → filled-body-text core
(token assembly, ordinal date, and the money/legal/gate guards wired in)."""
from __future__ import annotations

import hashlib
import json

import pytest

from subcontracts import subcontract_generate as gen
from subcontracts import terms
from subcontracts.subcontract_generate import SubcontractGenerateError


@pytest.mark.parametrize("day,expected", [
    (1, "1st"), (2, "2nd"), (3, "3rd"), (4, "4th"), (11, "11th"), (12, "12th"), (13, "13th"),
    (21, "21st"), (22, "22nd"), (23, "23rd"), (24, "24th"), (31, "31st"),
])
def test_format_agreement_date_ordinals(day, expected):
    assert gen.format_agreement_date(2026, 7, day) == f"{expected} day of July 2026"


_CONTRACTOR = {
    "entity": "Evergreen Renewables LLC", "signature_entity": "Evergreen Renewables LLC",
    "prime_contractor_default": "Evergreen Renewables of Virginia LLC",
    "address_lines": ["100 Spectrum"], "phone": "1", "config_version": 1,
}


def _record(**over):
    base = {
        "subcontractor_entity": "D.E.L. Electric OR, Inc.", "project_name": "Kendall Solar",
        "owner_entity": "Kendall Solar, LLC", "governing_law_state": "OR",
        "contract_price_cents": 27401850, "price_basis": "fixed", "agreement_ymd": [2026, 7, 11],
    }
    base.update(over)
    return base


def test_build_body_tokens_assembles_all_ten():
    t = gen.build_body_tokens(_record(), _CONTRACTOR)
    assert t["contractor_entity"] == "Evergreen Renewables LLC"
    assert t["subcontractor_entity"] == "D.E.L. Electric OR, Inc."
    assert t["prime_contractor"] == "Evergreen Renewables of Virginia LLC"  # default used
    assert t["contract_price_clause"] == "Two hundred seventy-four thousand eighteen dollars and fifty cents ($274,018.50)"
    assert t["governing_law_state_name"] == "the State of Oregon"  # derived from OR
    assert t["governing_law_venue"] == "the State of Oregon"
    assert t["agreement_date"] == "11th day of July 2026"
    assert set(t) == {"agreement_date", "contractor_entity", "subcontractor_entity", "project_name",
                      "prime_contractor", "owner_entity", "contract_price_clause",
                      "governing_law_state_name", "governing_law_venue", "signature_entity"}


def test_prime_contractor_override_wins():
    t = gen.build_body_tokens(_record(prime_contractor="Evergreen Renewables of Oregon LLC"), _CONTRACTOR)
    assert t["prime_contractor"] == "Evergreen Renewables of Oregon LLC"


def test_not_to_exceed_price_basis():
    t = gen.build_body_tokens(_record(price_basis="not_to_exceed"), _CONTRACTOR)
    assert t["contract_price_clause"].startswith("NOT TO EXCEED Two hundred seventy-four thousand")


def test_missing_field_and_bad_price_raise():
    with pytest.raises(SubcontractGenerateError, match="missing required"):
        gen.build_body_tokens(_record(subcontractor_entity="  "), _CONTRACTOR)
    with pytest.raises(SubcontractGenerateError, match="contract_price_cents"):
        gen.build_body_tokens(_record(contract_price_cents=1.5), _CONTRACTOR)
    with pytest.raises(SubcontractGenerateError, match="agreement_ymd"):
        gen.build_body_tokens(_record(agreement_ymd=None), _CONTRACTOR)


# ── render_body_text end-to-end (tmp cleared body + tmp config) ──────────────


def _seed_terms(tmp_path, legal_review="cleared"):
    body = (b"SUBCONTRACT AGREEMENT\n\nby and between {{contractor_entity}} and {{subcontractor_entity}} "
            b"for {{project_name}}.\n2.1 The Contract Price is {{contract_price_clause}}.\n"
            b"governed by the laws of {{governing_law_state_name}}.\n")
    # An attach-kind reference body for the negotiated_msa path (the tokens the attach render fills;
    # {{render_line}} is where the manifest reference line lands).
    attach = (b"SUBCONTRACT AGREEMENT\n\nby and between {{contractor_entity}} and {{subcontractor_entity}} "
              b"for {{project_name}}.\n2.1 The Contract Price is {{contract_price_clause}}.\n{{render_line}}\n"
              b"{{signature_entity}} SUBCONTRACTOR\n")
    tdir = tmp_path / "terms"
    tdir.mkdir()
    (tdir / "b_v1.md").write_bytes(body)
    (tdir / "attach_reference.md").write_bytes(attach)
    (tdir / "manifest.json").write_text(json.dumps({
        "manifest_version": 1, "profiles": {
            "standard_subcontract": {
                "kind": "library", "current_version": "v1",
                "versions": {"v1": {"file": "b_v1.md", "sha256": hashlib.sha256(body).hexdigest(),
                                    "tokens": ["contractor_entity", "subcontractor_entity", "project_name",
                                               "contract_price_clause", "governing_law_state_name"],
                                    "legal_review": legal_review}}},
            "negotiated_msa": {
                "kind": "attach",
                "render_line": "THE WORK IS UNDER, AND SUBJECT TO, THE NEGOTIATED MSA."},
        }}), encoding="utf-8")
    cdir = tmp_path / "config"
    cdir.mkdir()
    (cdir / "contractor.json").write_text(json.dumps({**_CONTRACTOR}), encoding="utf-8")
    return tdir, cdir


def test_render_body_text_fills_the_body(tmp_path, monkeypatch):
    tdir, cdir = _seed_terms(tmp_path)
    monkeypatch.setattr(terms, "TERMS_DIR", tdir)
    monkeypatch.setattr(terms, "CONFIG_DIR", cdir)
    sov = [{"description": "Work", "extended_cents": 27401850}]
    text = gen.render_body_text(_record(), sov)
    assert "Evergreen Renewables LLC and D.E.L. Electric OR, Inc." in text
    assert "Two hundred seventy-four thousand eighteen dollars and fifty cents ($274,018.50)" in text
    assert "the laws of the State of Oregon" in text
    assert "{{" not in text  # no unfilled tokens


def test_render_body_text_fences_on_sov_mismatch(tmp_path, monkeypatch):
    tdir, cdir = _seed_terms(tmp_path)
    monkeypatch.setattr(terms, "TERMS_DIR", tdir)
    monkeypatch.setattr(terms, "CONFIG_DIR", cdir)
    bad_sov = [{"extended_cents": 9999}]  # != 27401850
    with pytest.raises(SubcontractGenerateError, match="reconcile"):
        gen.render_body_text(_record(), bad_sov)


def test_render_body_text_fences_a_pending_body(tmp_path, monkeypatch):
    tdir, cdir = _seed_terms(tmp_path, legal_review="pending")
    monkeypatch.setattr(terms, "TERMS_DIR", tdir)
    monkeypatch.setattr(terms, "CONFIG_DIR", cdir)
    sov = [{"extended_cents": 27401850}]
    with pytest.raises(terms.TermsError, match="NOT cleared"):
        gen.render_body_text(_record(), sov)


def test_render_body_text_attach_renders_reference(tmp_path, monkeypatch):
    """An attach-kind profile (negotiated MSA) renders the one-page REFERENCE body — the manifest
    render_line + the deal facts, STRICT-filled — instead of fencing on 'attach-kind has no text'."""
    tdir, cdir = _seed_terms(tmp_path)
    monkeypatch.setattr(terms, "TERMS_DIR", tdir)
    monkeypatch.setattr(terms, "CONFIG_DIR", cdir)
    monkeypatch.setattr(
        terms, "_ATTACH_REFERENCE_SHA256",
        hashlib.sha256((tdir / "attach_reference.md").read_bytes()).hexdigest(),
    )
    sov = [{"extended_cents": 27401850}]
    text = gen.render_body_text(_record(), sov, terms_profile_id="negotiated_msa")
    # The manifest render_line is present (DERIVED from the manifest, not pinned — HOUSE_REFLEXES §5).
    assert terms.render_line("negotiated_msa") in text
    # Deal facts STRICT-filled; no unfilled contract blank survives.
    assert "D.E.L. Electric OR, Inc." in text
    assert "Two hundred seventy-four thousand eighteen dollars and fifty cents ($274,018.50)" in text
    assert "{{" not in text
    # negotiated_msa carries NO versions/legal_review — that it rendered (did not raise) proves the
    # attach branch skipped the library load + Layer-A gate entirely.
