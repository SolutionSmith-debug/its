-- Recurring checklists per job (#16) — the recurrence DEFINITION table.
--
-- ORDER DEPENDENCY: apply this to the live D1 BEFORE deploying the Worker. The same PR's
-- POST /api/fieldops/checklist/assign (recurring branch), GET /checklist/recurrences,
-- POST /checklist/recurrence/:id/deactivate, and the scheduled() cron generation pass all read
-- checklist_recurrences; a Worker deployed ahead of this migration would 500 those surfaces.
-- Blast radius is bounded — the whole feature ships DARK behind the Worker var
-- RECURRING_CHECKLISTS_ENABLED (default "false"), so the cron no-ops and the assign route refuses
-- a recurrence block with 400 recurring_disabled until the operator flips the var + redeploys — but
-- apply-before-deploy is the canonical order (README punch-list = the single apply-all-then-deploy
-- source; the stale-migrations-list lockout class, 2026-06-28).
--
-- WHY A NEW TABLE (not columns on an existing row): a checklist "assignment" is not a standing
-- entity today — POST /checklist/assign creates a one-shot kind='inspection' checklist_instances
-- row and returns; there is nothing to hang recurrence columns on. checklist_instances deliberately
-- carries NO template_id (0029:4-5 — an instance lives on its checklist_item_states SNAPSHOT,
-- decoupled from later template edits). A recurrence is a DEFINITION the generator reads each cycle
-- to spawn those one-shot instances on a cadence, so it gets its own table.
--
-- HOW GENERATION STAYS IDEMPOTENT (no double-spawn): each spawned instance is a normal
-- kind='inspection' checklist_instances row keyed on its on-cadence instance_date, so the EXISTING
-- UNIQUE(kind, job_id, assignee_personnel_id, instance_date) (0026:78) dedupes a re-run for the
-- same date via INSERT OR IGNORE. `last_generated_date` is the per-recurrence watermark the cron
-- advances so it only enumerates NEW on-cadence dates each pass (a crash before the watermark
-- advance is self-healing: the next pass re-enumerates from the same watermark and INSERT OR IGNORE
-- absorbs the already-created dates).
--
-- CADENCE IS APP-VALIDATED, NOT DB-CHECKed (deliberate — the operator asked for an EXTENSIBLE
-- cadence set): the Worker's RECURRENCE_CADENCES set (worker/fieldops_recurrence.ts) is the single
-- validation authority. Adding a cadence is then a Worker-code change (the interval math lives there
-- anyway) with NO table rebuild — unlike a CHECK, which SQLite can only widen via the 0020/0032
-- table-rebuild dance. A malformed cadence never reaches this table: the assign route rejects it
-- (400) before the INSERT.
--
-- Additive + re-apply-safe: CREATE TABLE / CREATE INDEX IF NOT EXISTS.
CREATE TABLE IF NOT EXISTS checklist_recurrences (
  id                     INTEGER PRIMARY KEY AUTOINCREMENT,
  -- The generic_inspection LIBRARY template spawned each cadence date (validated at assign time).
  template_id            INTEGER NOT NULL REFERENCES checklist_templates(id),
  -- The active personnel the spawned instances are assigned to (the same assignee the one-shot
  -- assign uses; validated active at assign time).
  assignee_personnel_id  INTEGER NOT NULL,
  -- Recurring is per-JOB (operator: "recurring checklists per job") — job_id is REQUIRED here
  -- (unlike the optional job on a one-shot assign) so generation can stop when the job closes.
  job_id                 TEXT    NOT NULL,
  -- daily | weekly | biweekly | monthly (extensible; validated by the Worker, see header).
  cadence                TEXT    NOT NULL,
  -- The "generates off of" date (YYYY-MM-DD, Pacific calendar) — the first on-cadence instance_date
  -- and the phase anchor for weekly/biweekly (day-of-week) and monthly (day-of-month) stepping.
  anchor_date            TEXT    NOT NULL,
  -- 1 = generating; 0 = stopped (explicit operator deactivate, OR auto-stopped when the job closes).
  active                 INTEGER NOT NULL DEFAULT 1,
  -- Watermark: the last Pacific date the generator scanned THROUGH (NOT necessarily an on-cadence
  -- date). NULL until the first spawn. The cron enumerates on-cadence dates in (watermark, today].
  last_generated_date    TEXT,
  -- Title SNAPSHOTTED at define time so the spawned instances + admin list show the authored name
  -- even after the library template is renamed/deleted (mirrors instances.template_title, 0029).
  template_title         TEXT,
  created_at             INTEGER NOT NULL DEFAULT (unixepoch()),
  -- The admin who defined the recurrence (display/audit only).
  created_by             TEXT,
  -- One recurrence definition per (template, person, job): re-defining the same triple is an UPSERT
  -- (reactivate + refresh cadence/anchor), never a duplicate generator. A deactivated definition is
  -- reactivated by re-assigning, not by a second row.
  UNIQUE (template_id, assignee_personnel_id, job_id)
);

-- The cron's per-cycle read is "all active recurrences" → an active-leading index keeps it cheap.
CREATE INDEX IF NOT EXISTS idx_checklist_recurrences_active
  ON checklist_recurrences (active, job_id);
