# Post-Box-Pivot Repo Doc Cascade — 2026-05-20

## Context

Planning project (Claude.ai) absorbed the Box OAuth pivot cascade earlier
today. Five new docs landed in project knowledge:

- ITS_Cascade_Unification_Update_2026-05-20_Box_OAuth_Pivot.docx
- ITS_Permissions_Ask_v4_2026-05-20.docx (replaces v3)
- ITS_Handover_Plan_v6_1_2026-05-20.docx (operative overlay on v6)
- ITS_Foundation_Scaffold_Update_v6_1_2026-05-20.docx (operative overlay on v6)
- ITS_Cascade_Implementation_Checklist_2026-05-20.docx

This PR completes Lane 3 of the Implementation Checklist — the two
repo-side doc updates that flow from the cascade.

## Changes

### CLAUDE.md stub/real table

Updated the `shared/box_client.py` row from `Stub | Sandbox JWT config
pending` to `Working, tested` with the OAuth User Authentication context
(refresh-token rotation invariant, sandbox vs Phase 1.5 user, setup +
smoke script references, PR #39 / commit 2ce6ece anchor).

Grep for residual JWT or service-account references in CLAUDE.md after
the edit: zero matches. The stub-table row was the only Box-JWT mention
in the file; no M365 service-account references appear here (M365's
service-account model is documented in `shared/graph_client.py` directly).

### README.md "Re-installing after dependency changes" section

New section inserted directly after "First-time setup" documenting the
`pip install -e ".[dev]"` re-run requirement after dep-adding PRs. Both
venv and system-Python paths shown. PR #39 (boxsdk addition) cited as
the canonical example.

Recommended in Cascade Implementation Checklist 2026-05-20 Lane 3.1.
Not codified as an Operational Standard — it is setup-time guidance,
not a cross-cutting standard.

## Not Touched

- docs/tech_debt.md — already current per PR #40 (commit 74c000f).
- .gitignore — already current per PR #40.
- shared/box_client.py — has its own canonical docstring; CLAUDE.md is
  a high-level pointer only.
- Any code, tests, dependencies, schemas, prompts, or workflows.

## Verification

- ruff check . → clean
- mypy . → 0 errors across 71 source files
- pytest -q → 533 passed, 2 skipped (baseline unchanged)

## Cross-references

- Permissions Ask v4
- Handover Plan v6.1
- Foundation Scaffold Update v6.1
- Cascade Unification Update 2026-05-20 (Box OAuth Pivot)
- Cascade Implementation Checklist 2026-05-20 Lane 3.1
