---
type: audit
date: 2026-06-28
status: active
related_prs: []
workstream: infrastructure
tags: [lessons-learned, forensic, standards, hooks, ci, capability-gating, post-incident, retrospective]
---

# Forensic Lessons-Learned → Standards Hardening (2026-06-28)

## Purpose

A forensic, project-wide retrospective: read the full narrative record — ~60 execution session
logs, 29 planning-blueprint session logs, the standing audits, the memory ledger, the tech-debt
register, and the GitHub/CI/commit ground truth — to find the **recurring** engineering mistake
classes, and convert the ones that keep biting into **mechanical guardrails** so future Claude
Code sessions stop repeating them.

This doc is **propose-and-record**. The exec-repo mechanical changes it motivates land as their own
PRs (tracked as issues, see [§7](#7-prioritized-hardening-map)); the **doctrine** proposals in
[§8](#8-doctrine-proposals--propose-only) are **propose-only** — no canonical `operational-standards.md`
file is edited here; Seth applies + version-bumps doctrine.

## Headline verdict

The project's engineering hygiene is genuinely strong — a disciplined **fix-forward** culture (zero
true reverts across 400 commits), a real CI gate (blocking ruff + mypy + pytest + gitleaks + a Worker
pool), a propose-only agent posture, and a documented post-merge verify ritual. **The failure pattern
is not ignorance — it is _narration without enforcement_.** The worst repeat offenders each already
have a "lesson" written down, but the lesson lives in a **memory entry, a session log, or a manual
ritual / advisory agent** that nothing mechanically forces. Every one of the 18 classes below
**recurred _despite_ being documented**. The single highest-leverage move is to **promote each
high-recurrence class from narration to a mechanical or checklist gate** — using the lightest surface
that closes the gap, respecting preservation-over-refactor (Op Stds §14): no speculative architecture.

The dominant repeat offender by a wide margin is **mocks-pass-but-live-fails** (20 instances): unit
tests / mocks / a non-representative runtime go green while the live SDK, CLI, or edge runtime rejects
or silently alters the same call. Its load-bearing control today is an *operator-discretion* live smoke
— which is exactly why it keeps recurring.

## 1. Method

Two independent, read-only forensic streams, then fused:

- **Stream A — narrative (26-agent workflow `wf_0940055f-28f`).** Parallel forensic readers over five
  chronological slices of the exec session logs, the blueprint session logs, the audits, the memory
  ledger (`MEMORY.md` + `memory-archive.md` + `claude-code-info-gap.md`), and a tech-debt sampler — each
  extracting structured *incident records* `{what, root-cause-class, how-caught, blast-radius, lesson,
  already-known, current-enforcement}`. A map agent catalogued the **36 existing enforcement surfaces**.
  A synthesis stage clustered **223 incident records → 18 mistake classes**; a propose stage drafted one
  concrete change per class; an adversarial critic checked for redundancy, over-engineering, and missed
  classes. Each numeric/structural claim the propose stage relied on was re-verified against live HEAD.
- **Stream B — GitHub/git ground truth.** PRs #71–#329, the last 400 commits, CI run history, and the
  CodeQL workflow — the objective record of what actually broke (feature→fix pairs, reverts, CI failures,
  CodeQL findings) that the session logs only narrate.

**Self-check.** The verification pass caught a stale claim _inside the analysis itself_ — Stream A
asserted "`.claude/agents/` is outside the doctrine-drift checker's scope," but `CURRENT_DOCTRINE_DIRS`
in `scripts/check_doctrine_drift.py` already includes it. That is mistake-class #3 (acting on a stale
current-state claim) occurring live, mid-audit — a useful proof that the class is real and that
verify-before-fix is the correct response.

## 2. The recurring mistake classes

`R` = distinct instances found; `★` = recurred *despite* prior codification (the promote-me signal).
Every class below is `★`.

| # | Class | R | Worst blast radius | Lives today (non-load-bearing) | Promote to |
|---|---|---|---|---|---|
| 1 | **mocks-pass-but-live-fails** (SDK / CLI / edge-runtime divergence) | 20 | 100% of WSR sends fail-closed (`SENDING` not in REGISTRY, #247→#253); wrong secret stored silently (keychain argv); external recipients silently dropped | §30 integration tests CI-deselected; operator-discretion live smoke + memory | mechanical pre-merge parity meta-tests; keep live-smoke as DoD |
| 2 | **deploy / D1 footguns** (false-clean signals + deploy-time side effects) | 8 | Total live auth lockout (stale `migrations list` reads a 25-commit-behind folder); `custom_domain:true` silently disabled `*.workers.dev` | memory reference files + manual `cutover_checklist.md` | session-wide deploy-preflight hook + `wrangler.jsonc` config fix |
| 3 | **stale-current-state-claim** (act on a brief / audit / memory premise that drifted) | 16 | Would have inverted kill-switch fail-open; built catch-up against a MAINTENANCE-blocked entrypoint; codified a junk-form bug as permanent doctrine | `brief-validator` agent (manual invoke) | wire verification into brief-authoring; CLAUDE.md "What NOT to do" |
| 4 | **doctrine / version / citation drift** (code, CLAUDE.md, agents, blueprint) | 15 | The doctrine-diff gate (`ops-stds-enforcer`) itself goes 2–5 majors stale-blind; canonical doctrine ships self-contradicting | `check_doctrine_drift.py` **WARN-only** + manual auditor agent | promote the `drift` tier to **blocking** + a citation-resolver leg |
| 5 | **worktree-on-live-daemon-tree** / shared-checkout collisions | 11 | Uncommitted feature code live in the customer pipeline within 60s; publish daemon strands the live tree → blocks all publishes | `worktree_discipline.md` (procedural, memory) | `SessionStart` advisory hook (the doc explicitly reserved this) |
| 6 | **narrated-not-enforced** (doctrine claims a control the code does not deliver) | 9 | Total-host death undetectable (F16); unscanned malware written to the customer's Box (Layer-6); ~0%-adoption mandated conventions | advisory agents + periodic forensic audits | scoped narrated-controls ledger + binding test (downscoped) |
| 7 | **silent fail-open internal paths** (config / error-class / liveness) | 9 | Watchdog blind to a real outage; wrong send schedule used silently; one bad submission ITS_Errors-spams every 60s | mostly **nowhere** (the same fix proposed 3× — C1/C2/A5 — never built) | per-daemon startup REQUIRED_CONFIG logging (lightened) |
| 8 | **tests coupled to / polluting live state** | 8 | Every operator publish red-CI'd the whole suite (live-catalog identity coupling); 2 watchdog staleness detectors silently disabled (#294) | caught at CI then fixed; session-log only | `conftest` autouse live-state redirect + write-guard |
| 9 | **trust-boundary / approval-attestation / input-validation defects** | 8 | Falsified safety data slipped past the human-review gate (data-integrity; send-gated, so not exfiltration); a wrong external recipient could receive a report | regression tests + manual adversarial review | CLAUDE.md DoD bullet (the weaker narration fallback) |
| 10 | **non-atomic / in-place state mutation** (corruption races, double-send, audit clobber) | 7 | An approved WSR emailed to the customer **every 15 minutes** on a transient post-send write failure | `state_io` + a **review-only** CLAUDE.md rule + write-ahead marker | AST test enforcing the state-write rule mechanically |
| 11 | **fail-open guards** (guard layer silently absent in some layout/clone) | 7 | All propose-only security guards silently absent on a CI / clone / non-sibling layout — the worst failure mode for a guard | `worktree_discipline.md` + watchdog Check M (host-only, post-hoc) | `test_guard_surface_present.py` (runs in CI's fresh-clone layout) |
| 12 | **capability-gate enrollment / coverage gaps** | 6 | A new external-send surface (edge Worker / JS shim) the AST gate can't see, silently defeating Invariant 1 | opt-in, Python-AST-only test (`"adding entries is the entire enforcement mechanism"`) | repo-walking enrollment meta-test (opt-out-with-a-reason) |
| 13 | **partial-PR-landed** (completion claimed, live post-merge state unverified) | 6 | Six PRs landed on a red `main`, undetected for multiple days (the three-part verify missed the `push:main` leg) | four-part-verify ritual + `pr-landed-verifier` agent (both manual) | watchdog Check N (mechanical `main`-CI-green) |
| 14 | **AI / agent autonomy & delegation hazards** | 6 | A real CodeQL finding auto-dismissed with no human in loop; a correct committed diff discarded as "phantom" by a wrong-tree reviewer | propose-only posture + agent-scoped hooks (all manual) | keep posture; covered by guard-surface + brief-validator |
| 15 | **launchd / daemon-environment footguns** (bare python, RunAtLoad, no catch-up) | 4 | All interval daemons silently dead after a reboot (#327); half-committed publish — form live in catalog but archive crashed (#241) | §31 convention + session-log lessons | `test_daemon_scaffold.py` (discovery-based) |
| 16 | **orphan / test-artifact accumulation in external SoR** (P2) | 10 | cosmetic now; accumulates across Smartsheet / Box / D1 / git; risks confusing future provisioning | runbook + WARN-on-orphan; ad-hoc name-guarded delete scripts | create-cap-constrained-child-first; periodic name-guarded reconcile |
| 17 | **regex overmatch / audit-grep blind spots** (P2) | 4 | a wrong audit / drift count hides remaining work and reads as "done" | acceptance-lock tests for the one fixed regex | anchor exclusion patterns; treat grep as necessary-not-sufficient |
| 18 | **§14 preservation duplication drift** (P2) | 3 | a heartbeat fix must be applied N times; one copy's missing dependency made it silently inert | nowhere (a `feat/heartbeat-extraction` WIP keeps not landing) | cheap cross-daemon **parity test** — **NOT** speculative extraction |

## 3. GitHub-confirmed classes the cluster under-named

Stream B surfaced three classes the narrative cluster scattered or missed. They belong in the taxonomy:

- **multi-surface / incomplete fan-out** — "did I find _all_ the surfaces?" A single logical change must be
  applied to N independent parallel implementations; fixing only some leaves the rest broken. **Canonical:
  #289 → #290** — job-prefixed PDF naming fixed **1 of 3** delivery surfaces (Box file only); the follow-up
  swept the other two (Smartsheet row attachment + Worker `Content-Disposition`); cf. memory
  `reference_pdf-three-delivery-surfaces`. Also **#200 → #201 → #202** (CSP headers in 3 patches) and
  **#297 → #298 → #299 → #300** (banner clip in 4). Distinct from class #1 (this is completeness, not
  live-vs-mock) — it needs its own named rule.
- **runtime secret / PII leakage into logs / tracebacks** — distinct from gitleaks (which is committed-secret,
  history-only). A live Smartsheet token rendered verbatim into a pytest traceback forced a **key rotation**;
  the Op Stds §40 migration-script PII-logging asymmetry; raw-response logging caught by CodeQL in **#292**.
  **No `test_*secret*` / redaction test exists**; the `error_log` triple-fire path and migration scripts handle
  untrusted data with zero mechanical backstop against leaking it into ITS_Errors / stdout / Sentry.
- **sandbox-masks-production-constraint** — a different axis than #1: the live **sandbox** (`evergreenmirror`)
  is green but **production** differs at/after cutover. Short sandbox sheet-names hid the 50-char cap (**#283**);
  sandbox approver accounts vs the exact production F22 approver emails (a typo silently blocks **every** send);
  bcrypt needs the Paid plan; the domain-flip references. `cutover_checklist.md` exists but the recurring
  *mistake* is unclustered and the checklist is unwired narration.

## 4. The GitHub corroboration (Stream B)

- **fix:feat ≈ 0.38** over 400 commits (**52 `fix(` : 137 `feat(`**, + 68 `docs(`) — ~1 fix per 2.6 features.
  Healthy *fix-forward* discipline, but the density of **same-day** fix bursts is the tell that first
  landings are routinely incomplete.
- **Zero true `git revert`s.** Problems are always patched forward — which is *why* the feature→fix-pair
  signature is so dense.
- **Two fix PRs explicitly name the prior PR they repair** — the cleanest machine-readable partial-landing
  signal: **#253 → #247** ("unbreaks send after #247" — the `SENDING` REGISTRY omission, the worst-blast
  instance in the whole corpus), **#123 → #74** ("(#74 regression)").
- **The Phase-2 form-editor day** shipped #211–#218 then needed **7 same-day fix PRs** (#222 / #224 / #233 /
  #236 / #241 / #242 / #244) — the densest partial-landing burst.
- **CI works.** Of 400 in-window runs: 386 success / 10 failure / 4 cancelled; **`main`-branch CI 100%
  success in-window** (no step-4 break observed). All 10 failures were **pre-merge** and CI *caught* them
  (#261 pytest 3× before green; #273 gitleaks FP 2×). The gate is sound; the gap is **pre-push discipline**
  and **post-merge mechanical verification of older reds** (class #13, which predates the window).
- **CodeQL** is GitHub *default setup* (no `codeql.yml`); the 4 CodeQL failures were all 2026-06-10 on
  #261/#262/#263 — the documented "CodeQL-infra → merge lands `unstable`" mechanic. The **code-scanning
  alerts API returns 403** because the configured PAT lacks `security_events` scope — _the operator cannot
  enumerate open CodeQL alerts via `gh` / the GitHub MCP as configured_ (a finding worth a scope bump).

## 5. Why these recur — narration vs enforcement

The 36 catalogued enforcement surfaces split cleanly:

- **Load-bearing (mechanical, blocks the mistake):** `block-dangerous-git.sh` (session-wide PreToolUse Bash),
  `test_capability_gating.py` (Invariant-1 AST gate), the `ci.yml` `test`/`portal`/`secrets` jobs (ruff +
  blocking-mypy + pytest + vitest-pool-workers + gitleaks), `state_io.py`.
- **Narration (depends on a human remembering to run it):** every memory entry, every session log, the
  `cutover_checklist.md` / `pr_merge_discipline.md` / `worktree_discipline.md` rituals, and **all advisory
  agents** (`brief-validator`, `ops-stds-enforcer`, `pr-landed-verifier`, `doc-reconciliation-auditor`,
  `portal-worker-security-reviewer`). The doctrine-drift, doc-conventions, and doc-index CI steps are
  **WARN-only** — they print and exit 0.

Every one of the 18 classes' "fix" lives in the **narration** column. That is the structural reason they
recur. The hardening in §7 moves the highest-recurrence ones into the **load-bearing** column.

## 6. Validated practices to keep and codify

These demonstrably *worked* and should be standard (not merely praised):

1. **Four-part PR-landed verify** — `state==MERGED` + `mergedAt` + `mergeCommit.oid` + **`main`-branch CI on
   the merge SHA == SUCCESS**. The model example of hardening a re-learned lesson (memory → runbook → dedicated
   agent) after three-part verify missed 6 post-merge reds. _Next step: a mechanical post-merge detector
   (§7 PR-5)._
2. **Mandatory live create→read-back round-trip** against the real CLI/SDK/edge runtime before trusting a fix
   or choosing a Smartsheet column type; **live daemon smoke** before merging new shared infrastructure. The
   only reliable catch for class #1.
3. **Verify-before-fix / brief-validator** — treat every brief, audit, memory artifact, and "landed" claim as a
   hypothesis; treat **zero grep hits as decisive** over confident memory. _Highest leverage: verify at
   authorship, not execution (§7 PR-6)._
4. **Adversarial multi-agent diff review** (attacker / auditor / skeptic / coverage / version-anchor lenses,
   including review of the agents' own tooling) — repeatedly found CRITICALs mocks structurally cannot.
5. **Per-task git worktree + its own venv; `~/its` on `main` between tasks; never edit Python or commit in
   `~/its` mid-cycle** (the daemons execute that tree every ~60s; the strict editable install shadows worktree
   edits without a dedicated venv).
6. **Config-gate inert SoR-path changes** — an unset ITS_Config value resolves to the legacy path, so risky
   new code merges + deploys dormant and activation is a reversible operator flip (landing ≠ activation).
7. **Park BLOCKED vs build against an unseen/assumed external SoR**, with a named unblock condition (the
   2026-05-18 reconcile: 54.9% of real Box folder names fell off the assumed schema).
8. **Name-guarded one-off SDK cleanup scripts** (hard-coded allowlist) for live-SoR teardown; **write-before-act**
   + **append-only** for durable audit records; irreversible ops routed to operator-run.
9. **Reproduce the CI/live failure in its real environment before fixing** (the diagnose loop) — e.g. PATH
   without `security` to reproduce the Linux KeychainError; `wrangler dev` + curl for the immutable-ASSETS 500.
10. **Structural footgun removal over configuration** — redact secrets in `__repr__` (`_SecretToken`), return
    fail-closed inspectable verdict objects instead of raising, named-field dataclasses over positional tuples
    carrying secrets.
11. **Fence cosmetic / presentation code away from the External Send Gate path** with a try/except fallback to
    the live-validated core (weekly_generate's branded cover falls back to plain `merge_pdfs`).
12. **`git pull` to latest `main` before auditing / listing migrations / operating** (a stale `~/its`
    manufactured 16 phantom version edits; separately caused the 2026-06-28 universal lockout).
13. **Prove a control empirically _bites_** — feed a forbidden import through the production helper and watch
    the capability test fail; don't just confirm the control is present.
14. **Drive a lint/type baseline clean, then make it blocking** (mypy 4→0 before becoming a blocking CI step).
15. **Front-loaded capacity / precondition gate that fails to the Review Queue** (`verify_sheet_cap` +
    margin-check before any find-or-create).

## 7. Prioritized hardening map

Each row becomes a tracked GitHub issue and lands as a focused, independently-CI-green PR. All new tests are
pure `tests/*.py` auto-collected by the existing **blocking** `ci.yml` `test` job (no `ci.yml` edit, no new
generation/send script → capability lists unaffected). Each is verified green on current `main` *before* the
PR (it pins today's state) and then proven to **bite** on a synthetic violation (practice #13).

| PR | Change | Surface | Closes class | Effort | Risk |
|---|---|---|---|---|---|
| 1 | `test_every_wsr_send_status_constant_is_registered` (derive `STATUS_*` from `wsr_review`; assert ⊆ `REGISTRY[SHEET_WSR_HUMAN_REVIEW]["Send Status"]`) | `tests/test_picklist_validation.py` | #1 (#247→#253) | small | low-FP, static |
| 1 | repo-walking enrollment meta-test (`*_generate`/`*_send`/`*_poll` must be enrolled or explicitly EXEMPT-with-reason) | `tests/test_capability_gating.py` | #12 | small | name-convention-bound |
| 1 | AST-walk forbidding direct `.write_text`/`.write_bytes` on a `~/its/state/`-tainted path | `tests/test_state_write_discipline.py` (new) | #10 | small | low-FP |
| 1 | discovery-based: every plist `RunAtLoad=true` (or registered catch-up); daemon subprocess uses `sys.executable` | `tests/test_daemon_scaffold.py` (new) | #15 | small | green on current fleet |
| 1 | assert `settings.json` wires `block-dangerous-git.sh`; no `.claude/{hooks,agents}` dangling symlink | `tests/test_guard_surface_present.py` (new) | #11 | small | low-FP |
| 2 | autouse live-state redirect + write-guard (opt-out for `@pytest.mark.integration`; capture real roots before monkeypatch) | `tests/conftest.py` | #8 | small | touches all tests; land alone |
| 3 | `--strict` (block on `drift` severity only) + citation-resolves-nowhere leg; flip `ci.yml` step | `scripts/check_doctrine_drift.py`, `ci.yml`, `tests/test_check_doctrine_drift.py` | #4 | small | drive main clean first |
| 4 | `block-stale-cloudflare-deploy.sh` (load-bearing = `wrangler.jsonc` `workers_dev:true`; git-behind narrowed to prod/`--remote`, fail-open) | `.claude/hooks/`, `settings.json`, `safety_portal/wrangler.jsonc` | #2 | small | CC-session-scoped |
| 4 | `warn-live-daemon-tree.sh` `SessionStart` advisory ("am I in a worktree / is `~/its` on main?") | `.claude/hooks/`, `settings.json` | #5 | small | advisory-only, zero friction |
| 5 | watchdog Check N — `_check_main_branch_ci_green()` scoped to required suites; reuse CRITICAL triple-fire | `scripts/watchdog.py`, `tests/test_watchdog.py` | #13 | small | scope to required suites |
| 6 | CLAUDE.md "What NOT to do": verify-before-fix, multi-surface fan-out, stale-checkout preflight | `CLAUDE.md` | #3, fan-out | trivial | narration (residual) |
| 6 | CLAUDE.md DoD: adversarial review on any trust-boundary/D1-write/send diff | `CLAUDE.md` | #9 | trivial | narration (residual) |
| 6 | wire `brief-validator` into the brief-authoring scaffolds (verify at authorship) | `~/its-blueprint/prompts/scaffold/*` | #3 | small | scaffold edit |
| 6 | per-daemon startup `REQUIRED_CONFIG` logging (resolved value + source; WARN-loud on missing) | daemon entrypoints | #7 | small | **lightened** (no shared abstraction) |
| 6 | memory entries: multi-surface-fan-out, stale-checkout-before-deploy, prove-the-control-bites | auto-memory | #3/#2/#13 | trivial | low |

### Explicitly dropped / downscoped (adversarial critic, §14 preservation)

- ❌ **`shared/config_resolve.py` + 4-daemon retrofit + `get_setting(...) or DEFAULT` AST scan** — the only
  proposal that ships new production architecture onto 4 working, independently-divergent resolvers
  (kill_switch / sheet_capacity / alert_dedupe / picklist_sync). A §14-discouraged refactor with a real
  false-positive surface. **Replaced** by the lighter startup-logging pass (PR-6) that does not touch the
  working resolvers.
- ❌ **`narrated_controls` mini-DSL parser + a `test_model_ids_match_anthropic_client` that ast-greps
  CLAUDE.md prose** — brittle (any prose reformat breaks it) and disproportionate for a one-developer system.
  Scoped to the ~8 named instances and moved to doctrine-propose-only (§8).
- ⚠️ **The two CLAUDE.md narration edits (#3 brief-validator, #9 trust-boundary)** add the least teeth — for
  the two highest-restated classes the package could find no clean mechanical surface and fell back to prose.
  Kept, but #3 is reinforced by the scaffold-wiring *process* change, and both are flagged here as
  **narration-only residuals** (honest accounting).

## 8. Doctrine proposals — PROPOSE-ONLY

Captured for Seth to apply + version-bump (no `operational-standards.md` edit here; the
`block-doctrine-write.sh` guard and the Seth-only version-gate are respected):

1. **Narrated-not-enforced principle (class #6).** Add a doctrine clause: *every doctrinal claim of a built
   **control/guarantee** ships either a binding test/lint that proves it, or a dated "doctrine-only, NOT built"
   exception.* Seed a small (~8-entry, curated) `narrated_controls:` ledger in `docs/doctrine_manifest.yaml`;
   a binding test asserts each entry resolves to code-evidence XOR a dated exception. (Drop the model-ID
   prose-grep + DSL.)
2. **Harden §31 daemon-scaffold definition-of-done (class #15).** Make explicit: `sys.executable` for every
   subprocess; `RunAtLoad=true` on every interval plist (single-host architecture → a reboot must not leave
   daemons dead); an external catch-up detector for every calendar-scheduled job; guard against empty-commit
   crashes in any daemon that does git work.
3. **Citation-resolves-nowhere as a doctrine-blessed gate (class #4).** Bless promoting the
   `check_doctrine_drift` `drift` tier to blocking and adding a leg that resolves every `Op Stds §N` / `FM §N`
   citation against the live doctrine TOC.
4. **Sandbox-masks-production (new class).** Convert the `cutover_checklist.md` from pure narration into a
   gated checklist with a mechanical pre-cutover verification step (esp. the silent F22 approver-email match
   and the sheet-name-length-cap classes).
5. **§43 successor-runbook coverage** — confirm every new Tier-2-reachable capability ships its §43 entry as
   DoD (several recent capabilities are thin here).
6. **Runtime secret/PII-leak backstop (new class).** A doctrine note + a `redact` / no-secret-in-logs test for
   the `error_log` triple-fire path and migration scripts (distinct from gitleaks). Also: **bump the GitHub
   PAT scope to include `security_events`** so the operator/`codeql-fp-triager` can actually enumerate
   CodeQL alerts (currently 403).

## 9. Verification protocol (applies to every PR)

In a per-task `git worktree` off `origin/main` with its **own venv** (`cp -R .venv .venv-wt &&
pip install -e . --no-deps`); never edit Python source or commit in live `~/its`:

1. **Pins, not false-alarm:** `pytest tests/test_<x>.py -q` → green on current `main`.
2. **Proves it bites:** inject a synthetic violation (drop `SENDING`; add a bare-`python` subprocess; a
   `.write_text()` under `state/`; a stale `Op Stds §99` citation) → confirm RED → revert.
3. **Full local gate before push:** `.venv/bin/mypy . && ruff check . && pytest -q` (mypy is blocking in CI;
   pytest+ruff alone misses it).
4. **doctrine-drift `--strict`:** run on `main`, drive clean, *then* flip CI to blocking.
5. **Each PR:** four-part PR-landed verify; up-to-date branch before merge; `git checkout main && git pull`
   between PRs.

## 10. Housekeeping observed

Working-tree debris (operator-run cleanup, separate from this work): `err_issue.txt`, `err_pr.txt` (both
0 bytes), and the stray `err_capture.log` (0 bytes, disclosed by the GitHub forensic agent) — `rm` when
convenient.

---

_Sources: workflow `wf_0940055f-28f` (26 agents, 223 incident records, 18 classes); a concurrent GitHub/git
forensic pass over PRs #71–#329; ~60 exec + 29 blueprint session logs; `docs/audits/`; the memory ledger;
`docs/tech_debt.md`. Doctrine references resolve against Operational Standards v18 / Foundation Mission v11._
