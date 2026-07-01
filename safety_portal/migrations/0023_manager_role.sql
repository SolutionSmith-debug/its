-- Field-Ops P2.6 — Manager tier (third portal role) + cap.crew.assign + crew→job placement.
--
-- Adds a MID-TIER role `manager` between submitter (field PM) and admin (office). The
-- capability model (migration 0013: roles / capabilities / role_capabilities, resolved
-- FAIL-CLOSED per request by auth.resolveCapabilities) was built so a third tier is a pure
-- INSERT: 0013 already replaced 0007's 2-value `CHECK (role IN ('submitter','admin'))` on
-- users.role with `FOREIGN KEY(role) REFERENCES roles(key)`, so seeding the `manager` role
-- here satisfies that FK — NO users-table rebuild is needed.
--
-- Manager grant (11 caps) = the submitter floor (8, INHERITED) + cap.personnel.read +
-- cap.personnel.manage + the NEW cap.crew.assign. Manager runs crews: creates NON-LOGIN
-- roster crew (the credential-minting branch of fieldops_personnel_write self-gates to
-- role='admin', so manager gets roster-only crew with ZERO route change), edits/links/
-- retires personnel, logs crew time (personnel_id, no submit_as), reads the Job Tracker,
-- and ASSIGNS/moves crew to a job (the new cap + the /assign route). Manager is WITHHELD
-- cap.jobtracker.manage (no job/task create), cap.admin.accounts, cap.admin.formbuilder,
-- cap.submit_as, and the *.manage caps for equipment/materials/tasks/checklist.
--
-- cap.crew.assign is the 19th capability (NEW here). admin's 0013 grant was a seed-time
-- catch-all (`SELECT key FROM capabilities`), so it does NOT auto-include a capability added
-- AFTER 0013 — this migration therefore grants cap.crew.assign to admin EXPLICITLY too.
--
-- personnel.current_job (nullable TEXT) is the crew→job placement ("who is where"): a SOFT
-- reference to jobs.job_id (no FK — consistent with personnel.username's soft link and
-- task_assignments.job_id). It is the CURRENT standing placement; time entries stay
-- ORTHOGONAL (a person placed on Job A may log time against any active Job B without
-- reassignment). Placement-change history rides audit_log (action 'personnel_assign').
--
-- ORDER DEPENDENCY (activation — the deploy-lockout class): apply this migration to the live
-- D1 with `wrangler d1 migrations apply its-safety-portal-db --remote` BEFORE the Worker that
-- parses/renders the `manager` role deploys. Same rule as 0007/0013. The three seed blocks
-- are INSERT OR IGNORE (safe to re-apply the seed portion); the ADD COLUMN is a standard
-- once-applied additive column (wrangler tracks applied migrations, so it runs once).

-- ── New role (satisfies the users.role FK from 0013) ─────────────────────────────
INSERT OR IGNORE INTO roles (key, label, is_system) VALUES
  ('manager', 'Manager (Crew lead)', 1);

-- ── New capability (the 19th) ────────────────────────────────────────────────────
INSERT OR IGNORE INTO capabilities (key, label, description) VALUES
  ('cap.crew.assign', 'Assign crew',
   'Assign / move a crew member to a job (their standing placement). Distinct from cap.jobtracker.manage — assigns crew WITHOUT creating jobs or tasks.');

-- ── Manager grant matrix — 11 caps (submitter floor 8 + personnel.read/manage + crew.assign) ──
-- Enumerated explicitly (NOT `SELECT … FROM capabilities`) so the grant is exact + auditable.
INSERT OR IGNORE INTO role_capabilities (role_key, capability_key) VALUES
  ('manager', 'cap.form.submit'),
  ('manager', 'cap.form.request'),
  ('manager', 'cap.time.log'),
  ('manager', 'cap.jobtracker.read'),
  ('manager', 'cap.equipment.field'),
  ('manager', 'cap.materials.receive'),
  ('manager', 'cap.tasks.own'),
  ('manager', 'cap.inspection.job'),
  ('manager', 'cap.personnel.read'),
  ('manager', 'cap.personnel.manage'),
  ('manager', 'cap.crew.assign');

-- ── admin gets the new capability too (0013's catch-all predated it) ─────────────
INSERT OR IGNORE INTO role_capabilities (role_key, capability_key) VALUES
  ('admin', 'cap.crew.assign');

-- ── Crew→job placement column (soft-ref to jobs.job_id; NULL = unplaced) ─────────
ALTER TABLE personnel ADD COLUMN current_job TEXT;
