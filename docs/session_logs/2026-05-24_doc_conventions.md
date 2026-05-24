---
type: session_log
date: 2026-05-24
status: closed
workstream: docs
related_prs: []
tags: [conventions, lint, indexes, retrofit]
---

# 2026-05-24 — Markdown doc conventions + index generation + lint

PR: TBD (filled in post-merge). Squash-merge commit + timestamp also TBD.

This PR formalizes the markdown doc conventions that cc + chat have been
following organically since the session-log convention landed 2026-05-17.
Adds programmatic queryability via YAML frontmatter + auto-generated
README indexes + a warn-only lint script. Existing 36+ session logs are
grandfathered; new docs must conform.

## Purpose

Codify the de-facto conventions, give scripts a frontmatter handle so
"find all session logs for `safety_reports`" becomes a 3-line query
rather than a grep-prose dance, and enforce going forward without
forcing a mass-migration of historical docs.

Three principles guided the design:

1. **Build on what's in practice** — codify, don't invent.
2. **Lazy retrofit, strict enforcement going forward** — old docs are
   grandfathered; new docs MUST conform.
3. **Programmatic queryability** — YAML frontmatter so a script answers
   structured questions about the doc corpus.

## Pre-flight findings

- Repo had 45 markdown files under `docs/` + 3 in `prompts/`.
- Conventions were partially captured: `docs/session_logs/README.md`
  established the three-record model + filename pattern;
  `docs/tech_debt.md` followed an implicit `## Title [STATUS DATE]`
  shape; `docs/operations/pr_merge_discipline.md` (PR #74) was the only
  pre-existing operations doc.
- PyYAML 6.0.3 already in `.venv` (transitive dep), no new packages
  needed.
- The `docs/` subtree had 3 loose top-level audit/reference files that
  fit better under purpose-specific subdirs (`audits/`, `references/`).

## Code changes

### New files
- **`docs/operations/doc_conventions.md`** — canonical spec
  (frontmatter, per-type sections, filename convention, workstream
  taxonomy, retrofit policy). The "canonical example" this PR's session
  log demonstrates.
- **`scripts/regen_doc_indexes.py`** (~260 lines) — walks the doc tree,
  parses YAML frontmatter, regenerates `<!-- BEGIN AUTO-INDEX -->`
  blocks in every `README.md`. Idempotent. `--check` mode for CI.
- **`scripts/lint_doc_conventions.py`** (~330 lines) — validates
  frontmatter + canonical taxonomies + filename convention per type.
  Warn-only default. `--strict` exit-non-zero mode flips post-retrofit.
- **`tests/test_regen_doc_indexes.py`** — 12 tests covering
  parse_doc / render_index_table / regenerate_one / main() / `--check`
  mode / sort order / idempotency.
- **`tests/test_doc_conventions.py`** — 17 tests covering canonical
  taxonomy invariants / lint_file / warn-only main / strict main /
  exempt list / grandfather behavior.
- **`docs/README.md`**, **`docs/reports/README.md`**,
  **`docs/operations/README.md`**, **`docs/audits/README.md`**,
  **`docs/references/README.md`** — 5 new index READMEs with
  AUTO-INDEX sentinel blocks. The existing `docs/session_logs/README.md`
  + `prompts/README.md` + `prompts/samples/README.md` were extended to
  include AUTO-INDEX blocks without disturbing their narrative content.

### Modified files
- **`.github/workflows/ci.yml`** — added two warn-only steps after the
  pytest run: `lint_doc_conventions` and `regen_doc_indexes --check`.
  Both designed to exit 0 even with violations during the retrofit
  window; CI log surfaces drift for the operator.
- **`CLAUDE.md`** — added doc-conventions reference under "Useful
  references in this repo" with the lint + regen entry points.
- **`README.md`** — added a "Documentation" section between
  Operational Conventions and Status, listing each `docs/` subdir +
  pointing at the conventions spec.
- **`pyproject.toml`** — added `yaml` to the mypy
  `ignore_missing_imports` list to match the existing pattern for
  vendor SDKs without published stubs.
- **`docs/tech_debt.md`** — two new `[OPEN 2026-05-24]` entries:
  - "Doc-conventions lint strict-mode flip after retrofit window
    closes" — tracks the ~60-day target (2026-07-24) for bulk retrofit
    + the `--strict` flag flip in CI.
  - "Nightly auto-index regen wiring" — deferred (CI `--check` is the
    load-bearing enforcement; nightly launchd path is optional add-on).

### File moves
- `docs/picklist_hardening_audit.md` → `docs/audits/picklist_hardening_audit.md`
- `docs/person_tag_audit_2026-05-19.md` → `docs/audits/person_tag_audit_2026-05-19.md`
- `docs/picklist_sync.md` → `docs/references/picklist_sync.md`
- All cross-references (10 hits across 9 files including
  `README.md`, `shared/picklist_validation.py`,
  `box_migration/parse_job_v3.py`, `tests/test_person_tag.py`,
  `scripts/audit_picklist_drift.py`, `docs/tech_debt.md`, and 3
  grandfathered session logs) updated in the same diff.

## Verification

| Stage         | Result                                                                                  |
|---------------|-----------------------------------------------------------------------------------------|
| pytest -q     | **1033 passed / 1 skipped / 16 deselected** (+29 from 1004 baseline; 12 regen + 17 lint tests). |
| mypy .        | **Success: no issues found in 127 source files**.                                       |
| ruff check .  | **All checks passed**.                                                                   |
| lint (warn)   | Surfaces a handful of grandfathered docs without frontmatter — exit 0 by design.        |
| regen --check | Clean — no AUTO-INDEX sections out of date.                                              |
| CI (post-merge)| TBD — captured in the merge verification quartet below.                                 |

## Live smoke

Not applicable — doc-tooling PR; no external services or live state.
The two CI steps (`lint_doc_conventions` + `regen_doc_indexes --check`)
will run on the merge commit; the four-part discipline below captures
the result.

## Out-of-scope notes

- **Bulk-retrofit of 36 existing session logs** — explicit lazy
  retrofit per the policy doc; grandfathered.
- **Strict-mode lint** — warn-only during retrofit window; flip
  tracked in `docs/tech_debt.md`.
- **Nightly auto-index regen via launchd** — deferred; CI `--check`
  is the load-bearing enforcement.
- **Docs-site generator** (mkdocs / docusaurus / etc.) — not
  warranted; GitHub renders markdown directly.
- **Multi-language docs** — single-language for the foreseeable
  future.
- **Restructuring `prompts/samples/`** beyond adding AUTO-INDEX — the
  manual cross-reference table in `prompts/samples/README.md` carries
  per-sample provenance the auto-gen can't infer.

## Sequencing context

This PR unblocks structured queries against the doc corpus. Specific
follow-on opportunities (not in this PR):

- A bulk-retrofit sweep (~60 days out per the tech-debt entry) that
  adds frontmatter to the 36 grandfathered session logs in one pass.
  Frontmatter values are largely derivable from filename + git log so
  this is mostly mechanical.
- A `query_docs.py` tool that exposes the indexed corpus to ad-hoc
  questions ("which session logs reference PR #75?") via the same
  YAML-parsing path the regen script uses.
- Strict-mode flip post-retrofit (one-line CI change).

Prereq it depends on: nothing — the conventions doc is the
self-contained reference and the lint/regen scripts are
independent of the rest of the codebase.

## Operator-side actions remaining

1. **(Optional) Opportunistic retrofit** — when you naturally edit a
   grandfathered doc, add frontmatter as part of the change. The
   retrofit policy is permissive; no rush.
2. **(Optional, ~2026-07-24) Bulk-retrofit sweep** — decide whether
   to run the bulk migration or leave grandfather state indefinitely.
   See the tech-debt entry "Doc-conventions lint strict-mode flip
   after retrofit window closes".
3. **(Optional) New workstream addition** — if a new workstream
   emerges (e.g., a Customer 2 onboarding stream), edit the
   `workstream` taxonomy in `docs/operations/doc_conventions.md` and
   the `CANONICAL_WORKSTREAMS` constant in
   `scripts/lint_doc_conventions.py` via a small PR before using the
   value.

## Merge verification quartet output

TBD post-merge — filled in after the four-part discipline runs.

```
# Step 1: PR-state triplet
gh pr view <num> --json mergedAt,mergeCommit,state
# → {"state": "MERGED", "mergedAt": "...", "mergeCommit": {"oid": "..."}}

# Step 2: capture merge SHA
MERGE_SHA=$(gh pr view <num> --json mergeCommit --jq '.mergeCommit.oid')

# Step 3: wait for push:main workflow
# → completed=true

# Step 4: assert push:main success
# → SUCCESS
```
