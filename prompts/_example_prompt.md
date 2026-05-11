---
name: _example_extract
version: 0.1.0
model: claude-sonnet-4-6
notes: Stub prompt — copy this when adding a new workstream extraction prompt.
---

You are extracting structured data from an example input.

### Inputs
- A user-provided text blob.

### Task
- Identify field_a (a string) and field_b (a number).
- Score your confidence overall (0–1).

### Output
Respond by calling the `_example_extract` tool with the extracted fields.
