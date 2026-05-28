---
type: session_log
date: 2026-05-28
status: closed
workstream: null
related_prs: [92, 93]
tags: [subagents, cc-tooling, hooks, codeql, security, brief-drift, verify-before-fix]
---

# 2026-05-28 — Subagent hardening (codeql propose-only + delegation/path/token fixes)

PRs: [#93](https://github.com/SolutionSmith-debug/its/pull/93) — squash-merged 2026-05-28T20:14:53Z, merge commit `43d7ba2adef17c883c3756608c94cf89ee7cb064`. [#92](https://github.com/SolutionSmith-debug/its/pull/92) — squash-merged 2026-05-28T20:04:30Z, merge commit `7fc8ace5a1a2e1d7de1c29763be888f2033dd577`. Both **four-part PR-landed verify clean** (state=MERGED, mergedAt non-null, mergeCommit.oid present, main-branch CI on merge commit = SUCCESS).

Hardening pass over the 8 ITS subagents (`.claude/agents/`, landed 2026-05-27 via #90 + validation-fix #91), driven by a v2 source-evaluation brief. Split into two PRs: the security-critical `codeql-fp-triager` rework isolated in #93; the delegation / path / token correctness fixes in #92.

## Purpose

The source evaluation flagged that `codeql-fp-triager` auto-dismissed CodeQL alerts on the weakest model (`haiku`) and was advertised as schedulable unattended — a misclassification could silently dismiss a real alert, contradicting the "failures observable, never silent / human-in-loop" design principle. Three other agents had a dead delegation, a frontmatter/body path contradiction, and token/path-handling gaps. Goal: close all of these as two narrow PRs off `main`, each four-part verified, with structural (hook-backed) enforcement where the control is security-relevant.

## Pre-flight findings (verify-before-fix)

The brief was treated as claims to verify, not ground truth. Several drifted:

- **The operator's opening `agents/initial-eight` commit+push command was moot.** The 8 agents were already on `main` (PR #90 + #91). The working tree was clean, so `git add` staged nothing and the `&&`-chained `git commit` would have aborted with "nothing to commit" — the push never fires. No empty branch created.
- **Op Stds is `v13`, not v12 (brief) or v11 (`CLAUDE.md` / memory).** Live `doctrine/operational-standards.md` frontmatter: `version: 13` (supersedes v12, last_verified 2026-05-25).
- **Brief item C2 dropped — premise false twice over.** (1) Every section the agents cite — §3.1, §23, §30, §38 (Local Agent Guardrails), §41 (GitHub Actions Version-Bump Discipline) — matches live v13; nothing renumbered. (2) `CONVENTIONS.md`'s stable-anchor rule governs *markdown cross-reference links* ("never link to a versioned filename"), not inline `§N` prose citations. Churning correct citations into anchors would be the speculative refactor §14 forbids.
- **Latent YAML defect found.** `sdk-integration-test-scaffold` and `smartsheet-rest-fallback` descriptions contained a `": "` that breaks strict YAML frontmatter parsing (the other 6 agents parse cleanly). PR #91's "validation pass" evidently never strict-parsed frontmatter. Both files were already being edited, so the colon was normalized to an em-dash under lazy-retrofit — all 8 frontmatters now parse.
- **Claude Code capability facts confirmed against the docs** (so the structural enforcement was viable): project subagents *do* support frontmatter `hooks` / `disallowedTools` / `permissionMode`; subagents **cannot spawn subagents** (the Agent tool is unavailable) — confirming `session-close-maintainer`'s delegation was genuinely dead; edited agent `.md` files **don't take effect until a session restart**; `tools`/`disallowedTools` gate by tool *name* only (can't path-scope → hooks are the right tool). The PreToolUse Bash input field `.tool_input.command` and exit-2-blocks are documented; the Edit/Write `.tool_input.file_path` field is **not** documented (relied on the tool schema + unit test; prompt guard stays primary).
- **`curl -H @file` is not a curl feature** (the brief offered it). Only `--config`/`-d`/`-F` read from `@file`; used `--config` to keep the bearer token off `argv`.

## Decisions made during session

- **codeql-fp-triager → propose-only + structural backstop, not model-bump alone.**
  - Alternative considered: keep auto-dismiss, just move `haiku` → `sonnet`.
  - Rationale: the defect is *directional* — using an LLM's ad-hoc read to overrule CodeQL (a purpose-built dataflow analyzer). A stronger model lowers false-negatives but does not make silent dismissal safe. So: propose-only (agent surfaces candidates with quoted evidence; operator applies), Pattern A must affirmatively assert "logs a name, not a value" and escalate when unprovable, `/loop`+`/schedule` removed, `sonnet` for proposal quality, and a hook as the structural guarantee.
- **Per-agent frontmatter `PreToolUse` hook, not a global `settings.json` hook or `disallowedTools`.**
  - Alternative considered: add the dismiss-block to `.claude/settings.json` (like §38 git-guardrails) or drop the tool via `disallowedTools`.
  - Rationale: a global hook would also block the *operator's own* manual dismissals; `disallowedTools` can't distinguish "list alerts" from "dismiss alert" (both are `Bash`/`gh`). A frontmatter hook scoped to the one subagent blocks only that agent's dismissals. `block-codeql-dismiss.sh` greps `code-scanning` + `dismiss`; list/read (GET) pass.
- **session-close-maintainer: split responsibilities, don't "fix" the delegation.**
  - Alternative considered: give it a way to call `session-log-writer`.
  - Rationale: subagents can't spawn subagents — impossible. Rescoped the maintainer to the living docs it actually owns (info-gap, memory-archive §G&lt;N&gt;, tech-debt) and made it FLAG the session log for the operator to run `session-log-writer` directly. Also fixed a latent contradiction in its boundaries ("re-invoke the verifier" — it can't). Added `block-doctrine-write.sh` (Edit|Write, `.tool_input.file_path` under `/doctrine/`) as defense-in-depth on the existing ask-once prompt rule (which stays primary).
- **sdk-integration-test-scaffold: fix the description + add a stop-on-exists guard.** The frontmatter advertised a `tests/integration/` subdir that doesn't exist (body + live layout are flat `tests/`). Added "if the target test exists, STOP — don't overwrite an operator's test."
- **smartsheet-rest-fallback: fail-closed `$CLAUDE_JOB_DIR` + token off `argv`.** Preflight asserts `$CLAUDE_JOB_DIR` set+writable (unset would land payloads at `/payload.json`); bearer token moved from an inline `-H` (visible via `ps`) into a short-lived umask-077 `--config` file, deleted at cleanup. Boundaries reconciled (the config file is the only place the token touches disk).
- **Hooks in `.claude/hooks/`, not `scripts/hooks/`** (the brief said the latter) — matching the established §38 git-guardrails precedent (`block-dangerous-git.sh` + `.claude/settings.json`).
- **Two PRs, not one** — the only security-consequence change (#93) is isolated; #92 is cohesive low-risk prompt/config edits.
- **Did NOT use `gh pr merge --admin` to bypass branch protection on #93.** After #92 merged, main advanced and #93 was "not up to date." Used `gh pr update-branch` (merge main in, no force-push) and waited for CI to re-pass, rather than `--admin` (which would bypass the protection that exists for a reason).

## CI runs

- **#92** main-branch CI on merge commit `7fc8ace`: `ci`/test run [26599137133](https://github.com/SolutionSmith-debug/its/actions/runs/26599137133) = SUCCESS; `CodeQL` run [26599135922](https://github.com/SolutionSmith-debug/its/actions/runs/26599135922) = SUCCESS. PR-build runs green before merge.
- **#93** main-branch CI on merge commit `43d7ba2`: `ci`/test run [26599648745](https://github.com/SolutionSmith-debug/its/actions/runs/26599648745) = SUCCESS; `CodeQL` run [26599647494](https://github.com/SolutionSmith-debug/its/actions/runs/26599647494) = SUCCESS. PR-build runs green on both the original head and the post-`update-branch` head before merge.

## Verification

| Stage | Result |
|-------|--------|
| Hook unit tests (run in CI) | `tests/test_hook_block_codeql_dismiss.py` 6 passed (block dismiss / allow list+read / allow unrelated); `tests/test_hook_block_doctrine_write.py` 5 passed (block doctrine path / allow references+tech-debt). |
| Agent frontmatter parse | All 8 `.claude/agents/*.md` parse under PyYAML (the two `": "` defects fixed). |
| ruff | clean on both new test files. |
| pytest collection | whole suite collects with no errors on both branches. |
| Op Stds v13 review (manual) | both diffs clean — no external-send path, no `shared/*` wrapper (§30 N/A), no version bump (§41 N/A), no sheet creation (§23 N/A), no working logic rewritten for style (§14 respected). |
| Four-part PR-landed verify | #92 clean; #93 clean (see header). |

Note: `.claude/agents/*.md` are **not** CI-linted (`lint_frontmatter.py` skips dot-dirs), so the green `test` job does not by itself validate the agent prose — the new pytest hook tests are the deterministic in-CI gate; frontmatter parse + Op Stds review were done manually.

## Open items handed off

1. **Restart the CC session** so the edited agents load — `codeql-fp-triager` and `session-close-maintainer` (incl. their frontmatter hooks) don't take effect until session start.
2. **Supervised CodeQL pass** to confirm `codeql-fp-triager` actually proposes-not-dismisses and that `block-codeql-dismiss.sh` refuses a dismiss command at runtime. This is deferred — it cannot be exercised in-PR.
3. **Frontmatter-hook runtime firing not yet observed.** Both hooks are schema-valid and their scripts are unit-tested, but that a frontmatter `PreToolUse` hook *fires* for these subagents (and that CC populates `.tool_input.file_path` for Edit/Write) is unconfirmed at runtime. Verify post-restart.
4. **`CLAUDE.md` (execution repo) still references "Operational Standards v11" throughout** — stale vs. live v13. Out of scope here; flag for a docs-pass PR (mechanical text substitution like the 2026-05-24 drift cleanup).
5. **Chat-side: memory #22 update** after these land — final model map + the codeql propose-only / operator-applies / structural-backstop caveat + PR refs (#90/#91 + #92 + #93).

## What was NOT touched

- The other 5 agents (`brief-validator`, `ops-stds-enforcer`, `pr-landed-verifier`, `session-log-writer`) — already sound (read-only or doc/test-scoped, well-bounded). No edits.
- Brief item C2 (citation→anchor churn) — dropped, premise stale (see pre-flight).
- Agent model assignments beyond codeql — all judgment agents were already `sonnet`; `pr-landed-verifier` / `session-log-writer` left as-is (not downgraded — verification-critical).
- `doctrine/*` — no version bump; this is execution-side CC tooling, not doctrine.
- `.claude/settings.json` — the new hooks are wired per-agent in frontmatter, not globally; the existing global git-guardrails hook is untouched.
- Direct edits to `CLAUDE.md` v11 refs — flagged as a separate pass (open item 4).

## Lessons captured to memory

- **[[feedback_verify_ci_diagnosis_before_fix]]** — reinforced again, third hit on the pattern. Previous instances were a stale CI failure cause and a stale API signature inside a brief; this session's drift was a wrong doctrine version (brief said v12, live v13) **and** a false "sections renumbered" premise that justified a whole fix-item (C2). Dropping C2 after verification killed its premise is the rule working as intended. The rule's framing ("briefs occasionally state stale/wrong specifics; pull the actual state and pause") already generalizes from "CI cause" to "any prescriptive brief claim — versions, section numbers, even a `curl` mechanism" — no new memory file warranted.
- **No new memory file written.** The session's durable facts (the propose-only/operator-applies contract; the per-agent-hook-vs-global decision; that `.claude/agents/*.md` aren't CI-linted) live in the agent files, their hook scripts, the PR bodies, and this log — discoverable surfaces that don't need a parallel memory entry. Consistent with the 2026-05-25 state-io precedent.
