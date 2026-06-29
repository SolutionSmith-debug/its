---
type: session_log
date: 2026-06-29
status: closed
workstream: infrastructure
tags: [forensic, lessons-learned, ci-gates, hooks, watchdog, doctrine-drift, retrospective]
related_prs: [330, 342, 343, 347, 348, 350, 351, 352]
---

# Session — Forensic lessons-learned retrospective → 6 standards-hardening PRs

**Focus:** Forensically review the whole ITS narrative (session logs + audits + memory + GitHub/CI),
extract the recurring mistake classes, and convert the worst repeat offenders from *narration*
(memory/session-log/manual-ritual) into *mechanical/checklist enforcement* — respecting §14 preservation.

## Method

Two independent read-only forensic streams, fused: a 26-agent workflow over ~60 exec + 29 blueprint
session logs + 5 audits + the memory ledger + tech-debt (**223 incident records → 18 recurring
mistake classes**, each tagged by current enforcement surface + recurred-despite-codification), and a
GitHub/git ground-truth pass (PRs #71–#329, 400 commits, CI/CodeQL). The findings → a report (D0),
11 tracked issues (D1), then 6 hardening PRs (D2).

## Commits / PRs landed (all merged to main, four-part-verified)

- **#330** `4d2557772` — docs(audit): forensic lessons-learned report (`docs/audits/2026-06-28_forensic-lessons-learned.md`). 18-class taxonomy + 3 GitHub-confirmed classes + validated practices + hardening map + propose-only doctrine appendix.
- **#342** `63b4411c0` — test(hardening): 5 recurrence-guard meta-tests (picklist Send-Status↔REGISTRY parity, capability-gating enrollment, state-write AST, daemon-scaffold RunAtLoad/sys.executable, guard-surface presence).
- **#343** `f069f3bca` — test(hardening): conftest drift-proof live-state write-guard.
- **#347** `2be93f5d5` — feat(ci): doctrine-drift `--strict` is now BLOCKING (M1 version + M4 sheet-id + new M7 citation-resolver; M2 excluded) + `max_section: 49`.
- **#348** `daa388b15` — feat(hooks): `block-stale-cloudflare-deploy.sh` + `warn-live-daemon-tree.sh` + wrangler custom_domain footgun note.
- **#350** `1a1e74d43` — feat(watchdog): Check S `_check_main_branch_ci_green` (CRITICAL on red origin/main ci.yml; fail-safe INFO).
- **#351** `51bb38d50` — docs(claude): CLAUDE.md best-practice rules (verify-before-fix, multi-surface fan-out, stale-checkout preflight, adversarial-review-DoD, observable config).
- **#352** (this log).

## CI / four-part verify

- pytest: **1932 passed / 0 skipped / 44 deselected** (final, post-#346-merge integration in #350)
- mypy: **0 errors / 214 source files**
- ruff: **clean**
- main-branch CI on final merge commit `51bb38d50`: **SUCCESS** (ci.yml test/portal/secrets, now under `--strict`)
- Note: the intermediate merge-commit runs for #347/#348/#350 show `cancelled` — that is the ci.yml
  `concurrency:` group superseding older runs as commits landed in quick succession, **not** a failure;
  the final HEAD run is the authoritative leg-4 (and exactly the class Check S now watches for).

## Decisions made during session

- **Promote narration → mechanical, lightest surface only.** Every new gate is a pure `tests/*.py`
  (auto-collected, no ci.yml edit), a session-scoped hook, or a CLAUDE.md rule. Each verified green on
  main (pins state) AND proven to bite on a synthetic violation. Rejected: heavier process/architecture.
- **`--strict` blocks on M1/M4/M7 only — NOT M2.** M2 (tech_debt self-closure) is a calibration-FP-prone
  heuristic with 2 live false positives on clean main (legitimately-OPEN entries citing adjacent completed
  PRs). Including it would have red-lit main or forced unilateral tech_debt edits. M2 still prints for the
  doc-reconciliation-auditor; it just doesn't gate. (Rejected: "block on all drift-severity findings.")
- **M7 citation-resolver is manifest-driven (`max_section: 49`), not blueprint-read.** CI runs in a fresh
  `~/its` clone with no `~/its-blueprint`, so the §-ceiling must be an in-repo manifest fact. Append-only
  numbering (v18 added §§45-49) makes §1..§49 valid; a citation above it resolves nowhere.
- **conftest is a guard, not a redirect.** Each daemon defines its own `STATE_DIR`/`WATCHDOG_MARKER_DIR`,
  so a redirect list would silently drift (the very failure it'd fix). The full suite was already green under
  a pure write-guard (no test writes live state), so the guard alone closes the class — strictly better than
  the critic's redirect+guard (which reintroduces the drift).
- **Did NOT flip `wrangler.jsonc workers_dev:true`.** The portal is intentionally custom-domain-only;
  re-exposing a public `*.workers.dev` URL for an auth'd portal is an operator decision, not a silent code
  change. Documented the custom_domain footgun inline instead (the real residual gap = "don't be surprised").
- **Watchdog check named "Check S", not "Check N".** "Check N" was already taken (`_check_stuck_wsr_send`);
  mid-session #346 (A4) landed Check Q/R (portal_poll) → a merge conflict in watchdog.py/test_watchdog.py
  resolved by keeping #346's Q/R and relabeling mine S (the function id `_check_main_branch_ci_green` is stable).
- **Dropped the `shared/config_resolve.py` abstraction** (adversarial-critic §14 over-engineering flag) —
  the per-daemon REQUIRED_CONFIG startup-logging is the lighter replacement, deferred (see below).

## Open items handed off

- **Issue #336** (follow-up): implement the per-daemon REQUIRED_CONFIG startup-logging (touches 4 live
  daemons → its own focused PR). The *standard* is now in CLAUDE.md.
- **Issue #341** (Seth): the propose-only doctrine §-additions (narrated-not-enforced principle; §31
  daemon-scaffold DoD; citation-resolves-nowhere; sandbox-masks-prod cutover gate; §43 coverage). Also:
  **bump the GitHub PAT scope to `security_events`** — the code-scanning alerts API currently 403s, so the
  operator/`codeql-fp-triager` cannot enumerate CodeQL alerts via `gh`.
- **Blueprint scaffold-wiring** (deferred): wire `brief-validator` into the brief-authoring scaffolds so
  verification happens at authorship — a planning-repo change under the doctrine-propose-only boundary.
- **P2 issues #337–#339**: orphan reconcile sweep; §14 cross-daemon heartbeat parity test; audit-grep
  blind-spot discipline. **Missing-class #340**: runtime secret/PII-leak backstop test.

## What was NOT touched

- **No blueprint doctrine file edited** — doctrine findings are propose-only (per operator decision);
  `block-doctrine-write.sh` + the Seth-only version-gate respected.
- **No live-daemon behavior changed except watchdog** (Check S is additive + fail-safe). The deploy/worktree
  hooks are CC-session-scoped and never affect the launchd daemons.
- **The 4 working config resolvers** (kill_switch / sheet_capacity / alert_dedupe / picklist_sync) — left
  intact (§14); the config-observability work is logging-only, deferred.
- **The 2 pre-existing M2 tech_debt findings** — left as-is (genuine FPs of the heuristic; excluded from the gate).

## Lessons captured to memory

- `project_forensic-hardening-2026-06-28.md` — the program + the now-active CI gates a future PR can newly fail on.
- `feedback_multi-surface-fan-out.md` — enumerate ALL delivery surfaces before "done" (the #289→#290 / #247→#253 class).
- `feedback_prove-the-control-bites.md` — a green control proves nothing until it RED-lights on a synthetic violation.
- (Also: 3 new CLAUDE.md "What NOT to do" / Operational-conventions rules, landed in #351.)
