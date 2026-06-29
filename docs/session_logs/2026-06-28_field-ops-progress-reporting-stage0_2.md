---
type: session_log
date: 2026-06-28
status: closed
related_prs: [325, 326, 327, 328]
workstream: safety_portal
tags: [session_log, field-ops, progress-reporting, materials, stage-0, m1, p-a1, a2, ui-fix, lockout, scaling, monthly-sheets, sheet-capacity, material-catalog, single-host-resilience, contamination-gate, personnel-crud]
---

# Session — Field-Ops Progress-Reporting program design + Stage-0 slices (M1/P-A1/A2) + live lockout fix

Designed the full "ITS — Progress Reporting" + P3 Materials program (structural twin of the Safety
Portal weekly pipeline: ITS-owned Smartsheet/Box SoR + externally-sent Weekly Progress Report),
hardened through grill-me + four reviewers + a contamination hunt + a four-fork ultra-plan pass +
reconciliation against the 2026-06-28 forensic scaling eval (20×20). Four strategic decisions
locked. Landed four PRs (all four-part-verified): M1 (materials catalog), P-A1 (sheet-cap margin
check), A2 (single-host resilience), and a field-ops UI fix. Diagnosed and operator-fixed a live
mirror lockout caused by D1 migrations 0013–0019 never applied to the remote D1 after `~/its` fell
25 commits stale. Personnel CRUD surface mapped in full by an Explore agent; nothing built.

## PRs landed (all four-part verified)

- **#325 `ef568c2` — M1: admin-editable `material_catalog` (migration 0019 + Worker CRUD + admin
  SPA).** 36-type vocabulary + soft-retire; `cap.materials.manage` / `cap.materials.receive` gates;
  reuses the 0013 capability table. D1-local, zero scaling coupling — built in parallel with Stage-0
  foundation slices.

  PR #325 — four-part verify clean
  - state: MERGED
  - mergedAt: 2026-06-28T20:29:36Z
  - mergeCommit: ef568c29b7cf145fad38ce641a6f43679d31d3a0
  - main CI on merge commit: SUCCESS (run 28335014315, workflow: ci)

- **#326 `b6ba870` — P-A1: `scripts/verify_sheet_cap.py` + `shared/sheet_capacity.py`
  margin-check.** Stage-0 Tier-A gate. Queries the live Smartsheet workspace sheet count; the
  runtime margin-check asserts headroom before any find-or-create and routes to Review Queue on
  breach (never silent). **Live finding: SAFETY_PORTAL workspace = 7 sheets; monthly model
  projects ~240 sheets/yr vs weekly ~1,040 sheets/yr (~4–5× improvement). MONTHLY sheet period
  adopted for both safety + progress.** Pro ($600) vs Business ($2,400) tier cap requires operator
  confirmation with Smartsheet; `smartsheet.sheet_count_ceiling` in ITS_Config is the runtime
  ceiling.

  PR #326 — four-part verify clean
  - state: MERGED
  - mergedAt: 2026-06-28T21:10:20Z
  - mergeCommit: b6ba8700002c5dd98bd9681f4addca7eebeb121c
  - main CI on merge commit: SUCCESS (run 28336099471, workflow: ci)

- **#327 `3b285f5` — A2: single-host resilience pack.** Hard network timeouts on all SDK calls
  in `shared/box_client.py`, `shared/smartsheet_client.py`, and `shared/keychain.py`; new
  `KeychainLockedError` for keychain-locked-after-reboot condition; `RunAtLoad=true` on 8 interval
  plists. Every new plist is authored hardened — the `template.plist` (`RunAtLoad=false`) default
  is now explicitly superseded by this PR. Box live smoke deferred to A3 (Box OAuth
  refresh-lock prerequisite). Operator follow-up: reload plists via `scripts/launchd/install.sh`
  to activate RunAtLoad on the host.

  PR #327 — four-part verify clean
  - state: MERGED
  - mergedAt: 2026-06-28T21:39:52Z
  - mergeCommit: 3b285f50bc3683bcfe39ea7eb3ca433fec48a3bf
  - main CI on merge commit: SUCCESS (run 28336875151, workflow: ci)

- **#328 `9ef3d5b` — field-ops UI fix: singular `PageShell` + restyle tracker pages.**
  Extracted the repeated page header/wrapper pattern into a single `src/components/PageShell.tsx`
  component; restyles Job Tracker / Equipment / Personnel / Materials pages to match the
  established kit. Confirmed working live after hard-refresh (browser cache held the old bundle
  hash `DvoDs9k6`; deployed hash was `DIq3aURp`).

  PR #328 — four-part verify clean
  - state: MERGED
  - mergedAt: 2026-06-28T22:48:10Z
  - mergeCommit: 9ef3d5b7d59424b23332b18e7c1bd4e2069edead
  - main CI on merge commit: SUCCESS (run 28338631169, workflow: ci)

## CI runs

Python PRs (#326, #327): pytest (integration excluded) + mypy + ruff, all clean, in worktree
venv `.venv-wt`. TS PRs (#325, #328): typecheck + worker vitest + SPA vitest via `npm` in
`safety_portal/`. All four main-branch CI runs returned SUCCESS across `portal`/`test`/`secrets`
checks. CodeQL-infra transient non-required check did not block any merge (known infra-fail class).

The note from the brief: "all four believed merge commit short SHAs (ef568c2, b6ba870, 3b285f5,
9ef3d5b) confirmed correct against gh-derived values."

## Decisions made during session

1. **Monthly sheets adopted for both safety + progress.** P-A1's live finding (SAFETY_PORTAL = 7
   sheets; weekly model ~1,040 sheets/yr, monthly ~240/yr) converted a planning-layer hypothesis
   into a measured result. The scaling eval #1 liability (sheet proliferation) is directly defused.
   Alternative considered: keep weekly for safety, monthly for progress only. Rejected — a split
   model introduces two different key-builder paths in the same parameterized `week_sheet` module,
   doubling the contamination surface. Monthly for both keeps the config symmetric and the weekly→
   monthly safety migration a deliberate, smoke-verified Tier-A step.

2. **Parameterize-not-clone as the contamination gate for security-critical modules.** The send/
   compile/week_sheet modules carry three required (no-default) config object bindings:
   `(workspace_id, sheet_period, key_builder)` for `week_sheet`; `SendConfig` for `weekly_send`;
   `DaemonConfig` for `weekly_send_poll`. A Workstream-tag column on WSR and WPR (backfill `safety`)
   + a `send_one_row` HARD-HOLD on mismatch is the cross-workstream contamination guard. Alternative
   considered: clone the modules per workstream. Rejected — §14 preservation-over-refactor applies;
   the parameterize path was vetted by the ops-stds-enforcer and four reviewers.

3. **Full Tier-A foundation front-loaded before any progress slice that creates sheets, compiles, or
   adds a daemon.** The 20×20 scaling eval showed the unsafe-clone path re-creates A6 CRITICALs,
   5,000-row silent-drop, and missing host resilience under load. Execution order: P0 (heartbeat
   extraction) → A2 (done) + P-A1 (done) → A3 (Box refresh-lock) → A6 (compile-core extraction) →
   A4 (unfiled-queue alert) → Stage-1 parameterize. M1 (materials catalog, D1-local) ran parallel
   because it has zero scaling coupling with the sheet model.

4. **ITS-owned Smartsheet + Box as SoR; canonical-Evergreen Smartsheet integration deferred.** The
   scaling eval #4 liability (PJOB→JOB key mismatch requiring reconciliation) makes canonical-
   Evergreen integration a discovery task, not a build task. ITS creates and owns the Progress
   Reporting workspace and sheets; the D1→Smartsheet up-sync (P7) writes to ITS-owned sheets only.
   Alternative: attempt to map to the canonical Evergreen schema now. Rejected — the schema is
   unseen (same blocker class as P2.4 parked decision).

5. **Same-PR doc skeleton + PDF-before-cutover as a definition-of-done obligation on every Stage-2
   slice.** §6/A8 enablement docs (§43 skeleton + §6a-manifest registration same-PR; distributable
   PDF before 20-job cutover) are load-bearing per the scaling eval B1/B4 findings and the
   documentation-program operator directive. Not deferred to a follow-up pass.

6. **Live lockout caused by running `wrangler d1 migrations list` before `git pull`.** `~/its` was
   25 commits stale; the stale migrations folder (0001–0012 only) reported "No migrations to apply"
   while the D1 was actually missing 0013–0019. The deployed Worker's `resolveCapabilities` is
   fail-closed: missing capability tables errored and returned zero caps for every account, locking
   out admin and PM. Fix: `git pull` `~/its` to latest main, then `wrangler d1 migrations apply
   --remote`. Lesson is operational: **always pull `~/its` to latest main before list/apply/deploy**.
   Architecture is unchanged; the fail-closed behavior is correct.

7. **Personnel CRUD: design-only this session; Option A (separate account + roster flows)
   recommended.** The Explore agent mapped the full `personnel` / `users` two-headed roster schema
   (nullable `username` NO FK, soft-link by string). Three product decisions queued before building:
   (a) keep account and roster creation separate vs inline toggle (rec: Option A); (b) validate
   `users.username` exists on create vs allow dangling reference (rec: validate, 422 on miss);
   (c) default role for account-linked personnel (rec: `submitter`). No build until operator
   confirms these.

## Open items / next session

**Stage-0 foundation (remaining; live-safety):**
- A3 — Box OAuth cross-process refresh-lock + keychain write-lock + 50-day idle marker (unblocks
  the deferred A2 Box live smoke).
- A4 — unfiled-queue backlog alert + `portal_poll` outage escalation.
- A6 — `weekly_generate` hardening (per-job SIGALRM timeout + memory guard + Rollup-watermark
  resumable skip) then extract hardened core to `safety_reports/compile_core.py`.
- P0 — `shared/heartbeat.py` extraction from `weekly_send_poll` + `portal_poll`.

**Personnel CRUD (task #22) — design complete, build pending:**
Full surface map in `docs/cc-brief_progress-reporting-program_2026-06-28.md`. Three operator
product decisions required before coding begins (see Decision 7 above).

**Operator follow-ups (not CC):**
- Reload daemon plists (`scripts/launchd/install.sh`) to activate A2 `RunAtLoad=true` and validate
  auto-start after reboot.
- Confirm real Smartsheet per-plan sheet cap (Pro $600 vs Business $2,400 tier); set
  `smartsheet.sheet_count_ceiling` in ITS_Config.
- Initiate §50 doctrine bump (v18→v19) early — gates P7 + M2 write-back; parallel to P0–P6.
- meta-002: define Tier-3 backup / escalation SLA before the 20-job cutover (~4 new daemons raise
  the escalation rate).
- Live D1 migrations: operator applies `wrangler d1 migrations apply --remote` BEFORE each Worker
  deploy; `npm run deploy` is operator-run.

## What was NOT touched

- No Stage-1 parameterize slices (`week_sheet` / `weekly_send` / `weekly_send_poll`) — gated on
  A3/A6/P0 completing first.
- No Stage-2 progress workspace, compile, or send — gated on Stage-1.
- No Personnel CRUD code — surface mapping + design only.
- No M2 (Material List manifest) or M3 (Material Incidents) — gated on P7 + §50.
- No Box calls from any landed PR's Worker code or daemon (Invariant 1 intact).
- No `send_mail` / `anthropic` / `graph_client` import in any landed PR (capability-gate tests
  unchanged).
- No new launchd jobs loaded — A2 plists updated on disk; activation is the operator `install.sh`
  follow-up.
- The safety intake pipeline, existing weekly_send / weekly_send_poll, and portal_poll are all
  unchanged.

## Cross-references

- Master plan (authoritative spec): `/Users/sethsmith/.claude/plans/let-s-go-with-option-greedy-fiddle.md`
- Handoff brief (state + resume detail): `/Users/sethsmith/its/docs/cc-brief_progress-reporting-program_2026-06-28.md`
- Forensic scaling eval: `docs/reports/2026-06-28_forensic-scaling-eval-20x20.md`
- Prior session (write-UI phase, Slices 3–4): `docs/session_logs/2026-06-28_field-ops-write-ui-phase.md`
- Prior session (P2.3 write routes): `docs/session_logs/2026-06-27_field-ops-p2.3-write-routes.md`
- Memory entries updated this session: `project_fieldops-portal-program`, `project_scaling-eval-20x20`,
  `decision_p2.4-parked-no-smartsheet-access` (evolves to ITS-owned SoR; canonical-Evergreen deferred)
- New memory entries to add: migrations-list-before-pull lockout gotcha; browser-cache-on-deploy gotcha
- Tech-debt additions: UI follow-ups (route form pages through PageShell; `.dash-section` duplicates
  `.card`); §6a doc DoD owed per Progress slice; A2 launchd activation pending
- Related PRs: #325, #326, #327, #328
