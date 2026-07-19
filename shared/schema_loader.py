"""Versioned JSON-schema loader for `schemas/` (ADR-0004 decision 8).

Purpose
-------
Realizes the `schemas/README.md` convention as code: every schema document in
`schemas/` carries a top-level `version` field, and a consuming script REJECTS the
schema on a version mismatch instead of silently running against a drifted contract.
`load_schema(name, expected_version=...)` is that contract — the first real occupant
is `schemas/vendor_estimate_extraction.json` (the vendor-estimate union schema that
drives BOTH Ollama `format=` constrained decoding and post-hoc `jsonschema.validate`
in `shared/ollama_client.py` / `po_materials/estimate_extract.py`).

Document shape (required):
    {
      "version": "<semver string>",
      "json_schema": { ... a JSON Schema object ... }
    }
Any additional top-level keys (name, description) are allowed and preserved — the
full parsed document is returned so callers read `doc["json_schema"]`.

Invariants
----------
* Version pinning is HARD: `expected_version` mismatch raises `SchemaVersionError`
  — the consuming script must be updated in the same commit as a schema bump
  (schemas/README convention). No fuzzy/semver-range matching, by design.
* `name` is restricted to `[a-z0-9_]` — a hostile/typoed name can never traverse
  out of the schemas directory.
* Fail-closed and LOUD: a missing file, malformed JSON, or missing `version` /
  `json_schema` key raises `SchemaLoaderError`. Never returns a partial document.

Failure modes
-------------
* Missing/unreadable file, bad JSON, wrong top-level shape → `SchemaLoaderError`.
* Version mismatch → `SchemaVersionError` (a subclass, so one except catches both).
* No I/O beyond the single local file read; no network, no AI, no sends.

Consumers
---------
* `po_materials/estimate_extract.py` — pins `vendor_estimate_extraction` @ 1.0.0.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

# Repo-root schemas directory (this file lives in shared/).
SCHEMAS_DIR = Path(__file__).resolve().parent.parent / "schemas"

_NAME_RE = re.compile(r"^[a-z0-9_]+$")


class SchemaLoaderError(Exception):
    """A schema document could not be loaded (missing / malformed / wrong shape)."""


class SchemaVersionError(SchemaLoaderError):
    """The schema document's `version` does not equal the caller's pin."""


def load_schema(
    name: str, *, expected_version: str, schemas_dir: Path | None = None
) -> dict[str, Any]:
    """Load `schemas/<name>.json`, enforcing the version pin.

    Args:
        name: Schema basename without extension, `[a-z0-9_]+` only
            (e.g. ``"vendor_estimate_extraction"``).
        expected_version: The exact `version` string the caller was written
            against. Mismatch raises — never a silent drift.
        schemas_dir: Override directory (tests); defaults to the repo `schemas/`.

    Returns:
        The full parsed document (dict) with at least `version` (str) and
        `json_schema` (dict) keys.

    Raises:
        SchemaLoaderError: bad name, missing file, malformed JSON, or a document
            missing the required `version` / `json_schema` shape.
        SchemaVersionError: `version` != `expected_version`.
    """
    if not _NAME_RE.match(name):
        raise SchemaLoaderError(f"invalid schema name {name!r} (allowed: [a-z0-9_]+)")
    path = (schemas_dir or SCHEMAS_DIR) / f"{name}.json"
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SchemaLoaderError(f"schema file unreadable: {path} ({exc})") from exc
    try:
        doc = json.loads(raw)
    except ValueError as exc:
        raise SchemaLoaderError(f"schema file is not valid JSON: {path} ({exc})") from exc
    if not isinstance(doc, dict):
        raise SchemaLoaderError(f"schema document must be a JSON object: {path}")
    version = doc.get("version")
    if not isinstance(version, str) or not version:
        raise SchemaLoaderError(f"schema document missing string 'version': {path}")
    json_schema = doc.get("json_schema")
    if not isinstance(json_schema, dict) or not json_schema:
        raise SchemaLoaderError(f"schema document missing object 'json_schema': {path}")
    if version != expected_version:
        raise SchemaVersionError(
            f"schema {name!r} version mismatch: file has {version!r}, "
            f"caller pins {expected_version!r} — update the consumer and the pin "
            "in the same commit (schemas/README convention)"
        )
    return doc
