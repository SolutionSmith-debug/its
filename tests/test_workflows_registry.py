"""Validate the workflow registry manifest + its Python consumers (form-builder workflow
selector). The TS side (registry.ts / publishValidation.ts) reads the SAME workflows.json.

Run with: pytest -q tests/test_workflows_registry.py
"""
from __future__ import annotations

import json
from pathlib import Path

import jsonschema

from shared import form_category

_ROOT = Path(__file__).resolve().parents[1]
_REGISTRY = json.loads((_ROOT / "safety_portal" / "workflows.json").read_text(encoding="utf-8"))
_SCHEMA = json.loads((_ROOT / "safety_portal" / "workflows.schema.json").read_text(encoding="utf-8"))
_IDS = {w["id"] for w in _REGISTRY["workflows"]}


def test_schema_is_valid_jsonschema() -> None:
    jsonschema.Draft202012Validator.check_schema(_SCHEMA)


def test_registry_conforms_to_schema() -> None:
    jsonschema.validate(_REGISTRY, _SCHEMA)


def test_default_is_a_registered_workflow() -> None:
    assert _REGISTRY["default"] in _IDS


def test_form_category_workflow_ids_include_registry() -> None:
    assert _IDS <= form_category.workflow_ids()


def test_is_valid_category_accepts_registry_rejects_junk() -> None:
    for wid in _IDS:
        assert form_category.is_valid_category(wid)
    assert not form_category.is_valid_category("definitely-not-a-workflow")
    assert not form_category.is_valid_category(123)
    assert not form_category.is_valid_category(None)


def test_workflow_ids_fall_back_when_registry_missing(monkeypatch) -> None:
    monkeypatch.setattr(form_category, "_REGISTRY_PATH", Path("/nonexistent/dir/workflows.json"))
    assert form_category._load_workflow_ids() == frozenset({"safety", "progress"})  # the floor
    assert form_category.is_valid_category("safety")
    assert form_category.is_valid_category("progress")


def test_workflow_ids_fall_back_when_registry_malformed(tmp_path, monkeypatch) -> None:
    bad = tmp_path / "workflows.json"
    bad.write_text("{ not valid json", encoding="utf-8")
    monkeypatch.setattr(form_category, "_REGISTRY_PATH", bad)
    assert form_category._load_workflow_ids() == frozenset({"safety", "progress"})
