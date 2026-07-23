---
type: report
date: 2026-07-23
status: draft
related_prs: [465]
workstream: null
tags: [closure-policy, archive-on-closure, proposal, section-51, box, d1, smartsheet]
---

# Project-closure policy proposal — dispositions for every per-job surface

> **Status: PROPOSAL, pending planning-project ratification.** Extending archival beyond the
> current §51 slice is a **doctrine change** (Op Stds v21 §51 + its folded riders), which is a
> FIXED high-capability class — nothing in this document is to be implemented until the planning
> layer ratifies a disposition set. The execution-side tracking issue references this document.
> This report also discharges the design-doc directive from the 2026-06-28 forensic scaling eval
> (`docs/reports/2026-06-28_forensic-scaling-eval-20x20.md:316` directed a
> `docs/designs/sheet_archival_strategy.md` that was never created — the archive-on-closure
> option it named is now the shipped slice this proposal builds on).

## Summary

The 2026-07-23 archive-path audit established: doctrine defines archive-on-closure **only** for
the ITS-owned standing tracker sheets (§51 "accumulating logs are period-split +
archived-on-closure, never `delete_rows`"), exactly that slice is implemented (the
`fieldops_sync` 4-tracker move, PR #465 / its#462), and **everything else a project owns has no
defined end-of-life** — its de-facto policy is retain-in-place, stated nowhere.

A full per-job inventory finds **~45 distinct per-job surfaces** (23 Smartsheet, 7 Box, 15 D1
groups). 4 have the archive move; the D1 groups have prune/purge hygiene; **26 surfaces
(19 Smartsheet + all 7 Box) have no end-of-life handling at all**. This proposal recommends a
disposition for each — the large majority **deliberate, ratified retain-in-place** — so that
"nothing happens to X at closure" becomes a decision instead of an accident.

Companion decision (separate, narrower, also open): the archive **trigger semantics**
(Inactive-vs-Archived, portal-origin-only, no-retry) — delivered to Seth as a decision memo;
whichever option he picks slots into the "already covered" row below unchanged.

## Methodology

Line-verified against live HEAD (`e74998a`, 2026-07-23) by a parallel claim-verification sweep:
every creation/write site, every existing end-of-life path, and the doctrine/mission anchors were
confirmed by direct code reads (file:line evidence in the Appendix). Zero-grep-hit claims
(e.g. "no Box move primitive") were confirmed decisive. Current-behavior reference:
`docs/runbooks/project_closure.md`.

## Findings — the per-job denominator and its gaps

1. **The only automated Smartsheet end-of-life is the 4-tracker move** (`<Job> — Hours Log /
   Equipment / Material List / Material Incidents` → `ITS — Archive / Closed Projects`), firing
   solely for portal-origin jobs explicitly set `lifecycle=archived`. It has **never fired
   live** (the Closed Projects folder — pre-wipe and post-rebuild — has never held a sheet).
2. **§51's text covers accumulating logs only.** The progress-reporting mission overreaches it —
   `workstreams/progress-reporting/mission.md:66/:143-146/:152/:218` sweep progress **week
   sheets** into "period-split + archive-on-closure", which was never built and is not in §51's
   own scope sentence. Mission and implementation must be reconciled in one direction or the
   other (recommendation below).
3. **Box has no closure concept and no primitives.** `shared/box_client.py` has **no move,
   rename, or delete function at all** — a Box archive convention is not primitives-ready
   (the underlying `boxsdk` does expose `BaseItem.move`, so a wrapper could be built under
   §30 discipline if ratified).
4. **Smartsheet folder-level closure is also not primitives-ready** — `shared/smartsheet_client`
   has sheet-level move/delete only; no folder move. The per-job folder *shells* stay behind
   even after a successful tracker move.
5. **Procurement lanes ignore job status entirely.** `po_poll` / `rfq_poll` /
   `subcontract_poll` contain zero `active_jobs` checks — they process whatever the office
   queues, for any job. Their per-job mirror sheets and flat-log rows have no closure
   dimension.
6. **D1 is the best-covered system** (guarded prune + the 30-day inactive grace + the manual
   `purge-job` cascade, all live and watchdog-observed via Check V) — with two small gaps:
   `equipment_logs` is in **neither** the purge cascade nor any prune stage (the one D1 table
   with no end-of-life anywhere), and `inspections` has no production writer (guarded and
   purged, never written).
7. **Workspace membership carries approval semantics** (§23/§46: workspace shares define
   approval authority). Moving sheets into `ITS — Archive` changes whose shares govern them —
   any archival expansion must state the intended Archive-workspace sharing posture.
8. **Doctrine §24's workspace-id inventory is stale post-rebuild** (it still lists the pre-wipe
   Archive id; `shared/sheet_ids.py` is the declared source of truth). Any ratified change
   should cite constants, never literal ids.

## Recommendations — proposed disposition per surface

**Legend.** RETAIN = ratify retain-in-place as the deliberate policy (no code). COVERED =
already handled today. DECIDE = a genuine option fork for the planning layer.

| # | Surface (system) | Proposed disposition |
|---|---|---|
| 1 | 4 standing progress trackers (Smartsheet) | **COVERED** — the §51 move (trigger semantics = the separate decision memo) |
| 2 | Safety week sheets + per-job folder (Smartsheet) | **RETAIN** — filed-submission SoR; bounded by job lifetime; folder move isn't primitives-ready |
| 3 | Progress week sheets + per-job folder shell (Smartsheet) | **RETAIN**, and **amend the progress mission** to trackers-only (`:66/:143-146/:152/:218` + the `:238-240` provenance block) — the alternative (build week-sheet archival) buys nothing while the base slice is still unproven |
| 4 | WSR / WPR human-review rows (Smartsheet) | **RETAIN** — send/approval audit history |
| 5 | ITS_Active_Jobs + ITS_Active_Jobs_Progress rows (Smartsheet) | **COVERED** — retained flagged (`Inactive`/`Archived`), by design; never delete |
| 6 | Per-job "Purchase Orders" / "RFQs" / "Subcontracts" sheets (Smartsheet) | **RETAIN** — live commercial records beside their flat ledgers |
| 7 | PO_Log / RFQ_Log / Estimate_Log / Subcontract_Log + procurement Pending_Review rows (Smartsheet) | **RETAIN** — §51 ledgers; growth is governed by row-cap period-split, not closure |
| 8 | Whether procurement lanes should refuse/flag work queued against a non-active job | **DECIDE** — business policy, not hygiene. Default recommendation: no gate (late invoicing/closeout POs are real); at most a WARN-level annotation. Flagged because today's behavior (silent acceptance) is undocumented |
| 9 | Box: the entire per-job tree (week PDFs, WSR/WPR packets, ITS Photos, PO/quote/RFQ/subcontract files) | **DECIDE** — (a) **RETAIN in place** (recommended: zero code, Box search/naming already isolates a job, matches "Evergreen retains in Box as long as needed") vs (b) move the job folder under a `Closed Projects` Box folder on closure — requires a NEW `box_client` move primitive (+ §30 integration test + a hook site); build only if Evergreen asks for visual separation |
| 10 | D1 field-ops SoR tables (time entries, tasks, equipment history, checklists, materials, photo pools) | **RETAIN** — payroll-grade source records under existing guarded prune/purge; the Smartsheet trackers are the archived artifact, so no export-at-closure is needed |
| 11 | D1 `equipment_logs` (equipment-keyed history) | **Small code fix, standalone** — add to the `purge-job` cascade (or document why equipment-keyed history deliberately survives a job purge). The one table with no end-of-life path anywhere; independent of doctrine ratification |
| 12 | D1 submissions / filed_pdfs / procurement rows | **COVERED** — existing prune stages + Check V |
| 13 | Legacy email-pipeline per-week folders + template sheets (`week_folder.py`, dormant path) | **RETAIN (dormant)** — do not harden a dormant subsystem; revisit only if Email Triage revives the path |
| 14 | Troubleshooting-tree job end-of-life node | **Follow-up after the trigger-semantics decision** — add a closure entry point once semantics are final, so the tree doesn't immediately drift |

**If ratified, the doctrine touch is:** §51's scope sentence stays as-is (recommendations above
extend nothing); the ratification adds a short "closure dispositions" statement (or §51 rider)
recording retain-in-place as deliberate for rows 2–7/9–10/13, plus the progress-mission
reconciliation (row 3). If instead the planning layer picks Box option (b) or a procurement
gate (row 8), those are §51-adjacent scope expansions and land as explicit riders with their
own briefs.

## Appendix — evidence anchors

- **Audit dossiers:** `logs/reviews/2026-07-23_arch_code.json`, `…_arch_docs.json`,
  `…_arch_inventory.json` (operator-local, not committed).
- **The implemented slice:** `field_ops/fieldops_sync.py:761` (trigger), `:811-869` (the move,
  tracker tuple `:856-861`), `:872-888` (no-retry WARN); `shared/smartsheet_client.py:1756-1778`
  (`move_sheet_to_folder`, breaker-guarded, deliberately not retry-enrolled —
  `tests/test_smartsheet_retry.py:649` enforces the exclusion).
- **Never-fired evidence:** pre-wipe dump `logs/migrations/prewipe_20260723T030026Z/smartsheet/`
  `ITS — Archive/_workspace.json` (0 sheets at every level); live post-rebuild probe 2026-07-23
  (`Closed Projects` id 4545207418021764, 0 sheets); no `lifecycle=archived` live event in any
  session log; its#462 closed on mocked tests with the §30 live smoke still queued
  (`docs/session_logs/2026-07-04_smartsheet-verify-hours-smoke-archive-on-closure.md:67-68`).
- **No-Box-primitive:** `shared/box_client.py` full surface (upload/download/list/find-or-create
  only; zero `def move|rename|archive|delete` hits); `boxsdk` `BaseItem.move` exists
  (`.venv/.../boxsdk/object/base_item.py:42`).
- **Procurement no-active-check:** zero `active_jobs`/`is_active` grep hits across
  `po_materials/po_poll.py`, `po_materials/rfq_poll.py`, `subcontracts/subcontract_poll.py`.
- **D1 coverage:** `safety_portal/worker/prune.ts` (stages + `INACTIVE_JOB_GRACE_DAYS=30`,
  jobs-row guard union), `safety_portal/worker/index.ts:2421-2496` (`purge-job`, 12-table
  atomic cascade + audit; `equipment_logs` absent), watchdog Check V.
- **Doctrine anchors:** Op Stds v21 §51 (`~/its-blueprint/doctrine/operational-standards.md:1153`)
  + folded riders `:1248/:1250`; §23 `:389-393` (workspace membership/approval); §24 `:397-399`
  (id inventory — declared subordinate to `shared/sheet_ids.py`);
  `workstreams/progress-reporting/mission.md:66/:143-146/:152/:218/:238-240` (the week-sheet
  overreach); `workstreams/safety-portal/mission.md:159` (Box retention posture) + `:273`
  (D1 prune contract).
- **Current-behavior runbook:** `docs/runbooks/project_closure.md`.
