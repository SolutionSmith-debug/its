-- Config-editor queue (§50 privileged code-actuation) — config_requests: the send-free D1
-- AUDIT QUEUE for the generic versioned-config editor. An office admin edits versioned config
-- (purchaser identity, tax table, terms) in the portal; the Worker VALIDATES + ENQUEUES a
-- config_requests row send-free (it NEVER git-commits or deploys — the Mac config daemon, built
-- LATER, is the sole privileged actuator, mirroring the External Send Gate / the form-editor
-- publish_requests pipeline this clones). Cloned from the publish_requests 0020 shape, adapted
-- for config: `workstream` (registry key, e.g. 'po_materials') + `artifact_key` (e.g.
-- 'purchaser' | 'tax' | 'terms') are the serialization key, replacing publish's parent_form_code.
--
-- STATE MACHINE (`status`): queued → validated → tested → merged → live → archived (happy path);
-- any stage → failed (carries failed_stage + failure_reason). Terminal = archived | failed.
-- 'queued' is the initial state and is NEVER a stamp target. The CHECK-constraint status/op sets
-- here are kept in LOCKSTEP with the Worker's LEGAL_PREDECESSORS / status / op sets in
-- safety_portal/worker/config.ts (the multi-surface fan-out rule — a new op/status must land in
-- BOTH surfaces in the same change).
--
-- op: 'edit' rewrites an existing config value (target_version NULL); 'add_version' appends a new
-- terms version (target_version = the new version id). payload is the JSON new value (edit) OR the
-- new terms-version object {text,...} (add_version).
--
-- lease_owner/lease_at are the Mac daemon's per-row lease (stale past the Worker's LEASE_TTL_S ⇒
-- re-claimable). Standalone audit queue — NO foreign keys (the clone needs no FK toggling and
-- references nothing else).
--
-- DEPLOY-ORDER-CRITICAL: apply this migration to the live D1 BEFORE the Worker that reads/writes
-- config_requests deploys. `git pull` ~/its to latest main BEFORE `wrangler d1 migrations apply`
-- (the stale-migrations-list lockout class). Append-only audit-style; never customer-facing,
-- never transmitted (Invariant 1).

CREATE TABLE config_requests (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at INTEGER NOT NULL DEFAULT (unixepoch()),
  updated_at INTEGER NOT NULL DEFAULT (unixepoch()),
  requested_by TEXT NOT NULL,
  workstream TEXT NOT NULL,               -- registry key, e.g. 'po_materials'
  artifact_key TEXT NOT NULL,             -- e.g. 'purchaser' | 'tax' | 'terms'  (serialization key WITH workstream)
  op TEXT NOT NULL CHECK (op IN ('edit','add_version')),
  target_version TEXT,                    -- add_version: new terms version id; NULL for edit
  payload TEXT NOT NULL,                  -- JSON: new config value (edit) OR new terms version {text,...} (add_version)
  status TEXT NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','validated','tested','merged','live','archived','failed')),
  failed_stage TEXT, failure_reason TEXT, lease_owner TEXT, lease_at INTEGER
);
CREATE INDEX idx_config_requests_status ON config_requests(status);
CREATE INDEX idx_config_requests_ws_artifact ON config_requests(workstream, artifact_key);
CREATE INDEX idx_config_requests_created ON config_requests(created_at);
