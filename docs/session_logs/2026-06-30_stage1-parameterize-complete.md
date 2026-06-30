---
type: session_log
date: 2026-06-30
status: closed
workstream: progress_reporting
related_prs: [353, 354, 355, 356, 359]
tags: [session_log, stage-1, parameterize, weekly-revert, week-sheet, compile-mutex, weekly-send, weekly-send-poll, send-config, daemon-config, contamination-guard, keychain-tty, box-token-reseed, doctrine-v19, progress-reporting]
---

# Session — Stage 1 parameterize complete (PRs #353–#359)

Stage 1 of the "ITS — Progress Reporting" program is complete. The entire safety send pipeline
(`week_sheet` / `compile_core` / `weekly_send` / `weekly_send_poll`) has been parameterized via
required config objects so Stage 2 can instantiate a second workstream without cloning or
forking any security-critical module. The session opened with a pre-work revert of the prior
session's "monthly sheets" decision back to weekly (8 files across both repos), continued through
5 slices merged to main, navigated a recovery detour (keychain TTY-trap + stale Box refresh
token), and closed with Op Stds ratified at v19 (§50 + §51) and a Stage-2 re-plan captured in
`~/.claude/plans/ok-we-are-going-scalable-flamingo.md`.

## PRs landed (all four-part verified)

- **#353 `dbfe991` — P1a: `week_sheet` → required `WeekSheetConfig(workspace_id, key_builder)`.**
  The sheet-period is now a property of the config object, not a baked constant. Safety binds
  byte-identically to the prior behavior. Sheet-capacity margin-check kept OUT of the config
  object (it is a pre-flight gate, not a per-sheet concern). Live integration smoke: 2 passed,
  green.

  PR #353 — four-part verify clean
  - state: MERGED
  - mergedAt: non-null (2026-06-29/30)
  - mergeCommit: dbfe991
  - main CI on merge commit: SUCCESS

- **#354 `8613ced` — P4-core: `shared/compile_mutex.py` (host-level compile mutex).**
  File-lock-backed mutex so a future Progress compile cycle cannot collide with a concurrent
  Safety compile. Fail-open for Safety: if the lock cannot be acquired, Safety compiles anyway
  and logs a WARN (preserving the established reliability contract). `compile_core` is unchanged
  — the mutex wraps the caller. Live compile smoke: byte-identical output to pre-mutex run.

  PR #354 — four-part verify clean
  - state: MERGED
  - mergedAt: non-null (2026-06-29/30)
  - mergeCommit: 8613ced
  - main CI on merge commit: SUCCESS

- **#355 `f98f56f` — Keychain TTY-trap fix (task #8 CLOSED).**
  `keychain.set_secret` now detects its call context: argv-based interactive invocation vs.
  daemon invocation; the daemon path has always been safe (`-w VALUE` form via launchd's
  no-controlling-terminal environment); the interactive path was vulnerable to macOS
  `security add-generic-password -w` (bare) reading `/dev/tty` when a TTY is present, ignoring
  piped stdin. Fix: always use `-w VALUE` argv form; scrub the value from any
  `CalledProcessError` chain to prevent credential leakage in logs. F04 (the original keychain
  stdin-write discipline from the 2026-05-28 session) is preserved — the scrub is additive.
  Operator validated interactively post-merge: no unexpected TTY prompt.

  PR #355 — four-part verify clean
  - state: MERGED
  - mergedAt: non-null (2026-06-29/30)
  - mergeCommit: f98f56f
  - main CI on merge commit: SUCCESS

- **#356 `2b843cd` — P1b: `weekly_send` → `SendConfig` + cross-workstream contamination guard.**
  `SendConfig` is a required (no-default) config object binding `(workstream_tag, ...)`. A
  `Workstream` column is backfilled `{safety}` on all existing `WSR_human_review` rows;
  `send_one_row` evaluates the column: **absent → WARN + skip** (graceful for rows created
  before the column exists), **whitespace-only → HARD-HELD + CRITICAL** (explicit blank is an
  operator error), **present-mismatch → HARD-HELD + CRITICAL** (cross-workstream contamination
  is a security boundary). Live send confirmed: a real safety row was approved and routed to
  SENT through the parameterized path. Adversarial 4-lens review (attacker/auditor/skeptic/
  invariant-checker) caught two defects before merge: a missed caller in
  `test_weekly_send_integration.py` that would have reached production without the review, and
  a Unicode-whitespace bypass in the whitespace-only guard (zero-width characters pass
  `.strip()` → a malicious actor could craft a deliberately blank workstream tag that evades the
  HARD-HELD path). Both fixed in the same PR.

  PR #356 — four-part verify clean
  - state: MERGED
  - mergedAt: non-null (2026-06-29/30)
  - mergeCommit: 2b843cd
  - main CI on merge commit: SUCCESS

- **#359 `763ad2b` — P1c: `weekly_send_poll` → `DaemonConfig` + extracted
  `safety_reports/send_poll_core.py`.**
  `DaemonConfig` is the required config object for the poll daemon, holding
  `(workstream_tag, sheet_locator, ...)`. `send_poll_core.py` is the reusable poll loop,
  extracted from `weekly_send_poll.py`; the safety daemon becomes a thin wrapper. Key
  invariants preserved verbatim: fail-CLOSED `_load_authorized_approvers` (an approver list
  that cannot be fetched hard-refuses all approvals — no fail-open; the alternate considered
  was WARN+skip which would silently skip the check under a Smartsheet transient), SENDING-row
  exclusion (rows already mid-send are never re-dispatched), and F22 approval-attestation
  verification. Contamination penetration-tested: a progress workstream tag in a safety daemon
  config produces HARD-HELD + CRITICAL on the contaminated row, leaving the clean rows
  unaffected.

  PR #359 — four-part verify clean
  - state: MERGED
  - mergedAt: non-null (2026-06-29/30)
  - mergeCommit: 763ad2b
  - main CI on merge commit: SUCCESS

## CI / four-part verify

All five PRs returned state=MERGED, mergedAt non-null, mergeCommit.oid present, and main-branch
CI SUCCESS on the merge commit.

Final integrated tree (P1c-merged tree, full Python suite):

- pytest: **1966 passed / 52 deselected**
- mypy: **0 errors / 220 source files**
- ruff: **clean**
- main-branch CI on merge commit `763ad2b`: **SUCCESS**

## Recovery detour (root-caused)

The P1b manual live-send smoke hit two independent failures in sequence, both root-caused and
resolved within the session:

1. **Keychain TTY-trap (#355, above).** The `set_secret` interactive call during an operator
   preflight check prompted unexpectedly. Resolved by #355 (always use `-w VALUE` form). This
   is the same class as the A3 Box OAuth keychain incident from the prior session — that
   incident seeded the tech-debt entry; this session closed it with code (#355).

2. **Stale Box refresh token.** The Box OAuth refresh token had been invalidated server-side
   by a prior cancelled rotation (Box rotates tokens on every exchange; a cancelled mid-rotation
   leaves the old token dead on the server while the Keychain still holds it). The daemon
   attempted a Box upload, got a 401, exhausted retries, and logged CRITICAL. Resolved by
   operator-run `~/reseed_box.py` using the `-w VALUE` argv form (now safe post-#355); freshness
   marker refreshed at `23:03Z`. Not a code defect — the A3 Box refresh-lock (PR #345, prior
   session) is the structural fix; the reseed is the operator recovery in the interim.

## Decisions made during session

1. **Monthly-to-weekly revert (sheets, pre-work decision).** The 2026-06-28 "monthly sheets"
   decision was reversed: both safety and progress use **weekly** sheet periods. Rationale: the
   sheet period must match the weekly report cadence — a safety submission on a Monday in a
   monthly model straddles two conceptual weeks, making Job-ID→Week-folder→Sheet resolution
   ambiguous. The weekly model has an unambiguous Sat→Fri window that matches `weekly_generate`
   and `weekly_send` exactly. Monthly is kept as a config-flip escape hatch via the armed
   `sheet_capacity` margin-check (the `sheet_count_ceiling` ITS_Config key already exists from
   P-A1). Operator confirmed Smartsheet is Business/Enterprise tier; sheet count is not the
   binding constraint. 8 files updated across both repos (plan, mission.md, brief, memory,
   info-gap, memory-archive, 2 CLAUDE.md references).

   Alternative considered: monthly for both (reduce sheet count). Rejected — cadence mismatch
   is a correctness problem, not an optimization target. Monthly as an escape-hatch costs
   nothing while the weekly model remains healthy.

2. **Parameterize-not-clone is the contamination gate (confirmed §14 deviation).** All three
   security-critical modules (`week_sheet`, `weekly_send`, `send_poll_core`) now bind required
   (no-default) config objects. A caller that omits or misspecifies the workstream tag fails at
   construction time, not at runtime. This is a deliberate deviation from Op Stds §14
   (preservation-over-refactor): the parameterize path was vetted by `ops-stds-enforcer` and
   adversarial review; the alternative (clone per workstream) would have doubled the
   contamination surface and the maintenance footprint for every future workstream.

3. **compile_mutex fails open for Safety.** If the host-level lock cannot be acquired (e.g.,
   the lock file is held by a concurrent compile), Safety compiles and logs WARN rather than
   aborting. Alternative considered: fail-closed (abort the Safety compile on lock failure).
   Rejected — Safety is established and live; introducing a new failure mode in the mutex path
   that causes a missed Friday compile is a worse outcome than a theoretical compile collision
   (which the existing per-job fence already mitigates). Progress compiles will acquire the lock
   normally; the fail-open is only a Safety-backward-compatibility safety net.

4. **HARD-HELD + CRITICAL on workstream mismatch; WARN on absent Workstream column.**
   Distinguishing absent from mismatch was deliberate: an absent `Workstream` column means the
   row predates the column (a common transition state during a progressive rollout) and should
   be skipped gracefully; an explicit mismatch means a row from one workstream is in another
   workstream's send queue, which is a security boundary violation and must surface loudly.
   The whitespace-only case (a blank string that passes `is None`) is treated as mismatch
   (HARD-HELD) because an explicit blank is an operator error, not a migration artifact.

5. **Unicode-whitespace bypass fixed before merge (adversarial review load-bearing).**
   The initial `whitespace-only` guard used `.strip() == ""`, which passes on standard ASCII
   whitespace but not on Unicode zero-width characters (U+200B, U+FEFF, etc.). An adversarial
   4-lens review caught this before the PR merged. Fix: `unicodedata.normalize("NFKC", ...)` +
   `.strip()`, which collapses all whitespace-class codepoints. The fix was made in the same
   PR; no follow-up. This is the direct payoff of the adversarial-review-as-DoD rule
   (CLAUDE.md operational convention; forensic class #9/#14).

6. **fail-CLOSED `_load_authorized_approvers` preserved.** The alternative of WARN+skip on a
   Smartsheet transient (allowing the poll cycle to proceed without an approver list) was
   considered as a resilience measure. Rejected — the approver-list fetch failing silently is
   the configuration error the fail-CLOSED behavior is designed to surface. A skipped check is
   not a graceful degradation; it is an undetected authorization bypass. The F22 invariant
   (approval-attestation) is non-negotiable.

7. **Stage-2 re-planned; not starting during Stage 1.** The operator locked a scope addition
   (Job Tracker as active-jobs SoR writer, dual physical sheets, full routing form) captured
   in `~/.claude/plans/ok-we-are-going-scalable-flamingo.md`. Decisions locked: two physical
   `ITS_Active_Jobs` sheets (one per workspace), typed-key-stable identity model (`Portal Job
   Key` bridge column; `origin` never flips), P2.5 as the first-class stage-2 slice, and the
   §50/§51 gate on the Smartsheet write. Per operator direction: Stage 1 finishes untouched
   first; Stage 2 queues behind it. Plan file is the authoritative specification; the
   greedy-fiddle plan is the cross-reference.

8. **Op Stds v18→v19 ratified (Seth).** §50 (D1-as-writer to ITS-owned Smartsheet, for
   Safety and Progress workspaces) and §51 (SoR write-back doctrine) were ratified by Seth
   this session. This unblocks Stage-2 P7 (field-ops up-sync) and M2 (material list
   manifest write-back). The `docs/doctrine_manifest.yaml` entry and CLAUDE.md reference
   updated to v19. `scripts/check_doctrine_drift.py --strict` passes clean on the updated
   manifest.

## Open items / next session

- **`~/its` cutover pull is INCOMPLETE.** Working-tree files conflicted during the fast-forward
  attempt at session close; operator is completing the pull via `git stash + git pull + git
  stash pop` (or backup-conflicted-files approach). Until the pull lands at `763ad2b`, the live
  daemons run the prior Stage-0 tree, not Stage-1. The send and poll daemons are unaffected
  (their launchd scripts have not been reloaded); the next `weekly_send_poll` cycle will pick up
  `SendConfig` + `DaemonConfig` once the operator completes the pull and reloads.

- **Stage-2: start at `ok-we-are-going-scalable-flamingo.md` Slice 0 (external gates).**
  Both gates are unblocked: §50/§51 ratified (v19); the greedy-fiddle plan carries Stage-2
  slices. Slice 1 (D1 schema + Worker plumbing) is §50-ungated and can be started as soon as
  the Stage-1 cutover is confirmed live.

- **Operator: confirm `~/its` pull completed to `763ad2b`** before starting any Stage-2 slice.
  Run `git -C ~/its log --oneline -1` to verify.

- **Task #8 (keychain TTY-trap) CLOSED** by #355. The tech-debt entry in `docs/tech_debt.md`
  can be marked resolved; operator to update.

- **Box refresh-token rotation discipline.** The stale-token incident is recovered, but the
  A3 freshness-marker (50-day watchdog Check P from PR #345) is the structural early-warning.
  No code change needed; the operator should confirm the marker is fresh after the `~/reseed_box.py`
  run (`~/its/state/box_oauth_freshness_marker.txt` timestamp = `23:03Z` 2026-06-29).

- **Standing carry-forwards:**
  - P2.4 mirror daemon — BLOCKED (no Smartsheet access to canonical Evergreen workspace);
    `decision_p2.4-parked-no-smartsheet-access` unchanged.
  - Issue #336 (REQUIRED_CONFIG startup-logging) — deferred from the forensic-hardening session;
    not in scope here.
  - Blueprint scaffold-wiring for `brief-validator` — planning-layer item, Seth.

## What was NOT touched

- **No progress workstream code built.** Stage 1 is purely parameterization of existing safety
  modules. The first progress-facing slice is Stage 2.
- **No D1 or Smartsheet schema changes.** All five PRs are Python-only; no migrations applied.
- **No external send paths changed.** Invariant 1 intact: `weekly_send`/`send_poll_core` are
  still in `SEND_SCRIPTS` with zero AI imports; `compile_core` and `compile_mutex` are in
  `GATED_SCRIPTS` with zero send/AI imports. Capability-gating tests unchanged.
- **No existing safety test coverage reduced.** The `test_weekly_send_integration.py` missed-
  caller fix in #356 added coverage; no test was removed or weakened.
- **No blueprint doctrine files edited.** Op Stds v19 update was authored by Seth in the
  planning project; the exec-repo side updated `docs/doctrine_manifest.yaml` and CLAUDE.md
  references only.
- **No Field-Ops portal routes, SPA pages, or Personnel CRUD changes.** Stage-1 is isolated
  to the safety report pipeline.
- **No launchd plists added or reloaded mid-session.** The `send_poll_core` extraction takes
  effect when the operator completes the `~/its` cutover pull and reloads `weekly_send_poll`.

## Cross-references

- Master plan (Progress Reporting program): `~/.claude/plans/let-s-go-with-option-greedy-fiddle.md`
- Stage-2 scope addition: `~/.claude/plans/ok-we-are-going-scalable-flamingo.md`
- Prior session (Stage-0 foundation + P0/A3/A4/A6): `docs/session_logs/2026-06-29_field-ops-progress-stage0-foundation-landed.md`
- Prior session (forensic hardening): `docs/session_logs/2026-06-29_forensic-lessons-learned-hardening.md`
- Prior session (Stage-0 design + M1/P-A1/A2): `docs/session_logs/2026-06-28_field-ops-progress-reporting-stage0_2.md`
- PR merge discipline: `docs/operations/pr_merge_discipline.md`
- Adversarial review rule (DoD): CLAUDE.md "Operational conventions — load-bearing" (adversarial review is definition-of-done on trust-boundary surfaces)
- Op Stds v19: `../its-blueprint/doctrine/operational-standards.md` (§50 D1-as-writer, §51 SoR write-back)
- Memory entries to update: `project_fieldops-portal-program` (Stage 1 complete; weekly revert; Stage-2 plan captured); `project_safety_portal_state` (keychain task #8 closed); `feedback_keychain-security-cli-tty-stdin` (TTY-trap now resolved in code by #355)
- Tech-debt: `docs/tech_debt.md` — task #8 RESOLVED; Box reseed note (operational, not code); `~/its/state/box_oauth_freshness_marker.txt` freshness
