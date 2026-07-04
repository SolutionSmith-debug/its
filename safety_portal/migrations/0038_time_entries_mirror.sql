-- P7 Slice 1 — per-job Hours Log up-sync watermark.
--
-- ORDER DEPENDENCY: apply this to the live D1 BEFORE deploying the Worker. The same PR's
-- GET /api/internal/fieldops/hours-pending reads `WHERE t.mirrored_at IS NULL`, so a Worker
-- deployed ahead of this migration would 500 that route. Blast radius is bounded (the hours pass
-- ships dark behind field_ops.fieldops_sync.hours_enabled, and ALTER ... ADD COLUMN is instant +
-- backward-compatible), but apply-before-deploy is the canonical order (README punch-list = the
-- single apply-all-then-deploy source).
--
-- time_entries (0015) is the D1 SoR for field-reported crew hours. Track 2 mirrors each
-- entry UP into a per-job standing "Hours Log" Smartsheet (progress workspace, one-way-up,
-- send-free + AI-free — Op Stds v19 §51). This adds the per-row mirror watermark the up-sync
-- needs; it adds NO reporting semantics (the compile-time rollup already amend-collapses).
--
-- WHY a per-row flag (mirrored_at), NOT a per-job high-watermark: an amend APPENDS a new
-- time_entries row (its own uuid, amends_uuid -> the prior) whose created_at can be older than
-- an already-mirrored entry, so a MAX(created_at) high-watermark would skip it. A per-row
-- "mirrored_at IS NULL" flag is amend-correct: every unmirrored row (original OR amend) is
-- picked up, mirrored, then stamped. Stamped = the mirror epoch (server unixepoch()), written
-- ONLY by /api/internal/fieldops/hours-mark-mirrored, idempotently (WHERE mirrored_at IS NULL).
--
-- Additive + backfill-safe: existing rows default mirrored_at = NULL (unmirrored). At cutover
-- the daemon's hours pass (shipped OFF behind field_ops.fieldops_sync.hours_enabled) drains the
-- backlog across cycles, capped per cycle. NEVER deletes (SoR).
ALTER TABLE time_entries ADD COLUMN mirrored_at INTEGER;

-- Pending scan: unmirrored rows, job-ordered for batched per-job find-or-create. Partial index
-- stays tiny (only the pending frontier, not the full accumulating table).
CREATE INDEX IF NOT EXISTS idx_time_entries_mirror_pending
  ON time_entries(job_id, created_at) WHERE mirrored_at IS NULL;
