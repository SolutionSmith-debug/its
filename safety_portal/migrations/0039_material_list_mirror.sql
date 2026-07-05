-- P7 Material List up-sync (M2) — per-job Material List standing-tracker mirror keys.
--
-- ORDER DEPENDENCY: apply this to the live D1 BEFORE deploying the Worker. The same PR's
-- GET /api/internal/fieldops/material-list-snapshot selects `jem.line_uuid` and `jem.unplanned`,
-- and the ADD-line INSERT (fieldops_expected_materials.ts) now writes `line_uuid`, so a Worker
-- deployed ahead of this migration would 500 those surfaces. Blast radius is bounded (the material
-- pass ships dark behind field_ops.fieldops_sync.materials_enabled, and ALTER ... ADD COLUMN is
-- instant + backward-compatible), but apply-before-deploy is the canonical order (README
-- punch-list = the single apply-all-then-deploy source).
--
-- job_expected_materials (0031) is the D1 SoR for the operator-authored per-job expected-materials
-- list. M2 mirrors that WHOLE list UP into a per-job standing "Material List" Smartsheet (progress
-- workspace, one-way-up, send-free + AI-free — Op Stds v19 §51). This adds the two columns the
-- one-way-up mirror needs; it adds NO receipt semantics (the receive/flag-incident routes are
-- unchanged).
--
--   • line_uuid — the stable per-line mirror key (find-or-create + change-only upsert authority),
--     the material-list analogue of time_entries.uuid / the Hours Log "Entry UUID". Nullable at the
--     schema level (multiple NULLs allowed under the unique index below — partial-safe) so the
--     ALTER is instant; the ADD-line INSERT sets it to crypto.randomUUID(), and the UPDATE below
--     backfills every pre-existing row. Every downstream row therefore carries a distinct uuid.
--   • unplanned — 0/1 flag surfacing an OFF-MANIFEST line (a field-added line NOT on the operator's
--     authored list). Defaults 0. NO off-list-add write path exists yet (the only INSERT is the
--     office cap.materials.manage add, which is on-manifest by definition); when such a path lands
--     it sets unplanned=1 and the mirror already surfaces it (Material List "Unplanned" = Yes).
--
-- ONE-WAY-UP ONLY: this is deliberately NOT bidirectional. No `smartsheet_row_id` column is added —
-- that (the reverse-link a Smartsheet→D1 down-sync would need) belongs to a FUTURE bidirectional
-- model, explicitly out of scope for M2. NEVER deletes (SoR); a removed (deactivated) line is
-- marked "On List = Removed" on the sheet, never dropped.
--
-- Additive + backfill-safe: the ALTERs are instant; the UPDATE backfills existing rows once. At
-- cutover the daemon's material pass (shipped OFF behind field_ops.fieldops_sync.materials_enabled)
-- re-projects the live per-job list each cycle.
ALTER TABLE job_expected_materials ADD COLUMN line_uuid TEXT;
ALTER TABLE job_expected_materials ADD COLUMN unplanned INTEGER NOT NULL DEFAULT 0;

-- Backfill every pre-existing row with a distinct uuid (16 random bytes → 32 hex chars), matching
-- the crypto.randomUUID() shape closely enough for a stable find-or-create key (uniqueness, not
-- format, is what the mirror requires).
UPDATE job_expected_materials SET line_uuid = lower(hex(randomblob(16))) WHERE line_uuid IS NULL;

-- Uniqueness guard for the mirror key. Partial-safe: SQLite permits multiple NULLs in a UNIQUE
-- index, so a future INSERT that (wrongly) omits line_uuid never trips it — the backfill above +
-- the ADD-line write mean live rows are all non-NULL + distinct.
CREATE UNIQUE INDEX IF NOT EXISTS idx_job_expected_materials_line_uuid
  ON job_expected_materials(line_uuid);
