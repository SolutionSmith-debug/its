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
- **P7 Slice 1 — Hours Log: LANDED + live-smoked (2026-07-04).** `progress_reports/hours_log.py` mirrors
  `time_entries` into a per-job standing `<Job> — Hours Log` sheet (PR #461); **archive-on-closure LANDED**
  (`smartsheet_client.move_sheet_to_folder` + the `fieldops_sync` archive hook — PR #465 / its#462) — the last §51
  guard. Live smoke GREEN (4 rows mirrored, idempotent, row-cap WARN — see
  `docs/audits/2026-07-04_smartsheet-wiring-audit.md` Appendix). Ships DARK: operator flips
  `field_ops.fieldops_sync.hours_enabled=true` (Workstream=field_ops) to go live.
- **P7 Slice 2 — Equipment Status & Location** (NEXT). **OPEN DECISION (confirm w/ Seth):** snapshot-vs-full-event
  depth — recommend a latest-location + readiness **snapshot** projection (one row/item, updated in place), NOT the
  accumulating-log shape (which changes the §51 guards: never-delete = retire-in-place + archive-on-closure;
  row-cap/period-split largely moot for a bounded snapshot).
- **P7 Slice 3 — Materials Status & Location.**
- **M2** — per-job **Material List** + bidirectional receive. **OPEN DECISION:** the landed table is
  `job_expected_materials` (0031); the mission specs a `material_list` (line_uuid/smartsheet_row_id/unplanned) that
  does NOT exist — recommend **EXTEND** the landed table (§14) with those 3 columns rather than adding a new table.
- **M3** — Material Incidents referencing a Material-List line + a fenced `portal_poll` photo deep-screen pass.
Design source: `progress-reporting/mission.md` §11–§13/§16.

### Track 3 — Scale-hardening for the 20×20 cutover
Most of the 14-row growth time-bomb table (`~/.claude/plans/unbounded-growth-audit.md`) is fixed (GS1 Check O /
sheet_capacity wiring, GS2 prune heartbeat + Check V, Sentry reclassification, D5 registry split). Remaining:
- Verify the **2 unverified Smartsheet quotas** (per-plan sheet cap; pooled attachment-storage quota) — one support ticket.
  **The 2026-07-04 audit found `smartsheet.sheet_count_ceiling` + `_margin` are ABSENT from `ITS_Config`** → the
  capacity guard runs on the hardcoded default (1500/50) SILENTLY (forensic class #7); set the real plan cap under `Workstream=global`.
- **meta-002 Tier-3 backup / escalation SLA** before the 20-job cutover (operator).
- `REQUIRED_CONFIG` startup logging (#336) · host-log prune (time-bomb #14) · watchdog hang-killer · confirm
  the installed plists' `RunAtLoad` is actually active · `brief-validator` scaffold-wiring (#341).

### Track 4 — Operator PDF documentation program (P1 / A8) — *delivery-critical subset by Aug 7*
Near-term scope = the **delivery-critical PDF set** of the Aug-7 program
(`docs/2026-07-09_aug7_delivery_program.md` WS3): the md→branded-PDF pipeline (`docs_pdf/` +
`scripts/build_docs_pdfs.py` + the §6a `docs/enablement/manifest.yaml`), 12 PDFs (6 existing guides + safety-forms
+ admin-dashboard + PO builder + ITS Owner's Manual + auto-generated `ITS_Config` data dictionary + operator-dashboard
guide), SHA-256 doc-currency check wired into CI (warn) + the cutover checklist. Full every-function A8 coverage
continues post-delivery on the same pipeline. *(Distinct from the internal CC-session context system — this is
operator-facing.)*

### Track 5 — Aug-7 Evergreen DELIVERY (production cutover + PO workstream + dashboard + docs)
**The umbrella for everything through 2026-08-07 — canonical program: `docs/2026-07-09_aug7_delivery_program.md`**
(decision register D1–D18, WS1 Purchase-Order generator slices S0–S8, WS2 operator dashboard, WS3 docs subset per
Track 4, WS4 host migration + tenant cutover + Aug-7 runbook, master calendar, risk register, Day-1 operator list).
Highlights: old-MBP production host provisioned Jul 10 / one-way flip Jul 13 / burn-in through the Jul 25–30 gap;
Phase-1.4 residue = Paid-plan-or-PBKDF2 verdict + WAF `/api/login` rate-limit + ClamAV/EICAR; tenant cutover Aug 3
(§53-gated via `scripts/verify_cutover.py`); dress rehearsals Aug 4–5; delivery + Step-8 acceptance Aug 7 (handover
v10 amendment: Tier-2 clearance moves post-delivery, D17). Attachment screening Layers 1-3 for *email* stays
Email-Triage-owned (unchanged).

---

## Backlog — parked with unblock conditions (not on the near path)
- **Canonical-Evergreen Smartsheet integration + PJOB→JOB reconciliation** — DEFERRED indefinitely; unblock =
  Seth gains read access to the canonical Evergreen schema. (ITS-owned SoR write-back is *not* blocked — §50/§51.)
- **Doctrine** §23/§24 seven-workspace topology text + any §-adds — Seth-owned, version-bump.
- **Future workstreams:** URS-Marine (Customer 2, active — briefs B1–B5); ~~Purchase Orders~~ → **promoted to
  Track 5** (Aug-7 program WS1; the RFQ stage + Subcontracts remain future — first post-delivery builds); Email
  Triage (owns Invariant-2 Layer 6 — preserve the email code seed); AI Employee (Phase 3+; vector store → Phase 4).
- **Small feature / tech-debt:** publish rollback-UI picker; form-editor S1 per-item authoring; HTML email for
  weekly_send; time-entry personnel picker; finish `jobs.progress` %-removal (D1 column drop); `recipient_health`
  no-recipient severity (Seth); cosmetic tab-title/favicon still "ITS Portal"; `boxsdk`→`box_sdk_gen`;
  `build_wsr_human_review_sheet.py` ABSTRACT_DATETIME fresh-create bug; P2.5 `fieldops_sync` fast-follows (2 of 6).
- **Verify (likely already built — audit flagged stale memory):** PR-6 Form-Request month filter
  (`/api/filed/months`); A5/Check-O row-cap rotation (present in `watchdog.py`).
