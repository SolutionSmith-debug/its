---
type: session_log
date: 2026-05-28
status: closed
workstream: infrastructure
related_prs: [110, 111]
tags: [agents, session-close-maintainer, mattpocock-skills, agent-skills, doc-conventions, staleness-guard, parallel-session-overlap, op-stds-42]
---

# 2026-05-28 — Agent-infrastructure follow-ons: session-close-maintainer staleness guard + agent-skills config landed

Follow-on work in the same session as the `alert_dedupe`→`state_io` migration
(see `2026-05-28_alert-dedupe-state-io-migration.md`). Two agent-infrastructure
items + a doc-conventions hygiene fix, all triggered by gaps the migration
session surfaced. Landed as PR #110, PR #111, and this close-out PR.

## Purpose

1. Harden the `session-close-maintainer` agent against the stale-local-`main`
   failure mode that produced a duplicate `§G9` in the migration session.
2. Finish + land the long-pending `## Agent skills` CLAUDE.md WIP + `docs/agents/`
   (the `setup-matt-pocock-skills` supporting config) that had ridden along
   uncommitted across several sessions.
3. Exempt `docs/agents/*` from the doc-conventions frontmatter lint (consequence
   of #2 — those files follow the upstream skills convention, not the ITS schema).

## Pre-flight findings

- **Parallel-session overlap.** A second Claude session was running concurrently
  against both repos, landing the doc-reconciliation cluster (exec #101/#103/#106/
  #107/#109; blueprint #17/#18/#20/#21). This caused `main` to advance repeatedly
  mid-session — surfacing as merge conflicts (resolved) and a post-CI `BEHIND`
  block on #110 that required `gh pr update-branch`. A full local↔origin audit
  confirmed **no overlap damage**: every PR landed, content intact, local `main`
  a clean ancestor of origin in both repos (no divergence, no lost commits).
- **`session-close-maintainer` numbered off stale local `main`.** In the migration
  session the agent (sonnet) appended memory-archive `§G9` computed from local
  `main` (highest was §G8) while origin already had §G9 from the parallel session's
  #18 — forcing a manual renumber to §G10 at merge. It also wrote the section at
  level-1 `#` when the recent convention is level-2 `##`, and refreshed info-gap §8
  against a stale base (collided with #17's §8 refresh).
- **The `## Agent skills` WIP predated this session** (present in the session-start
  `git status`). Investigation showed it is the mattpocock/skills agent-OS config —
  distinct from #109's `## Agents` (subagents) and from Op Stds §37 (the skills
  *install* convention).

## Code changes

### PR #110 — `session-close-maintainer` staleness guard
Merge `b7d51a1cda1b340fdc961255bd2918ff707fd386`, mergedAt 2026-05-29T00:42:32Z. Four-part verify clean.
- Process step 1: `git fetch origin` both repos FIRST, survey against `origin/main`
  (not `HEAD`/local), with an offline-fetch STOP guard.
- Memory archive: compute next `§G<N>` from the FETCHED `origin/main` via a
  **level-agnostic** command (`^#+ §G[0-9]+ ` — catches the older level-1 §G5–G7
  *and* current level-2 §G8–G10; trailing space excludes subsections like §G10.4) +
  explicit level-2 heading template.
- Info-gap §8: reconcile against origin/main (don't clobber a concurrent session's
  refresh); cite current canonical version strings; verify PR numbers via `gh pr view`.
- **Model kept at sonnet** — see Decisions.

### PR #111 — land the `## Agent skills` config WIP
Merge `02cad962865d17fdac23950137f2e1aa2272c53f`, mergedAt 2026-05-29T01:01:25Z. Four-part verify clean.
- `docs/agents/{issue-tracker,triage-labels,domain}.md` committed (the real config
  the planning/engineering skills consume).
- CLAUDE.md `## Agent skills` pointer block placed **adjacent to** the existing
  `## Skills usage (mattpocock/skills, repo-local)` section (not orphaned at file
  end as the raw WIP had it), with a lead-in tying it to the consuming skills.
- Resolves the multi-session uncommitted WIP — working tree now clean.

### This close-out PR — doc-conventions exemption + this log
- `scripts/lint_doc_conventions.py`: new `_is_exempt_agents` exempting `docs/agents/*.md`
  (parallel to the `prompts/` direct-children carve-out) + module-docstring exempt list.
- `docs/operations/doc_conventions.md`: `docs/agents/*.md` added to the "Exempt from
  frontmatter" section with rationale.
- `tests/test_doc_conventions.py`: `test_lint_exempts_docs_agents` (+ scoping guard
  that a non-agents doc still flags).
- This session log.

## Decisions made

- **Model stays sonnet; fix instructions, not the model.** The operator asked
  whether switching the agents to opus would prevent the session's mistakes. It
  would not: the predicted-PR-number slip (#103→#104) was made by the *opus* main
  loop, and the §G collision was sonnet following correct instructions against a
  *stale local view* (it never `git fetch`ed). Both are information-staleness /
  process gaps, not reasoning-capability gaps. The durable fix is the fetch-first /
  origin-derived-numbering instructions (#110), which work on sonnet for free. Only
  `doc-reconciliation-auditor` remains opus (genuine semantic-tier judgment).
- **WIP disposition (a) finish + land** — verify-before-decide. Read both sides;
  confirmed the `## Agent skills` config is distinct + non-duplicative (not #109's
  subagents, not §37's install convention, not the existing `## Skills usage` list).
  The CLAUDE.md block is already a pointer to the real `docs/agents/` files. Not (b)
  trim-to-pointer (it doesn't restate §37) and not (c) discard.
- **`docs/agents/*` exempted rather than retrofitted with ITS frontmatter.** They
  follow the upstream mattpocock format (the installer would overwrite added
  frontmatter); exempting them — exactly like `prompts/` direct children — is the
  principled fix. Verified the exemption is scoped (a non-agents doc without
  frontmatter still flags).
- **Adversarial pre-commit review on #110 paid off.** The first draft of the §G
  count command (`^## §G`) was blind to level-1 headings (§G5–G7) and would have
  re-caused the collision; the reviewer caught it and it was fixed to `^#+ §G[0-9]+ `
  before commit. Captured as a [[feedback_verify_ci_diagnosis_before_fix]]-class win.

## Verification

| Stage | Result |
|-------|--------|
| pytest -q | **1090 passed / 16 deselected** (+1 in this PR: `test_lint_exempts_docs_agents`) |
| mypy . | **0 errors / 134 source files** |
| ruff check . | **clean** |
| doc-conventions lint | warn-only; `docs/agents/` no longer flagged after the exemption |
| #110 main-branch CI on merge `b7d51a1` | **SUCCESS** |
| #111 main-branch CI on merge `02cad96` | **SUCCESS** |

#110 + #111 four-part PR-landed verify clean (state=MERGED, mergedAt non-null,
mergeCommit.oid present, main CI on merge = SUCCESS). This close-out PR's own
four-part verify is reported in-session at landing.

## Out of scope

- The **blueprint's** own `## Agent skills` WIP (`M CLAUDE.md` + untracked `docs/`
  in `~/its-blueprint`) — a separate mirror of the agent-OS config; left untouched.
  Blueprint local `main` was synced to origin (`3408e25`) this session; that WIP is
  preserved for a separate disposition.
- **Op Stds §37 cross-reference** to the new CLAUDE.md `## Agent skills` config —
  flagged as a blueprint-side follow-on, not bundled (doctrine is version-gated).
- The 5 exec + 4 blueprint stale local branches (parallel-session squash-merge
  residue) — operator force-deletes (`git branch -D`); guardrails block CC from `-D`.

## Cross-references

- Migration session log: `docs/session_logs/2026-05-28_alert-dedupe-state-io-migration.md`.
- Parallel doc-reconciliation cluster: exec #101/#103/#106/#107/#109; blueprint #17/#18/#20/#21.
- Doctrine: Op Stds §37 (CC Skills Usage Convention), §42 (self-documentation — the
  #110 agent now embeds its staleness rationale inline).
- Memory: [[feedback_verify_ci_diagnosis_before_fix]] (extended by the §G-count adversarial catch).
