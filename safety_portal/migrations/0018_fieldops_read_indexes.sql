-- 0018 — indexes the field-ops READ layer (P2.2) needs to stay O(page), not O(table).
-- The field-ops tables accumulate for YEARS at 10-50-person-firm scale; every dashboard read
-- is keyset-paginated + "latest per group" via a windowed batch, so each index's LEADING column
-- is the filter/partition key and the TRAILING column is the sort key (covering the window).
-- Index-only additions (CREATE INDEX IF NOT EXISTS) — no schema/data change; safe to re-apply.
-- ORDER DEPENDENCY: apply before the Worker that serves /api/fieldops/* deploys (the routes
-- assume these indexes for their query plans, but degrade to a scan, not an error, if absent).
CREATE INDEX IF NOT EXISTS idx_time_entries_personnel     ON time_entries(personnel_id, created_at);    -- Personnel list+detail
CREATE INDEX IF NOT EXISTS idx_inspections_job            ON inspections(job_id, created_at);            -- Job-detail inspections (URS flag B fix)
CREATE INDEX IF NOT EXISTS idx_equipment_location_job     ON equipment_location(job_id, recorded_at);    -- Job equipment-on-site fan-out fix (URS flag A)
CREATE INDEX IF NOT EXISTS idx_task_assignments_personnel ON task_assignments(personnel_id, status);     -- Personnel who-is-where
CREATE INDEX IF NOT EXISTS idx_personnel_active           ON personnel(active, name);                    -- roster keyset list
CREATE INDEX IF NOT EXISTS idx_equipment_active           ON equipment(active, name);                    -- fleet keyset list
CREATE INDEX IF NOT EXISTS idx_jobs_status_name           ON jobs(status, project_name);                 -- Job Tracker status-filter + name sort
