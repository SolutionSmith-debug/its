"""Tests for shared/schema_loader.py — the versioned schemas/ loader (ADR-0004
decision 8) — and the vendor_estimate_extraction v1.0.0 document itself.

RED musts covered:
  * version-mismatch pin raises SchemaVersionError (the reject-on-mismatch
    convention realized in code);
  * the shipped json_schema is a VALID JSON Schema and a real VALUE gate —
    explicit numeric maxima reject an absurd cents value, additionalProperties
    rejects an injected control field (red-team #6).

Run with: pytest -q tests/test_schema_loader.py
"""
from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest

from shared.schema_loader import SchemaLoaderError, SchemaVersionError, load_schema

NAME = "vendor_estimate_extraction"
VERSION = "1.0.0"


# ---- The real shipped schema --------------------------------------------------------


def test_loads_vendor_estimate_extraction_at_pinned_version():
    doc = load_schema(NAME, expected_version=VERSION)
    assert doc["version"] == VERSION
    schema = doc["json_schema"]
    assert schema["type"] == "object"
    assert set(schema["required"]) == {"doc_type", "confidence", "line_items"}


def test_shipped_json_schema_is_itself_valid():
    doc = load_schema(NAME, expected_version=VERSION)
    jsonschema.Draft202012Validator.check_schema(doc["json_schema"])


def _minimal_instance(**overrides):
    base = {
        "doc_type": "quote",
        "confidence": 0.9,
        "line_items": [
            {
                "description": "PV wire",
                "qty": 2500,
                "unit": "M",
                "unit_cost_cents": 109890,
                "extended_cents": 274725,
            }
        ],
    }
    base.update(overrides)
    return base


def test_schema_accepts_a_legitimate_instance():
    doc = load_schema(NAME, expected_version=VERSION)
    jsonschema.validate(instance=_minimal_instance(), schema=doc["json_schema"])


def test_red_schema_numeric_maximum_bites():
    """The schema IS the value gate (red-team #6): a cents value above the
    explicit maximum is rejected — delete the maxima and this test fails."""
    doc = load_schema(NAME, expected_version=VERSION)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(
            instance=_minimal_instance(subtotal_cents=3_000_000_000),
            schema=doc["json_schema"],
        )
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(
            instance=_minimal_instance(confidence=1.5), schema=doc["json_schema"]
        )


def test_red_schema_rejects_injected_control_field():
    """additionalProperties: false — an AI-invented control field (send_to) never
    passes the schema gate."""
    doc = load_schema(NAME, expected_version=VERSION)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(
            instance=_minimal_instance(send_to="attacker@example.com"),
            schema=doc["json_schema"],
        )


def test_red_schema_rejects_oversized_line_items():
    doc = load_schema(NAME, expected_version=VERSION)
    lines = [{"description": f"item {i}"} for i in range(201)]  # maxItems 200
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(
            instance=_minimal_instance(line_items=lines), schema=doc["json_schema"]
        )


# ---- Loader contract ----------------------------------------------------------------


def test_red_version_mismatch_raises():
    with pytest.raises(SchemaVersionError):
        load_schema(NAME, expected_version="9.9.9")


def test_missing_file_raises(tmp_path: Path):
    with pytest.raises(SchemaLoaderError):
        load_schema("nope", expected_version="1.0.0", schemas_dir=tmp_path)


def test_malformed_json_raises(tmp_path: Path):
    (tmp_path / "bad.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(SchemaLoaderError):
        load_schema("bad", expected_version="1.0.0", schemas_dir=tmp_path)


def test_missing_version_key_raises(tmp_path: Path):
    (tmp_path / "noversion.json").write_text(
        json.dumps({"json_schema": {"type": "object"}}), encoding="utf-8"
    )
    with pytest.raises(SchemaLoaderError):
        load_schema("noversion", expected_version="1.0.0", schemas_dir=tmp_path)


def test_missing_json_schema_key_raises(tmp_path: Path):
    (tmp_path / "noschema.json").write_text(
        json.dumps({"version": "1.0.0"}), encoding="utf-8"
    )
    with pytest.raises(SchemaLoaderError):
        load_schema("noschema", expected_version="1.0.0", schemas_dir=tmp_path)


def test_red_traversal_shaped_name_refused(tmp_path: Path):
    """A hostile/typoed name can never traverse out of the schemas directory."""
    for bad in ("../evil", "evil/../../x", "UPPER", "a.b", ""):
        with pytest.raises(SchemaLoaderError):
            load_schema(bad, expected_version="1.0.0", schemas_dir=tmp_path)


def test_version_mismatch_is_a_loader_error_subclass():
    """One `except SchemaLoaderError` catches both failure classes."""
    assert issubclass(SchemaVersionError, SchemaLoaderError)
