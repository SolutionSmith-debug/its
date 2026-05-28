---
type: audit
date: 2026-05-28
status: active
related_prs: [101, 103, 105]
workstream: null
tags: [doc-reconciliation, op-stds-v13, cross-repo-drift, self-test]
---

# Doc-reconciliation audit (2026-05-28) — agent self-test

First run of the `doc-reconciliation-auditor` (PROPOSE-ONLY) against current HEAD,
captured as its self-test so the operator can see the **false-positive rate before
trusting it**. Baseline: `~/its` `origin/main` `c5cc456` · `~/its-blueprint`
`origin/main` `ac9e44e` · `docs/doctrine_manifest.yaml` `manifest_version 1`.

Every finding below was **independently adversarially verified** (8 skeptic passes,
each reading the actual files and trying to *refute* the classification). Result:
**0 false positives, 0 false negatives** — all 4 drift calls confirmed real, all 4
clean calls confirmed clean.

> This is a propose-only report. Nothing here was applied by the agent. Items
> marked "fixed by PR #N (this series)" are already in flight; items marked
> "follow-on" are proposed for the operator.

## Mechanical drift (script-backed — `scripts/check_doctrine_drift.py`)

### [M1] Op Stds v11 → v13 in CLAUDE.md (12 refs) — fixed by PR #101 (this series)
- **Evidence:** `CLAUDE.md` lines 26, 57, 59, 71, 122, 140, 142, 143, 147, 163, 194, 318 cite `Op Stds v11` / `Operational Standards v11` present-tense as canonical (e.g. L26 "Canonical docs: … Operational Standards v11"; L318 "If something here contradicts … Operational Standards v11), the planning project wins").
- **Canonical:** `blueprint/doctrine/operational-standards.md` frontmatter `version: 13, status: canonical` (manifest `doctrine_versions.operational_standards.current: 13`).
- **Fix:** bump all 12 to v13 (section numbers §3.1/§18/§23.3/§31–§34 carry forward under v13). **Applied in PR #101.**
- **Accept:** `grep -c "Op Stds v11\|Operational Standards v11" CLAUDE.md` == 0; gate green.

### [M1] Op Stds v11 in README.md (3 refs) — follow-on
- **Evidence:** `README.md` lines 13, 68, 102 cite `Op Stds v11`. (L68 pairs a *correct* `Foundation Mission v8` cite — only the Op Stds label is stale, confirming this is genuine version drift not a placeholder.)
- **Fix:** bump to v13. **Out of PR #101's scope (CLAUDE.md only); propose a small follow-on README sweep.**
- **Accept:** no `Op Stds v11` in `README.md`.

### [M2] stale tech-debt — none
- No `[OPEN]` entry asserts its own completion. (The `[jwt]` entry is closed in PR #101 by status flip + evidence, not caught here because its body never *claimed* done — that is the subtler code-says-fixed case, handled by PR #101 directly.)

### [M4] sheet-ID mismatch — none (confirmed clean)
- `shared/sheet_ids.py` `SHEET_CONFIG=3072320166907780`, `SHEET_DAEMON_HEALTH=4529351700729732` (12 cols) match the manifest verbatim.

## Semantic drift (opus-tier judgment — confidence + two locations)

### [HIGH] `ops-stds-enforcer.md` hardcodes "Operational Standards v11" + treats §41 as terminal — follow-on
- **Code/doc says:** `.claude/agents/ops-stds-enforcer.md` — description "review a diff … against Operational Standards v11 … §41 (version-bump verification)"; body "You are the Operational Standards v11 enforcer"; output template "Op Stds v11 review:". Highest section enumerated: §41.
- **Doctrine says:** Op Stds is **v13**; v13 added **§42** (code-level self-documentation).
- **Assessment:** real drift — the enforcer agent's static identity is two major versions behind and is unaware of §42. Mitigated only partially by its own "read frontmatter, it changes" instruction. Beyond the mechanical M1 scope (which scans CLAUDE.md/README/docs/operations, not `.claude/agents/`).
- **Fix (follow-on):** bump the agent's v11→v13 references, add §42 to its clause list, and consider widening `check_doctrine_drift.py` M1 scope to include `.claude/agents/*.md`.

### [MED] blueprint `workstreams/README.md` omits safety-portal — follow-on (blueprint-side)
- **Code/doc says:** `blueprint/workstreams/README.md` table lists **5** workstreams.
- **Reality says:** `blueprint/workstreams/safety-portal/` exists with `mission.md` (v1, 2026-05-25, `status: canonical`) + `brief.md` — violating the README's own "each subdirectory is one workstream" convention.
- **Fix (follow-on):** add the safety-portal row. (The manifest already records 6 slugs and flags this; the exec-side info-gap §8 "6 workstreams" correction is in PR #17.)

### Model currency — flagged for verification, NOT asserted
- The manifest records `claude-sonnet-4-6` / `claude-haiku-4-5-20251001` / `claude-opus-4-7` as `verify_required`. The agent **flags these for the operator to verify against current Anthropic docs** and does not bless or bump them. (No drift asserted.)

## Confirmed clean — verified NOT drift (do not "fix")

- **CLAUDE.md:40 "Earlier framing in Op Stds v4 … is superseded"** — historical/past-tense; matches canonical `foundation-mission.md` + `operational-standards.md` verbatim. Correctly skipped by the proximity guard.
- **Workstreams email_triage / purchase_orders / subcontracts / ai_employee_capabilities** — planning-only (canonical blueprint missions, Phase 2/3, no exec code yet). Correctly-unbuilt, not drift. (safety_portal + safety_reports are acknowledged in the exec repo.)
- **Portal-pivot superseded-model** — fully reconciled by PRs #98/#99/#100: every former-PDF-email doc now carries a portal-canonical note, a `[SUPERSEDED]` marker, or describes the live pipeline the portal feeds. No residual contradiction.
- **Sheet IDs** — match the manifest (see M4).

## Coverage (informational — not drift)

- **§42 docstrings:** 21 `shared/*` modules + 6 workstream entrypoints carry 0/4 of the four headings (the lone compliant module, `untrusted_content.py`, is retrofitted in PR #101). Retrofit is **opportunistic per §14** — not a sweep. See `docs/reports/2026-05-28_section42_compliance_inventory.md`.
- **Doctrine §42 example vs reality:** Op Stds v13 §42's worked `state_io.py` example was never landed in the real `shared/state_io.py` (0/4). Land it the next time that module is touched.

## Totals & false-positive rate

| Class | Count |
|-------|-------|
| Mechanical drift (real) | 15 (12 CLAUDE.md → #101; 3 README → follow-on) |
| Semantic drift (real) | 2 (ops-stds-enforcer; blueprint README) + 1 model-currency *flag* |
| Confirmed clean | 4 classes |
| Coverage (informational) | §42 (27 targets) |
| **False positives** | **0 / 8 verified** |

**Follow-on worklist for the operator** (none are in this 3-PR series' scope):
1. README.md `Op Stds v11` → v13 (3 refs).
2. `.claude/agents/ops-stds-enforcer.md` v11→v13 + add §42; widen M1 scope to `.claude/agents/`.
3. blueprint `workstreams/README.md` — add the safety-portal row.
4. Document the other 7 agents in CLAUDE.md (already flagged in the new `## Agents` section).
5. Verify the three model strings against current Anthropic docs.
