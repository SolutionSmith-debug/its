-- Assigned-Tasks tab (P4 field-ops feature) S1 — grant cap.tasks.assign to the manager tier.
--
-- DELIBERATE, operator-approved reversal of the P2.6 invariant recorded at 0023_manager_role.sql:15
-- ("Manager is WITHHELD cap.jobtracker.manage (no job/task create)"). Decision #7 of the
-- 2026-07-01 Assigned-Tasks grilling gives managers FULL task authority — create / edit / assign /
-- complete tasks for subcontractor accounts. That authority is minted here as the pre-designed
-- cap.tasks.assign (seeded in 0013), NOT as cap.jobtracker.manage: job create / close / lifecycle /
-- routing stay admin-only (cap.jobtracker.manage). The re-gated task routes
-- (fieldops_task_write.ts: POST /job/:job_id/task, POST /task/:id/assign) now accept
-- cap.jobtracker.manage OR cap.tasks.assign, with a subcontractor-target guard so a manager
-- (cap.tasks.assign but NOT cap.jobtracker.manage) may only target a personnel whose linked account
-- role is 'submitter'. So 0023:15's "no task create" is now documented as reversed, not a silent
-- contradiction.
--
-- NOTE: cap.tasks.own is ALREADY granted to manager (0023_manager_role.sql:53) — the Assigned-Tasks
-- tab + own-task status gate. This migration therefore grants ONLY the genuinely-new cap.tasks.assign;
-- re-granting cap.tasks.own would be a redundant no-op (and INSERT OR IGNORE would swallow it anyway).
--
-- Pure INSERT into role_capabilities (like 0023). cap.tasks.assign already exists in `capabilities`
-- (seeded 0013), so no new capability row is needed.
--
-- ORDER DEPENDENCY (activation — the deploy-lockout class): apply this migration to the live D1 with
-- `wrangler d1 migrations apply its-safety-portal-db --remote` BEFORE the Worker that re-gates the
-- task routes on cap.tasks.assign deploys. Same rule as 0007/0013/0023. INSERT OR IGNORE is safe to
-- re-apply.

-- ── Grant cap.tasks.assign to the manager tier (the 12th manager capability) ──────
INSERT OR IGNORE INTO role_capabilities (role_key, capability_key) VALUES
  ('manager', 'cap.tasks.assign');
