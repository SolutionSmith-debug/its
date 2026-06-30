-- P2.5 Slice 1 — job-tracker → Smartsheet up-sync: the source-of-truth fields the portal
-- "new job" form now owns, plus the dual-sheet mirror version-vector.
--
-- The portal becomes the authoritative writer of a job's routing + lifecycle. The Mac-side
-- mirror daemon (field_ops/fieldops_sync.py, Slice 5) reads the dirty portal rows over
-- GET /api/internal/fieldops/pending-jobs and find-or-creates a row in BOTH ITS-owned
-- Active-Jobs sheets (safety ITS_Active_Jobs + ITS_Active_Jobs_Progress), keyed by the typed
-- job_id carried in each sheet's "Portal Job Key" column.
--
-- Identity model (typed-key-stable — no destructive AUTO_NUMBER conversion):
--   * job_id (TEXT PK, 0003) stays the portal's only identity + every downstream FK.
--   * origin (0017) stays 'portal' FOREVER → permanently fenced from the down-sync sweep;
--     the portal is the SOLE writer of that row's state. (The old 0017 "flip origin→smartsheet"
--     footnote is a bug — a flipped row would be double-deactivated every cycle.)
--   * sync_state (0017) is the coarse dirty flag: 'pending' = needs up-sync, 'synced' = both
--     sheets caught up. canonical_job_id (0017) = the safety sheet's read-back JOB-#### (the
--     duplicate pre-pass in /api/internal/sync keys on it).
--
-- Version vector (dual-sheet partial-failure consistency — no 2-phase commit across two sheets):
--   mirror_version bumps on every create / lifecycle / contact change. Each sheet has its own
--   watermark; the daemon advances ONLY a sheet's watermark after THAT sheet confirms. A job is
--   dirty when min(safety_mirrored_version, progress_mirrored_version) < mirror_version. Partial
--   failure (safety OK, progress raises) advances only safety's watermark; the job stays dirty;
--   next cycle re-attempts both (safety find-or-create no-ops on the existing row). The vector
--   encodes exactly which sheet is behind — a first-class self-healing state, never silent
--   divergence. safety_row_id / progress_row_id cache the Smartsheet row ids for crash-safe
--   find-or-create.
--
-- lifecycle is a DEDICATED column (active|inactive|archived), NOT an overload of status
-- (active|closed|on_hold, 0014) or active (0003) — it is the operator's job-state selector and
-- maps to each sheet's "Active" picklist. /close becomes a thin alias → lifecycle='inactive'.
--
-- ORDER DEPENDENCY (activation): apply this migration to live D1 BEFORE deploying the Worker
-- whose POST /api/fieldops/job + /api/internal/fieldops/* reference these columns, else those
-- routes 500 on unknown columns (mirror of the 0017 / 0007 activation rule). Backfill is
-- implicit: every existing row is a legacy down-synced job (lifecycle 'active', mirror_version 0,
-- empty SoR strings) — harmless, since only origin='portal' rows are ever read by the up-sync.

ALTER TABLE jobs ADD COLUMN address TEXT NOT NULL DEFAULT '';
ALTER TABLE jobs ADD COLUMN stakeholder_name TEXT NOT NULL DEFAULT '';
ALTER TABLE jobs ADD COLUMN stakeholder_email TEXT NOT NULL DEFAULT '';
ALTER TABLE jobs ADD COLUMN stakeholder_phone TEXT NOT NULL DEFAULT '';
ALTER TABLE jobs ADD COLUMN safety_contact_name TEXT NOT NULL DEFAULT '';
ALTER TABLE jobs ADD COLUMN safety_contact_email TEXT NOT NULL DEFAULT '';
-- JSON array of CC email strings (exploded into the sheets' CC 1..5 columns by the daemon).
ALTER TABLE jobs ADD COLUMN safety_cc TEXT NOT NULL DEFAULT '[]';
ALTER TABLE jobs ADD COLUMN progress_contact_name TEXT NOT NULL DEFAULT '';
ALTER TABLE jobs ADD COLUMN progress_contact_email TEXT NOT NULL DEFAULT '';
ALTER TABLE jobs ADD COLUMN progress_cc TEXT NOT NULL DEFAULT '[]';
-- lifecycle: active|inactive|archived (dedicated; maps to each sheet's "Active" picklist).
ALTER TABLE jobs ADD COLUMN lifecycle TEXT NOT NULL DEFAULT 'active';
-- Version vector for the dual-sheet mirror.
ALTER TABLE jobs ADD COLUMN mirror_version INTEGER NOT NULL DEFAULT 0;
ALTER TABLE jobs ADD COLUMN safety_mirrored_version INTEGER NOT NULL DEFAULT 0;
ALTER TABLE jobs ADD COLUMN progress_mirrored_version INTEGER NOT NULL DEFAULT 0;
-- Cached Smartsheet row ids (crash-safe find-or-create; NULL until first mirrored).
ALTER TABLE jobs ADD COLUMN safety_row_id INTEGER;
ALTER TABLE jobs ADD COLUMN progress_row_id INTEGER;

-- The mirror daemon's dirty-row scan is scoped to origin='portal' AND sync_state='pending'
-- (sync_state stays 'pending' until BOTH watermarks reach mirror_version, so it is an exact,
-- index-friendly proxy for "the version vector shows a sheet behind").
CREATE INDEX IF NOT EXISTS idx_jobs_mirror_pending ON jobs(origin, sync_state);
