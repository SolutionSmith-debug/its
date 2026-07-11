-- 0048: widen config_requests.op to allow 'create_profile' — the terms CREATE-PROFILE op (mint a
-- brand-new terms profile: a manifest entry + an immutable sha-pinned initial version file for a
-- library kind, or a render_line for an attach kind). SQLite can't ALTER a CHECK constraint, so
-- recreate the table; INSERT ... SELECT copies any in-flight rows (the queue is a send-free audit
-- queue, usually empty/near-empty). Kept in LOCKSTEP with worker/config.ts CONFIG_OPS and
-- po_materials/config_apply.py apply_config dispatch (the multi-surface fan-out rule — a new op lands
-- in the DB CHECK, the Worker set, AND the actuator dispatch in the SAME change). config_requests has
-- NO foreign keys (standalone audit queue), so no FK toggling is needed. Preserves the cleared_at
-- column added by 0047.
--
-- DEPLOY-ORDER-CRITICAL: apply to the live D1 BEFORE the Worker that enqueues op='create_profile'
-- deploys. `git pull` ~/its to latest main BEFORE `wrangler d1 migrations apply` (the
-- stale-migrations-list lockout class).

CREATE TABLE config_requests_new (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at INTEGER NOT NULL DEFAULT (unixepoch()),
  updated_at INTEGER NOT NULL DEFAULT (unixepoch()),
  requested_by TEXT NOT NULL,
  workstream TEXT NOT NULL,
  artifact_key TEXT NOT NULL,
  op TEXT NOT NULL CHECK (op IN ('edit','add_version','set_current','create_profile')),
  target_version TEXT,
  payload TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','validated','tested','merged','live','archived','failed')),
  failed_stage TEXT, failure_reason TEXT, lease_owner TEXT, lease_at INTEGER,
  cleared_at INTEGER
);

INSERT INTO config_requests_new
  (id, created_at, updated_at, requested_by, workstream, artifact_key, op, target_version, payload, status, failed_stage, failure_reason, lease_owner, lease_at, cleared_at)
  SELECT id, created_at, updated_at, requested_by, workstream, artifact_key, op, target_version, payload, status, failed_stage, failure_reason, lease_owner, lease_at, cleared_at
  FROM config_requests;

DROP TABLE config_requests;
ALTER TABLE config_requests_new RENAME TO config_requests;

CREATE INDEX idx_config_requests_status ON config_requests(status);
CREATE INDEX idx_config_requests_ws_artifact ON config_requests(workstream, artifact_key);
CREATE INDEX idx_config_requests_created ON config_requests(created_at);
CREATE INDEX idx_config_requests_cleared ON config_requests(cleared_at);
