---
type: session_log
date: 2026-07-04
status: complete
workstream: field_ops
related_prs: [465]
tags: [session-log, smartsheet, audit, hours-log, live-smoke, archive-on-closure, section51, field_ops, progress-reporting]
---

# Session 2026-07-04 (overnight) — Smartsheet wiring verification + Hours Log go-live smoke + archive-on-closure

**Mandate (handoff).** Finish the defined field-ops/progress build + prove the Smartsheet system-of-record is
wired correctly and behaves end-to-end. Optimize for correctness/completeness; use subagents/workflows to fan
out audits and adversarially verify.

## Orientation (trust live code, never the claim)
- Live tree `71feb62`; blueprint `12024188` (Op Stds v19 + both v19.x riders). `brief-validator` verified **all
  12** handoff code-shape claims — zero drift.
- **Deploy confirmed LIVE** (the handoff's open question): migration `0038` applied to remote D1, the Worker
  serves `/api/internal/fieldops/hours-{pending,mark-mirrored}`, and an authenticated probe returned **4 pending
  rows**. The program-file "§2.1 held / live tree at cb58ca8" note was stale. §2.1 = **DONE**.

## Task B — Smartsheet wiring audit (read-only, B1–B6) → `docs/audits/2026-07-04_smartsheet-wiring-audit.md`
**Verdict: the SoR is wired correctly — no correctness breaks.** 3 code-side subagents + brief-validator built
the "expected" maps; live truth pulled via the daemons' own `shared/smartsheet_client`. Parity + capability
tests pass.
- **CLEAN:** picklist 3-way parity (`VALID_WORKSTREAMS` == `_WORKSTREAM_VALUES_GLOBAL`; WSR {safety}/WPR
  {progress}/SENDING-inclusive/Active lifecycle), topology (all 9 sheet-id constants resolve; WSR/WPR date
  columns are live `DATE` — the ABSTRACT_DATETIME/1142 latent bug is fresh-create-path-only), Active-Jobs
  Portal-Job-Key bridge + contacts, §46 shares (all 3 workspaces → seths@, non-empty), the 6 live daemons.
- **Findings (hygiene, tech_debt-tracked):** M-1 `smartsheet.sheet_count_ceiling`/`_margin` absent → capacity
  guard on a silent 1500/50 default; M-2 five stale `ITS_Daemon_Health` rows (incl. `intake_poll`, a *deleted*
  daemon); M-3 fieldops_sync interval 300(row)/90(launchd) mismatch; S-1 systemic silent config defaults (#336).

## Task A — Hours Log live smoke (GREEN)
Surgical: called `fieldops_sync._mirror_hours_pass` directly (daemon left **dark**, no flag flip, no lock
contention). 4 JOB-000018 entries mirrored to `Portal create test 2 — Hours Log` (display names via
`personnel.name`, hours, dates, Active) ✅ · idempotent re-mirror (no dup) ✅ · row-cap WARN forced (→ WARN +
Review-Queue period-split row) ✅. All smoke mutations cleaned; the Hours Log sheet + 4 mirrored rows left as the
consistent artifact. **Go-live = operator flips `field_ops.fieldops_sync.hours_enabled=true` after #462.**

## Task C1 — archive-on-closure (its#462) — BUILT + MERGED (PR #465)
`smartsheet_client.move_sheet_to_folder` (MOVE, never delete) + a fully-fenced
`fieldops_sync._archive_closed_job_trackers` (lifecycle=archived → find-no-create the Hours Log → move to
`WORKSPACE_ARCHIVE`/Closed Projects; idempotent, never-delete) + a §30 integration scaffold + a §43 runbook Fault
F. Built in an isolated worktree (fresh venv), adversarially reviewed. **ops-stds-enforcer: no BLOCK (9 dims
clean); one WARN fixed** — the "self-heals next dirty cycle" wording was false (the move runs after
`mark_fieldops_jobs_mirrored` advances both watermarks, so a failed move does not auto-retry; runbook now leads
with the guaranteed manual move). Clears the §2.3 archive gate. **S-3 resolved:** `FOLDER_ARCHIVE_CLOSED_PROJECTS`
is in `WORKSPACE_ARCHIVE` (the `sheet_ids.py` comment claiming safety-portal was wrong → fixed).

### Four-part landing verify (PR #465 → `185ca86`)
- pytest: 2398 passed / 48 deselected (worktree `.venv-wt`)
- mypy: 0 errors / 246 source files
- ruff: clean
- main-branch CI on merge commit `185ca86`: `state=MERGED` · `mergedAt=2026-07-05T03:51:29Z` · `mergeCommit` present · `test`+`secrets` SUCCESS, `portal` (unchanged-worker suite) confirmed green

## Task-C open decisions (surfaced — need Seth before building)
- **P7 Slice 2 (Equipment):** snapshot-vs-full-event — recommend a latest-location + readiness **snapshot**
  projection (one row/item, updated in place), which changes the §51 guards (never-delete = retire-in-place +
  archive-on-closure; row-cap/period-split moot for a bounded snapshot).
- **M2 (Material List):** recommend **EXTEND** `job_expected_materials` (0031) with
  `line_uuid`/`smartsheet_row_id`/`unplanned` (§14) rather than a new `material_list` table.

## Operator queue
Pull `~/its` (behind origin/main by #463 docs + #462); §2.2 its#460 (progress@ mailbox + Entra Mail.Send, HELD-safe);
the M-1/M-2/M-3 audit fixes; flip `hours_enabled` for Hours Log go-live (after #462 live); run the §30 live move
smoke (`pytest -m integration -k move_sheet_to_folder`) before relying on archival; the two Task-C decisions.
