-- Field-Ops unified job-create flow — index personnel.current_job for the crew-convergence query.
--
-- SEMANTICS SHIFT (see fieldops_jobtracker.ts crew legs): a job's "crew" now MEANS the people
-- currently PLACED on it (personnel.current_job, migration 0023 — the P2.6 crew→job placement),
-- NOT the distinct assignees of its task_assignments rows. Both the Job Tracker LIST route (windowed
-- per page job_id) and the DETAIL route now compute crew as
--   SELECT id, name, trade FROM personnel WHERE current_job = <job_id> AND active = 1
-- Index current_job so that leg is a keyed lookup, mirroring idx_task_assignments_job(job_id, status)
-- for the task legs. Without it every job list/detail fetch full-scans personnel for the crew leg.
--
-- ORDER DEPENDENCY (activation — the deploy-lockout class): apply this migration to the live D1 with
-- `wrangler d1 migrations apply its-safety-portal-db --remote` BEFORE the Worker whose crew query
-- filters on current_job deploys. Same rule as 0013/0023. `IF NOT EXISTS` → safe to re-apply.

CREATE INDEX IF NOT EXISTS idx_personnel_current_job ON personnel(current_job);
