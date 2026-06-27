-- URS Marine — equipment management + readiness status + time-entry task ref (B4 follow-on).
--
-- Extends the 0014 equipment roster + the 0015 integrity-bar tables to make equipment a
-- first-class managed entity, NOT just a seeded read surface. Three threads:
--
--   1. READINESS STATUS on equipment — the mission-capability of each unit:
--        fmc       — Full Mission Capable (green)
--        degraded  — operable with a caveat (gold)
--        down      — out of service (red)
--      A denormalized CURRENT snapshot lives on `equipment` (status / status_note /
--      status_changed_at / status_actor) so the list + detail render the pill without a
--      correlated subquery. The CHANGE itself is recorded the integrity-bar way: an
--      append-only `equipment_logs` row with log_type='status' (carrying the new value in
--      `status_value` + the nuance note in `detail`), server-stamped, dual-attributed,
--      amend-chainable — plus an `audit_log` row. So the snapshot is fast and the history
--      is honest (the worker writes both ATOMICALLY in one D1 batch — see POST
--      /api/urs/equipment/:id/status).
--
--   2. MOVE attribution on equipment_location — the table predates the integrity bar
--      (0014) and had no actor column; a dashboard "move between job sites" write now
--      records WHO moved a unit (`actor_username`), distinct from the server-authoritative
--      `recorded_at` and the field-reported `read_at`. Append-only, unchanged otherwise.
--
--   3. OPTIONAL task reference on a time entry — `task_id` lets time be logged against a
--      specific task of a job OR generally against the job (NULL). A soft reference to
--      task_assignments(id); the worker validates it belongs to the same job before binding.
--
-- ORDER DEPENDENCY (activation): apply BEFORE the Worker that writes these columns deploys
-- (the equipment-management endpoints bind status/status_value/actor_username/task_id).
-- Mirrors the 0013/0015 activation rule. The new capability follows 0013's grant model.

-- 1) Readiness status — denormalized current snapshot on the roster row.
ALTER TABLE equipment ADD COLUMN status            TEXT NOT NULL DEFAULT 'fmc'; -- fmc | degraded | down
ALTER TABLE equipment ADD COLUMN status_note       TEXT;                        -- nuance annotation (current)
ALTER TABLE equipment ADD COLUMN status_changed_at INTEGER;                     -- server-stamped at last change
ALTER TABLE equipment ADD COLUMN status_actor      TEXT;                        -- attributed account at last change

-- 2) equipment_logs gains status_value so a readiness change is an append-only event in the
--    unit's timeline (log_type='status'); NULL for the existing fuel / hours / maintenance rows.
ALTER TABLE equipment_logs ADD COLUMN status_value TEXT;

-- 3) equipment_location gains DUAL move attribution (it predates the integrity bar; nullable for
--    the existing field-EXIF reads, which have no actor). actor_username = the authenticated mover;
--    submitted_as = the attributed account (forging it requires cap.submit_as) — mirrors the
--    dual-attribution on the other accumulating records (time_entries / equipment_logs / inspections).
ALTER TABLE equipment_location ADD COLUMN actor_username TEXT;
ALTER TABLE equipment_location ADD COLUMN submitted_as   TEXT;

-- 4) Optional task reference on a time entry (soft ref; worker re-validates job ownership).
ALTER TABLE time_entries ADD COLUMN task_id INTEGER REFERENCES task_assignments(id);

-- 5) New capability: equipment ROSTER management (add / edit / retire) — admin-only,
--    mirroring cap.admin.accounts. Operational writes (status / move / maintenance /
--    inspection) reuse the existing cap.dashboard.equipment / cap.machine.log /
--    cap.inspection.fill grants (supervisor + admin) — no new key needed for those.
INSERT OR IGNORE INTO capabilities (key, label, description) VALUES
  ('cap.admin.equipment', 'Equipment management', 'Add, edit, and retire equipment in the fleet roster.');

-- Grant to admin only. (0013 already granted admin "everything" via SELECT ... FROM
-- capabilities, but that ran before this row existed — so grant the new key explicitly.)
INSERT OR IGNORE INTO role_capabilities (role_key, capability_key) VALUES
  ('admin', 'cap.admin.equipment');
