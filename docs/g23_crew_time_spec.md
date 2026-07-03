---
title: "G2.3 spec — crew edit/retire + time amend/void (locked semantics)"
workstream: safety_portal
status: active
related_prs: []
---

# G2.3 — Scoped crew EDIT/RETIRE + non-destructive time AMEND/VOID (mini-spec)

Operator-confirmed epic (2026-07-03): *"a subcontractor who typos a crew name can't fix it,
and a wrong time entry can't be corrected by anyone."* This spec records the verified schema
ground + the locked semantics; the build and its reviews are checked against this document.

Branch `feat/g23-crew-time`, worktree `~/its-g23` off `origin/main` `3fc1e3a`. Not committed.

---

## 1. Verified schema ground (live HEAD, not memory)

### 1.1 `time_entries` (migration 0015 + 0016) — the amend chain ALREADY EXISTS

`migrations/0015_urs_integrity_bar.sql` creates:

```sql
CREATE TABLE IF NOT EXISTS time_entries (
  uuid            TEXT    PRIMARY KEY,        -- client-supplied id (idempotency / amend target)
  job_id          TEXT    NOT NULL,
  personnel_id    INTEGER REFERENCES personnel(id),
  work_started_at INTEGER,                    -- field-reported epoch claim
  work_ended_at   INTEGER,                    -- field-reported epoch claim
  hours           REAL,
  notes           TEXT,
  created_at      INTEGER NOT NULL DEFAULT (unixepoch()),   -- server-authoritative
  edited_at       INTEGER NOT NULL DEFAULT (unixepoch()),
  actor_username  TEXT    NOT NULL,           -- authenticated session user
  submitted_as    TEXT,                       -- attributed account (dual attribution, 0008 style)
  amends_uuid     TEXT                        -- append-only edit chain; NULL = original
);
```

- 0016 adds `task_id INTEGER REFERENCES task_assignments(id)`.
- 0018 adds `idx_time_entries_personnel (personnel_id, created_at)`; 0015 adds
  `idx_time_entries_job (job_id, created_at)`. **No index on `amends_uuid`** — the heads-only
  read probes it per row, so migration **0034 adds one** (see §3). Nothing else is missing:
  uuid PK ✓, amends_uuid ✓, actor_username ✓, hours ✓, task_id ✓, notes ✓ (void reason rides
  `notes`; **no new column needed**).
- The `submissions` `amends_uuid` chain (0003/0008) is the repo precedent; `fieldops_rollup.ts`
  already collapses chains with `NOT EXISTS (SELECT 1 FROM time_entries x WHERE x.amends_uuid = t.uuid)`.

### 1.2 `personnel` (0014 + 0023 + 0027)

`id INTEGER PK`, `name`, `trade`, `username` (soft link to users, NULL = non-login),
`active` (soft-retire flag), `current_job` (0023 standing placement, soft ref),
`created_by TEXT` (0027 — the scoped-crew-create provenance; NULL for admin/manager-created).

### 1.3 Existing routes (verified in `worker/`)

- `fieldops_crew_write.ts` — `POST /api/fieldops/crew` (cap.crew.create; non-login only —
  LOGIN_KEYS rejected 400; auto-placed on the ACTOR's own `current_job`, 422 `not_placed`;
  stamps `created_by = actor`; W4 atomic batch + audit `crew_create`) and
  `GET /api/fieldops/crew/mine` (own linked personnel OR `created_by = actor`, active only).
- `fieldops_personnel_write.ts` — cap.personnel.manage `POST /personnel` (account branch
  self-gated `role==='admin'`), `/:id/update`, `/:id/link`, `/:id/unlink`, `/:id/retire`
  (retire ALSO self-gated `role==='admin'` — manager-no-retire, operator 2026-07-01).
  These remain UNCHANGED — the fuller tier.
- `fieldops_time_write.ts` — `POST /api/fieldops/time-entry` (cap.time.log): hours REQUIRED
  (0,24] else 422 `invalid_hours`; job must exist AND `active=1`; personnel must be active
  roster + subcontractor scoping (holder of cap.time.log WITHOUT cap.personnel.manage may
  target only own linked personnel OR `created_by = actor` → else 403 `forbidden_personnel`);
  task must belong to the job; submit-as needs cap.submit_as. **It currently passes a raw
  body `amends_uuid` straight into the INSERT with NO validation** — no head check, no
  existence check, no ownership check, no same-job check. See §4.3 for its disposition.
- Displays: `fieldops_jobtracker.ts` detail time leg (R7 — `task_description`,
  `recorded_by_name` display-name-only, keyset on `(created_at, uuid)`);
  `fieldops_personnel.ts` list latest-entry-per-person + detail time history;
  `fieldops_rollup.ts` labor sum (already amend-collapsed). **None of the first two are
  amend-aware today** — an amended entry double-shows. §5 fixes both.

### 1.4 Capability grants (verified 0013/0023/0027)

`cap.time.log`: submitter ✓ (0013), manager ✓ (0023), admin ✓ (0013 catch-all).
`cap.personnel.manage`: manager + admin. `cap.crew.create`: submitter + admin (0027 —
manager deliberately NOT granted; managers use the fuller personnel routes).

---

## 2. Locked semantics (operator confirm-all defaults)

1. **Crew edit** (name/trade only): a `cap.crew.create` holder may edit crew
   **WHERE `created_by` = self AND `active` = 1**. Managers/admins keep the existing
   unrestricted `cap.personnel.manage` route. Bounded fields (name ≤128, trade ≤64 — same as
   create); W4 atomic audit (`crew_update`).
2. **Crew retire** (scoped soft-retire, `active=0`): a `cap.crew.create` holder may retire
   crew they created IFF **(a)** the person has NO time entries logged by anyone else
   (`time_entries.personnel_id = person AND actor_username != actor` is empty — the actor's
   OWN entries don't block: a typo'd duplicate the sub logged time against is still theirs to
   retire) AND **(b)** the person isn't placed on a DIFFERENT job than the actor
   (`current_job IS NULL OR current_job = actor's current_job` — a person the office moved
   elsewhere is a real worker; escalate). Violations → **409** with clear copy
   (`crew_has_foreign_time` / `crew_on_other_job`). Not-owned / unknown id → 404 (no
   existence oracle). Already-retired owned row → 200 `already_retired` (idempotent, parity
   with the admin retire). Admin retire unchanged.
3. **Time amend — NON-DESTRUCTIVE.** `POST /api/fieldops/time-entry/:uuid/amend` creates a
   NEW row (fresh client `uuid`, `amends_uuid` = the target's uuid, **same `job_id` inherited
   from the target — never client-chosen**) carrying the corrected
   `{personnel_id, task_id, hours, notes, work_started_at, work_ended_at}`. The old row is
   NEVER mutated (UPDATE-free by construction).
   - **Who:** the ORIGINAL RECORDER (`target.actor_username === session actor`) — subject to
     their own live scoping for the corrected `personnel_id` (subcontractor {self, created-by}
     rule, same as create) — OR any `cap.personnel.manage` holder (manager/admin, unrestricted
     recorder-wise; corrected personnel must still be active roster). Everyone else → 403
     `forbidden_amend`.
   - **Chain rule — head-only:** only the HEAD of a chain (no row's `amends_uuid` points at
     it) can be amended. Amending a non-head → **409 `not_head`** (client copy points at the
     newest version). Enforced ATOMICALLY: the head check is folded into the INSERT
     (`INSERT … SELECT … WHERE NOT EXISTS (SELECT 1 FROM time_entries WHERE amends_uuid = ?target)`)
     so two concurrent amends of one head cannot fork the chain (house TOCTOU-fold style).
   - **Full-replacement body:** the amend carries the COMPLETE corrected entry (the UI
     prefills from the old row); omitted `personnel_id`/`task_id` mean job-level, NOT
     "keep old". Referential guards identical to create (active personnel, task belongs to
     the entry's job).
   - **Job-active NOT required** (deliberate divergence from create): the epic exists because
     "a wrong time entry can't be corrected by anyone" — blocking amends on a closed job would
     recreate exactly that. Safe because the amend inherits its job binding (no new placement
     on a closed job is possible) and every amend is chained + audited. The target row itself
     must exist (404 otherwise).
   - **Attribution:** `actor_username` = the corrector (session); `submitted_as` is INHERITED
     from the target (the attribution of the WORK doesn't change because someone corrected the
     record). No `submitted_as` body input on the amend route.
   - Audit action: `time_entry_edit` (the 0015-named action; `detail.void = true` on voids).
4. **Time void** = an amend with `hours = 0` + a **REQUIRED** non-empty reason (stored in
   `notes` — no new column; the schema already has notes). Missing reason → 422
   `void_requires_reason`. Amend hours bounds are therefore **[0, 24]** (create stays (0, 24]).
   A void row is displayed struck-through + "voided". A void is a chain head like any other —
   amending it again is allowed (recovery from a mistaken void).
5. **Display — chain HEADS only.** Every time read resolves to heads:
   `AND NOT EXISTS (SELECT 1 FROM time_entries x WHERE x.amends_uuid = t.uuid)` — **NOT
   EXISTS, never `NOT IN`** (a NULL in a `NOT IN` subquery poisons the whole predicate; the
   0033 prune fixed this exact class). Applied to: the jobtracker detail time leg, the
   personnel list latest-entry window, the personnel detail history. The rollup already does
   it. Head rows that ARE amendments (`amends_uuid IS NOT NULL`) get a "corrected" pill;
   voided heads (`amends_uuid IS NOT NULL AND hours = 0`) render struck-through with a
   "voided" pill (history stays reachable via the audit log — no new history UI, keep it
   minimal). The wire adds worker-computed `amended`/`voided`/`can_amend` booleans;
   `can_amend` (viewer is the recorder OR holds cap.personnel.manage) keeps the raw
   `actor_username` OFF the wire (R1 W9 display-name-only posture preserved).
6. **UI.**
   - **JobTracker time table:** rows gain Edit / Void controls, rendered ONLY when
     `can_amend` (and the job section's write area is visible). Edit reuses the log-time
     form in "amend mode" (prefilled from the row, banner + Cancel); Void asks for the
     required reason inline. Per-row busy + inline row feedback (the R7 optimistic-task
     pattern); never-silent errors.
   - **AddCrewSection (My Tasks — where a sub sees their crew today):** the crew list becomes
     rows with Edit / Retire controls on members the sub CREATED (`created_by_me`, new field
     on `/crew/mine`; the sub's OWN linked row gets no controls). Edit = inline name/trade
     mini-form; Retire = confirm + clear 409 copy routing real workers to the office.
     Never-silent + per-row busy per the house standard.

---

## 3. Migration 0034 (`0034_time_amend_index.sql`)

The schema already carries the full amend chain (§1.1) — 0034 is **performance-only**:

```sql
CREATE INDEX IF NOT EXISTS idx_time_entries_amends
  ON time_entries(amends_uuid) WHERE amends_uuid IS NOT NULL;
```

Backs the per-row `NOT EXISTS` head probe in every time read + the amend route's head-fold.
Partial (`WHERE amends_uuid IS NOT NULL`) because originals vastly outnumber amendments.
**No void column** (reason rides `notes`), **no lockout risk** (no route structurally
requires the index — apply-before-deploy still the rule, but a missed apply degrades to a
slower scan, not a 500). README punch-list row added (— placeholder) + activation section.

---

## 4. Route contracts (server = the boundary; SPA gates are convenience)

### 4.1 `POST /api/fieldops/crew/:id/update` — scoped crew edit (NEW, fieldops_crew_write.ts)

Gate: `requireSession` + `requireCapability("cap.crew.create")`. Body `{name, trade?}`,
create-route bounds. Ownership folded into the UPDATE (atomic):

```sql
UPDATE personnel SET name = ?2, trade = ?3
WHERE id = ?1 AND active = 1 AND created_by = ?4
```

`changes()=0` → 404 `not_found` (unknown id, retired, or not created by the actor — one
answer, no oracle). Audit `crew_update` via `auditStmtIfChanged` in the same batch.

### 4.2 `POST /api/fieldops/crew/:id/retire` — scoped crew retire (NEW, fieldops_crew_write.ts)

Gate: same as 4.1. No body. Guards folded into the UPDATE (atomic — no check-then-act):

```sql
UPDATE personnel SET active = 0
WHERE id = ?1 AND active = 1 AND created_by = ?2
  AND NOT EXISTS (SELECT 1 FROM time_entries WHERE personnel_id = ?1 AND actor_username != ?2)
  AND (current_job IS NULL OR current_job =
        (SELECT current_job FROM personnel WHERE username = ?2 AND active = 1 ORDER BY id ASC LIMIT 1))
```

`changes()=0` → disambiguate (read-back, the link-route pattern): no row `id AND created_by=actor`
→ 404 `not_found`; owned but `active=0` → 200 `already_retired`; foreign time exists → 409
`crew_has_foreign_time`; placed elsewhere → 409 `crew_on_other_job`. Audit `crew_retire`
(`auditStmtIfChanged`).

### 4.3 `POST /api/fieldops/time-entry` — create tightened (fieldops_time_write.ts)

A body `amends_uuid` is now **REJECTED (400 `use_amend_route`)**. Rationale: the raw
pass-through (§1.3) let any cap.time.log holder chain onto ANY uuid — dangling, cross-job,
non-head, another user's entry — silently bypassing every §2.3 rule; with the amend route in
place the pass-through is pure bypass surface. No SPA caller ever sent it (`logTime` has no
such field — verified), so nothing breaks. The two existing tests exercising create-with-
amends_uuid are rewritten against the amend route (the count only grows).

### 4.4 `POST /api/fieldops/time-entry/:uuid/amend` (NEW, fieldops_time_write.ts)

Gate: `requireSession` + `requireCapability("cap.time.log")` (all three tiers hold it, §1.4).
Body `{uuid, hours, personnel_id?, task_id?, notes?, work_started_at?, work_ended_at?}`.

Order: body guard → target load (`SELECT … WHERE uuid = ?param` → 404 `not_found`) →
**who** (`actor === target.actor_username` OR cap.personnel.manage → else 403
`forbidden_amend`) → hours bounds [0,24] (422 `invalid_hours`); `hours === 0` requires
non-empty trimmed notes (422 `void_requires_reason`) → personnel guard (active roster 422
`unknown_personnel`; subcontractor {self, created-by} scoping 403 `forbidden_personnel`) →
task guard (belongs to `target.job_id` — 422 `unknown_task`) → atomic head-folded INSERT +
conditional audit in ONE batch:

```sql
INSERT INTO time_entries
  (uuid, job_id, personnel_id, task_id, work_started_at, work_ended_at, hours, notes,
   actor_username, submitted_as, amends_uuid)
SELECT ?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11
WHERE NOT EXISTS (SELECT 1 FROM time_entries WHERE amends_uuid = ?11)
```

(`?2` = target.job_id, `?10` = target.submitted_as, `?11` = target uuid.) `changes()=0` →
409 `not_head`; UNIQUE violation on the new uuid → 409 `uuid_conflict` (replay dedupe).
`created_at`/`edited_at` omitted → server `unixepoch()` (integrity-bar rule 1). Audit
`time_entry_edit` with `{uuid, job_id, amends_uuid, void: hours===0}` via `auditStmtIfChanged`.

### 4.5 Reads — heads-only (fieldops_jobtracker.ts, fieldops_personnel.ts)

Jobtracker time leg gains (inside the existing keyset WHERE):

```sql
AND NOT EXISTS (SELECT 1 FROM time_entries x WHERE x.amends_uuid = t.uuid)
```

plus selected `t.amends_uuid` + `t.actor_username`, mapped in JS to
`amended = amends_uuid !== null`, `voided = amended && hours === 0`,
`can_amend = capPersonnelManage || actor_username === viewer`, then BOTH raw columns are
stripped before `c.json` (W9: no raw usernames on the wire). Same `NOT EXISTS` added to the
personnel LIST latest-entry inner window and the personnel DETAIL history. Rollup untouched
(already collapsed; a void's `hours = 0` contributes nothing by arithmetic).

### 4.6 `GET /api/fieldops/crew/mine` — `created_by_me` (fieldops_crew_write.ts)

Adds `CASE WHEN created_by = ?1 THEN 1 ELSE 0 END AS created_by_me` so the SPA can gate the
Edit/Retire controls to created-by-me rows without exposing raw `created_by` usernames.

---

## 5. Out of scope / untouched

- Python (`safety_reports/`, `shared/`, `scripts/`) — zero edits; `shared/portal_client.py`
  only *documents* the rollup's amend-collapse (comment). pytest must stay 2291 passing.
- `fieldops_personnel_write.ts` (manager/admin tier) — unchanged.
- Task/`task_assignments` UI regions of FieldOpsJobTracker/MyTasks — G2.6 territory; edits
  stay confined to the TIME + CREW regions.
- No history-browsing UI (audit log + chain suffice); no submissions-side changes.

## 6. Tests (the scoping matrix)

Worker (`test/fieldops-time-amend.test.ts`, `test/fieldops-crew-edit-retire.test.ts`, edits
to `fieldops-time-write.test.ts` + `fieldops-jobtracker.test.ts`):
creator/other-sub/manager/admin × crew-edit/crew-retire/time-amend/time-void; head-only
(non-head → 409, concurrent-fork impossible by construction — assert the folded INSERT's
0-changes path); non-destructive (original row byte-identical after amend); void requires
reason; amend inherits job + submitted_as; closed-job amend allowed; create rejects
amends_uuid (400 `use_amend_route`); heads-only jobtracker/personnel reads + the NULL-poison
regression (a NULL amends_uuid row present while filtering — NOT EXISTS immune); retire
guards (foreign time → 409, other-job placement → 409, own-time-only → retires); 404-no-oracle.
SPA (`FieldOpsJobTracker.test.tsx`, `AddCrewSection.test.tsx`): Edit/Void visible iff
`can_amend`; amend prefill + submit wire shape; void reason required client-side; voided
strike-through + corrected pill; crew Edit/Retire gated on `created_by_me`; 409 copy surfaced.

## 7. Gate (exit-checked in ~/its-g23)

`npm run typecheck` clean · worker vitest ≥ 638 baseline all-pass · SPA vitest ≥ 450
baseline all-pass · `.venv-wt/bin/python -m pytest` 2291 passed (Python untouched).
