-- URS Marine — the INTEGRITY BAR tables (B4, mission §4 / brief §2).
--
-- THIS IS LOAD-BEARING. URS D1 holds timesheet-grade data of real value that feeds
-- payroll/billing — it REVERSES the Safety Portal's "stores nothing of value" scoping
-- and its unlimited-backdating acceptance. So for time/work, inspections, and machine
-- logs, backdating WITHOUT A TRACE is unacceptable. Every such table enforces:
--
--   1. TWO DISTINCT TIMESTAMP CLASSES.
--      * RECORD time  — `created_at` / `edited_at` — SERVER-AUTHORITATIVE, set with
--        unixepoch() at receipt/edit. NEVER the phone clock. The Worker write path does
--        NOT bind these from request input (see worker/index.ts POST /api/urs/time-entry:
--        the INSERT hard-codes `unixepoch()` for both — a forged created_at in the body
--        is ignored). A field cannot move its own record time.
--      * EVENT time   — when the worker SAYS the work happened — a FIELD-REPORTED CLAIM in
--        its own column(s) (`work_started_at`/`work_ended_at`, `performed_at`), distinct
--        from the record time. Stored verbatim, never authoritative.
--   2. ATTRIBUTION — `actor_username` (the authenticated session user) + `submitted_as`
--      (the attributed account; equals actor on a self-submit) — generalizes migration
--      0008's dual attribution onto every accumulating record (not just submissions).
--   3. APPEND-ONLY EDIT CHAIN — `amends_uuid` points at the prior row this amends; an edit
--      is a NEW row, the original is NEVER mutated. Plus an `audit_log` row per create/edit
--      (action time_entry_create|time_entry_edit|inspection_*|equipment_log_*). The audit
--      trail proves who entered/edited each record and when — so backdating leaves a trace.
--   4. VERSION-PINNING — each filled `inspections` row pins the `form_code` + `version` it
--      was captured against, so checklist content is reproducible.
--
-- A PM tool (Monday) holds NONE of this as backbone — only bounded rollups pushed via the
-- B3 adapter.
--
-- ORDER DEPENDENCY (activation): apply BEFORE the Worker that writes these tables deploys.

-- time_entries — time/work per job. Accumulating (SoR). INTEGRITY BAR.
CREATE TABLE IF NOT EXISTS time_entries (
  uuid            TEXT    PRIMARY KEY,        -- client-supplied id (idempotency / amend target)
  job_id          TEXT    NOT NULL,
  personnel_id    INTEGER REFERENCES personnel(id),
  -- EVENT time (field-reported claims) — distinct from the record timestamps below:
  work_started_at INTEGER,                    -- field-reported epoch claim
  work_ended_at   INTEGER,                    -- field-reported epoch claim
  hours           REAL,                       -- field-reported
  notes           TEXT,
  -- RECORD time (server-authoritative — NEVER from the client):
  created_at      INTEGER NOT NULL DEFAULT (unixepoch()),
  edited_at       INTEGER NOT NULL DEFAULT (unixepoch()),
  -- attribution (generalized from 0008):
  actor_username  TEXT    NOT NULL,
  submitted_as    TEXT,
  -- append-only edit chain (NULL = original; non-NULL = amends the named prior uuid):
  amends_uuid     TEXT
);
CREATE INDEX IF NOT EXISTS idx_time_entries_job ON time_entries(job_id, created_at);

-- inspections — filled machine checklists. Follows the inherited `submissions` shape,
-- VERSION-PINNED. Accumulating (SoR). INTEGRITY BAR.
CREATE TABLE IF NOT EXISTS inspections (
  uuid            TEXT    PRIMARY KEY,
  job_id          TEXT    NOT NULL,
  equipment_id    INTEGER REFERENCES equipment(id),
  form_code       TEXT    NOT NULL,           -- version-pin
  version         INTEGER NOT NULL,           -- version-pin (reproducible checklist content)
  payload_json    TEXT    NOT NULL,           -- the filled checklist (submissions-shaped)
  performed_at    INTEGER,                    -- field-reported event-time claim
  created_at      INTEGER NOT NULL DEFAULT (unixepoch()),
  edited_at       INTEGER NOT NULL DEFAULT (unixepoch()),
  actor_username  TEXT    NOT NULL,
  submitted_as    TEXT,
  amends_uuid     TEXT
);
CREATE INDEX IF NOT EXISTS idx_inspections_equipment ON inspections(equipment_id, created_at);

-- equipment_logs — maintenance / hours / fuel event log. Accumulating (SoR). INTEGRITY BAR.
CREATE TABLE IF NOT EXISTS equipment_logs (
  uuid            TEXT    PRIMARY KEY,
  equipment_id    INTEGER NOT NULL REFERENCES equipment(id),
  log_type        TEXT    NOT NULL,           -- fuel | hours | maintenance
  value_num       REAL,                       -- hours reading / fuel qty (field-reported)
  detail          TEXT,                       -- free-text (field-reported)
  performed_at    INTEGER,                    -- field-reported event-time claim
  created_at      INTEGER NOT NULL DEFAULT (unixepoch()),
  edited_at       INTEGER NOT NULL DEFAULT (unixepoch()),
  actor_username  TEXT    NOT NULL,
  submitted_as    TEXT,
  amends_uuid     TEXT
);
CREATE INDEX IF NOT EXISTS idx_equipment_logs_equipment ON equipment_logs(equipment_id, created_at);
