-- Field-Ops core structured-data tables (PORTED from URS-Marine 0014_urs_core_tables; P2).
--
-- The accumulating field-ops reference tables: clients, personnel, equipment,
-- task_assignments, equipment_location, plus additive ALTERs to `jobs`. The integrity-bar
-- tables (time_entries, inspections, equipment_logs) land in 0015. Per the ITS data-residency
-- model (D1-primary + Smartsheet mirror, P2), the Mac mirror daemon mirrors these UP to
-- Smartsheet as the operator-visible system of record; Box remains the document SoR.
--
-- ORDER DEPENDENCY (activation): apply this migration to the live D1 BEFORE any Worker
-- that reads/writes these tables deploys. Additive (new tables + additive ALTER), so
-- applying it ahead of the new Worker is safe. Mirror of the 0005/0006/0007 rule.

-- clients — client info. Accumulating (SoR).
CREATE TABLE IF NOT EXISTS clients (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  name       TEXT    NOT NULL,
  contact    TEXT,
  phone      TEXT,
  email      TEXT,
  created_at INTEGER NOT NULL DEFAULT (unixepoch())
);

-- jobs — URS jobs are fed from WORK CONTRACTS (NOT a PM-tool mirror; the template's
-- minimal jobs table replaced the prior PM-mirror shape). Extend it with the URS columns
-- rather than a new table, so the inherited /api/jobs dropdown keeps working.
-- Accumulating (SoR).
ALTER TABLE jobs ADD COLUMN client_id INTEGER REFERENCES clients(id);
ALTER TABLE jobs ADD COLUMN status    TEXT NOT NULL DEFAULT 'active';   -- active | closed | on_hold
ALTER TABLE jobs ADD COLUMN progress  INTEGER NOT NULL DEFAULT 0;       -- 0..100 percent
-- NOTE (ITS port): SQLite/D1 forbids a NON-CONSTANT default on ALTER ... ADD COLUMN (unlike
-- the CREATE TABLEs above), so `(unixepoch())` is rejected here. Backfill existing
-- (Smartsheet-origin) jobs with 0; the portal job-create route stamps the real unixepoch() on INSERT.
ALTER TABLE jobs ADD COLUMN created_at INTEGER NOT NULL DEFAULT 0;

-- personnel — roster. Optional link to a portal account (users.username). Accumulating
-- (SoR, reference). Tier mapping is via the account's role/capabilities (0013), not here.
CREATE TABLE IF NOT EXISTS personnel (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  name       TEXT    NOT NULL,
  username   TEXT,                 -- portal account (users.username), nullable
  trade      TEXT,                 -- e.g. operator, foreman, laborer
  active     INTEGER NOT NULL DEFAULT 1,
  created_at INTEGER NOT NULL DEFAULT (unixepoch())
);

-- equipment — inventory / vehicles. Accumulating (SoR, reference).
CREATE TABLE IF NOT EXISTS equipment (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  name        TEXT    NOT NULL,
  kind        TEXT,                -- e.g. skid-steer, telehandler, barge, vehicle
  identifier  TEXT,                -- unit # / VIN / asset tag
  active      INTEGER NOT NULL DEFAULT 1,
  created_at  INTEGER NOT NULL DEFAULT (unixepoch())
);

-- task_assignments — assignments. Accumulating; "open tasks" = the bounded status!='done' view.
CREATE TABLE IF NOT EXISTS task_assignments (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  job_id       TEXT    NOT NULL,
  personnel_id INTEGER REFERENCES personnel(id),
  description  TEXT    NOT NULL,
  status       TEXT    NOT NULL DEFAULT 'open',   -- open | in_progress | done
  assigned_by  TEXT,                              -- actor_username who assigned
  created_at   INTEGER NOT NULL DEFAULT (unixepoch())
);
CREATE INDEX IF NOT EXISTS idx_task_assignments_job ON task_assignments(job_id, status);

-- equipment_location — append-only POINT-IN-TIME location reads (an event log). "Current
-- location" = the bounded latest-read view (MAX(read_at) per equipment). NO LIVE TRACKING:
-- the EXIF/location capture stays display-only, best-effort, with the REQUIRED 'unavailable'
-- path (mission §2 / Invariant 2). `read_at` is the field-reported claim; `recorded_at` is
-- the server-authoritative receipt time.
CREATE TABLE IF NOT EXISTS equipment_location (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  equipment_id INTEGER NOT NULL REFERENCES equipment(id),
  job_id       TEXT,
  lat          REAL,                 -- nullable: the 'unavailable' path stores NULL/NULL
  lon          REAL,
  label        TEXT,                 -- human label / 'unavailable'
  read_at      INTEGER,              -- FIELD-REPORTED point-in-time claim (EXIF/device)
  recorded_at  INTEGER NOT NULL DEFAULT (unixepoch())  -- SERVER-authoritative receipt time
);
CREATE INDEX IF NOT EXISTS idx_equipment_location_latest ON equipment_location(equipment_id, recorded_at);
