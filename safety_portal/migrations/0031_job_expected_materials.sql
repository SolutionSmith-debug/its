-- Material receipts (M1) — per-job EXPECTED-materials list (`job_expected_materials`).
--
-- The office (cap.materials.manage) records what materials a job is expecting — at job creation
-- or as the job develops — and managers later confirm receipt against this list (the manager-side
-- receive/flag routes ship here in M1; the daily-form receipt flow + the material-incident form
-- wire them into the field workflow in M2). One row per expected arrival:
--   • material_id (nullable) → material_catalog.id for catalog-picked rows (the 0019 TYPE
--     vocabulary; validated ACTIVE at write). NULL = a free-text row, where `description` is
--     REQUIRED (bounded at write). Soft reference — no FK — consistent with the catalog's own
--     "retire keeps the target" posture: retiring a type never orphans an expectation.
--   • status ∈ expected | received | incident. The receive/flag-incident routes guard the
--     transition IN-WHERE (status='expected') so a repeat action is a clean 409, never a
--     double-stamp. `received_at`/`received_by`/`qty_received`/`note` are stamped by that action.
--   • received_by stores the acting ACCOUNT username; reads resolve it to the personnel DISPLAY
--     NAME only (W9 posture — an unmatched account yields NULL, never the raw username).
--   • job_id is a soft reference to jobs.job_id (validated at write — same convention as
--     personnel.current_job / task_assignments.job_id).
--   • seq orders the list (the checklist 10/20/30 renumber convention); active=1 soft-delete
--     ("deactivate") so a received/incident row keeps its history when removed from view.
--
-- Capabilities: cap.materials.manage (admin — expectation CRUD) + cap.materials.receive
-- (submitter+manager+admin — read + receive/flag, per-job ownership-scoped) are ALREADY seeded
-- (0013 + 0023) — cap.materials.receive was reserved for exactly this. NOT re-seeded here.
--
-- NUMBERING: 0030 is taken by the D4 slice (built in a parallel worktree); both migrations are
-- purely additive, so apply order is safe regardless of merge order.
--
-- ORDER DEPENDENCY (activation): apply BEFORE the Worker that reads/writes
-- job_expected_materials deploys (worker/fieldops_expected_materials.ts binds these columns).
-- Mirrors the 0019 activation rule. See safety_portal/README.md "Expected materials (M1 — 0031)".

CREATE TABLE IF NOT EXISTS job_expected_materials (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  job_id        TEXT    NOT NULL,                      -- soft-ref jobs.job_id (validated at write)
  material_id   INTEGER,                               -- nullable soft-ref material_catalog.id (catalog-picked rows)
  description   TEXT,                                  -- free-text description (REQUIRED when material_id IS NULL)
  qty           REAL,                                  -- expected quantity (optional; > 0 when present)
  unit          TEXT,                                  -- unit label, e.g. 'panels' / 'ft' / 'pallets' (bounded at write)
  expected_date TEXT,                                  -- optional YYYY-MM-DD arrival estimate
  status        TEXT    NOT NULL DEFAULT 'expected'
                CHECK (status IN ('expected','received','incident')),
  received_at   INTEGER,                               -- unixepoch stamp set by receive / flag-incident
  received_by   TEXT,                                  -- acting ACCOUNT username (reads resolve display-name-only — W9)
  qty_received  REAL,                                  -- optional actual quantity recorded at receive
  note          TEXT,                                  -- receive / incident note (bounded at write)
  seq           INTEGER NOT NULL DEFAULT 0,            -- display order (checklist renumber convention)
  active        INTEGER NOT NULL DEFAULT 1,            -- 1 = live, 0 = deactivated (soft-delete; history kept)
  created_at    INTEGER NOT NULL DEFAULT (unixepoch())
);

-- The per-job read is the hot path (job detail + the M2 daily tab): job scope, active rows, seq order.
CREATE INDEX IF NOT EXISTS idx_job_expected_materials_job
  ON job_expected_materials(job_id, active, seq);
