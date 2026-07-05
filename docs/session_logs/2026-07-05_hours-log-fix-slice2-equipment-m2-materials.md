---
type: session_log
date: 2026-07-05
status: complete
workstream: field_ops
related_prs: [468, 469, 470]
tags: [session-log, field_ops, progress-reporting, section51, equipment, material-list, hours-log, live-fix, smartsheet]
---

# Session 2026-07-05 (morning) — Hours Log go-live + P7 Slice 2 Equipment + live decouple fix + M2 Material List

**Continuation note.** The overnight 2026-07-04 arc (Smartsheet wiring audit, Hours Log live smoke, PR #465
archive-on-closure) is already logged at
[`2026-07-04_smartsheet-verify-hours-smoke-archive-on-closure.md`](./2026-07-04_smartsheet-verify-hours-smoke-archive-on-closure.md).
This log covers the morning continuation the operator drove live: Hours Log go-live, the two remaining
Task-C decisions (Equipment snapshot shape, Material List model) executed, a live starvation bug found and
fixed, and M2 landed with one real doctrine-drift finding left for Seth.

## Operator-directed Smartsheet changes (not code — recorded for the audit trail)

- **`field_ops.fieldops_sync.hours_enabled` flipped to `true`.** Hours Log up-sync (P7 Slice 1, PR #461,
  held on §51 pending the v19.x Path-B rider — see 2026-07-04 log) is now LIVE.
- **5 stale `ITS_Daemon_Health` rows deleted** — the M-2 finding from the 2026-07-04 wiring audit
  (`docs/tech_debt.md`): `safety_reports.intake_poll` (a DELETED daemon, `Enabled=True`, month-stale) +
  4 `NEVER_RAN` placeholders (`weekly_generate`, `weekly_send`, `watchdog`, `shared.picklist_sync`).
  The operator visibility surface is now exactly the **6 live self-reporting daemons** (`portal_poll`,
  `weekly_send_poll`, `compile_now_poll`, `progress_send_poll`, `publish_daemon`, `fieldops_sync`) — no
  ghost rows.

## Commits landed

- `95a9384` — **#468 feat(fieldops): P7 Slice 2 — Equipment Status & Location tracker (snapshot, §51)**
- `466e1e8` — **#469 fix(fieldops): decouple hours/equipment passes from a pending-jobs fetch failure
  (live Hours Log starvation)**
- `f7f3764` — **#470 feat(fieldops): P7 M2 — per-job Material List tracker (portal-authored, one-way-up
  snapshot, §51)**

### #468 — P7 Slice 2, Equipment Status & Location (one-way-up SNAPSHOT, ships DARK)

Resolves the first Task-C open decision from 2026-07-04: **snapshot, not full-event log** (one row per
equipment currently on a job, upserted in place — never a growing history table). New send-free, fully-bound
internal route `GET /api/internal/fieldops/equipment-snapshot` (latest `equipment_location` per equipment via
a `ROW_NUMBER` window ⋈ active equipment ⋈ active jobs), `progress_reports/equipment_status.py`
(change-only upsert-by-Equipment-ID, retire-off-job never-delete, row-cap watchdog, display-name-only,
TEXT controlled-vocab), and `fieldops_sync._mirror_equipment_pass` gated OFF by
`field_ops.fieldops_sync.equipment_enabled`.

**ops-stds-enforcer caught a real BLOCK before merge:** `_mirror_equipment_pass` only visited jobs present
in the *current* cycle's snapshot payload — a job whose entire equipment complement drops to zero (all
items moved off, or deactivated) produces zero snapshot rows for that job, so it's never bucketed and its
Equipment sheet is never revisited. Stale `On Job=Active` rows would persist forever, silently breaking the
"re-projects the whole live state every cycle" invariant this tracker depends on. Fixed in a follow-up
commit inside the same PR: the Worker route now also returns `jobs_with_equipment` (a roster of every
active job with equipment history), and `_mirror_equipment_pass` reconciles against that roster —
`_reconcile_job_zeroed` retires a job's entire Equipment sheet when it drops off the roster. Distinct from
the pre-existing full-snapshot-id-set guard (that one protects against a *partial* upsert failure wrongly
retiring survivors; this one protects against a *job* silently falling out of view entirely).
**portal-worker-security-reviewer: CLEAN.**

### #469 — live decouple fix (operator-reported: "logged time not showing")

Root cause: `_sync_inside_lock` returned early on a `PortalTransportError` from `GET /pending-jobs` —
*before* the hours pass (and the equipment pass just merged in #468) ran. Those two passes hit
**independent** Worker endpoints (`/hours-pending`, `/equipment-snapshot`), so a transient job-queue fetch
failure was silently starving the Hours Log mirror on every cycle it happened on — exactly matching the
operator's live symptom of crew time trickling into the sheet instead of appearing promptly.

Fix: a transient job-fetch failure now records the error (`counters['errors']`, DEGRADED heartbeat — never
silent) and leaves jobs dirty, but the cycle **falls through** so the hours + equipment passes still run.
A `PortalAuthError` (401 — the shared bearer failing every endpoint) still stops the whole cycle unchanged.

**ops-stds-enforcer flagged a second-order regression in the same PR:** decoupling the passes lets a cycle
complete (a marker gets written) even when the job-queue fetch fails on every cycle — which silently
removed the prior "no marker → Check-C-stale" escalation path for a *sustained* job-fetch-only outage.
Fixed in a follow-up commit: a persisted consecutive-failure counter
(`~/its/state/fieldops_pending_fetch_failures.json`, via `state_io`, the same pattern as `portal_poll`
Check-Q) escalates the per-cycle ERROR to CRITICAL (triple-fire) at **≥5 consecutive cycles**; a successful
fetch resets it. New tests cover both the transient (hours pass still runs) and sustained
(ERROR→CRITICAL escalation) paths; reverting either fix red-lights its test. Runbook gained a Symptom-D
note naming the observable trigger and correcting the remediation pointer to Worker reachability (not the
Smartsheet circuit breaker). Python-only; no Worker/TS change.

### #470 — P7 M2, Material List tracker (portal-authored, one-way-up SNAPSHOT, ships DARK)

Resolves the second Task-C decision: **Option A** (operator-ratified) — the operator authors the per-job
material list in the portal (`cap.materials.manage` CRUD on `job_expected_materials`, migration 0031); M2
mirrors the whole list — expected content + delivery state + an `unplanned` flag — **one-way UP** to a
per-job `<Job> — Material List` Smartsheet. Structural clone of the just-merged Equipment tracker (same
roster-reconcile / count-drops-to-zero pattern from #468, applied from the start this time).

- **Migration 0039** — `job_expected_materials` += `line_uuid` (mirror key, backfilled + unique index;
  Worker mints `crypto.randomUUID()` on add) + `unplanned` (default 0). Deliberately **no
  `smartsheet_row_id`** column — that's a bidirectional-only concern, commented as deferred.
- New send-free, fully-bound route `GET /api/internal/fieldops/material-list-snapshot` →
  `{ lines, jobs_with_materials }`; `received_by` resolved display-name-only, matching the existing
  expected-materials read route.
- `progress_reports/material_list.py` (mirrors `equipment_status.py`): find-no-create + change-only
  upsert-by-Line-UUID, retire-removed (never delete), row-cap watchdog.
- `fieldops_sync._mirror_material_list_pass` gated OFF by `field_ops.fieldops_sync.materials_enabled`,
  runs as a downstream pass (benefits from the #469 decouple fix — survives a transient job-fetch
  failure). Archive-on-closure hook extended to move all three trackers (Hours Log, Equipment, Material
  List) together.

**Doctrine-drift finding (WARN, not BLOCK — needs Seth before enabling):** `ops-stds-enforcer` flagged that
Op Stds v19 §51 (`~/its-blueprint/doctrine/operational-standards.md` line 847) and the Progress-Reporting
mission both explicitly describe the Material List as **"bidirectional with split column ownership"** (the
operator owns content columns, the field owns delivery columns, neither overwrites the other) — but what
shipped in #470 is **one-way-up only**, per the operator's live Option-A ratification during this session.
This is a genuine drift between what's written in doctrine/mission and what's built, not a build defect —
the PR itself is internally consistent and reviewed clean. Flagging rather than silently reconciling:
**Seth needs to either (a) update §51 + the mission to describe the one-way-up model as the accepted
final shape, or (b) treat one-way-up as an interim M2 slice with bidirectional receive still queued** before
`materials_enabled` is flipped live. Tracked so this doesn't get silently absorbed as "done" against a
doctrine text that still says something different. **portal-worker-security-reviewer: CLEAN** (no
security-relevant finding; the flag above is a doctrine/build alignment concern, not a vulnerability).

## Four-part landing verify (all three PRs — quote verbatim)

**#468 → `95a9384`:**
- pytest: 2437 passed / 48 deselected
- mypy: 0 errors / 248 source files
- ruff: clean
- main-branch CI on merge commit `95a9384`: `state=MERGED` · `mergedAt=2026-07-05T15:01:48Z` ·
  `mergeCommit` present · `test`+`portal`+`secrets` SUCCESS
- worker vitest: 790 passed (52 files)

**#469 → `466e1e8`:**
- pytest: full pass (no regressions)
- mypy: 0 errors / 248 source files
- ruff: clean
- main-branch CI on merge commit `466e1e8`: `state=MERGED` · `mergedAt=2026-07-05T16:00:52Z` ·
  `mergeCommit` present · `test`+`portal`+`secrets` SUCCESS

**#470 → `f7f3764`:**
- pytest: 2475 passed / 48 deselected
- mypy: 0 errors / 250 source files
- ruff: clean
- worker vitest: 799 passed (53 files)
- main-branch CI on merge commit `f7f3764`: `state=MERGED` · `mergedAt=2026-07-05T16:34:10Z` ·
  `mergeCommit` present · `test`+`portal`+`secrets` SUCCESS

**Honest record on #470's CI — a flake, not a build defect.** The first main-branch CI attempt on
`f7f3764` (`run_attempt=1`) came back `test`=success, `secrets`=success, **`portal`=failure** — the
failing test was `src/pages/__tests__/FormFillPage.r3.test.tsx > FormFillPage — R3 dirty guard >
touching a field reports dirty + arms beforeunload; submit clears both`, a timing-sensitive
`vi.fn()` last-call assertion in an unrelated SPA test file. M2 (#470) touched **zero** SPA files (it's a
Worker route + two D1 migration files + Python daemon code) — the failure had no causal link to this PR's
diff. Re-running the failed job (`run_attempt=2`) came back clean on all three legs. The four-part verify
above reflects the **final** state after the re-run — `test`+`portal`+`secrets` SUCCESS — which is the
honest, complete answer: clean, but only after a re-run, and the re-run target is named so this doesn't
read as "just passed."

## Decisions made during session

1. **Equipment tracker shape = SNAPSHOT, not event log.** Rejected a full-event append-log (one row per
   location-change event) in favor of one row per equipment item, upserted in place. Reasoning: matches the
   §51 low-volume-log precedent (the 2026-07-04 v19.x Hours Log rider) — a bounded, always-current view is
   what the operator actually wants to see ("where is equipment X right now"), not a growing history table
   that needs its own row-cap/period-split machinery. Carried decision from 2026-07-04 Task-C, executed here.
2. **Material List model = Option A (one-way-up), not bidirectional-from-day-one.** Rejected building the
   full bidirectional split-column-ownership model (operator owns content, field owns delivery) that §51 and
   the mission describe, in favor of a simpler one-way mirror of the operator-authored list. Reasoning:
   ships a working tracker now against the portal's existing `cap.materials.manage` CRUD without inventing
   an unbuilt down-sync/receive path; explicitly deferred (not abandoned) per the migration comment and the
   PR's "NOT bidirectional (deferred future model)" framing. **Consequence: opened the doctrine-drift finding
   above** — the tradeoff was made knowingly, but the doctrine text wasn't updated in the same PR, so it's
   flagged rather than silently reconciled.
3. **The #469 fix falls through on transient failure, still hard-stops on 401.** Rejected "always keep going
   regardless of error type" in favor of preserving the existing auth-failure hard-stop. Reasoning: a bad
   shared bearer fails every downstream endpoint identically, so there's nothing to gain by attempting the
   hours/equipment passes anyway, and collapsing that distinction would have hidden a real outage behind a
   partial-success cycle.
4. **Sustained-outage escalation via a persisted consecutive-failure counter, not a bare log line.** Rejected
   leaving the job-fetch failure as a per-cycle ERROR only. Reasoning: the decouple fix itself removed the
   prior "no marker → stale → Check C" escalation path for this specific failure mode; a silent WARN-forever
   would have been a real regression in observability. Reused the exact pattern already proven in
   `portal_poll` Check-Q (persisted JSON counter via `state_io`, ≥5 consecutive → CRITICAL, reset on success)
   rather than inventing a new one.

## Open items handed off

- **Seth: reconcile the Material List doctrine-drift.** Op Stds v19 §51 (line 847) and
  `~/its-blueprint/workstreams/progress-reporting/mission.md` both name the Material List as
  "bidirectional with split column ownership"; the shipped M2 (#470) is one-way-up only. Resolve before
  flipping `field_ops.fieldops_sync.materials_enabled=true` — either amend §51/mission to describe the
  one-way-up shape as accepted, or explicitly re-open the bidirectional-receive slice as a committed
  follow-up (M2b) before go-live.
- **Operator go-live steps for M2 (per the PR body):** apply migration 0039 to remote D1, deploy the
  Worker, then flip `field_ops.fieldops_sync.materials_enabled=true`. Ships dark until then.
- **Operator go-live step for Slice 2 (per the PR body):** flip
  `field_ops.fieldops_sync.equipment_enabled=true` after review/smoke. Ships dark until then.
- **Hours Log Started/Ended → Task column** — logged 2026-07-05 in `docs/tech_debt.md` [OPEN 2026-07-05],
  NOT built in the M2 PR. The `Started`/`Ended` columns are always-empty in practice (the portal time-log
  form never captures wall-clock start/end); the operator wants a `Task` column resolved from
  `task_assignments.description` instead. Multi-surface fan-out already enumerated in the tech-debt entry
  (Worker route, `hours_log.py`, `fieldops_sync.py`, `portal_client.py`, tests, plus a one-time live
  sheet-schema migration for the already-created Hours Log sheet).
- **The underlying `/pending-jobs` transport flakiness is still open** (per #469's own note) — likely
  transient Worker 500s and/or Cloudflare bot-fight challenges on the daemon's requests. #469 mitigates the
  *impact* (hours/equipment no longer held hostage); the transport-cause hardening (retry/UA/WAF) is a
  separate, not-yet-scoped item.

## What was NOT touched

- No canonical/customer-owned Evergreen Smartsheet integration work (still parked per
  `decision_p2.4-parked-no-smartsheet-access.md` — unrelated to this session's ITS-owned-SoR trackers).
- No down-sync / bidirectional receive path for the Material List was built (see doctrine-drift item above
  — explicitly deferred, not silently dropped).
- No Worker/TypeScript change in #469 — Python-only fix, as stated in its own commit message.
- Neither `equipment_enabled` nor `materials_enabled` was flipped this session — both ship dark, awaiting
  operator go-live per each PR's stated steps (only `hours_enabled` was flipped).
- M3 (material incidents + photos) was not started — still queued behind M2 per the roadmap.

## Lessons captured to memory

- Confirms the **§34 Option-D-adjacent snapshot pattern** (`reference_section34-option-d-photo-pool.md`'s
  sibling for tracker mirrors, not photos): a per-job standing snapshot sheet + roster-reconcile for
  count-drops-to-zero is now proven twice (#468 Equipment, #470 Material List clone) — the roster-reconcile
  fix from #468 was correctly *carried forward from the start* in #470 rather than rediscovered, which is
  the payoff of naming and tracking the pattern rather than re-deriving it per-tracker.
- Reinforces **"don't act on a stale current-state claim" in the doctrine direction too** — a PR can be
  internally clean and adversarially reviewed clean while still drifting from what doctrine *says* the
  final shape should be; `ops-stds-enforcer` catching that gap (rather than just checking the diff against
  itself) is why the doctrine-drift finding above exists instead of a quiet divergence.
- No new House Reflexes entry needed this session — the live-decouple bug (#469) and the reconcile-roster
  bug (#468) are both instances of the existing "enumerate all delivery/consumption surfaces" reflex
  (`feedback_multi-surface-fan-out.md`): a daemon pass silently assumed its dependency's payload was the
  complete surface of "jobs that need attention," which it wasn't once a job fell to zero rows.
