-- Assigned-Tasks (P4 field-ops feature) Slice T — Subcontractor tier: scoped crew-create.
--
-- The `submitter` tier is being re-presented to users as "Subcontractor" — a DISPLAY-LABEL-ONLY
-- rename. The role KEY stays `'submitter'` (auth.coerceRole/parseRole's security-load-bearing
-- FAIL-SAFE default: "unknown → submitter, never upward"), so NO role/vocabulary row changes here
-- and the grant matrix is preserved. This migration ONLY adds the one new scoped capability + the
-- provenance column that backs it.
--
-- NEW capability `cap.crew.create` — a subcontractor may create a NON-LOGIN roster person and have
-- them auto-placed on the subcontractor's OWN current job (the scoped `POST /api/fieldops/crew`
-- route). It is DISTINCT from cap.personnel.manage (edit/link/unlink/retire others, admin+manager)
-- and from cap.crew.assign (place ANY person on ANY active job, admin+manager). A cap.crew.create
-- actor can ONLY mint a non-login person on their own job — login-mint stays admin-only
-- (fieldops_personnel_write self-gates the account branch to role='admin').
--
-- Granted to `submitter` (the subcontractor tier keeps all 8 of its 0013 caps + gains this 9th).
-- admin's 0013 grant was a seed-time catch-all (`SELECT key FROM capabilities`), so it does NOT
-- auto-include a capability added AFTER 0013 — this migration therefore grants cap.crew.create to
-- admin EXPLICITLY too (same pattern as 0023's cap.crew.assign). `manager` already holds the fuller
-- cap.personnel.manage + cap.crew.assign, so it is NOT granted the scoped cap (it uses the fuller routes).
--
-- personnel.created_by (nullable TEXT) records the account username that created the roster person
-- via the scoped crew-create route (NULL for admin/manager-created or pre-existing rows). It is the
-- provenance the time-route scoping keys on: a subcontractor (cap.time.log WITHOUT cap.personnel.manage)
-- may log time only for their OWN linked personnel OR a personnel whose created_by = their username.
-- A SOFT reference to users.username (no FK — consistent with personnel.username / current_job).
--
-- ORDER DEPENDENCY (activation — the deploy-lockout class): apply this migration to the live D1 with
-- `wrangler d1 migrations apply its-safety-portal-db --remote` BEFORE the Worker that gates
-- `POST /api/fieldops/crew` on cap.crew.create / reads personnel.created_by deploys — else the route
-- 403s every caller (fail-closed empty cap) and the crew-create INSERT + time-scoping SELECT 500 on
-- the missing `created_by` column. Same rule as 0007/0013/0023/0025. INSERT OR IGNORE seeds are safe
-- to re-apply; the ADD COLUMN is a standard once-applied additive column (wrangler tracks applied
-- migrations). Always `git pull` `~/its` to latest `main` BEFORE `wrangler d1 migrations apply` — the
-- stale-migrations-list lockout class.

-- ── New capability (the scoped crew-create) ──────────────────────────────────────
INSERT OR IGNORE INTO capabilities (key, label, description) VALUES
  ('cap.crew.create', 'Create crew',
   'Create a NON-LOGIN roster person auto-placed on the actor''s own current job. Distinct from cap.personnel.manage (edit/link/retire others) and cap.crew.assign (place anyone on any job).');

-- ── Grant to the subcontractor tier (role key stays 'submitter') ─────────────────
INSERT OR IGNORE INTO role_capabilities (role_key, capability_key) VALUES
  ('submitter', 'cap.crew.create');

-- ── admin gets the new capability too (0013's catch-all predated it) ─────────────
INSERT OR IGNORE INTO role_capabilities (role_key, capability_key) VALUES
  ('admin', 'cap.crew.create');

-- ── Provenance column: who created this roster person via the scoped route (soft-ref) ──
ALTER TABLE personnel ADD COLUMN created_by TEXT;
