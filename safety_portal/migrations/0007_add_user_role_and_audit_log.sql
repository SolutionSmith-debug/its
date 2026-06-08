-- Admin Safety Dashboard (Phase 1) — user role model + audit log.
--
-- users.role: 'submitter' (default — every existing + future field PM) | 'admin'
-- (the CEO + head PM who get the dashboard). requireSession reads this column per
-- request alongside `disabled` (migration 0006), so a role change takes effect on
-- the NEXT request — same fail-closed posture, no cookie re-issue. The CHECK keeps
-- a stray value out at the DB layer; the Worker validates role server-side too.
--
-- ORDER DEPENDENCY (activation): apply this to the live D1 BEFORE the Worker that
-- SELECTs `role` deploys — otherwise requireSession's lookup errors and (fail-closed
-- by design) 401s every session until the column exists. Exact mirror of the
-- 0006/disabled activation rule. See safety_portal/README.md "Deploy".
ALTER TABLE users ADD COLUMN role TEXT NOT NULL DEFAULT 'submitter'
  CHECK (role IN ('submitter', 'admin'));

-- audit_log: append-only security event stream for the admin surface — who did what
-- to whom. Written by the session+role-gated /api/admin/* routes (account
-- create / edit-credentials / role-change / delete) and, in a later slice, by
-- /api/submit on a submit-as (impersonation) event. NOT customer-facing and never
-- transmitted anywhere (Invariant 1) — the operator reads it out-of-band
-- (`wrangler d1 execute … "SELECT * FROM audit_log …"`); no UI in Phase 1.
--   actor_username  — the authenticated session user who performed the action
--   action          — user_create | user_edit | role_change | user_delete | submit_as
--   target_username — the account acted upon / attributed account (NULL where N/A)
--   detail          — optional JSON context (fields changed, from/to role, uuid, …)
CREATE TABLE IF NOT EXISTS audit_log (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at      INTEGER NOT NULL DEFAULT (unixepoch()),
  actor_username  TEXT    NOT NULL,
  action          TEXT    NOT NULL,
  target_username TEXT,
  detail          TEXT
);
CREATE INDEX IF NOT EXISTS idx_audit_log_created ON audit_log(created_at);
