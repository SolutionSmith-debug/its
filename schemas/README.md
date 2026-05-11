# JSON Schemas

JSON schemas paired with Anthropic tool-use calls for structured extraction.

Convention: every schema has a `version` field at the top. Scripts reject responses on
schema-version mismatch. When changing a schema, bump the version, update the consuming
script in the same commit.

Naming: `<workstream>_<purpose>.json` — e.g., `safety_extract.json`, `safety_summary.json`,
`rfq_request.json`.

Schemas are loaded by `shared/anthropic_client.py` callers and passed as `tools=[...]` to
`call(...)`.
