---
type: audit
status: draft
date: 2026-06-03
workstream: null
title: "ITS Unifying Forensic Alignment & Drift Audit"
---

# ITS Unifying Forensic Alignment & Drift Audit — 2026-06-03

> PROPOSE-ONLY. Read-and-report. Nothing in this audit was written, edited, executed, or merged. Every remediation pointer is a proposal for an operator, not an action taken.

## 1. Verified canonical state

**Pinned doctrine versions** (confirmed by live blueprint frontmatter AND git tags, `doctrine_tags_confirmed: true`):

| Doctrine | Version | Evidence |
|---|---|---|
| Foundation Mission | **v11** | `doctrine/foundation-mission.md:3` — `version: 11`, `status: canonical` |
| Operational Standards | **v16** | `doctrine/operational-standards.md:3` — `version: 16`, `status: canonical` |
| Handover Plan | **v8** | `doctrine/handover-plan.md:3` — `version: 8` |
| Vision & Roadmap | **v9** | `doctrine/vision-and-roadmap.md:3` — `version: 9` |
| Excellence Roadmap | **v4** | `doctrine/excellence-roadmap.md:3` — `version: 4` |

- **Exec HEAD:** `8eba3ed` — *"docs(session-log): 2026-06-03 Phase 3a/3b + E1 cutover (#154)"*.
- **Blueprint HEAD:** `2bbad18`.
- **Model-as-built (de-1b):** training-bounded co-resolution. Four **FIXED** high-capability-class categories — External Send Gate / secrets-auth / doctrine / code change — plus the **both-rule** (a fault escalates to Tier 3 if **novel OR high-class**). No structural maintenance enforcement layer exists or is to be built (`operational-standards.md:772` — *"No structural maintenance enforcement layer exists, and none is to be built."*). de-1b cascade merge `8c708b9` confirmed ancestor of blueprint HEAD (`git merge-base --is-ancestor 8c708b9 HEAD` → `IS_ANCESTOR`).

**Brief-validator returned: REVISE** — three brief claims were stale in the optimistic-pessimistic direction (D2 "blueprint has no hooks" → false: 4 hooks present; D3 "gitleaks/doctrine-drift not in CI" → false: both present; watchdog "6 of 7 checks" → false: 11 operational). All three are corrected below and entered in the drift register as **brief drift**.

This section is the factual basis the rest of the doc cites back to. Where sources conflict, authority order is: (1) live blueprint frontmatter + git tags, (2) committed exec-main code, (3) planning docs, (4) the brief, (5) operator memory.

## 2. Alignment verdict per axis (A–F)

| Axis | Verdict | Confidence | One-line basis |
|---|---|---|---|
| A — Doctrine internal alignment | MIXED | High | de-1b is the single model across all 5 docs; both invariants unsoftened; only frontmatter-freshness drift (content edit without version bump; stale `last_verified_against`). |
| B — Planning ↔ intentions | MIXED | High | Live roadmaps carry de-1b; rejected-1b survives only in a correctly-dated historical session log; one real drift: info-gap doc cites stale FM v9. |
| C (rollup) | MIXED | High | Send Gate (C1) genuinely enforced; 5 of 6 adversarial layers built (C2); watchdog 11 checks not 6 (C3); daemons live + kill-switch honest (C4/C5). |
| — C1 External Send Gate | CONFIRMED-ALIGNED | High | AST gate real, 8 passed, CI-blocking, bites empirically. |
| — C2 Adversarial Input (Inv 2) | MIXED | High | L1–L5 built & wired; L6 attachment screening PLANNED-only (zero code). |
| — C3 Watchdog checks | DRIFTED | High | 11 operational checks (A,B,C,D,F,G,I,J,K,L,M); CLAUDE.md says "6 of 7". |
| — C4/C5 Daemon liveness & kill-switch | MIXED | High | 6 launchd jobs live, fresh heartbeats; kill-switch fail-open & honestly documented; same watchdog-count doc drift. |
| D — Two-sources-of-truth & tooling | MIXED | High | Symlinks/CI/manifest verified; H1 hook self-presence gap UNFIXED; M1 drift-checker scope gap. |
| E — Citation sweep | DRIFTED | High | One stale current-version claim: `ops-stds-enforcer.md` self-IDs as "Op Stds v13" (canonical v16), checker-blind. |
| F — Orientation/memory layer | MIXED | High | Structural map accurate; HEAD-SHA pointers expected-stale & self-disclosed (lowest authority). |

**Axis A.** All five docs carry the de-1b reframe and the both-rule; every "non-developer-safe enforcement layer" mention is removal/rejection/provenance framing (`foundation-mission.md:16` — *"v11 removes the v10 framing that named a "non-developer-safe enforcement layer" as a pre-cutover build requirement"*). Invariant 1 reads *"Permanent, not time-bounded"* (`foundation-mission.md:48`); Invariant 2 is six-layer with Layer 5 reframed as a tripwire (`foundation-mission.md:92`). Ship-and-leave is now in prose (`vision-and-roadmap.md:26`), resolving the prior tag-only finding. Drift is freshness-only: commit `275e664` (PR #34) edited doctrine BODY across all five docs after the de-1b cascade with no version/`last_verified` bump, and `last_verified_against: 585823d` is an exec-repo SHA not resolvable in the blueprint (`git log -1 585823d` → *"585823d not found as commit"*).

**Axis B.** Live roadmaps describe de-1b (`excellence-roadmap.md:20`, `vision-and-roadmap.md:45` — *"There is no structural "non-developer-safe enforcement layer," and none is required"*). The rejected-1b model survives only in `session-logs/2026-05-29_successor-maintenance-doctrine.md:25` — a correctly-dated historical log explicitly superseded by the 2026-06-01 de-1b cascade. Op Stds §43/§44 + both-rule + four FIXED categories exist verbatim (`operational-standards.md:691`, `:760`). The chat-named `ITS_exec_roadmap_successor-maintenance-doctrine.md`, an "Entity Masters" rename, and a standalone "form-maintenance note" do NOT exist on disk → CANNOT-VERIFY (chat-side only). One real drift: `references/claude-code-info-gap.md:298,:310` cites stale "FM v9" though refreshed this session.

**Axis C1 (External Send Gate).** `tests/test_capability_gating.py` uses static AST import-inspection (`ast.parse` + `ast.walk`, lines 84–97) to forbid send capability in generation scripts and AI capability in send scripts. GATED_SCRIPTS = intake.py, intake_poll.py, weekly_generate.py; SEND_SCRIPTS = weekly_send.py, weekly_send_poll.py (lines 43–78). Ran green: **"8 passed in 0.16s"**. CI runs it BLOCKING (`ci.yml:38-39`, `pytest ... -q`; collected, not deselected). The gate **bites** empirically — feeding `from shared import anthropic_client` through the production helper surfaces `shared.anthropic_client`, failing the `assert needle not in imp` check. Only drift: docstring cites "Foundation Mission v8" (comment-only, FM is v11).

**Axis C2 (Adversarial Input, Invariant 2).** Five of six layers built and wired into the live intake path: L1 header-forgery (`shared/header_forgery.py:153`, called at `intake.py:496`; nuance — DKIM not locally re-validated, a documented scope boundary), L2 `untrusted_content.wrap` (`untrusted_content.py:80`), L3 capability gating (cross-ref C1), L4 Anthropic tool-use JSON schema (`intake.py:677-678`), L5 anomaly_logger tripwire (`anomaly_logger.py:71-73`, correctly framed as detection not defense). **L6 attachment screening is PLANNED-only** — repo-wide grep for `pyclamd|clamav|virustotal|magic-number|yara` returns zero source matches; "Stage 10" is Box upload, not screening. Cross-cutting: all five built modules cite "FM v8" docstrings (stale label, substance current).

**Axis C3 (Watchdog).** `scripts/watchdog.py` CHECKS list (lines 1268–1301) registers **11 operational checks: A,B,C,D,F,G,I,J,K,L,M**. Check E (Anthropic spend) is the only deferred check (needs Admin API key, lines 1294–1300). No Check H (naming artifact). Check C TRACKED_JOBS is populated (5 jobs, lines 131–140), not inert. Check L (`_check_token_write_capability`) is operational, backed by real `verify_write_capability()` (`smartsheet_client.py:1163`). CLAUDE.md:198 says "6 of 7 checks operational" → DRIFTED (under-counts by 5; omits J/K/L/M).

**Axis C4/C5.** Six ITS launchd jobs loaded and installed; the four documented-current daemons present and correctly scheduled (picklist-sync `StartInterval 3600`, watchdog 07:00). Polling daemons have FRESH heartbeats (safety_intake `16:38:53Z`, weekly_send `16:24:12Z` vs now ~16:39Z) with seeded ITS_Daemon_Health row_ids. The 2026-06-01 scheduling redesign is correctly NOT built (grep → zero implementation). Kill-switch is fail-open on all three modes (`kill_switch.py:13-16`) and honestly documented as *"an operator-convenience pause, not a security control ... the External Send Gate (Invariant 1) ... is the security boundary"* (`CLAUDE.md:150-153`). Live ITS_Daemon_Health read → CANNOT-VERIFY-IN-SESSION (no Smartsheet token), but local heartbeat-file evidence proves the row is seeded.

**Axis D.** Blueprint `.claude/agents`+`/hooks` are RELATIVE symlinks into `../../its/.claude/...` (single source of truth; dangle if non-sibling). `block-dangerous-git.sh` wired session-wide via `settings.json:5-9`; the other three guards wired only per-agent. Git carve-outs verified correct. **H1 stands UNFIXED:** grep for `readlink|test -L|test -e` across all four hooks → zero matches; hooks fail-OPEN silently if the symlink dangles. `check_doctrine_drift.py` M1 scope (lines 53–54) walks only CLAUDE.md/README/docs-operations — 18 stale FM citations in `shared/`+`safety_reports/` Python are invisible to it. Brief's "gitleaks/doctrine-drift NOT in CI" claim is STALE: `ci.yml:84-85` runs blocking gitleaks v8.30.1 `--exit-code 1`, and `ci.yml:57-58` runs warn-only `check_doctrine_drift`. Manifest `operational_standards.current:16`/`foundation_mission.current:11` MATCH live frontmatter; meta SHAs are benign-stale-provenance.

**Axis E (Citation sweep).** Highest authority (live blueprint frontmatter): Op Stds v16, FM v11. The mechanical checker's 2 "drift" hits (CLAUDE.md:65, :81 — *"reframed FM v9, audit F13"*) are FALSE POSITIVES (correct history; the `_HIST_MARKERS` regex omits "reframed"). One genuine stale current-version claim: `.claude/agents/ops-stds-enforcer.md` self-IDs as the "Operational Standards **v13**" enforcer at lines 3, 8, 67, 92 — checker-blind because M1 never scans `.claude/agents/`. All `shared/*`/`safety_reports/*` `.py` inline pins are preserved provenance (governed by `docs/tech_debt.md:1201`), NOT drift.

**Axis F (Memory).** Structural pointers all confirmed against live code: SHEET_CONFIG `3072320166907780` (`sheet_ids.py:82`), SHEET_DAEMON_HEALTH `4529351700729732` (`:89`), SHEET_PROJECT_ROUTING `3500842291253124` (`:85`); 9 agents, 4 hooks; Box OAuth User-Auth (not JWT). HEAD-SHA pointers expected-stale: MEMORY.md/session-2026-06-03 say `9ff87ea` (now HEAD~1), tier-a says `46a5c9a` (now HEAD~9) — both self-disclosed point-in-time, lowest authority. No rejected-1b residue in memory. Memory's watchdog "Checks A–M" claim is MORE current than CLAUDE.md.

## 3. Drift register

### Critical
None. No divergence would cause the wrong thing to be built or misrepresent an invariant/customer-facing claim. The most consequential prior gap — the Tier-2 enforcement-layer build requirement — was correctly **superseded by de-1b doctrine** (not silently dropped); see §4.

### High

**H-E1 · `ops-stds-enforcer` agent pinned at Op Stds v13 — three majors behind live v16; blind to §43/§44 and the de-1b reframe.**
- **Where:** `.claude/agents/ops-stds-enforcer.md:3, :8, :67, :92`.
- **Evidence:** `:8` — *"You are the Operational Standards v13 enforcer for ITS."*; `:92` — *"Op Stds v13 is the single source of operational truth for ITS."* grep for `v14/v15/v16` and `§43/§44` in the file → zero matches. Live: `operational-standards.md:3` — `version: 16`. §43 (`operational-standards.md:691`) and §44 (`:729`) exist as load-bearing sections with no clause in this agent.
- **Why it matters:** The agent that gates diffs against Op Stds will under-enforce the two newest disciplines (successor-remediation DoD §43, Tier-2 repair path §44) and is unaware of the training-bounded reframe. The gap is invisible to automated drift detection (M1 does not scan `.claude/agents/`).
- **Severity adjustment:** The original recon finding rated this **High**; verification verdict RECON-1 ruled `severity_verdict: too-high → corrected Medium`, reasoning the agent is PROPOSE-ONLY/advisory (does not structurally gate merges; `:8` instructs re-reading live doctrine each run, which self-corrects loaded clauses). **Adjusted to Medium** per the verification verdict — but NOT dropped: the stale v13 framing is baked into the clause list with no §43/§44 clauses to check, so under-enforcement of the two newest disciplines is real and self-perpetuating.
- **Remediation pointer (NOT executed):** Bump the agent to Op Stds v16, add §43/§44 clauses, and widen `check_doctrine_drift` M1 scope to `.claude/agents/`. Confidence: High on evidence; Med on the downgrade (advisory-vs-gating judgment).

*(Net: after the verification adjustment, this register has no surviving High finding; H-E1 is carried at Medium below as well for the consolidated table. No findings were dropped on verification.)*

### Medium / Low (compact)

| ID | Sev | Title | Where · Evidence | Remediation (NOT executed) | Conf |
|---|---|---|---|---|---|
| DR-E1 | Med | `ops-stds-enforcer` stale at v13, §43/§44-blind (downgraded from High per RECON-1) | `.claude/agents/ops-stds-enforcer.md:8` — *"the Operational Standards v13 enforcer"* | Bump to v16; add §43/§44; widen M1 to `.claude/agents/` | High/Med |
| DR-D1 | Med | Guard hooks have no self-presence check — fail-OPEN if `.claude`/blueprint symlink dangles (H1, unfixed) | `grep readlink|test -L .claude/hooks/*.sh` → no output; blueprint hooks = relative symlinks | Add a session-start readlink integrity assertion that fails CLOSED, or harden worktree_discipline.md precondition | High |
| DR-B1 | Med | info-gap doc cites stale FM v9 in two current-state lists (live FM v11), refreshed this session | `claude-code-info-gap.md:298` — *"reflect FM v9 + Op Stds v16"*; `:310` | Normalize FM v9 → FM v11 | High |
| DR-C2 | Med | Layer 6 attachment screening entirely unbuilt; legacy PDF-email attachments to unified `safety@` would upload unscanned | `intake.py:1278` *"# Stage 10: Box upload."*; repo-wide zero pyclamd/clamav | Confirm legacy-path attachment reachability; track Phase 1.4 Stage-10 gap in tech_debt | High |
| DR-C3 | Low | CLAUDE.md:198 watchdog row says "6 of 7"; live = 11 operational | `CLAUDE.md:198` vs `watchdog.py:1268-1301` | Update row to "11 of 12 (E deferred)", enumerate J/K/L/M | High |
| DR-C3b | Low | watchdog.py module docstring (lines 22–80) also stops at Check I | `watchdog.py:57` (I) vs CHECKS list J/K/L/M | Extend docstring "Checks shipped" block | High |
| DR-A1 | Low | Doctrine body edited (Check-H→C) across 5 docs without version/`last_verified` bump | `git show 275e664 --stat`; `foundation-mission.md:178` | Add `last_content_change`/SHA marker on vN.x absorptions | High |
| DR-A2 | Low | `last_verified_against: 585823d` is an exec-repo SHA unresolvable in blueprint, predates last edit | `operational-standards.md:6`; `git log -1 585823d` → not found | Re-verify against current HEAD; note SHA repo-of-origin | High |
| DR-A3 | Low | Operative cross-doc cites name "Op Stds v11 §N" (version-of-introduction) not current v16 | `handover-plan.md:134`; `vision-and-roadmap.md:73` | Normalize to v16 §N with provenance note | Med |
| DR-C1/C2v | Low | Capability-gating + all 5 Invariant-2 modules cite "FM v8" docstrings (live FM v11) | `test_capability_gating.py:1`; `header_forgery.py:12` | Bump docstring labels to FM v11 | High |
| DR-D3 | Low | M1 drift-checker never scans Python docstrings — 18 stale FM citations invisible | `check_doctrine_drift.py:53-54`; `grep FM v[0-9]+ ... | grep -v v11 | wc -l` → 18 | Extend M1 at "coverage" severity honoring §42/§14 retrofit policy | High |
| DR-D4 | Low | Manifest provenance SHAs (`8c708b9`) stale vs live HEAD `2bbad18` (benign) | `doctrine_manifest.yaml:42`; only post-sync commit `275e664` did not bump version | Refresh `blueprint_head` on any doctrine commit, not just bumps | Med |
| DR-C5 | Low | `kill_switch.py:15` cites "Op Stds v11 §1" (append-only-stable; v16 §1 canonical) | `kill_switch.py:15` vs `CLAUDE.md:153` | Normalize in deferred inline-pin pass | Med |
| DR-F1 | Low | Memory HEAD-SHAs superseded by live HEAD `8eba3ed` (#154); self-disclosed | `MEMORY.md:7` `9ff87ea`; `tier-a-prs:24` `46a5c9a` | Next session-close: refresh to `8eba3ed`, delete tier-a per its own exit condition | High |
| DR-B2 | Low | `smartsheet-handoff.md` v5 pinned to Op Stds v9.3 §23; no Entity-Masters rename | `smartsheet-handoff.md:3` `version: 5` | Leave until Ben's meeting resolves rename; re-pin at next §23 cascade | Med |

### Brief drift (the brief itself was stale, optimistic direction — correct the brief/memory, not the code)

| ID | Title | Evidence that the brief was wrong |
|---|---|---|
| BD-1 | Brief D2 "blueprint has no hooks" | 4 hooks present via relative symlink into exec repo; `readlink ~/its-blueprint/.claude/hooks` → `../../its/.claude/hooks`. Single source of truth, not two copies. |
| BD-2 | Brief D3 "gitleaks + doctrine-drift NOT in CI" | `ci.yml:84-85` blocking gitleaks v8.30.1 `--exit-code 1`; `ci.yml:57-58` warn-only `check_doctrine_drift`. Both present. |
| BD-3 | Brief D1 "subagents are independent copies" | RELATIVE symlinks, not copies (`readlink` → `../../its/.claude/agents`); diff is trivially identical. |
| BD-4 | Brief "watchdog 6 of 7 checks (E deferred)" (CLAUDE.md echoes it) | 11 operational checks A,B,C,D,F,G,I,J,K,L,M; only E deferred. |
| BD-5 | Brief LIVE "missing daemon-health row is a Check F fail-open hazard" | Check F reads ITS_Config `mail_intake.*` + Graph, not ITS_Daemon_Health; the liveness floor is Check C (marker staleness) + heartbeat seeding (`watchdog.py:442-466`). |

## 4. Consolidated open-findings register

One table replacing the four prior-audit lists (from the §5 reconciliation). Verification dropped nothing.

| ID | Title | Severity | Source audit | Status | Evidence |
|---|---|---|---|---|---|
| OPEN-1 | `ops-stds-enforcer` pinned at Op Stds v13 — 3 majors behind v16; unaware of §43/§44 | Medium (was High; RECON-1) | 2026-05-28_doc-reconciliation (DR-HIGH-enforcer) | still-open | `ops-stds-enforcer.md:3,8,67,92`; live `operational-standards.md:3 version:16` |
| OPEN-2 | `session-close-maintainer` has no worktree-hygiene survey line | Medium | 2026-05-29_agent-workflow (M1) | still-open | `grep -ci worktree session-close-maintainer.md` = 0; not in tech_debt |
| OPEN-3 | `settings.json` copied/unguarded; no CI sync check across repos | Low | 2026-05-29_agent-workflow (M2) | still-open | ci.yml has test/secrets/doctrine jobs only |
| OPEN-4 | Trusted-contacts Stage 2 gate may still be inert (`SHEET_TRUSTED_CONTACTS=0`) | Medium | 2026-05-28_forensic-eval (MED-1) | still-open (unverified) | forensic-eval L175; not re-read this pass — fail-safe direction |
| OPEN-5 | README.md Op Stds version refs — unverified-resolved follow-on | Low | 2026-05-28_doc-reconciliation (DR-README) | still-open (unverified) | doc-recon L34-37; README not inspected |
| ~~OPEN-6~~ | ~~CI lacks concurrency group (cancel-in-progress)~~ → **CLOSED-SINCE** (orchestrator-corrected: recon agent marked this "unverified"; direct read refutes it) | — | 2026-05-28_forensic-eval (LOW-3) | closed-since | `ci.yml:8-12` — `concurrency: group: ci-${{ github.ref }}` / `cancel-in-progress: true` IS present (comment: "Cancel superseded in-flight runs for the same ref"). main-branch CI on merge commits unaffected. |
| OPEN-7 | Email Triage attachment screening (Inv 2 Layer 6) unbuilt — surface reassigned, not eliminated | Medium | 2026-05-28_forensic-eval (HIGH-2) | superseded-for-safety-reports, still future for Email Triage | forensic-eval L112; CLAUDE.md §34 Phase 1.4 |
| OPEN-8 | Hygiene: `boxsdk[jwt]` extra; stale FM v6 test-docstring refs | Low | 2026-05-28_forensic-eval (LOW-1/2) | still-open (unverified) | not re-inspected this pass; no correctness impact |
| DR-HIGH-enforcer | (= OPEN-1; tracked under the agent-version drift class) | Medium | doc-reconciliation | still-open | see OPEN-1 |

**Now closed-since / superseded (do not re-open):**

- **Tier-2 enforcement-layer build gap → SUPERSEDED by de-1b.** The v15 cascade named a "non-developer-safe Tier-2 enforcement layer" as a hard pre-cutover build gap; the de-1b cascade (`8c708b9`, ancestor-confirmed) REMOVED that requirement in doctrine body (`operational-standards.md:772`; `foundation-mission.md:16`). Correctly closed-by-supersession, not silently dropped. This was the single load-bearing prior gap.
- **H1 dangling-symlink fail-open → CLOSED-SINCE (detection).** The deferred "assert symlink resolves" check is now built as watchdog **Check M** (`_check_blueprint_guard_symlinks`, `watchdog.py:1227`), WARNing when guard hooks may be silently absent. (Note: the hooks' *own* self-presence check, DR-D1, remains separately unfixed — Check M detects post-hoc, the hooks still fail-open at call time.)
- **OBS-1 cross-repo version drift → CLOSED-SINCE.** CLAUDE.md grep for v11/v13/v14 = 0; v16 appears 15×; manifest matches live frontmatter.
- **gitleaks re-audit recommendation → CLOSED-SINCE.** Superseded by the blocking per-push CI secrets job (`ci.yml:84-85`).
- **G1–G7 successor-maintenance findings → all closed-since** via the v15→v16 cascade (role abstraction, §43, §44, ship-and-leave prose, dangling-symlink guard, frontmatter-integer reconciliation).
- **FE-HIGH1 tag-breakout injection → closed-since** (PRs #95/#96; `untrusted_content.py` neutralizes the closing tag).
- **B1 label note:** "B1" is the brief author's shorthand; the original gap was first flagged in `2026-05-29_successor-maintenance-audit.md`, and the transient 1b-vs-training-bounded divergence is now resolved on-disk.

## 5. Where we stand — customer-presentable, honesty-guarded snapshot

Written so nothing would have to be walked back to a funder.

### Built & working
- **External Send Gate (the genuinely strong control).** No external transmission occurs without explicit human approval, and it is enforced in code, not just documented. Generation processes and send processes are physically separated; a static AST test forbids send capability in generation scripts and AI capability in send scripts. **Verified this audit:** the test suite ran **"8 passed in 0.16s"**, the test runs as a **BLOCKING** CI step (`ci.yml:38-39`, non-zero exit fails the PR), and the gate **bites** — a forbidden import was shown to fail the assertion. (State the strength only to this extent — that is exactly what C1 verified.)
- **Adversarial-input defense, Layers 1–5 built and wired live:** sender allowlist + scope + header-forgery routing (SPF/DKIM/DMARC + Return-Path); untrusted-content XML tagging with tag-breakout neutralization; capability gating (same AST mechanism as the Send Gate); Anthropic tool-use JSON-schema enforcement; anomaly-logging tripwire.
- **Safety-reports intake pipeline** running live in production (polling daemon, 60s cadence) with fresh heartbeats and seeded daemon-health rows.
- **Self-healing & observability:** watchdog with **11 operational checks** (review-queue staleness, open CRITICALs, scheduled-job marker staleness across 5 tracked jobs, reviewer-chain forward scan, mail-intake silent-disable, alert-dedupe sweep, weekly-generate Friday-crash catch-up, circuit-breaker, alert-rate cap, **token write-capability probe**, **blueprint guard-symlink resolution**). Triple-fire CRITICAL alerting (Resend + Sentry + email), gitleaks secret-scanning blocking CI.

### In flight
- **Safety Portal pivot** (form-fill replacing PDF-email) — portal-marker intake branches PLANNED, legacy PDF-email is the documented fallback; the `safety@` mailbox is unified.
- **Doctrine/manifest freshness hygiene** — a content edit landed without a version bump; provenance SHAs lag live HEADs (benign).
- **Agent/checker currency** — the Op-Stds-enforcer reviewer agent and the mechanical drift-checker's scope need a forward pass (Medium).

### Deferred (by design or scheduling)
- **Adversarial-input Layer 6 (attachment malware/structural screening) is NOT built** — no ClamAV/pyclamd, magic-number, PDF-JS, or VirusTotal code exists. For safety reports this is N/A under the portal pivot; the load-bearing surface is reassigned to Email Triage (Phase 1.4). **Honest caveat:** legacy PDF-email attachments can still reach the unified `safety@` mailbox and would currently upload to Box unscanned.
- **Watchdog Check E (Anthropic spend trend)** — deferred pending an Admin API key (operator prerequisite, not a code gap).
- **Trusted-contacts Stage 2 gate** may still be inert pending a sheet-ID backfill (fail-safe direction: quarantines everyone on placeholder).

### Honesty guardrails (explicit)
- **Maintenance-boundary safety is training + the both-rule + graduated co-resolution, NOT structural enforcement.** There is no non-developer-safe enforcement layer, by design (`operational-standards.md:772` — *"No structural maintenance enforcement layer exists, and none is to be built."*). The boundary holds by the trained operator's judgment, the both-rule (novel OR high-class → Tier 3), and co-resolution with the developer on the four FIXED high-class categories.
- **The kill-switch is an operator-convenience pause, NOT a security boundary.** It is fail-open by design — sheet-unreachable / row-missing / invalid-value all resolve to ACTIVE-with-WARN (`kill_switch.py:13-16`). The External Send Gate is the real security boundary (`CLAUDE.md:150-153`).
- **Adversarial-input layers built vs not:** Layers 1–5 are built and wired (C2); Layer 6 (attachment screening) is NOT built anywhere in the repo.
- **Layer 5 (anomaly logging) is a post-hoc detection tripwire, not a defense barrier** — trivially evaded by paraphrase; prevention rests on Layers 2–4 plus the External Send Gate.

### Restated foundation invariants (in-code status)

**Invariant 1 — External Send Gate.** Real quoted text (A2): *"No external transmission without explicit human approval. Permanent, not time-bounded. Earlier framing in Op Stds v4 that described review as a 30-60 day window is superseded."* and *"Two-process model. Generation scripts (which call the Anthropic API) have zero send capability. Send scripts (which transmit) have zero AI step."* (`foundation-mission.md:48`, `:52-54`).
- **Holds in code: YES.** Per C1 — AST capability-gating test passes (8/8), is CI-blocking, and demonstrably fails on a forbidden import. The two-process generation-vs-send split is verified asymmetric and real.

**Invariant 2 — Adversarial Input Handling.** Real quoted text (A2): *"All content originating outside the operating customer tenant is untrusted data. Six-layer defense"* and *"Layer 5 is a low-effort detection tripwire, not a defense layer. It does not prevent a successful prompt injection; it raises a post-hoc signal ... so it must never be relied on as a barrier."* (`foundation-mission.md:68`, `:92`).
- **Holds in code: PARTIALLY (5 of 6 layers).** Per C2 — Layers 1 (header-forgery/allowlist/scope), 2 (untrusted-content tagging), 3 (capability gating), 4 (structured-output JSON schema), and 5 (anomaly tripwire) are built and wired into the live intake path. **Layer 6 (attachment screening) is PLANNED-only — not built.** The residual risk ceiling holds because the actual prevention is Layers 2–4 plus the two-process External Send Gate, exactly as doctrine frames it.
