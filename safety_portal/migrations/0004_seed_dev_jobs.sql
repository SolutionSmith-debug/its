-- DEV/VALIDATION-ONLY seed for `jobs` (local + validation environment). In
-- production the Phase-3 D1 sync populates `jobs` from ITS_Active_Jobs — do NOT
-- rely on this seed there. Job IDs are 6-digit AUTO_NUMBER form (amendment c).
INSERT OR IGNORE INTO jobs (job_id, project_name, active) VALUES
  ('JOB-000001', 'Bradley 1',   1),
  ('JOB-000002', 'Bradley 2',   1),
  ('JOB-000003', 'Brimfield 1', 1),
  ('JOB-000004', 'Brimfield 2', 1),
  ('JOB-000005', 'Huntley',     1),
  ('JOB-000006', 'Rockford',    1);
