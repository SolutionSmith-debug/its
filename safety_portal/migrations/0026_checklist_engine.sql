-- Assigned-Tasks tab (P4 field-ops feature) S2 — the checklist ENGINE schema + the daily_default seed.
--
-- One templates→instances engine (spec Q6) serves BOTH the daily "Progress Report" checklist and the
-- admin-composed inspection checklists. S2 builds + USES only the template side (daily_default global
-- row + per-job job_override rows, edited on the Job Tracker detail); instance generation / completion
-- / loop-closure / rollup / the inspection library are LATER slices (S3–S6). The instance tables are
-- CREATED here so the schema is complete + indexed, but nothing writes them until S3.
--
-- THE MERGE (the load-bearing bit, built + tested in the Worker + tests this slice): a job's EFFECTIVE
-- daily checklist =
--   [ daily_default items whose id is NOT in the job's job_override suppresses_default_item_id set ]
--   ∪ [ the job_override template's own added items ]
-- ordered by seq. A job with no job_override row → just the daily_default items. Editing the default
-- propagates to every un-overridden job; a per-job ADD = an item row on the job_override template; a
-- per-job REMOVE of a DEFAULT item = a suppression marker (a job_override item row whose
-- suppresses_default_item_id points at the hidden default item). Computed, never stored.
--
-- CAP: template authoring is cap.checklist.manage (admin; seeded 0013) — no new capability row.
--
-- ORDER DEPENDENCY (activation — the deploy-lockout class, same rule as 0007/0013/0023/0025): apply
-- this migration to the live D1 with
--   wrangler d1 migrations apply its-safety-portal-db --remote
-- BEFORE the Worker that registers the checklist routes deploys — otherwise those routes 500 on the
-- missing tables. All CREATE TABLE IF NOT EXISTS + guarded seed (INSERT … WHERE NOT EXISTS /
-- INSERT OR IGNORE), so re-applying is safe.

-- ── checklist_templates — the template header. kind partitions the four template classes. ──────────
-- daily_default: exactly ONE global row (job_id NULL) — the rolling SOP daily checklist. job_override:
-- one row per customized job (job_id set) carrying that job's added items + suppression markers.
-- generic_inspection / specific_inspection: the S6 inspection library (unused in S2).
CREATE TABLE IF NOT EXISTS checklist_templates (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  kind            TEXT    NOT NULL,            -- daily_default | job_override | generic_inspection | specific_inspection
  job_id          TEXT,                        -- set for job_override; NULL for the global default + library
  title           TEXT,
  source_form_code TEXT,                        -- daily_default seeds from 'daily-report'
  active          INTEGER NOT NULL DEFAULT 1,
  created_at      INTEGER NOT NULL DEFAULT (unixepoch())
);
-- One job_override template per job (the merge assumes a single override row per job). Partial unique
-- so only job_override rows are constrained; the single daily_default row (job_id NULL) is unaffected.
CREATE UNIQUE INDEX IF NOT EXISTS idx_checklist_templates_job_override
  ON checklist_templates(job_id) WHERE kind = 'job_override';
CREATE INDEX IF NOT EXISTS idx_checklist_templates_kind ON checklist_templates(kind);

-- ── checklist_items — the ordered items on a template. ─────────────────────────────────────────────
-- item_type: form_linked (deep-links + auto-checks on a matching submission — S4) | manual_attest
-- (check + optional note/photo) | count (value ≥ target_count) | inspection (link to an inspection form).
-- A job_override row with suppresses_default_item_id SET is a suppression MARKER: it hides that
-- daily_default item for the job and carries no item content of its own (label/type are placeholders).
CREATE TABLE IF NOT EXISTS checklist_items (
  id                          INTEGER PRIMARY KEY AUTOINCREMENT,
  template_id                 INTEGER NOT NULL REFERENCES checklist_templates(id),
  seq                         INTEGER NOT NULL DEFAULT 0,
  item_type                   TEXT    NOT NULL,   -- form_linked | manual_attest | count | inspection
  label                       TEXT,
  form_code                   TEXT,               -- form_linked / inspection: the target form
  target_count                INTEGER,            -- count: the N
  config_json                 TEXT,               -- per-type extras
  suppresses_default_item_id  INTEGER             -- job_override rows: hide this daily_default item (the merge)
);
CREATE INDEX IF NOT EXISTS idx_checklist_items_template ON checklist_items(template_id, seq);
CREATE INDEX IF NOT EXISTS idx_checklist_items_suppresses ON checklist_items(suppresses_default_item_id);

-- ── checklist_instances — a materialized checklist for a (job, assignee, date). UNUSED until S3. ────
-- daily: one per (job_id, placed-manager, instance_date), generated Worker-on-read (idempotent on the
-- UNIQUE key). inspection: an admin-assigned instance (S6). rolled_up_submission_uuid = the auto-filed
-- Daily Report submission (S5).
CREATE TABLE IF NOT EXISTS checklist_instances (
  id                       INTEGER PRIMARY KEY AUTOINCREMENT,
  kind                     TEXT    NOT NULL,       -- daily | inspection
  job_id                   TEXT,
  assignee_personnel_id    INTEGER,
  instance_date            TEXT,                    -- daily: the local date the instance covers
  status                   TEXT    NOT NULL DEFAULT 'open',   -- open | complete
  rolled_up_submission_uuid TEXT,
  created_at               INTEGER NOT NULL DEFAULT (unixepoch()),
  UNIQUE(kind, job_id, assignee_personnel_id, instance_date)
);
CREATE INDEX IF NOT EXISTS idx_checklist_instances_assignee
  ON checklist_instances(assignee_personnel_id, status);

-- ── checklist_item_states — per-instance item snapshot + completion. UNUSED until S3. ──────────────
-- Snapshots the effective item at generation time (source_item_id → the checklist_items row it came
-- from) so a later template edit never mutates an in-flight instance.
CREATE TABLE IF NOT EXISTS checklist_item_states (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  instance_id    INTEGER NOT NULL REFERENCES checklist_instances(id),
  source_item_id INTEGER,
  item_type      TEXT    NOT NULL,
  label          TEXT,
  form_code      TEXT,
  target_count   INTEGER,
  status         TEXT    NOT NULL DEFAULT 'open',   -- open | done
  completed_by   TEXT,
  completed_at   INTEGER,
  note           TEXT,
  photo_ref      TEXT,
  value_num      REAL
);
CREATE INDEX IF NOT EXISTS idx_checklist_item_states_instance ON checklist_item_states(instance_id);

-- ── Seed the daily_default template + its starter items (from forms/daily-report-v1.json sections) ──
-- id=1 by AUTOINCREMENT on a fresh table. INSERT OR IGNORE + a NOT-EXISTS guard keep the seed a
-- one-shot on re-apply (the partial unique index doesn't cover kind='daily_default', so a second run
-- would otherwise duplicate it). source_form_code = the 'daily-report' catalog parent (the form the
-- daily checklist rolls up into, S5). The admin edits these items later via the S2 default-editor routes.
INSERT INTO checklist_templates (id, kind, job_id, title, source_form_code, active)
SELECT 1, 'daily_default', NULL, 'Daily Progress Report checklist', 'daily-report', 1
WHERE NOT EXISTS (SELECT 1 FROM checklist_templates WHERE kind = 'daily_default');

-- Starter items on the daily_default template: the Daily Field Report itself as a form_linked item
-- (auto-checks in S4 when a daily-report submission exists for the job+day), then one manual_attest
-- per SOP-style section of daily-report-v1.json (crew progress, tomorrow's goals, equipment, deliveries,
-- site visitors). Minimal + sensible — the admin tailors it. Guarded so re-apply is a no-op.
INSERT INTO checklist_items (template_id, seq, item_type, label, form_code)
SELECT 1, 10, 'form_linked', 'File the Daily Field Report', 'daily-report'
WHERE NOT EXISTS (SELECT 1 FROM checklist_items WHERE template_id = 1);
INSERT INTO checklist_items (template_id, seq, item_type, label)
SELECT 1, 20, 'manual_attest', 'Record crew / subcontractor progress'
WHERE NOT EXISTS (SELECT 1 FROM checklist_items WHERE template_id = 1 AND seq = 20);
INSERT INTO checklist_items (template_id, seq, item_type, label)
SELECT 1, 30, 'manual_attest', "Note tomorrow's progress goals"
WHERE NOT EXISTS (SELECT 1 FROM checklist_items WHERE template_id = 1 AND seq = 30);
INSERT INTO checklist_items (template_id, seq, item_type, label)
SELECT 1, 40, 'manual_attest', 'Log equipment on site'
WHERE NOT EXISTS (SELECT 1 FROM checklist_items WHERE template_id = 1 AND seq = 40);
INSERT INTO checklist_items (template_id, seq, item_type, label)
SELECT 1, 50, 'manual_attest', 'Record deliveries received'
WHERE NOT EXISTS (SELECT 1 FROM checklist_items WHERE template_id = 1 AND seq = 50);
INSERT INTO checklist_items (template_id, seq, item_type, label)
SELECT 1, 60, 'manual_attest', 'Record site visitors'
WHERE NOT EXISTS (SELECT 1 FROM checklist_items WHERE template_id = 1 AND seq = 60);
