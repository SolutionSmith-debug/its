---
type: audit
date: 2026-06-08
status: active
related_prs: [185, 191]
workstream: safety-portal
tags: [doc-reconciliation, op-stds-v18, cross-repo-drift, mirror-activation]
---

# Doc-reconciliation audit (2026-06-08) — PROPOSE-ONLY

Run by `doc-reconciliation-auditor` at the close of the 2026-06-08 Safety Portal
mirror-activation session. The agent is write-blocked by its `PreToolUse` hook
(it cannot write even its own findings file), so it returned the report inline;
this file persists it verbatim-in-substance. **PROPOSE-ONLY — operator applies.**

**Baseline:** `~/its` working-tree HEAD `f3ad814` · `~/its` `origin/main` `d393ee6`
· `~/its-blueprint` HEAD `0e85a1a` (clean) · **local** manifest recorded Op Stds
**v16** (stale); `origin/main` manifest records Op Stds **v18** / Handover **v9** / FM **v11**.

**Headline:** the biggest "finding" is a **stale local checkout**, not unreconciled
drift. PR #191 (`d393ee6`) already landed the entire v16→v18 reconciliation on
`origin/main` (2026-06-07). At audit time the local `~/its` tree was one commit behind
at `f3ad814`, so the local CLAUDE.md / README / manifest were all still pre-#191 (v16).
**Fix for finding #1 is a `git pull` / merge of #191, not 16 edits** — and that has since
been done on branch `chore/2026-06-08-mirror-activation-docs`.

---

## Mechanical drift (script-backed)

`scripts/check_doctrine_drift.py` against the **stale local v16 manifest** reported only
2 hits, both false alarms (FM v9 provenance refs at `CLAUDE.md:64,80` — the `(reframed FM
v9, audit F13)` Invariant-2 Layer-5 history, correctly preserved by #191; they escape the
`_HIST_MARKERS` proximity window).

Against the **canonical v18 facts**, the real drift the local checker was blind to — **all
already fixed on `origin/main` by #191**, present locally only because the checkout was stale:

```
CLAUDE.md: 13× "Op Stds v16"/"Operational Standards v16"/"Handover Plan v8" → canonical v18/v9
README.md:  3× "Op Stds v16"/"Operational Standards v16"                     → canonical v18
docs/doctrine_manifest.yaml: operational_standards.current 16                 → 18
```
KEEP (correct history, not drift): `CLAUDE.md:64,80` "(reframed FM v9, audit F13)";
`CLAUDE.md:131` "the Op Stds v16 / FM v11 reframe"; `CLAUDE.md:50` "Op Stds v4 … superseded".

- **[M1 — v16→v18 + Handover v8→v9] severity HIGH (already-fixed-upstream; stale checkout).**
  Fix: `git pull`/merge #191 — do NOT hand-edit. **Status: DONE** (branch carries #191 + this
  session's daemon-state edits).
- **[M2 stale tech-debt] none.** The PR-H CodeQL item correctly flipped to `[CLOSED 2026-06-08]`
  with four-part-verify evidence; the 3 new `[OPEN 2026-06-08]` items describe new gaps.
- **[M4 sheet-ID] none.** `SHEET_CONFIG`/`SHEET_DAEMON_HEALTH` match the manifest.

---

## Semantic drift (opus judgment)

- **[high] Stale local checkout** — root cause of the entire "v16→v18 follow-up". Reconciled on
  `origin/main` by #191, merely not pulled. `git log f3ad814..origin/main` = exactly one commit
  (#191). **Status: reconciled** on the branch.
- **[high] This session's CLAUDE.md daemon-state edits are AHEAD of both origin/main and the
  blueprint's `last_verified_against`.** Working tree now asserts `portal_poll.py` + the intake
  portal-marker branch + `weekly_generate`/`weekly_send`/`weekly_send_poll` are "built + live-validated
  (2026-06-08 mirror)"; `origin/main:CLAUDE.md` still said "PLANNED, not built", and blueprint
  `safety-portal/mission.md` (v3, `last_verified_against: f3ad814`, 2026-06-07) still calls Phase 6/7
  "live-inert until activation". The mirror-activation claim is real + code-backed; it postdates the
  blueprint anchor. **Fix:** (a) **exec** — commit this session's edits (DONE on the branch); (b)
  **blueprint (separate doctrine session)** — bump safety-portal + safety-reports mission
  `last_verified_against` past f3ad814 to record the 2026-06-08 mirror live-validation; flip
  portal_poll PLANNED→as-built.
- **[high] Blueprint `workstreams/README.md:9` lists safety-portal as "planning only"** — contradicts
  mission v3 + a mirror deployment. **Blueprint-side fix** for the doctrine session.
- **[med] `safety_reports/portal_poll.py` (now-live entrypoint) absent from the checker's §42
  `ENTRYPOINTS`** (`scripts/check_doctrine_drift.py:56-63`) and has 0/4 of the §42 RST headings.
  **Fix (propose):** add `"safety_reports/portal_poll.py"` to `ENTRYPOINTS`; retrofit the 4 headings
  opportunistically (§14) when portal_poll is next touched.
- **[low] `.claude/agents/ops-stds-enforcer.md:3,8,67,92` cites "Operational Standards v13"** (5
  versions stale; out of the mechanical checker's scan scope; partially self-correcting since the
  agent is told to read the doctrine at runtime). **Fix (propose):** bump v13→v18.
- **[verify-required] Model strings** — `CLAUDE.md:231-232` (`claude-sonnet-4-6`,
  `claude-haiku-4-5-20251001`, `claude-opus-4-7`) are manifest `verify_required: true`. The agent
  does NOT bless/bump them — **FLAGGED for the operator** to verify vs current Anthropic docs
  (`claude-opus-4-7` is one behind 4-8).

---

## Confirmed clean (NOT drift — do not "fix")

CLAUDE.md:64/80 + :131 FM-v9 / "v16 reframe" provenance (correct history); CLAUDE.md:50 "Op Stds v4
superseded"; PR-H item → CLOSED (correct self-closure w/ four-part evidence); the 3 new tech-debt
items (correctly OPEN, new gaps); `portal_poll.py` "built + live-validated" (code-backed — module
present, launchd plist exists, live revocation + 1042-recovery recorded); `email_triage` /
`purchase_orders` / `subcontracts` / `ai_employee_capabilities` (correctly-unbuilt planning-only);
sheet IDs (M4); FM version (v11).

---

## Summary — queued follow-ups for the operator / blueprint session

1. **Op Stds v16→v18 exec sync was already done by PR #191** — local was stale; reconciled on
   `chore/2026-06-08-mirror-activation-docs`. Do NOT hand-re-do the version cites.
2. **This session's daemon-state edits** are real forward drift to commit (DONE) + for the blueprint
   to absorb (bump safety-portal/safety-reports mission `last_verified_against` past f3ad814; flip
   portal_poll PLANNED→as-built).
3. **Blueprint `workstreams/README.md` mislabels safety-portal "planning only"** — blueprint-side fix.
4. **Low-priority exec follow-ups:** `.claude/agents/ops-stds-enforcer.md` cites v13 (→ v18); add
   `safety_reports/portal_poll.py` to `scripts/check_doctrine_drift.py` `ENTRYPOINTS`.
5. **Model strings** (`claude-opus-4-7` etc.) — operator to verify currency; not blessed/bumped here.

Nothing was applied by the auditor — propose-only; the write hook blocked every mutation including
this file (persisted by the session-close on the operator's behalf).
