-- Split-brain fence: portal-CREATED jobs (origin='portal') must NOT be deactivated by the
-- Smartsheet down-sync (/api/internal/sync), which runs a full-replace deactivation over every
-- active job_id ABSENT from the Smartsheet payload. A portal-created job does not exist in
-- ITS_Active_Jobs yet, so without this fence the very next 60s sync would deactivate it —
-- defeating the point of creating jobs in the portal (P2 data-residency: D1-primary + mirror).
--
-- The Mac mirror daemon (field_ops/fieldops_sync) later PROMOTES a portal job into
-- ITS_Active_Jobs (Smartsheet AUTO_NUMBER assigns the canonical JOB-####), writes that id back
-- into canonical_job_id, and flips sync_state 'pending' -> 'synced'.
--   origin            'smartsheet' (default — the existing down-synced set) | 'portal' (created here)
--   sync_state        'synced' (default) | 'pending' (a portal job awaiting promotion to Smartsheet)
--   canonical_job_id  NULL until the mirror daemon writes back the Smartsheet JOB-#### id
--
-- ORDER DEPENDENCY (activation): apply this migration to live D1 BEFORE deploying the Worker
-- whose /api/internal/sync deactivation is scoped to `origin='smartsheet'`. Backfill is implicit:
-- every existing job row is Smartsheet-origin (the NOT NULL DEFAULT fills them).
ALTER TABLE jobs ADD COLUMN origin TEXT NOT NULL DEFAULT 'smartsheet';
ALTER TABLE jobs ADD COLUMN sync_state TEXT NOT NULL DEFAULT 'synced';
ALTER TABLE jobs ADD COLUMN canonical_job_id TEXT;
CREATE INDEX IF NOT EXISTS idx_jobs_origin ON jobs(origin);
