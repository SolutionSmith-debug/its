# JSON Schemas

JSON schemas paired with Anthropic tool-use calls for structured extraction.

Convention: every schema has a `version` field at the top. Scripts reject responses on
schema-version mismatch. When changing a schema, bump the version, update the consuming
script in the same commit.

Naming: `<workstream>_<purpose>.json` — e.g., `safety_extract.json`, `safety_summary.json`,
`rfq_request.json`.

Schemas are loaded by `shared/anthropic_client.py` callers and passed as `tools=[...]` to
`call(...)`.

Local-inference schemas (ADR-0004): documents shaped `{"version": "...",
"json_schema": {...}}` are loaded through `shared/schema_loader.load_schema(name,
expected_version=...)`, which enforces the version convention in code (mismatch raises
`SchemaVersionError`). The same `json_schema` object drives Ollama `format=` constrained
decoding AND post-hoc `jsonschema.validate` in `shared/ollama_client.py`. First occupant:
`vendor_estimate_extraction.json` (v1.0.0).
