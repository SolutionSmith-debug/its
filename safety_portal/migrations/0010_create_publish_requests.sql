-- Phase 2 — slice 3a — the auto-publish request queue (design brief B5 / C6 / C8 / C12).
--
-- The form editor's Publish is SEND-FREE at the Worker: POST /api/admin/publish
-- VALIDATES the composed definition server-side (meta-schema + renderer-contract
-- rules + the reserved-key denylist + cross-section-unique keys + hard bounds, C3)
-- and, only if valid, ENQUEUES one row here. The privileged Mac daemon (slice 3b,
-- the sole actuator — mirrors the External Send Gate) pulls queued rows, RE-validates
-- against the live git HEAD, then commits / merges / deploys / health-checks, stamping
-- `status` through each stage. The Worker never commits or deploys; it can only queue.
--
-- status state machine (C6): queued -> validated -> tested -> merged -> live -> archived
-- (happy path); any stage can go -> failed (with failed_stage + failure_reason). Terminal
-- = archived | failed. Per-parent serialization (C8): a 2nd Publish for a parent_form_code
-- with a NON-terminal row is rejected ("a publish for this form is in progress").
--   op               create | edit | add_version | delete | rollback
--   parent_form_code the form-type parent (the serialization key)
--   identity         the version-independent identity being published
--   target_form_code the resulting form_code (e.g. jha-v2); NULL for delete/rollback-by-pointer
--   definition_json  the composed FormDefinition (NULL for delete / rollback — no new file)
--   lease_owner/at   the daemon's per-row lease (C6 heartbeat; resume/fail a stuck row)
--
-- ORDER DEPENDENCY (activation): apply this to the live D1 BEFORE the Worker that
-- writes/reads `publish_requests` deploys — else /api/admin/publish errors. Same rule
-- as 0006/0007/0009. See safety_portal/README.md "Deploy". Append-only audit-style;
-- never customer-facing and never transmitted (Invariant 1).
CREATE TABLE IF NOT EXISTS publish_requests (
  id               INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at       INTEGER NOT NULL DEFAULT (unixepoch()),
  updated_at       INTEGER NOT NULL DEFAULT (unixepoch()),
  requested_by     TEXT    NOT NULL,
  op               TEXT    NOT NULL CHECK (op IN ('create', 'edit', 'add_version', 'delete', 'rollback')),
  parent_form_code TEXT    NOT NULL,
  identity         TEXT    NOT NULL,
  target_form_code TEXT,
  definition_json  TEXT,
  status           TEXT    NOT NULL DEFAULT 'queued'
                     CHECK (status IN ('queued', 'validated', 'tested', 'merged', 'live', 'archived', 'failed')),
  failed_stage     TEXT,
  failure_reason   TEXT,
  lease_owner      TEXT,
  lease_at         INTEGER
);
CREATE INDEX IF NOT EXISTS idx_publish_requests_status ON publish_requests(status);
CREATE INDEX IF NOT EXISTS idx_publish_requests_parent ON publish_requests(parent_form_code);
CREATE INDEX IF NOT EXISTS idx_publish_requests_created ON publish_requests(created_at);
