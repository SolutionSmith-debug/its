---
type: reference
status: active
workstream: docs
tags: [roadmap, forward-path, canonical]
---

# ITS Roadmap — the single marching order

**Purpose.** The one top-level forward path for ITS, consolidating the field-ops program, the workstream
missions, the two 2026-07-03 audits (complete-state, unbounded-growth), and tech-debt into a single ordered
list. Detail lives in the sources cited per track — **this file is the index of what's next, not a restatement.**

- Field-ops detail: `project_fieldops-portal-program.md` (auto-memory — the P/R/D/M/CS/S/G series + operator queue).
- Design source: `~/its-blueprint/workstreams/*/mission.md` (planning-layer wins).
- Current-state (what's built): `CLAUDE.md` "What's stubbed vs. real" table.
- Working standards: `docs/HOUSE_REFLEXES.md`.

> **Anti-sprawl contract:** new scope is added HERE (or to tech_debt), at the right track — not in a new
> top-level plan file. `~/.claude/plans/` is scratch; the canonical roadmap is this doc.

---

## Now → next (ordered)

### Track 0 — Finish the Progress Reporting go-live *(in-flight; started ad-hoc, complete it correctly)*
The formal 6-step sequence is `~/.claude/plans/complete-state-audit.md` A2. Status this session: gate ⑥
flipped, plists ⑤ loaded, Box folder ① made, **box-root config ② set (row 44) ✅**. Remaining:
- ② the other config rows: **dup `worker_base_url` under `Workstream=progress_reports`** (fixes the confirmed
  silent rollup-page skip), `progress_reports.progress_send.from_mailbox` (+ confirm the mailbox exists in the
  Graph access policy).
- ③ **§46 re-share** workspace `5988851429730180` to approver identities (else every send fails-closed HELD).
- ④ add `progress_reports` to the `ITS_Review_Queue` Workstream picklist.
- **Wire progress Compile-Now:** generalize `safety_reports/compile_now_poll.py` (§14) to iterate BOTH
  safety + progress week configs — same existing daemon, no new plist. Worktree + tests + §43 runbook.
- Validate: manual `progress_weekly_generate` → weekly progress packet under the Box root + a `WPR_human_review`
  row (no Review-Queue drain, rollup page present). Then clean up this session's smoke test data.

### Track 1 — Close out the field-ops build *(operator, near-done)*
Deploy confirmed (migrations 0028→0037 applied). Remaining operator work:
- **Confirmation flags:** D1 required-content floor · M2 `category:progress` · S5 per-manager Daily-Report
  rollup · **CS4 Part-B keep/revert** (cap.form.submit/request enforcement) · `photo-test-v1` retire-or-canary.
- **Mandatory live smokes:** photo-stays (#454) · **malicious photo RED-lights** on G1 (#452) + v6 pool (#456)
  · daemon-health self-provision (2 new rows) · capacity tripwire on the next weekly cycle · P2.6 manager smoke.
- **Cleanup:** delete orphan branch `feat/cs4b-vestigial-caps` on origin.

### Track 2 — Standing per-job trackers (P7 + M2) *(the largest net-new build)*
The design: **job = folder; weekly sheets = the per-week flow; standing per-job sheets = the running state.**
Build the cumulative, one-per-job (NOT per-week) Smartsheets, one-way-up mirrored from D1 (send/AI-free per §51;
period-split + archive-on-closure; find-or-create + capacity margin-check; never `delete_rows`):
- **P7** — per-job **Hours Log** (mirror `time_entries`), **Equipment Status & Location**, **Materials Status
  & Location**. Extend the `field_ops/fieldops_sync.py` + `shared/active_jobs_writer.py` up-sync (which today
  mirrors job identity only) to write these per-job standing sheets into the job's Progress-workspace folder.
  §50/§51 ratified (unblocked); gated on Track 0 (workspace live) + the A5 row-cap + capacity guards (built).
- **M2** — per-job **Material List** (manifest) + bidirectional receive (operator content cols / field delivery cols).
- **M3** — Material Incidents referencing a Material-List line + a fenced `portal_poll` photo deep-screen pass.
Design source: `progress-reporting/mission.md` §11–§13/§16; audit confirms hours are captured in `time_entries`
but surface only as a transient PDF number — no persisted per-job Hours Log sheet exists yet.

### Track 3 — Scale-hardening for the 20×20 cutover
Most of the 14-row growth time-bomb table (`~/.claude/plans/unbounded-growth-audit.md`) is fixed (GS1 Check O /
sheet_capacity wiring, GS2 prune heartbeat + Check V, Sentry reclassification, D5 registry split). Remaining:
- Verify the **2 unverified Smartsheet quotas** (per-plan sheet cap; pooled attachment-storage quota) — one support ticket.
- **meta-002 Tier-3 backup / escalation SLA** before the 20-job cutover (operator).
- `REQUIRED_CONFIG` startup logging (#336) · host-log prune (time-bomb #14) · watchdog hang-killer · confirm
  the installed plists' `RunAtLoad` is actually active · `brief-validator` scaffold-wiring (#341).

### Track 4 — Operator PDF documentation program (P1 / A8)
A guide / manual / troubleshooting tree per ITS function (portal flows, ~17 Smartsheet operator surfaces, the
daemons + CLIs); the `ITS_Config` data-dictionary PDF; the §6a enablement-doc DoD per progress-reporting slice;
a doc-currency mechanism (PDFs drift as the form editor publishes). Enabling precondition for the
distributed-Evergreen-operator model. *(Distinct from the internal CC-session context system — this is operator-facing.)*

### Track 5 — Evergreen PRODUCTION cutover (Phase 1.4/1.5 hardening → live tenant)
`/api/login` rate-limiting + PBKDF2 (paid-plan/Cloudflare config); attachment screening Layers 1-3
(Email-Triage-owned Invariant-2 Layer 6; ClamAV prerequisite); then the sandbox → production tenant cutover.

---

## Backlog — parked with unblock conditions (not on the near path)
- **Canonical-Evergreen Smartsheet integration + PJOB→JOB reconciliation** — DEFERRED indefinitely; unblock =
  Seth gains read access to the canonical Evergreen schema. (ITS-owned SoR write-back is *not* blocked — §50/§51.)
- **Doctrine** §23/§24 seven-workspace topology text + any §-adds — Seth-owned, version-bump.
- **Future workstreams:** URS-Marine (Customer 2, active — briefs B1–B5); Purchase Orders; Subcontracts; Email
  Triage (owns Invariant-2 Layer 6 — preserve the email code seed); AI Employee (Phase 3+; vector store → Phase 4).
- **Small feature / tech-debt:** publish rollback-UI picker; form-editor S1 per-item authoring; HTML email for
  weekly_send; time-entry personnel picker; finish `jobs.progress` %-removal (D1 column drop); `recipient_health`
  no-recipient severity (Seth); cosmetic tab-title/favicon still "ITS Portal"; `boxsdk`→`box_sdk_gen`;
  `build_wsr_human_review_sheet.py` ABSTRACT_DATETIME fresh-create bug; P2.5 `fieldops_sync` fast-follows (2 of 6).
- **Verify (likely already built — audit flagged stale memory):** PR-6 Form-Request month filter
  (`/api/filed/months`); A5/Check-O row-cap rotation (present in `watchdog.py`).
