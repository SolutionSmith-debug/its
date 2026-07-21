-- 0057 — the Evergreen job number + structured job address (operator asks, 2026-07-20).
--
-- `job_no` is the Evergreen YYYY.NNN tracking number (e.g. 2026.123) — DISTINCT from the
-- internal JOB-###### job_id. Until now it existed only as a name-prefix convention plus
-- manual re-typing in every builder (PO / RFQ / estimate / subcontract); this gives it a
-- structured home on the jobs SoR so a dropdown pick auto-fills it everywhere.
--
-- The single free-text `address` column becomes the STREET line; city/state/zip get their
-- own columns (the Coker report: the whole address auto-filled into one line). Existing
-- rows keep their `address` content unchanged; the new columns default '' and are
-- operator-editable in the Job Tracker's routing editor.
--
-- Writers: fieldops_job_write.ts (create + contacts edit, origin='portal'-scoped).
-- Readers: /api/jobs (job_no for the dropdowns), /api/po/jobs/:id/ship-to (all four,
-- feeding the PO/RFQ builder ship-to autofill). The /api/internal/sync full-replace names
-- its columns explicitly, so smartsheet-origin down-syncs never touch these.
--
-- DEPLOY ORDER (lockout class #2): apply this migration --remote BEFORE deploying the
-- Worker that SELECTs these columns — a Worker deploy first would 500 every /api/jobs.
ALTER TABLE jobs ADD COLUMN job_no TEXT NOT NULL DEFAULT '';
ALTER TABLE jobs ADD COLUMN address_city TEXT NOT NULL DEFAULT '';
ALTER TABLE jobs ADD COLUMN address_state TEXT NOT NULL DEFAULT '';
ALTER TABLE jobs ADD COLUMN address_zip TEXT NOT NULL DEFAULT '';
