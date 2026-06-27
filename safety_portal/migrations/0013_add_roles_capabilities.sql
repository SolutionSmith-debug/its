-- Field-Ops P0 — DB-driven role / capability model (ported + adapted from URS-Marine 0013).
--
-- Generalizes the hardcoded 2-value role (migration 0007's
-- `CHECK (role IN ('submitter','admin'))`) into an admin-editable schema: `roles`
-- (the role vocabulary), `capabilities` (the stable capability vocabulary), and
-- `role_capabilities` (the grant junction). The Worker resolves a user's role KEY to
-- its capability SET per request (auth.resolveCapabilities) — FAIL-SAFE: an unknown /
-- missing role, or a role with no grants, yields NO capabilities (never a privileged
-- one). This is migration 0007's "belt to the suspenders" reasoning, now load-bearing
-- in code: the value-list CHECK is SUPERSEDED by the `roles` FK + the fail-safe resolver.
--
-- ITS adaptation (vs URS): seed only the TWO tiers this product uses today —
-- `submitter` (the field PM, who runs the daily SOP checklist) and `admin` (the office,
-- who manages jobs/crew/equipment/materials and assigns tasks). Both values already exist
-- in the live `users.role` column (0007's CHECK), so the new FK is satisfied when rows are
-- copied. Adding a third tier later (e.g. `supervisor`) is a pure INSERT — no schema change.
-- The capability vocabulary + grant matrix below are ITS field-ops, NOT URS's photo/machine set.
--
-- audit_log (migration 0007) is PRESERVED untouched. Its action vocabulary gains
-- `capability_change` / `role_capability_change` once roles become admin-editable (the
-- admin-edit routes are a follow-on; the columns already accommodate the new actions).
--
-- ORDER DEPENDENCY (activation): apply this migration to the live D1 BEFORE the Worker
-- that resolves capabilities in requireSession deploys — otherwise the per-request
-- resolve errors and (fail-closed by design) yields no caps / 401s every session. Exact
-- mirror of the 0006/0007/0009 activation rule. The users-table rebuild below PRESERVES
-- existing rows; `users` has NO incoming FK (audit_log.actor_username, submissions.job_id,
-- pdf_requests.account are plain TEXT, not FKs), so the rebuild is safe.

-- ── Role + capability vocabulary ────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS roles (
  key        TEXT    PRIMARY KEY,
  label      TEXT    NOT NULL,
  is_system  INTEGER NOT NULL DEFAULT 0,   -- 1 = built-in tier, not operator-deletable
  created_at INTEGER NOT NULL DEFAULT (unixepoch())
);

CREATE TABLE IF NOT EXISTS capabilities (
  key         TEXT PRIMARY KEY,
  label       TEXT NOT NULL,
  description TEXT
);

CREATE TABLE IF NOT EXISTS role_capabilities (
  role_key       TEXT NOT NULL,
  capability_key TEXT NOT NULL,
  PRIMARY KEY (role_key, capability_key),
  FOREIGN KEY (role_key)       REFERENCES roles(key),
  FOREIGN KEY (capability_key) REFERENCES capabilities(key)
);

-- ── Seed the two tiers (is_system) ──────────────────────────────────────────────
-- submitter = field PM (runs the daily SOP checklist, field actions). admin = office
-- (manages everything + assigns tasks). Field crew with no login are the non-login
-- personnel roster (migration 0014), not a role here.
INSERT INTO roles (key, label, is_system) VALUES
  ('submitter', 'Submitter (Field PM)', 1),
  ('admin',     'Admin (Office)',       1);

-- ── Seed the ITS field-ops capability vocabulary ────────────────────────────────
INSERT INTO capabilities (key, label, description) VALUES
  ('cap.form.submit',        'Submit forms',           'Submit safety + progress forms.'),
  ('cap.form.request',       'Form request/download',  'Browse + request + download a job''s filed forms.'),
  ('cap.submit_as',          'Submit-as',              'Attribute a submission to another account (admin impersonation).'),
  ('cap.time.log',           'Log time',               'Record time/work entries against a job + task.'),
  ('cap.jobtracker.read',    'Job Tracker (read)',     'View jobs, crew, tasks, equipment-on-site (read-only).'),
  ('cap.jobtracker.manage',  'Job Tracker (manage)',   'Create/edit/close jobs, assign crew, tasks CRUD.'),
  ('cap.equipment.field',    'Equipment field actions','Log equipment status / move / maintenance / inspection.'),
  ('cap.equipment.manage',   'Equipment roster',       'Add/edit/retire equipment units.'),
  ('cap.materials.receive',  'Materials receive',      'Receive materials against a job + file material incident reports.'),
  ('cap.materials.manage',   'Materials catalog',      'Manage the datasheet-backed material catalog.'),
  ('cap.personnel.read',     'Personnel dashboard',    'View the Personnel tab (who is where, hourly history).'),
  ('cap.personnel.manage',   'Personnel manage',       'Manage personnel + the non-login crew roster.'),
  ('cap.tasks.own',          'Own tasks',              'View + complete own assigned + daily-checklist tasks.'),
  ('cap.tasks.assign',       'Assign tasks',           'Assign tasks to other accounts.'),
  ('cap.checklist.manage',   'Checklist template',     'Edit the rolling daily SOP checklist template.'),
  ('cap.inspection.job',     'Job inspections',        'File job-level inspections (trenching/QC/etc.).'),
  ('cap.admin.accounts',     'Account management',     'Create / edit / disable accounts + roles + capabilities.'),
  ('cap.admin.formbuilder',  'Form builder',           'Form-builder + publish pipeline.');

-- ── Grant matrix ────────────────────────────────────────────────────────────────
-- submitter (field PM): submit/request forms, log time, Job Tracker READ, equipment field
-- actions, receive materials + incidents, own tasks/checklist, job inspections. NO manage,
-- NO assign-to-others, NO personnel dashboard, NO submit-as.
INSERT INTO role_capabilities (role_key, capability_key) VALUES
  ('submitter', 'cap.form.submit'),
  ('submitter', 'cap.form.request'),
  ('submitter', 'cap.time.log'),
  ('submitter', 'cap.jobtracker.read'),
  ('submitter', 'cap.equipment.field'),
  ('submitter', 'cap.materials.receive'),
  ('submitter', 'cap.tasks.own'),
  ('submitter', 'cap.inspection.job');

-- admin (office) = everything.
INSERT INTO role_capabilities (role_key, capability_key)
  SELECT 'admin', key FROM capabilities;

-- ── Rebuild `users` to drop the 2-value CHECK and add FOREIGN KEY(role)→roles(key) ──
-- SQLite cannot `ALTER ... DROP CHECK` or `ADD FOREIGN KEY`, so a copy-preserving table
-- rebuild is required. The `roles` rows are seeded ABOVE, so the FK is satisfied for the
-- existing 'submitter'/'admin' role values when rows are copied. Column set is IDENTICAL
-- to the live users table after migrations 0001/0006/0007/0009.
CREATE TABLE users_new (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  username      TEXT    NOT NULL UNIQUE,
  password_hash TEXT    NOT NULL,
  created_at    INTEGER NOT NULL DEFAULT (unixepoch()),
  disabled      INTEGER NOT NULL DEFAULT 0,
  role          TEXT    NOT NULL DEFAULT 'submitter',   -- value-list CHECK superseded by the FK below
  session_epoch INTEGER NOT NULL DEFAULT 0,
  FOREIGN KEY (role) REFERENCES roles(key)
);
INSERT INTO users_new (id, username, password_hash, created_at, disabled, role, session_epoch)
  SELECT id, username, password_hash, created_at, disabled, role, session_epoch FROM users;
DROP TABLE users;
ALTER TABLE users_new RENAME TO users;
