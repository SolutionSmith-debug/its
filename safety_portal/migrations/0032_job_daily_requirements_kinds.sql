-- SOP daily form slice D5 — widen the per-job daily-form requirement KIND vocabulary
-- (operator: "an admin can add ANY field type — not just free text").
--
-- 0030 shipped the closed 4-kind vocabulary (note / confirm / text / form_link); D5 adds three
-- more answer kinds the renderers (SPA FormRenderer + Python form_pdf via the filed
-- self-describing values array) understand:
--   number — a numeric answer (<input type="number">; the response files as a string, like text)
--   date   — a calendar-date answer (<input type="date">; files as the YYYY-MM-DD string)
--   select — pick-one from an admin-authored option list (files as the chosen option string)
-- Photo is DELIBERATELY excluded: an untrusted field-worker image upload is a §34 image-class
-- screening surface and needs that design first — see docs/tech_debt.md "Checklist item-state
-- photo CAPTURE — render-half only" [OPEN 2026-07-02] (same design gap, same rule here).
--
-- WHY A TABLE REBUILD: SQLite cannot ALTER a CHECK constraint in place, so we rebuild
-- job_daily_requirements with the extended kind-CHECK + the new `options` column (the canonical
-- SQLite "add an enum value" pattern — the 0020 publish_requests precedent). The table has NO
-- foreign keys and nothing references it, so the rebuild needs no FK toggling. Existing rows are
-- preserved (options copied as NULL — no pre-D5 kind uses it).
--
-- `options` holds a JSON array of option strings (e.g. '["Day shift","Night shift"]') for
-- kind='select' ONLY; NULL for every other kind. Bounds are route-side (1..20 options, each
-- 1..120 chars — worker/fieldops_daily_requirements.ts parseRequirement), mirroring how `label`
-- and `form_code` are bounded route-side under 0030.
--
-- RE-APPLY SAFETY: unlike 0030 (pure CREATE IF NOT EXISTS), a rebuild is NOT naturally
-- idempotent — a second run would copy the already-widened table onto itself and lose nothing,
-- but the plain CREATE TABLE below would first fail on the existing _new name. This is the SAME
-- shape as the shipped 0020 rebuild: no sentinel guard, because `wrangler d1 migrations apply`
-- tracks applied migrations in d1_migrations and never re-runs one (and the test harness's
-- applyD1Migrations does the same). Keep it simple per that precedent; the index recreate keeps
-- IF NOT EXISTS as belt-and-braces.
--
-- ORDER DEPENDENCY (activation — the deploy-lockout class, same standing rule as
-- 0007/0013/0020/0023/0025/0026/0027/0028/0029/0030): as of authoring (2026-07-03) the live D1
-- has applied NEITHER 0030 NOR 0031 (see the README punch-list) — the operator applies
-- 0030 → 0031 → 0032 in sequence with ONE command:
--   cd ~/its && git pull origin main   # ALWAYS first — the stale-migrations-list lockout class
--   cd safety_portal && npx wrangler d1 migrations apply its-safety-portal-db --remote
-- BEFORE the Worker that validates/serves the new kinds deploys. Applying 0032 straight after
-- 0030 on the same run is exactly the intended path: 0030 creates the 4-kind table (empty),
-- 0032 immediately rebuilds it to the 7-kind + options shape (zero rows to copy). See
-- safety_portal/README.md (§ Pending live activation) + docs/runbooks/fieldops_checklists.md
-- (Symptom F) for the activation checklist.

CREATE TABLE job_daily_requirements_new (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  job_id     TEXT    NOT NULL,                 -- jobs.job_id (existence checked route-side, same as 0026)
  seq        INTEGER NOT NULL DEFAULT 0,       -- display order (seq ASC, id ASC)
  kind       TEXT    NOT NULL CHECK (kind IN ('note', 'confirm', 'text', 'form_link', 'number', 'date', 'select')),
  label      TEXT    NOT NULL,                 -- bounded route-side (1..256, same MAX_LABEL as 0026 items)
  form_code  TEXT,                             -- form_link only: a catalog PARENT family (Worker-validated)
  options    TEXT,                             -- select only: JSON array of option strings (route-bounded); NULL otherwise
  active     INTEGER NOT NULL DEFAULT 1,       -- soft-delete flag (deactivate route)
  created_at INTEGER NOT NULL DEFAULT (unixepoch())
);

INSERT INTO job_daily_requirements_new
  (id, job_id, seq, kind, label, form_code, options, active, created_at)
SELECT
  id, job_id, seq, kind, label, form_code, NULL, active, created_at
FROM job_daily_requirements;

DROP TABLE job_daily_requirements;
ALTER TABLE job_daily_requirements_new RENAME TO job_daily_requirements;

-- The one read path: active items for a job in display order (same index as 0030).
CREATE INDEX IF NOT EXISTS idx_job_daily_requirements_job
  ON job_daily_requirements (job_id, active, seq);
