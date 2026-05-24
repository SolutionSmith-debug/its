---
type: session_log
date: 2026-05-24
status: closed
workstream: docs
related_prs: []
tags: [memory, consolidation, cc-file-based-memory, parallel-pass]
---

# 2026-05-24 — CC file-based memory consolidation

PR: TBD (filled in post-merge).

Parallel pass to the chat-side memory consolidation that landed earlier today (`its-blueprint/session-logs/2026-05-24_memory-consolidation.md`). Applies the same philosophy — structural navigation + hard-fought rule enforcement — to cc's file-based memory at `/Users/sethsmith/.claude/projects/-Users-sethsmith/memory/`.

## Purpose

Trim cc memory of historical snapshots and doctrine-restatement that's now better captured in `~/its-blueprint/` or in code/git, keeping only behavioral rules (with documented violation history) and structural pointers cc actually consults during a session.

## Pre-audit state

9 memory entries + MEMORY.md index = 10 files, 559 total lines:

| File | Lines | Size | Type |
|---|---|---|---|
| `user_role.md` | 28 | 1462B | frame-setting |
| `feedback_preservation_over_refactor.md` | 61 | 3449B | behavioral rule (hard-fought) |
| `feedback_verify_ci_diagnosis_before_fix.md` | 49 | 2738B | behavioral rule (hard-fought) |
| `feedback_pr_scoping_narrow.md` | 49 | 2512B | behavioral rule |
| `feedback_verify_merge_before_branch_delete.md` | 20 | 2476B | behavioral rule (hard-fought) |
| `feedback_customer_data_commit_scope.md` | 38 | 1903B | behavioral rule |
| `project_session_logs_convention.md` | 58 | 3171B | doctrine-restatement → pointer candidate |
| `project_m365_graph_landed.md` | 36 | 2040B | historical milestone |
| `project_phase1_status.md` | 211 | 24999B | historical snapshot (largest by far) |
| `MEMORY.md` | 9 | 1786B | index |

## Per-entry action

### KEEP verbatim (6)

| File | Why kept |
|---|---|
| `user_role.md` | Frame-setting; every session needs it. Cross-references preservation-over-refactor. |
| `feedback_preservation_over_refactor.md` | Brief's explicit hard-fought-rule list. Carries PR #4 / commit 1295a93 and PR #8 / commit 8dfc6e8 violation history + the parse_job_v3 F841 cleanup story. Pointing at Op Stds v11 §14 would lose the canonical examples. |
| `feedback_verify_ci_diagnosis_before_fix.md` | Brief's explicit hard-fought-rule list ("verify-before-fix"). Carries the 2026-05-17 CI-fix and 2026-05-19 Smartsheet-logging.Filter diagnoses. |
| `feedback_pr_scoping_narrow.md` | Behavioral rule with documented context (2026-05-18 smartsheet_client.py wiring direction from operator). CC-specific to code work; not duplicated in chat memory. |
| `feedback_verify_merge_before_branch_delete.md` | Foundation of the four-part PR-landed discipline. PR #34 ghost is the canonical violation. CC-specific gotcha (`git branch -d` warning interpretation). |
| `feedback_customer_data_commit_scope.md` | Behavioral rule with documented violation (2026-05-18 box_migration reconcile). CC-specific to commit decisions. Not in chat memory; not duplicated in doctrine. |

### REPLACE with pointer (1)

`project_session_logs_convention.md` — was 58 lines of convention restatement; now 29 lines pointing at the canonical sources:

- `~/its/docs/session_logs/README.md` (original convention)
- `~/its/docs/operations/doc_conventions.md` (PR #76 formalization)
- `~/its-blueprint/session-logs/README.md` (planning-side counterpart, 2026-05-24)
- `~/its-blueprint/prompts/scaffold/session-log.md` (orchestration scaffold, 2026-05-24)

The convention is now well-documented across four canonical surfaces; memory carrying the full restatement competes with those for authority. Dropped: the stale "auto-mode classifier blocks direct push to main" constraint (observed 2026-05-21; not reproduced 2026-05-24 across multiple direct session-log pushes to main in its-blueprint).

### REMOVE (2)

| File | Where the content lives now |
|---|---|
| `project_m365_graph_landed.md` | Code in `shared/graph_client.py` + `scripts/smoke_test_graph.py`; PRs #5/#6/#8 in git log; CLAUDE.md stub-state table flipped at PR #8. |
| `project_phase1_status.md` | Git log (commits + tags); session logs (`2026-05-17_*` through `2026-05-23_*`); `shared/defaults.py::BOX_PROJECT_FOLDERS`; `docs/tech_debt.md`; CLAUDE.md stub table. The 25KB churn-prone snapshot was textbook drift surface — every PR aged it. |

Brief explicitly named both as "textbook historical snapshots."

### UPDATE (1)

`MEMORY.md` index trimmed to 7 lines reflecting the 7 retained entries. Doctrine pointers in the index (preservation-over-refactor "Op Stds v9 §14" → "Op Stds v11 §14", verify-ci-diagnoses "Op Stds v9 §13" → "Op Stds v11 §13") refreshed since they were stale; entry file contents themselves left verbatim per KEEP rule.

## Post-audit state

7 entries + MEMORY.md index = 8 files, 281 total lines (-50% line count, -65% byte count driven by `project_phase1_status.md` removal):

### Category breakdown (matches chat-memory consolidation pattern)

| Category | Count | Entries |
|---|---|---|
| **Frame-setting** | 1 | `user_role` |
| **Behavioral rules (hard-fought)** | 5 | `preservation-over-refactor`, `verify-ci-diagnosis-before-fix`, `pr-scoping-narrow`, `verify-merge-before-branch-delete`, `customer-data-commit-scope` |
| **Structural pointers** | 1 | `session-logs-convention` |

No operational gotchas in cc memory (those were all in chat memory — Box OAuth rotation, MCP-gap REST fallback). No architectural pointers (chat memory carries those). The cc memory shape reflects its narrow role: code-writing discipline + frame-setting.

## Decisions made during session

- **Decision**: Treat `project_session_logs_convention.md` as REPLACE rather than KEEP or REMOVE.
  - **Alternative considered (REMOVE)**: Drop entirely, relying on doc_conventions.md + the scaffold to carry the rule.
  - **Alternative considered (KEEP)**: Preserve the full 58-line entry including the auto-mode constraint note.
  - **Rationale for REPLACE**: The convention is load-bearing for end-of-session behavior (write a log if X and Y), so a pointer in memory still fires the rule. Full restatement is now duplicative across four sources; verbatim KEEP would compete for authority. Auto-mode constraint dropped because the 2026-05-21 observation didn't reproduce in 2026-05-24 sessions (multiple direct session-log pushes to main in its-blueprint succeeded).

- **Decision**: KEEP `feedback_pr_scoping_narrow.md` and `feedback_customer_data_commit_scope.md` verbatim despite neither being on the brief's explicit hard-fought-rule list.
  - **Alternative considered**: REPLACE with slim pointers since each cites operator direction once.
  - **Rationale for KEEP**: Both are cc-specific (PR scope discipline is about code work; commit-scope discipline is about file decisions) and not duplicated in chat memory or doctrine. Brief criteria for KEEP includes "CC-specific pattern not duplicated elsewhere" — both qualify.

- **Decision**: Update doctrine version pointers in MEMORY.md index (v9 → v11) while leaving entry file contents verbatim.
  - **Alternative considered**: Don't touch the version pointers; leave them stale to honor "KEEP verbatim."
  - **Rationale**: The index is metadata describing the entries, not the entries themselves. Stale doctrine pointers in the index would confuse — the entries' bodies acknowledge they're carrying historical version references ("was v7 §14 through 2026-05-18; cascade absorb 2026-05-19 bumped to v9"). The index needs to be operationally accurate; the bodies preserve the change history.

## Out of scope

- Did NOT modify chat memory (separate system, `memory_user_edits` accessible only to chat; cc has no tool to modify it).
- Did NOT modify any its-blueprint repo files (the §G6 + session log for the chat-side consolidation already landed in PR #2 + PR #3).
- Did NOT touch any code modules.
- Did NOT add a `docs/tech_debt.md` entry — the consolidation surfaced no actionable code-side gap.

## Verification

| Stage | Result |
|---|---|
| pytest | **1033 passed / 16 deselected** (no change — session-log-only PR) |
| mypy | **Success: no issues found in 127 source files** |
| ruff check | clean |
| Memory directory post-state | 8 files, 281 lines (was 10 files, 559 lines) |

## Sequencing context

This is the third and final consolidation pass in the 2026-05-24 work cluster:

1. **its-blueprint migration** — .docx forest → 35 markdown files (initial commit `3e7f967` + linters established)
2. **prompts/ scaffolding** — orchestration scaffolds + snippets landed (PR #1 in its-blueprint, merge `42ac7e0`)
3. **Chat-side memory consolidation** — 30 → 15 entries (executed in Claude.ai); §G6 + rationale-anchor session log landed (its-blueprint PR #2 merge `1a07a31`, PR #3 merge `1163075`)
4. **CC file-based memory consolidation (this PR)** — 9 → 7 entries (file deletion + 1 in-place replace + index update + this session log)

After this lands, both memory surfaces (chat and cc) are structurally clean for the next phase. No further consolidation queued.

## Cross-references

- Chat-side memory consolidation log: `~/its-blueprint/session-logs/2026-05-24_memory-consolidation.md`
- Memory archive §G6 (Contacts Data Quality): `~/its-blueprint/references/memory-archive.md`
- Brief authored against the 2026-05-24 consolidation philosophy.

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
