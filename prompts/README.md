# Prompts

System and user prompts, version-controlled in markdown.

Convention:
- Each prompt is a `.md` file. The top of the file has a YAML front-matter block with
  `name`, `version`, `model`, and notes.
- The body is the prompt text. Use clear sections (### Inputs, ### Task, ### Output schema).
- When a prompt is paired with a JSON schema, name them with the same stem
  (e.g., `safety_extract.md` ↔ `schemas/safety_extract.json`).

Prompts are loaded as plain strings by `shared/anthropic_client.py` callers.
