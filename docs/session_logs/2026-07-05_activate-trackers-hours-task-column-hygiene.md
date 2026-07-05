---
type: session_log
date: 2026-07-05
status: complete
workstream: field_ops
related_prs: [472, 473]
tags: [session-log, field_ops, hours-log, task-column, equipment, materials, section51, config-gate, hygiene]
---

# Session 2026-07-05 (part 2) — Activate shipped-dark trackers + Hours Log Task column + hygiene (PRs #472–#473)

**Continuation note.** This is a distinct session from the morning of 2026-07-05, already logged at
[`2026-07-05_hours-log-fix-slice2-equipment-m2-materials.md`](./2026-07-05_hours-log-fix-slice2-equipment-m2-materials.md)
(PRs #468–#470). This session picked up the operator's live blocker on activating those shipped-dark
trackers, replaced the always-empty Hours Log Started/Ended columns with a Task column, and landed two
hygiene fixes.

## Orientation — the operator's blocker

The operator could not find ITS_Config rows to flip `equipment_enabled`/`materials_enabled` to `true`.
Root cause: `field_ops.fieldops_sync.equipment_enabled` and `.materials_enabled` had **no row at all** in
ITS_Config — these gates ship dark by row-absence (`fieldops_sync._read_bool_setting` defaults `False`
when the row is missing, `Workstream=field_ops`), not by an explicit `false` value. Verified the live
ITS_Config sheet (`3072320166907780`) held only `sync_enabled=true` + `hours_enabled=true`. Independently
verified the Worker deploy itself via unauthenticated `401` probes on
`/api/internal/fieldops/equipment-snapshot` + `/material-list-snapshot` (both `401 application/json` =
route deployed; a missing route would SPA-fallback to `200 text/html` per the known Worker gotcha).

## Config rows created (operator-directed Smartsheet change, not code — recorded for audit trail)

- `field_ops.fieldops_sync.equipment_enabled = true` — **ACTIVATED**.
- `field_ops.fieldops_sync.materials_enabled = false` — row created but left `false`; §51-BLOCKED (see
  doctrine-drift item below) but now visible for the operator to flip once resolved.

## Equipment activation — live-smoked GREEN

The `fieldops_sync` daemon (90s launchd) picked up `equipment_enabled=true` on its next cycle (17:53Z) and
steadily reported `equipment upserted=1 retired=0 reviewed=0 errors=0` over ~45 minutes of observation. The
SoR artifact `Portal create test 2 — Equipment` sheet exists in the progress workspace with the expected
row. Materials stayed dark (`upserted=0`) as intended, pending the §51 rider below.

## Commits landed

- `9aada58` — **#472 feat(fieldops): Hours Log — replace always-empty Started/Ended with a Task column**
- `86bfab0` — **#473 fieldops hygiene: SYNC_INTERVAL_SECONDS 300→90 (M-3) + stabilize flaky R3 dirty-guard test**

### #472 — Hours Log Task column

Closes the tech-debt item logged in the morning session: the `Started`/`Ended` columns on the per-job
`<Job> — Hours Log` sheet are always empty in practice (the portal time-log form never captures wall-clock
start/end). Replaced both with a single `Task` column resolved from `task_assignments.description`.
Multi-surface fan-out, all enumerated and touched in one PR — **no D1 migration** (`task_id`/`description`
already exist on `task_assignments`):

- Worker `/hours-pending` route — `LEFT JOIN task_assignments ta ON ta.id = t.task_id AND ta.job_id =
  t.job_id`, projects `ta.description AS task`; drops `work_started`/`work_ended` from **this projection
  only** (the underlying columns are untouched elsewhere).
- `progress_reports/hours_log.py` — new `COL_TASK`, drops the two dead columns from the sheet schema.
- `field_ops/fieldops_sync.py` — hours-pass mapping updated; `work_date` now derived from `created_at`
  (previously derivable from `work_started`); dead `_fmt_epoch_time` helper removed.
- `shared/portal_client.py` — docstring updated to match the new projection shape.
- Tests updated for the new mapping; new migration script `scripts/migrations/hours_log_task_column.py`
  (two-phase, name-guarded, idempotent) for the one-time live-sheet schema change.
- `docs/runbooks/hours_log_sync.md` — new Fault M entry.

**Three-agent adversarial review, all clean or fixed before merge:**
- `portal-worker-security-reviewer` — CLEAN; the reviewer's suggested job-scope hardening was applied,
  plus a cross-job test added.
- `ops-stds-enforcer` — clean against §51/§14/§42/Invariant-1, with one §43 BLOCK caught and fixed in the
  same PR (the Fault M runbook entry above was missing on the first pass).
- Completeness critic — complete and correct on a second pass; two fixes applied: a scoped workspace
  traversal replaced a `/search`-based lookup, and a unit test was added for the migration script itself.

**Remaining operator step (not run this session):** the two-phase live-sheet migration against the
already-created `Portal create test 2 — Hours Log` sheet — `--phase add --commit` **before** the `~/its`
pull + Worker deploy, `--phase drop --commit` **after**. Order is KeyError-critical (dropping first would
break the still-old-code daemon's write against the still-old sheet schema).

### #473 — hygiene sweep

- **M-3** (from the 2026-07-04 Smartsheet wiring audit): `SYNC_INTERVAL_SECONDS` in `fieldops_sync.py`
  changed `300` → `90`, matching what the installed launchd plist and `install.sh` default already run at.
  The constant feeds the daemon-health cadence display, so the mismatch was a cosmetic-but-confusing drift
  between what the code claimed and what actually ran.
- **R3**: stabilized the flaky `FormFillPage.r3.test.tsx` dirty-guard test flagged as an unrelated failure
  in the morning session's #470 CI run. Fix: await the two `toHaveBeenLastCalledWith(false)` last-call
  assertions instead of asserting synchronously. Confirmed 8/8 stable runs via `vitest.config.spa.ts`.

## §51 materials rider — proposal drafted (Seth-owned, blocks `materials_enabled`)

Canonical Op Stds v19 §51 names the Material List as **"bidirectional with split column ownership"** —
the morning session's M2 (#470) shipped one-way-up only, per the operator's live Option-A ratification at
the time. Drafted a ratification-ready rider this session with both honest framings laid out for Seth to
choose between, rather than picking one silently:

- **Framing A** — one-way-up is a strict subset of "bidirectional with split ownership" (the receive-side
  simply hasn't been built yet); a clean v19.x rider that describes the *current* shipped shape as an
  accepted interim state, with bidirectional receive still queued as M2b.
- **Framing B** — one-way-up actually *removes* the operator-input capability the bidirectional model
  implied (the field can no longer write delivery-state back in a way the operator sees split-owned); this
  is a real capability change and may warrant a v20 recharacterization rather than a same-major rider.

This complements (does not duplicate) the tech-debt entry #471 already filed in the morning session for
the same drift. No doctrine file was edited this session — drafting only, per the "don't edit doctrine"
boundary; ratification is Seth's call.

## Four-part landing verify (quote verbatim)

**#472 → `9aada583edc6cae8419c15b8bb5d51c87f5c3dae`:**
- pytest: 83 passed
- mypy: no issues in 252 files
- ruff: clean
- worker typecheck: clean
- worker vitest: 10 passed
- main-branch CI on merge commit `9aada583edc6cae8419c15b8bb5d51c87f5c3dae`: `state=MERGED` ·
  `mergedAt=2026-07-05T18:40:00Z` · `mergeCommit` present · `test`/`portal`/`secrets` all
  COMPLETED/SUCCESS

**#473 → `86bfab0a3d437fe3ca54c7389b422b6e6ba0aa98`:**
- pytest: fieldops_sync 52 passed
- mypy: clean
- ruff: clean
- worker vitest (R3 SPA test): 8/8 stable
- main-branch CI on merge commit `86bfab0a3d437fe3ca54c7389b422b6e6ba0aa98`: `state=MERGED` ·
  `mergedAt=2026-07-05T18:49:14Z` · `mergeCommit` present · all 3 required checks green on the PR ·
  **main-branch push CI on `86bfab0a` = COMPLETED/SUCCESS (4th leg independently confirmed at session
  close via `gh run list --commit 86bfab0a --event push`).**

## Decisions made during session

1. **Config gates ship dark by row-absence, not an explicit `false`.** Confirmed rather than assumed —
   `_read_bool_setting` treats a missing row identically to a `false` row. This explains why the operator
   found no row to flip: the row simply didn't exist. Created explicit rows for both gates so the operator
   has something to toggle going forward, rather than leaving the row-absence behavior as the only path.
2. **Verify Worker deploy via unauthenticated 401 probes, not just a code read.** Rejected relying on the
   morning session's code-landed claim alone. Reasoning: a route can be merged to main and still not be
   live on the deployed Worker; a 401 (vs. a SPA-fallback 200) is the cheap, decisive live-signal per the
   known Worker gotcha (`reference_worker-spa-fallback-200-on-deleted-asset.md`'s sibling case for a route
   that was never deployed rather than removed).
3. **Materials row created but left `false`.** Rejected flipping `materials_enabled=true` alongside
   `equipment_enabled=true`, even though both were equally blocked-on-a-missing-row. Reasoning: the §51
   doctrine-drift finding from the morning session is a genuine unresolved doctrine/build mismatch, not a
   build defect — activating would ship a capability gap live before Seth has chosen a framing.
4. **Hours Log fix ships as a same-PR multi-surface change, not a staged rollout.** Rejected fixing the
   Worker route first and following up with the daemon/schema changes later. Reasoning: the five surfaces
   (`route`, `hours_log.py`, `fieldops_sync.py`, `portal_client.py`, tests) are tightly coupled to one
   projection shape; landing them separately would have left an intermediate state where the daemon reads a
   column the route no longer returns.
5. **§51 rider drafted with two honest framings, not one silent pick.** Rejected resolving the
   doctrine-drift finding unilaterally in the direction that unblocks materials fastest. Reasoning:
   doctrine text is Seth's canonical surface; the session-log-writer/agent boundary is propose-only here —
   surfacing both readings lets Seth make the actual call rather than inheriting a pre-made one.

## Open items handed off

- **§51 materials rider ratification (Seth)** — blocks `materials_enabled=true`. Two framings drafted
  above; needs a decision, then either a v19.x rider or a v20 recharacterization.
- **Hours Log two-phase live-sheet migration (Developer-Operator)** — `--phase add --commit` before the
  `~/its` pull + Worker deploy for #472; `--phase drop --commit` after. Not run this session.
- **`smartsheet.sheet_count_ceiling` still absent from ITS_Config** — capacity guard still runs on the
  hardcoded 1500/50 default (wiring-audit M-1, unresolved; roadmap Track 3).
- **its#460 progress@ mailbox** — still open, blocks progress sends only (unrelated to this session's
  trackers).
- ~~PR #473's 4th-leg main-branch CI confirmation~~ — **RESOLVED**: `gh run list --commit 86bfab0a
  --event push` = COMPLETED/SUCCESS. Both #472 and #473 are fully four-part verified.

## What was NOT touched

- `materials_enabled` was NOT flipped — stays `false` pending the §51 rider.
- No doctrine file was edited — the §51 rider is a draft proposal only, per the session-log-writer's
  doctrine-editing boundary; ratification is Seth's.
- No bidirectional Material List receive path was built — still deferred per the morning session's M2b
  framing, unaffected by this session's config-gate work.
- No canonical/customer-owned Evergreen Smartsheet integration work (still parked per
  `decision_p2.4-parked-no-smartsheet-access.md` — unrelated).

## Lessons captured to memory

- **`docs/HOUSE_REFLEXES.md` §5 addition made this session:** **a config gate ships dark by row-absence,
  indistinguishable from an intentional `false` from the operator's point of view** — seed the gate row
  (even `=false`) in the same change that ships the gated code, so activation is always a visible cell-flip,
  not a phantom. It bit the 2026-07-05 equipment/materials activation (the operator had no row to find), and
  recurs for every dark-shipped gate, so it went in as a reflex rather than a one-off note.
- **Auto-memory:** appended a sibling-case note to `reference_worker-spa-fallback-200-on-deleted-asset.md`
  (the inverse 401-probe check — a genuinely-deployed gated route 401s `application/json`; a never-deployed
  route SPA-falls-back to `200 text/html`). No new memory FILE — the §51 materials finding extends the
  already-tracked `#471` tech-debt item and the M-1 sheet-count-ceiling finding, not a new class.
