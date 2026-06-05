-- Portal D1 mirror + cache tables (Phase 4 PR 2).

-- jobs: mirror of ITS_Active_Jobs Active rows — the job-dropdown source. Populated
-- by the Phase-3 D1 sync (deferred to the deploy session); the portal NEVER reads
-- Smartsheet. Seed 0004 is dev/validation-only.
CREATE TABLE IF NOT EXISTS jobs (
  job_id       TEXT PRIMARY KEY,
  project_name TEXT NOT NULL,
  active       INTEGER NOT NULL DEFAULT 1
);

-- submissions: the portal's own recent-submissions cache, for Amend prefill. The
-- DURABLE record is the per-job week sheet + Box (intake.py renders + files, Phase 5).
CREATE TABLE IF NOT EXISTS submissions (
  submission_uuid TEXT PRIMARY KEY,
  job_id          TEXT NOT NULL,
  form_code       TEXT NOT NULL,
  work_date       TEXT NOT NULL,
  payload_json    TEXT NOT NULL,
  amends_uuid     TEXT,
  created_at      INTEGER NOT NULL DEFAULT (unixepoch())
);
CREATE INDEX IF NOT EXISTS idx_submissions_lookup ON submissions(job_id, form_code, work_date, created_at);
