---
type: session_log
date: 2026-05-24
status: closed
workstream: docs
related_prs: []
tags: [doctrine, version-bump, cleanup, drift, mechanical-text-substitution]
---

# 2026-05-24 — Execution-repo doctrine version drift cleanup

PR: TBD (filled in post-merge).

Single mechanical text-substitution PR closing the long-standing drift the 2026-05-22 cascade audit flagged as "CLAUDE.md + README.md cc reconciliation pending." All active-prose references in the execution repo brought current with canonical doctrine state in its-blueprint (FM v8 / Op Stds v11).

## Purpose

Eliminate the v6/v7/v8/v9/v10 doctrine-version drift that accumulated across active prose (CLAUDE.md, docs/tech_debt.md, docs/references/, shared/, safety_reports/) since the 2026-05-22 cascade. Bring all references to FM v8 + Op Stds v11 with section numbers verified per-reference against current doctrine. No code-behavior changes; docstring/comment/prose updates only.

## Pre-flight findings

The brief's audit grep had a substring bug: `grep -v "session_logs/\|audits/\|reports/"` excluded `safety_reports/` along with the intended `docs/reports/` historical-artifacts dir. Initial counts came in at 24 + 8 = 32 hits, below the brief's ~39 expectation. Re-baselined with corrected exclusion (`docs/session_logs/\|docs/audits/\|docs/reports/`); accurate count is 25 + 11 = 36 hits, within brief's ~39 estimate. Flagging in the session log because future audits using the same grep pattern would under-count.

## Decisions made

- **Decision**: Skip `CLAUDE.md:40` (`Op Stds v4 that described review as a 30–60 day window is superseded`).
  - **Alternative considered**: Bulk-substitute v4 → v11 alongside the other hits.
  - **Rationale**: Line 40 is a historical statement *about* v4's old framing, not a current authority pointer. Updating "Op Stds v4" → "Op Stds v11" here would erase the supersession history and read as if v11 itself had a 30–60 day window (it doesn't). The verify-before-fix discipline applied per the brief's anti-pattern note.

- **Decision**: Map section numbers explicitly via current `doctrine/operational-standards.md`, not by token substitution alone.
  - **Sections that moved**:
    - Push-vs-record: v9 §3 / v9 §27 → **v11 §3.1** (broken out as subsection of §3 Error Logging Pattern)
    - MCP-gap REST fallback: v9 §22 / v10 §25 → **v11 §25** (carry-forward from v10)
  - **Sections that stayed**:
    - Triple-fire CRITICAL alert path: §3 (top-level)
    - Kill-switch fail-open: §1
    - Reviewer-chain forward scan: §18
    - Preservation-over-refactor: §14
    - Smartsheet topology: §23
    - Failure isolation: §27
    - SDK-vs-Live integration test: §30
  - The v9 §3 / v9 §27 split mapping was the trickiest — same section reference meant different rules in different files. Resolved by reading the surrounding prose to determine which rule was meant.

- **Decision**: `.docx` pointers redirect to blueprint-repo paths, phrased as `in the its-blueprint repo` (not as GitHub URLs).
  - **Why**: The blueprint repo is private; raw GitHub URLs don't render for non-collaborators. Repo-relative paths phrased as `<path> in the its-blueprint repo` are the durable form.
  - **Destinations verified**:
    - `ITS_Daemon_Health_Schema_2026-05-21.docx` → `references/daemon-health-schema.md` (file exists in blueprint)
    - `ITS_Q4-Q8_Resolution_2026-05-21.docx` → `workstreams/safety-reports/mission.md` (Q4-Q8 resolutions folded into mission.md lines 35/79/93/101-109)

- **Decision**: Update doctrine references in `docs/tech_debt.md` (5 lines naming v7/v8/v9/v10) even though several describe past decisions made under older doctrine versions.
  - **Alternative considered**: Leave version refs verbatim as preserved historical context for the tech-debt entry's authoring moment.
  - **Rationale**: The brief explicitly listed tech_debt.md as a target (6 Op Stds refs). The active rules these entries cite (preservation §14, kill-switch §1, push-vs-record §3.1, MCP-gap §25) are all still in force at the same logical content in v11 — only the version label changed. Updating refreshes the pointer; preserving would have left readers cross-referencing retired doctrine versions.

## Per-file change summary

| File | Hits | Substitutions |
|---|---|---|
| `CLAUDE.md` | 3 | v9 §3 push-vs-record → v11 §3.1; v9 §18 → v11 §18; `.docx` pointer → blueprint path |
| `docs/tech_debt.md` | 6 | v9 §14, v7 §14, v8 §14 → v11 §14 (×3); v9 §27 push-vs-record → v11 §3.1; v10 §25 → v11 §25; v8 §1 → v11 §1 |
| `docs/references/picklist_sync.md` | 2 | FM v7.1 → FM v8; v9 §3/§22/§27 → v11 §3.1/§25/§27 |
| `shared/alert_dedupe.py` | 2 | v9 §3 triple-fire → v11 §3; v9 §27 push-vs-record → v11 §3.1 |
| `shared/error_log.py` | 3 | v8 §3 → v11 §3; v9 §27 push-vs-record → v11 §3.1; v9 §3 triple-fire → v11 §3 |
| `shared/sentry_client.py` | 1 | v8 §3 triple-fire → v11 §3 |
| `shared/resend_client.py` | 2 | v8 §3 triple-fire → v11 §3; FM v6 → FM v8 |
| `shared/kill_switch.py` | 1 | v8 §1 → v11 §1 |
| `shared/picklist_sync.py` | 3 | v9 §27 push-vs-record → v11 §3.1; v9 §22 MCP-gap → v11 §25; v9 §3 triple-fire → v11 §3 |
| `shared/scheduling.py` | 1 | v9 §27 failure-isolation → v11 §27 |
| `shared/review_queue.py` | 3 | Op Stds v8 → v11 (×2); FM v6 → FM v8 |
| `shared/smartsheet_client.py` | 1 | FM v6 → FM v8 |
| `shared/graph_client.py` | 1 | FM v6 → FM v8 |
| `shared/anomaly_logger.py` | 1 | FM v6 → FM v8 |
| `shared/quarantine.py` | 1 | FM v6 → FM v8 |
| `shared/untrusted_content.py` | 1 | FM v6 → FM v8 |
| `safety_reports/intake.py` | 2 | FM v6 Invariant 1/2 → FM v8 Invariant 1/2 |
| `safety_reports/intake_poll.py` | 1 | v9 §3 (seen-set push framing) → v11 §3.1 |
| `safety_reports/README.md` | 2 | FM v6 → FM v8; `.docx` pointer → blueprint path |
| **Total** | **36** | **19 files, symmetric +38/-38 lines** |

## Verification

| Stage | Result |
|---|---|
| Op Stds drift grep (post-edit, corrected exclusion) | 1 hit — `CLAUDE.md:40` v4 historical, intentional |
| FM drift grep (post-edit) | clean (0 hits) |
| `.docx` in active prose grep (post-edit) | clean (0 hits) |
| pytest | **1033 passed / 16 deselected** (unchanged) |
| mypy | **Success: no issues found in 127 source files** |
| ruff check . | clean |

## Out of scope

- `docs/session_logs/**/*.md`, `docs/audits/**/*.md`, `docs/reports/**/*.md` — historical artifacts; correctly preserve doctrine state at authorship.
- `box_migration/parse_job_v2.py`, `box_migration/parse_job_v3.py` — preserved per Op Stds v11 §14.
- Doctrine *content* itself — lives in blueprint repo; this PR only updates *references to* doctrine in execution-repo code/prose.
- Substantive code changes — none. No imports moved, no signatures changed, no test behavior changed.
- `shared/quarantine.py` Mail.app rule reference (line 3-4 mentions "routed by Mail.app rule") — Mail.app rules deprecated in v11 §31 (polling daemons are canonical). Content change out of scope; only the FM version label was updated.
- Doctrine `version` fields in blueprint repo files — separate concern; bumps follow `prompts/scaffold/doctrine-revision.md` in blueprint.

## Sequencing context

This PR completes the 2026-05-22 cascade audit's outstanding "execution-side reconciliation" item (`audits/2026-05-21_cascade-verification.md` Section H). The audit named the gap but the execution-side cleanup never landed during the cascade's original turnaround. Today's its-blueprint PRs (#1, #2, #3) restructured the planning side; this is the parallel execution-side cleanup.

After this lands, execution-repo prose is consistent with the canonical doctrine state. Future doctrine v-bumps will land via the `prompts/scaffold/doctrine-revision.md` procedure (blueprint side) followed by a parallel cleanup PR like this one on the execution side.

## Merge verification quartet output

TBD post-merge — filled in after the four-part discipline runs.

```
# Step 1: PR-state triplet
gh pr view <num> --json mergedAt,mergeCommit,state

# Step 2: capture merge SHA
MERGE_SHA=$(gh pr view <num> --json mergeCommit --jq '.mergeCommit.oid')

# Step 3: wait for push:main workflow

# Step 4: assert push:main success
```
