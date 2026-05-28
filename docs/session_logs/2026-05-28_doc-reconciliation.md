---
type: session_log
date: 2026-05-28
status: closed
workstream: docs
related_prs: [101, 103, 106]
tags: [op-stds-v13, doctrine-version-drift, canonical-manifest, doc-reconciliation-auditor, section-42, cross-repo-drift, verify-before-fix]
---

# 2026-05-28 — Doc-reconciliation: doctrine-version drift + canonical manifest + reconciliation agent

PRs (all **four-part PR-landed verify clean** — state=MERGED, mergedAt non-null, mergeCommit.oid present, main-branch CI on merge commit = SUCCESS):
- [#101](https://github.com/SolutionSmith-debug/its/pull/101) — squash-merged 2026-05-28T23:33:17Z, `4b145b8`. Task 1 (doctrine-version drift correction).
- [#103](https://github.com/SolutionSmith-debug/its/pull/103) — squash-merged 2026-05-28T23:33:23Z, `9d6378c`. Task 2 (canonical-doctrine manifest).
- [#106](https://github.com/SolutionSmith-debug/its/pull/106) — squash-merged 2026-05-28T23:39:06Z, `feba074`. Task 3+3.5+4 (reconciliation agent + registration + guard wiring). Superseded #105 (auto-closed — see Out-of-scope notes).
- Blueprint [its-blueprint#17](https://github.com/SolutionSmith-debug/its-blueprint/pull/17), `da6adff` — companion Task-1 blueprint half (see `session-logs/2026-05-28_doc-reconciliation.md` there).

## Purpose

Make the execution repo consistent with already-canonical blueprint doctrine (Op Stds **v13**, FM v8) and build tooling to keep it that way. A forensic pass found doctrine had moved two versions while ~93 execution-repo references and the §42 discipline lagged, invisibly — root cause: cross-repo coupling with no automated divergence check. Does not invent doctrine; reconciles to it.

## Pre-flight findings (verify-before-fix)

Every claim re-confirmed against the real HEADs before editing:
- **Stated bases were stale.** The resume note cited `~/its` `8c09a6b` / blueprint `133afb8`; the real HEADs were `c5cc456` / `ac9e44e` (two/one merges further along — #97/#102 session-logs). Re-anchored against the real HEADs and rebased #101 onto post-portal main, preserving the parallel session's portal prose and bumping only current-doctrine refs.
- **`[jwt]` already fixed.** `pyproject.toml:18` = `boxsdk>=3.10.0,<4.0.0` at HEAD; PR #96 closed it. tech_debt was stale → flipped to `[CLOSED 2026-05-28]`.
- **§42 effectively un-applied, not "partially."** 0 of 22 `shared/*` modules + 0 of 6 entrypoints carried the four headings — including the doctrine's own worked `state_io.py` example, never landed in the real file.
- **"five workstreams" → 6.** `safety_portal` (mission v1, 2026-05-25) post-dates the figure; `workstreams/README.md` still omits it.
- **Agent-availability gap.** CC rooted at `/Users/sethsmith`, so repo-local `.claude/agents/` are unreachable — close-out done manually (same as the portal session).

## Code changes

- **#101:** `CLAUDE.md` 12 current-doctrine `Op Stds v11`→v13 (historical refs left) + governing-versions note; `docs/tech_debt.md` `[jwt]` OPEN→CLOSED (credit #96); `shared/untrusted_content.py` §42 docstring retrofit (docstring only, behavior unchanged); new `docs/reports/2026-05-28_section42_compliance_inventory.md`.
- **#103:** `docs/doctrine_manifest.yaml` — machine-readable canonical facts; execution-resident + blueprint-derived (CI never checks out the blueprint, so the checker's facts must be self-contained here, with per-fact provenance pointers upstream).
- **#106:** `.claude/agents/doc-reconciliation-auditor.md` (propose-only, opus, mechanical+semantic tiers); `.claude/hooks/block-doc-reconciliation-write.sh` (write-block backstop) + `tests/test_hook_block_doc_reconciliation_write.py` (22 cases); `scripts/check_doctrine_drift.py` (deterministic mechanical tier) + `tests/test_check_doctrine_drift.py`; `CLAUDE.md` `## Agents` section + session-close invocation line; `docs/operations/doc_conventions.md` drift-guard reference; `docs/audits/2026-05-28_doc-reconciliation.md` (self-test findings).

## Verification

- pytest: 1088 passed / 16 deselected / 0 failed
- mypy: 0 errors / 134 source files
- ruff: clean
- doc-conventions lint: exit 0 · regen_doc_indexes --check: exit 0
- main-branch CI on merge commits: SUCCESS (#101 `4b145b8` — re-run after a concurrency-cancel; #103 `9d6378c`; #106 `feba074`)

## Live smoke

N/A — docs + tooling only; no live-tenant surface touched. The mechanical checker was exercised against HEAD (`python -m scripts.check_doctrine_drift`): 15 real drift (12 CLAUDE.md → #101; 3 README → follow-on), 0 false positives.

## Out-of-scope notes (deliberately deferred — operator follow-ons)

- README.md `Op Stds v11` (3 refs) → v13. Out of #101's CLAUDE.md scope.
- `.claude/agents/ops-stds-enforcer.md` hardcodes v11/§41, unaware of §42 → bump + add §42; consider widening checker M1 scope to `.claude/agents/`.
- blueprint `workstreams/README.md` — add the safety-portal row.
- Document the other 7 agents in CLAUDE.md (flagged in the new `## Agents` section).
- Verify the three model strings against current Anthropic docs (manifest flags them verify-required).
- §42 retrofit of the 27 remaining modules — opportunistic per §14, NOT a sweep.

## Sequencing context

Parallel session to the portal-pivot/HIGH-2 work (§G8); landed second + rebased onto its merges, per the coordination plan. The new agent is the heavy half of the cross-repo drift guard whose light half (session-close-maintainer check + doc_conventions note) the portal session landed in #100.

## Operator-side actions remaining

- The follow-ons above (none block; each is a small PR).
- A future in-repo `session-close-maintainer` run (CC rooted at `~/its`) could refresh info-gap §5 with the agent-availability trap if not already present.

## Merge verification quartet output

```
#101 — four-part verify clean: state=MERGED / mergedAt 2026-05-28T23:33:17Z / mergeCommit 4b145b8 / main CI (ci+CodeQL) SUCCESS
#103 — four-part verify clean: state=MERGED / mergedAt 2026-05-28T23:33:23Z / mergeCommit 9d6378c / main CI (ci+CodeQL) SUCCESS
#106 — four-part verify clean: state=MERGED / mergedAt 2026-05-28T23:39:06Z / mergeCommit feba074 / main CI (ci+CodeQL) SUCCESS
```

### Stacked-PR + CI lessons (for the next session)
- Squash-merging a base PR with `--delete-branch` **auto-closes** the PR stacked on it (here #105, stacked on #103). Recovery: rebase the child onto main (the base's squashed commit is dropped as already-applied) and open a fresh PR (#106).
- Rapid sequential squash-merges trip `concurrency: cancel-in-progress` (the LOW-3 config) — the intermediate merge commit's main `ci` run is cancelled by the next merge's. Re-run the cancelled `ci` on the intermediate commit (#101 `4b145b8`) for a clean four-part leg-4.
