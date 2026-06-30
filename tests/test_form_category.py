"""Tests for shared/form_category.py — the form_code -> safety|progress resolver (P3).

Run with: pytest -q tests/test_form_category.py
"""
from __future__ import annotations

from pathlib import Path

from shared import form_category


def test_safety_forms_resolve_safety():
    for code in ("jha-v1", "jha-v3", "toolbox-talk-v1"):
        assert form_category.resolve_category(code) == "safety"


def test_progress_form_resolves_progress():
    # The Daily Field Report is the lone progress form in the live catalog (field-ops P1b).
    assert form_category.resolve_category("daily-report-v1") == "progress"
    assert form_category.resolve_category("daily-report") == "progress"


def test_unknown_or_blank_defaults_safety():
    # Deny-by-route: only a positively-catalogued progress form ever routes to progress.
    for code in ("does-not-exist", "", "   ", "jha-v999"):
        assert form_category.resolve_category(code) == "safety"


def test_resolver_unwraps_the_parents_wrapper():
    # Regression guard: catalog.json is {"manifest_version", "parents":[...]}, NOT a bare
    # list. An un-unwrapped read builds an empty map → everything mis-defaults to safety
    # (the bug the live resolver-smoke caught).
    mapping = form_category._form_code_to_category()
    assert mapping, "resolver built an empty map — the {parents:[...]} wrapper was not unwrapped"
    assert mapping.get("daily-report-v1") == "progress"
    assert any(v == "safety" for v in mapping.values())


def test_missing_catalog_fails_safe_to_safety(monkeypatch):
    monkeypatch.setattr(form_category, "_CATALOG_PATH", Path("/nonexistent/dir/catalog.json"))
    # Never raises; a catalog problem degrades to today's behavior (everything safety).
    assert form_category.resolve_category("daily-report-v1") == "safety"


def test_malformed_catalog_fails_safe_to_safety(tmp_path, monkeypatch):
    bad = tmp_path / "catalog.json"
    bad.write_text("{ this is not valid json", encoding="utf-8")
    monkeypatch.setattr(form_category, "_CATALOG_PATH", bad)
    assert form_category.resolve_category("daily-report-v1") == "safety"


def test_invalid_category_value_defaults_safety(tmp_path, monkeypatch):
    cat = tmp_path / "catalog.json"
    cat.write_text(
        '{"parents":[{"parent_form_code":"x","category":123,'
        '"forms":[{"current_form_code":"x-v1","versions":[{"form_code":"x-v1"}]}]}]}',
        encoding="utf-8",
    )
    monkeypatch.setattr(form_category, "_CATALOG_PATH", cat)
    assert form_category.resolve_category("x-v1") == "safety"


def test_progress_category_value_is_honored(tmp_path, monkeypatch):
    # Positive control: a well-formed progress parent IS routed progress (proves the
    # safety-default isn't swallowing legitimate progress forms).
    cat = tmp_path / "catalog.json"
    cat.write_text(
        '{"parents":[{"parent_form_code":"p","category":"progress",'
        '"forms":[{"current_form_code":"p-v2","versions":['
        '{"form_code":"p-v1"},{"form_code":"p-v2"}]}]}]}',
        encoding="utf-8",
    )
    monkeypatch.setattr(form_category, "_CATALOG_PATH", cat)
    assert form_category.resolve_category("p-v1") == "progress"  # historical version too
    assert form_category.resolve_category("p-v2") == "progress"
    assert form_category.resolve_category("p") == "progress"     # parent code
