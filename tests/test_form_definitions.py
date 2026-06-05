"""Validate every Safety Portal form definition against the meta-schema.

`safety_portal/forms/*.json` are the single source of truth both renderers (the
TS display runtime + the Python PDF renderer) consume, so they MUST conform to
`forms/meta-schema.json`. This test is the enforcement.
"""
from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest

_ROOT = Path(__file__).resolve().parents[1]
FORMS_DIR = _ROOT / "safety_portal" / "forms"
REF_DIR = _ROOT / "safety_portal" / "reference_forms"
META = json.loads((FORMS_DIR / "meta-schema.json").read_text())
DEF_PATHS = sorted(p for p in FORMS_DIR.glob("*.json") if p.name != "meta-schema.json")


def _load(p: Path) -> dict:
    return json.loads(p.read_text())


def test_meta_schema_is_itself_valid_jsonschema() -> None:
    jsonschema.Draft202012Validator.check_schema(META)


def test_there_are_definitions() -> None:
    assert DEF_PATHS, "no form definitions found"


@pytest.mark.parametrize("path", DEF_PATHS, ids=lambda p: p.stem)
def test_definition_conforms_to_meta_schema(path: Path) -> None:
    jsonschema.validate(_load(path), META)


@pytest.mark.parametrize("path", DEF_PATHS, ids=lambda p: p.stem)
def test_definition_source_pdf_exists(path: Path) -> None:
    d = _load(path)
    assert (REF_DIR / d["source_pdf"]).exists(), f"{d['source_pdf']} not in reference_forms/"


def test_form_codes_are_unique() -> None:
    codes = [_load(p)["form_code"] for p in DEF_PATHS]
    assert len(codes) == len(set(codes)), "duplicate form_code"


@pytest.mark.parametrize("path", DEF_PATHS, ids=lambda p: p.stem)
def test_signature_tables_have_exactly_one_signature_column(path: Path) -> None:
    for s in _load(path)["sections"]:
        if s["type"] == "signature_table":
            sig = [c for c in s["columns"] if c["input"] == "signature"]
            assert len(sig) == 1, f"{path.stem}/{s['key']}: need exactly one signature column"


@pytest.mark.parametrize("path", DEF_PATHS, ids=lambda p: p.stem)
def test_checklist_groups_have_scale_and_items(path: Path) -> None:
    for s in _load(path)["sections"]:
        if s["type"] == "checklist":
            for g in s["groups"]:
                assert g["scale"], f"{path.stem}/{g['key']}: empty scale"
                assert g["items"], f"{path.stem}/{g['key']}: no items"


def test_all_five_parent_types_present() -> None:
    parents = {_load(p)["parent_form_code"] for p in DEF_PATHS}
    assert parents == {
        "jha", "equipment-preinspection", "toolbox-talk",
        "visitor-sign-in", "hsse-work-observation",
    }


def test_jha_mandatory_footer_and_signature_present() -> None:
    d = _load(FORMS_DIR / "jha-v1.json")
    texts = [s["text"] for s in d["sections"] if s["type"] == "static_text"]
    assert any("REVIEW AND REVISE THE PLAN" in t for t in texts)
    assert any(s["type"] == "signature_table" for s in d["sections"])


def test_equipment_lockout_legal_text_present() -> None:
    for code in ("equipment-telehandler-v1", "equipment-skid-steer-v1"):
        d = _load(FORMS_DIR / f"{code}.json")
        texts = [s["text"] for s in d["sections"] if s["type"] == "static_text"]
        assert any("lock/tag-out" in t for t in texts), code


def test_equipment_telehandler_item_count() -> None:
    # The Telehandler tri-state checklist must keep all items (no silent drop).
    d = _load(FORMS_DIR / "equipment-telehandler-v1.json")
    checklist = next(s for s in d["sections"] if s["type"] == "checklist")
    total = sum(len(g["items"]) for g in checklist["groups"])
    assert total == 64, f"expected 64 telehandler items, got {total}"


def test_hsse_has_eleven_assessment_categories() -> None:
    d = _load(FORMS_DIR / "hsse-work-observation-v1.json")
    s1 = next(s for s in d["sections"] if s.get("key") == "section_1")
    assert len(s1["groups"][0]["items"]) == 11


def test_toolbox_five_variants_with_content_and_signin() -> None:
    tb = [p for p in DEF_PATHS if _load(p)["parent_form_code"] == "toolbox-talk"]
    assert len(tb) == 5
    for p in tb:
        d = _load(p)
        assert any(s["type"] == "content_blocks" and s["blocks"] for s in d["sections"])
        assert any(s["type"] == "signature_table" for s in d["sections"])
