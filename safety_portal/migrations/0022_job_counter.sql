-- 0022 — portal-owned canonical job-number allocator (P2.5 Slice 6).
--
-- The portal now ASSIGNS the canonical JOB-###### itself, replacing the Smartsheet AUTO_NUMBER
-- that previously lived on ITS_Active_Jobs."Job ID". The office employee no longer types a Job ID
-- at creation — they type only the Project Name, and POST /api/fieldops/job allocates the next
-- number atomically:
--
--     UPDATE job_counter SET last_value = last_value + 1 WHERE id = 1 RETURNING last_value
--
-- and formats it `JOB-` + zero-pad-6 (matching the legacy AUTO_NUMBER format, e.g. JOB-000017).
-- The UPDATE…RETURNING is a single atomic statement; D1 serializes writes, so two concurrent
-- creates can never receive the same number (no read-then-write race). The allocated number is
-- the D1 job_id (PK) AND canonical_job_id from birth — one identity across the portal, both
-- Active-Jobs sheets, every report, and Box. The Smartsheet-generates-then-read-back handshake
-- is gone.
--
-- SEED = 16. The live ITS_Active_Jobs AUTO_NUMBER had reached JOB-000016 (1 live row) at the
-- 2026-06-30 cutover, so the first portal-assigned number is JOB-000017 — it can never collide
-- with a historical JOB-000001..JOB-000016. `INSERT OR IGNORE` makes a re-apply a no-op (it never
-- resets a counter that has already handed out numbers).
--
-- OPERATOR (cutover, before flipping field_ops.fieldops_sync.sync_enabled ON) — IN THIS ORDER:
--   1. Add a `Portal Job Key` TEXT column to ITS_Active_Jobs (the daemon's find-or-create key).
--   2. Retype ITS_Active_Jobs."Job ID" from AUTO_NUMBER → TEXT_NUMBER (it stops auto-generating;
--      the existing JOB-000016 value persists as text).
--   3. Apply this migration `--remote` BEFORE redeploying the Worker (deploy-order: a missing
--      job_counter table makes the create route return 500 counter_unavailable, fail-closed —
--      never a malformed id). This step CREATES + seeds the job_counter table.
--   4. NOW that the table exists, confirm the live ITS_Active_Jobs max Job ID is ≤ JOB-000016. If a
--      higher number exists (more jobs were auto-created since), bump the counter to match BEFORE
--      the first portal create:  UPDATE job_counter SET last_value = <live max> WHERE id = 1;
--   5. Redeploy the Worker.
CREATE TABLE IF NOT EXISTS job_counter (
  id         INTEGER PRIMARY KEY CHECK (id = 1),
  last_value INTEGER NOT NULL
);
INSERT OR IGNORE INTO job_counter (id, last_value) VALUES (1, 16);
