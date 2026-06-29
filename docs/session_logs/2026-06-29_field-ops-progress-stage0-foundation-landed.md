---
type: session_log
date: 2026-06-29
status: closed
related_prs: [329, 344, 345, 346, 349]
workstream: field_ops
tags: [session_log, field-ops, tier-a, stage-0, personnel-crud, heartbeat-extraction, box-oauth-refresh-lock, weekly-generate-hardening, portal-poll-backlog-marker, watchdog, build-and-hold-smoke, keychain-tty-incident, compile-core, check-p, check-q, check-r]
---

# Session — Tier-A Stage-0 foundation + Personnel CRUD landed (PRs #329, #344, #345, #346, #349)

Second wave of the 2026-06-28 scaling-eval Tier-A program: Personnel CRUD backend (#329) and the
four Stage-0 foundation slices (P0 heartbeat extraction / A3 Box refresh-lock / A6 weekly_generate
hardening / A4 portal_poll backlog marker). All four foundation slices were built in parallel (4
`fork` subagents, per-slice `ops-stds-enforcer` review, held locally until operator live-smoked
each), then serialized through the branch-protection merge-train. Personnel (#329) was CC-built and
mergeable independently; it landed first. The session also produced a root-caused keychain TTY
incident during the A3 smoke that corrupted `ITS_BOX_REFRESH_TOKEN` — recovered and tracked as
tech-debt.

## PRs landed (all four-part verified)

- **#329 `6914945` — Personnel CRUD** (`fieldops_personnel_write.ts`). Inline create-with-account /
  roster-only / link (atomic `WHERE EXISTS`, 422 `unknown_account`) / unlink / retire. `parseRole`
  shared to `auth.ts`; defense-in-depth admin gate added to the account branch. `portal-worker-
  security-reviewer`: 12/13 clauses clean; W5 link check-then-act race closed before merge.

  PR #329 — four-part verify clean
  - state: MERGED
  - mergedAt: non-null (2026-06-29)
  - mergeCommit: 6914945
  - main CI on merge commit: SUCCESS

- **#344 `334ea9e4` — P0: `shared/heartbeat.py` extraction.** Eight heartbeat helpers consolidated
  from `portal_poll.py` and `weekly_send_poll.py` into `HeartbeatReporter`; thin
  `_write_heartbeat`/`_write_heartbeat_row` seams preserved so existing mock-based tests require no
  change. Live smoke confirmed heartbeats land correctly in ITS_Daemon_Health under the extracted
  module.

  PR #344 — four-part verify clean
  - state: MERGED
  - mergedAt: non-null (2026-06-29)
  - mergeCommit: 334ea9e4
  - main CI on merge commit: SUCCESS

- **#345 `27726f2c` — A3: Box OAuth cross-process refresh-lock + keychain write-lock + 50-day
  freshness marker + watchdog Check P.** Prevents concurrent refresh races across daemons. The
  50-day freshness marker surfaces impending token expiry pre-emptively so the operator can
  intervene before a daemon cycles through its retry budget. Check P is the new watchdog leg that
  fires on Box auth staleness. Live smoke required a re-seed after the TTY incident (see Decisions).

  PR #345 — four-part verify clean
  - state: MERGED
  - mergedAt: non-null (2026-06-29)
  - mergeCommit: 27726f2c
  - main CI on merge commit: SUCCESS

- **#346 `f916c5a2` — A6: `weekly_generate` hardening + `safety_reports/compile_core.py`
  extract.** Per-job SIGALRM timeout + pre-merge memory guard + resumable watermark-last ordering +
  `RunSummary`. Extracts `safety_reports/compile_core.py` (stdlib-only, capability-GATED: no
  `anthropic`/`graph_client`/`send_mail` imports). Live smoke via the `Compile Now` Rollup checkbox:
  forced full compile, new Rollup snapshot appended, trigger auto-cleared.

  PR #346 — four-part verify clean
  - state: MERGED
  - mergedAt: non-null (2026-06-29)
  - mergeCommit: f916c5a2
  - main CI on merge commit: SUCCESS

- **#349 `9ef14610` — A4: `portal_poll` unfiled-backlog marker + watchdog Checks Q and R.**
  Backlog marker writes confirmed live; Checks Q (fetch-outage) and R (unfiled-backlog) returned
  INFO live. A4 was reconciled against P0+A3 after those two merged (watchdog `CHECKS` re-letter
  required; `portal_poll.py` auto-merged cleanly — P0 heartbeat region is disjoint from A4 backlog
  region). First `push:main` CI run on the merge commit was concurrency-CANCELLED (not a failure —
  merge-train serialization overlap); the re-run was SUCCESS.

  PR #349 — four-part verify clean
  - state: MERGED
  - mergedAt: non-null (2026-06-29)
  - mergeCommit: 9ef14610
  - main CI on merge commit: SUCCESS (re-run; first run was concurrency-CANCELLED)

## CI runs

All five PRs returned SUCCESS on main-branch CI (state=MERGED, mergedAt non-null, mergeCommit.oid
present, main-branch CI SUCCESS). A4 (#349): first `push:main` run was concurrency-CANCELLED;
four-part verify was completed on the re-run result (the cancelled run is not a failure — it
confirms the conclusion was not reached, not that a check failed). Per
`docs/operations/pr_merge_discipline.md` step 4, the re-run conclusion is authoritative.

Final integrated re-gate (A4-merged tree, full Python suite):

- pytest: all pass / 52 deselected
- mypy: 0 errors / 212 source files
- ruff: clean
- main-branch CI on all four foundation merge commits: SUCCESS

## Decisions made during session

1. **Build-and-hold-for-smoke model.** All four foundation slices (P0/A3/A4/A6) were built in
   parallel (4 `fork` subagents + 3 read-only scout agents + per-slice `ops-stds-enforcer`
   reviews), committed locally, and HELD until the operator completed a live smoke on each before
   merge. Personnel (#329) was CC-built and fully CC-mergeable; it landed first without a hold.
   Rationale: the "mocks-pass-but-live-fails" class (memory `feedback_mandatory-live-smoke`) is
   directly relevant for shared infrastructure touching Keychain, Box OAuth, and watchdog state —
   each of which has a live-vs-mocked surface that unit tests cannot exercise.

2. **LIVE SMOKE INCIDENT (root-caused): `keychain.set_secret` reads `/dev/tty` when a controlling
   terminal is present.** During the A3 Box OAuth smoke, `set_secret` was called interactively.
   macOS `security add-generic-password -w` (bare — no value argument) reads the controlling TTY
   and ignores piped stdin. The operator was prompted unexpectedly and pasted the Box client secret
   at the TTY prompt → `ITS_BOX_REFRESH_TOKEN` was corrupted with the client secret value.
   Recovered via a re-seed script (`~/reseed_box.py`) using the `-w VALUE` argv form, which works
   in both interactive and headless (launchd) contexts. Root cause: `set_secret`'s stdin-feeding
   path functions correctly only when launchd invokes the daemon (no controlling terminal); the
   interactive-vs-headless divergence was not previously documented. This does NOT affect production
   daemons (launchd has no controlling terminal); the risk is confined to operator-run scripts.
   Alternative considered: update `set_secret` to always use `-w VALUE` form inline. Chosen path:
   tech-debt entry to harden `set_secret` against the TTY trap (detect TTY at call site, always
   use `-w VALUE`). Memory `feedback_keychain-security-cli-tty-stdin` reinforced.

3. **Merge-train serialization and watchdog re-letter.** PRs serialized via `gh pr update-branch`
   (branch-protection require-up-to-date). A4 (#349) was reconciled against P0+A3 after those two
   landed; this required a watchdog `CHECKS` constant re-letter: **P = Box refresh-lock (A3),
   Q = fetch-outage (A4), R = unfiled-backlog (A4); O reserved for A5** (not yet built).
   `portal_poll.py` auto-merged cleanly with no conflict — the P0 heartbeat region and A4 backlog
   region are non-overlapping file sections.

4. **A4 CI cancellation was NOT a failure.** The first `push:main` run on #349's merge commit was
   concurrency-CANCELLED (a concurrent re-run of the same commit triggered the concurrency group).
   The four-part verify was completed on the re-run result (conclusion=SUCCESS). The cancelled run
   has no conclusion and is not a disqualifying signal; per `docs/operations/pr_merge_discipline.md`
   step 4, the re-run conclusion is the authoritative gate.

5. **Cutover.** After all five merges, operator fast-forwarded `~/its` (clean `git pull`) and
   reloaded portal-poll, weekly-send-poll, weekly-generate, and compile-now-poll to pick up P0's
   heartbeat extraction and A3/A4/A6's hardening changes live.

## Open items / next session

**Stage-0 remaining:**
- A5 (watchdog Check O) — the next Tier-A watchdog leg; reserved check letter O. Not built this
  session; no blocking dependency on any post-A4 slice.
- Monthly-sheet migration for safety pipeline — gated on A6 landing (now complete). A Stage-1
  parameterize pass follows.

**Tech-debt additions (this session):**
- `keychain.set_secret` TTY hardening — always use `-w VALUE` argv form; detect TTY in interactive
  contexts and warn. Tracked in `docs/tech_debt.md`.
- A3 freshness-marker integration test — live Keychain dependency prevents pytest coverage; smoke
  confirmed correct behavior but no regression test exists. Tech-debt entry added.

**Standing carry-forwards:**
- §50 D1-as-writer doctrine bump (Op Stds v18→v19, for Seth) — planning-layer ceremony item;
  carried from P2.3 (2026-06-27).
- P2.4 mirror daemon — BLOCKED (no Smartsheet access); `decision_p2.4-parked-no-smartsheet-access`
  unchanged.
- Operator: confirm Smartsheet tier sheet cap and set `smartsheet.sheet_count_ceiling` in ITS_Config
  (unresolved from #326 / P-A1).

## What was NOT touched

- No doctrine edits (Op Stds, Foundation Mission, mission files).
- No D1 or Smartsheet schema migrations — all A-series slices are Python/config changes only;
  migration 0019 (M1 materials catalog) was landed in the prior session (#325) and is unchanged.
- No external send paths — Invariant 1 intact. `compile_core.py` is explicitly GATED: no
  `anthropic` / `graph_client` / `send_mail` / `resend` / `smtplib` imports permitted; capability-gate
  tests cover the new module.
- No field-ops read routes, SPA pages, or existing write modules modified in #329 beyond the
  `auth.ts` `parseRole` extraction (behaviour-preserving).
- P3, P4, P5 phases — not in scope this session.
- A1 (verify_sheet_cap / #326) and A2 (single-host resilience / #327) — already landed in the
  prior session; unchanged.
- A5 and Brief-3 — deferred.
- No new launchd plists added (A2's `RunAtLoad=true` plists were already on disk from #327; this
  session's cutover reloaded them).

## Cross-references

- Scaling eval that spawned Tier-A: `docs/reports/2026-06-28_forensic-scaling-eval-20x20.md`
- Handoff brief (program spec): `docs/cc-brief_progress-reporting-program_2026-06-28.md`
- Prior session (Stage-0 M1/P-A1/A2 + live lockout): `docs/session_logs/2026-06-28_field-ops-progress-reporting-stage0_2.md`
- Prior session (write-UI Slices 3–4): `docs/session_logs/2026-06-28_field-ops-write-ui-phase.md`
- PR merge discipline (cancellation-vs-failure interpretation): `docs/operations/pr_merge_discipline.md`
- Memory entries reinforced: `feedback_mandatory-live-smoke`, `feedback_keychain-security-cli-tty-stdin`,
  `project_scaling-eval-20x20`, `project_fieldops-portal-program`
- Watchdog check map (current after A3/A4): A=stale review queue, B=open CRITICALs, C=scheduled-jobs
  staleness, D=14-day forward scan, F=mail-intake silent-disable, G=alert-dedupe sweep,
  I=weekly_generate catch-up, P=Box refresh-lock (A3), Q=fetch-outage (A4), R=unfiled-backlog (A4);
  O reserved for A5.
- Op Stds v18 §31 (polling-daemon pattern), §43 (successor runbooks shipped with A4/A6), §34
  (capability gating — `compile_core.py` GATED), §1 (kill-switch fail-open — unchanged)
