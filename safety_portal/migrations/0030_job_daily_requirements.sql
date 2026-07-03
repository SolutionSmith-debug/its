-- SOP daily form slice D4 — per-job daily-form REQUIREMENTS (the admin-authored additive overlay).
--
-- Operator intent (2026-07-02): "admin accounts can edit the daily form per job as specific
-- requirements develop or are outlined by the client." The BASE daily form stays a git-owned
-- definition (daily-report-v4 carries a placeholder `job_requirements` section); per-job tailoring
-- is THIS table — an ADDITIVE overlay of requirement items the portal fetches at render time and
-- injects into that section. The manager's answers file WITH the submission
-- (values.job_requirements = [{label, kind, response}], self-describing), so the filed PDF shows
-- the client requirements + answers and stays stable regardless of later requirement edits.
-- Additive-only by design: base-section suppression is OUT of scope (defer until a real need).
--
-- One flat table (no template/instance split): unlike the checklist engine (0026), requirements
-- have no per-day instances — they are definition-side content, snapshotted into each submission's
-- VALUES at fill time, so no lineage table is needed. `kind` is the closed item vocabulary the
-- renderers understand:
--   note      — read-only guidance text shown inside the form (no answer)
--   confirm   — a checkbox the manager checks ("Confirmed")
--   text      — a free-text answer
--   form_link — a "Create <form> →" deep link (form_code = a catalog PARENT family, validated by
--               the Worker against catalog.json; launch:"daily-tab" parents are REFUSED — a
--               deep link back into the daily tab itself would be circular)
-- `active` is a soft-delete flag (the deactivate route flips it; the audit_log keeps the forensic
-- record) so a requirement the client later drops disappears from NEW renders while historical
-- submissions keep their self-describing values.
--
-- CAP: authoring is cap.checklist.manage (admin; seeded 0013) — no new capability row. The tab
-- read (GET /api/fieldops/daily-form/requirements) is cap.tasks.own + the SAME per-job ownership
-- scope as /api/fieldops/daily-form/status (non-admin actors only their own placement).
--
-- ORDER DEPENDENCY (activation — the deploy-lockout class, same standing rule as
-- 0007/0013/0023/0025/0026/0027/0028/0029): apply this migration to the live D1 with
--   wrangler d1 migrations apply its-safety-portal-db --remote
-- BEFORE the Worker that registers the daily-requirements routes deploys — otherwise those routes
-- 500 on the missing table. (Always `git pull` `~/its` to latest `main` BEFORE
-- `wrangler d1 migrations apply` — the stale-migrations-list lockout class.) CREATE TABLE/INDEX
-- IF NOT EXISTS, so re-applying is safe. See docs/runbooks/fieldops_checklists.md (§ per-job
-- daily-form requirements) for the full activation checklist.

CREATE TABLE IF NOT EXISTS job_daily_requirements (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  job_id     TEXT    NOT NULL,                 -- jobs.job_id (existence checked route-side, same as 0026)
  seq        INTEGER NOT NULL DEFAULT 0,       -- display order (seq ASC, id ASC)
  kind       TEXT    NOT NULL CHECK (kind IN ('note', 'confirm', 'text', 'form_link')),
  label      TEXT    NOT NULL,                 -- bounded route-side (1..256, same MAX_LABEL as 0026 items)
  form_code  TEXT,                             -- form_link only: a catalog PARENT family (Worker-validated)
  active     INTEGER NOT NULL DEFAULT 1,       -- soft-delete flag (deactivate route)
  created_at INTEGER NOT NULL DEFAULT (unixepoch())
);

-- The one read path: active items for a job in display order.
CREATE INDEX IF NOT EXISTS idx_job_daily_requirements_job
  ON job_daily_requirements (job_id, active, seq);
