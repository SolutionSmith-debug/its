---
type: session_log
date: 2026-07-22
status: closed
workstream: operator_dashboard
tags: [operator-dashboard, cutover, phase1-hybrid, branch-protection, verify-cutover, system-map, config-editor, watchdog, tech-debt]
---

# 2026-07-22 — Cutover-day-1 CC items + operator-dashboard refinement pass (autonomous afternoon)

Autonomous afternoon session (operator away; scope confirmed live before departure): land the
Phase-1 cutover checklist's Tuesday CC items, then a full refinement pass on the operator
dashboard (system-map depth, config-editor reorg, coverage completeness), then
cutover-supporting tech debt. Gate-bypass authorization granted for REVERSIBLE actions with a
full return brief; external sends / secret rotation / doctrine bumps explicitly out.

## Commits landed

- **(repo settings, no commit)** — branch protection enabled on `main`: required checks
  `test` + `portal` + `secrets`, strict up-to-date, `enforce_admins=true`, NO required
  reviews (preserves the durably-authorized autonomous merge). **Bite-proven**: a direct
  push was attempted and server-rejected ("3 of 3 required status checks are expected"),
  then the test commit discarded. Closes CL-23 (top of the Aug-7 punch-list).
- `f2bb9a0` — **#654** `feat(cutover): verify_cutover --profile phase1-hybrid` — named
  sandbox-scan exemption profiles (checklist §3.5, due today). Live smoke on the mirror:
  the profile exempts exactly the three `worker_base_url` rows and still fails the six
  not-yet-repointed mailbox/operator rows.
- `16acbf3` — **#655** `feat(dashboard): system-map depth` — operator briefs
  (`sheet_briefs.py`), multi-doc links + Smartsheet permalink out-links on every sheet
  node, Orphaned-Reports + Forms-Catalog + 4 promoted registry satellites
  (Quarantine/Project-Routing/Time-Off/Picklist-Sync-Config), stale N/T badges fixed +
  J/L/M/S/W placed, 3 new runbooks (its_errors_triage / review_queue_triage /
  time_off_reviewer_chain, tree.yaml-referenced), parity teeth extended (watchdog letters,
  live SHEET_* constants, docs-exist, briefs-no-live-state).
- `e5f08e5` — **#656** `fix(cutover): CO-4 builder repoint + CO-2 live-clamd EICAR smoke +
  ledger truth-up` — the two Safety-Portal sheet builders repointed off the pre-2026-06-05
  location BEFORE tomorrow's production-tenant builder run (dry-runs find the exact live
  folder ids); skip-if-no-clamd EICAR bite tests for Phase C; three stale ledger entries
  closed with mechanical evidence.
- `202b7b6` — **#657** `feat(dashboard): config-editor reorg` — curated GROUP_ORDER,
  stable slug anchors, semantic GROUP_INTROS (no-live-state test extended over them),
  ON/OFF pills, gold send-gate / red brake accents, tier-homogeneity teeth. Write flow
  byte-identical.
- `a2623fd` — **#658** `feat(watchdog): per-check sweep results file + dashboard sweep
  panel` — CHECK_LETTERS registry (parity-pinned to len(CHECKS)), `_run_check` returns
  sweep records, main() persists `state/watchdog_results.json` via state_io, new
  WatchdogSweepSource panel ("did last night's sweep run; which letters passed" — a green
  sweep was previously invisible). Plus the CLAUDE.md watchdog-row count/letters
  reconciliation (A–W/20 distinct, 21 registered/22 defs) and the WS2-2 stale
  verify_cutover dashboard-description residual.

## Decisions made during session

- **Branch protection without required reviews** — required reviews would end the
  durably-authorized autonomous slice-PR merging; the checks-only + enforce_admins config
  blocks the actual threat (accidental direct push to the live-daemon branch) while
  keeping the working model. Alternative (reviews required) rejected as a workflow-breaker.
- **`--profile` as data, mutually exclusive with `--allow-sandbox`** — a phase gate must
  name its exemptions reviewably; making the two flags exclusive prevents a profile run
  from being silently degraded to the blanket waiver. Exemptions still presence-checked;
  every exercised exemption is named in the PASS summary.
- **Sheet briefs in a companion module** (`sheet_briefs.py`), not inline MapNode fields —
  keeps the topology registry scannable; parity test forces a brief for every sheet node.
- **Smartsheet permalinks fetched live (1h TTL, fail-soft)** rather than hardcoded — the
  permalink token is not derivable from the numeric id and is tenant-specific; hardcoding
  would break at the token-swap cutover. `page_size=1` keeps the fetch tiny.
- **Watchdog letters badge their SUBJECT node**; the watchdog's own infra checks (M/S/W)
  badge the watchdog node, L badges ITS_Config as the canonical "can ITS write to
  Smartsheet" surface, J rides the alerting spine. Letter set pinned against
  `len(CHECKS)` so a new check forces a badge decision in the same PR.
- **CO-1 was already fixed** (#585, 2026-07-14) — the tech-debt entry was stale; the
  session verified at HEAD instead of re-implementing (forensic class #3 avoided).
- **Sweep records carry the RAW severity** (pre-MAINTENANCE-downgrade) with
  `alerts_suppressed` at the file's top level — the panel annotates rather than lies.
- **Sweep-panel getattr fallback** for the deploy window where the observed live tree
  predates `WATCHDOG_RESULTS_PATH` (the dashboard observes `~/its`, not its own checkout —
  surfaced by a live-tree/worktree module-resolution test failure).
- **Deferred, deliberately**: WAF rate-limiting (checklist Thursday operator item —
  zone-level Cloudflare access), pytest-pollutes-live-logs test-infra hardening
  (recommended for next week, not cutover-critical), the two Seth-owned error-flood
  design gaps (ITS_Errors outage durability; outage paging posture).

- **(this close-out PR)** — ITS_Documentation_Index BUILT + SEEDED live (22 rows, sheet
  `5219712047730564`; `system.docs_index_sheet_id` recorded under Workstream `infrastructure`),
  closing the corpus-program index residual. The run surfaced + fixed two builder seams:
  the create→read 404 propagation window aborting the verify-after (now a bounded 5×2s
  retry), and `_record_sheet_id` expecting `get_setting`→None where it RAISES
  SmartsheetNotFoundError (now caught); the skip path now also completes the config
  record, so a crashed run self-heals on re-run — all three proven live.

## Open items handed off

- **E1/E2 (Smartsheet admin) remain the day-1 critical path** — plan-tier confirmation +
  its@ invite (checklist §2). Nothing CC-side blocks the Wednesday builder run now.
- **WAF rate-limit `/api/login` + `/api/*`** — Thursday checklist item, operator-run
  (Cloudflare dashboard), tech_debt line ~1789 still OPEN.
- **Phase-C EICAR smokes** — run `pytest tests/test_photo_screen.py -k live_clamd -v` on
  the production Mac once ClamAV installs (CO-2's operator half).
- **First real sweep file** appears at the next watchdog run (07:00) or via a manual
  kickstart; the sweep panel shows the informational first-run state until then.
- **Test-infra hardening** (pytest network calls polluting the live dated log + state
  locks) — recommend a dedicated session before Monday's live processing.

## What was NOT touched

- No External-Send-Gate flips, no send-daemon loads/unloads, no secret writes or
  rotations, no doctrine version bumps, no Worker/SPA deploys (dashboard work is all
  Mac-local Python; the mirror Worker stays exactly as the checklist requires this week).
- The live `ITS_Config` sheet (read-only reads only).
- `docs/enablement/` sha-coupled docs — deferred to the close-out docs PR with manifest
  re-record (subcontracts.md line-109 staleness + the operator_dashboard.md delta).
- The `safety_intake` heartbeat/marker residue files (operator file-delete, flagged in
  the return brief).

## Lessons captured to memory

- New topic memory `project_dashboard-refinement-cutover-day1-2026-07-22.md` (session
  outcomes + do-not-redo pointers), indexed in MEMORY.md.

Session-log line convention:
- pytest: 4318 passed / 2 skipped / 49 deselected
- mypy: 0 errors / 453 source files
- ruff: clean
- main-branch CI on merge commits: SUCCESS (f2bb9a0, 16acbf3, e5f08e5, 202b7b6, a2623fd)
