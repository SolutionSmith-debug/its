-- Phase-2 — form-builder workflow selector: carry a parent-level workflow `category` on a
-- publish request, and allow the new `recategorize` op.
--
-- `category` is set ONLY for create(new-parent) + recategorize; edit / add_version / delete /
-- rollback leave it NULL. The Worker's validateCategory is the first gate; the Mac daemon's
-- apply_publish (with the workflows.json valid-set passed in) is the authoritative re-check.
--
-- WHY A TABLE REBUILD: SQLite cannot ALTER a CHECK constraint in place, and `recategorize` is a
-- new `op` value the existing CHECK (0010) rejects. So we rebuild publish_requests with the
-- extended op-CHECK + the new `category` column (the canonical SQLite "add an enum value"
-- pattern). publish_requests has NO foreign keys (it is a standalone audit queue), so the
-- rebuild needs no FK toggling and references nothing else. Existing rows are preserved
-- (category copied as NULL).
--
-- DEPLOY-ORDER-CRITICAL: apply to the live D1 BEFORE the Worker that writes op='recategorize' /
-- a `category`, and reads `category`, deploys — same rule as 0010. (`git pull` ~/its to latest
-- main BEFORE `wrangler d1 migrations apply`, per the stale-migrations-list lockout class.)
-- Append-only audit-style; never customer-facing, never transmitted (Invariant 1).

CREATE TABLE publish_requests_new (
  id               INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at       INTEGER NOT NULL DEFAULT (unixepoch()),
  updated_at       INTEGER NOT NULL DEFAULT (unixepoch()),
  requested_by     TEXT    NOT NULL,
  op               TEXT    NOT NULL CHECK (op IN ('create', 'edit', 'add_version', 'delete', 'rollback', 'recategorize')),
  parent_form_code TEXT    NOT NULL,
  identity         TEXT    NOT NULL,
  target_form_code TEXT,
  definition_json  TEXT,
  category         TEXT,
  status           TEXT    NOT NULL DEFAULT 'queued'
                     CHECK (status IN ('queued', 'validated', 'tested', 'merged', 'live', 'archived', 'failed')),
  failed_stage     TEXT,
  failure_reason   TEXT,
  lease_owner      TEXT,
  lease_at         INTEGER
);

INSERT INTO publish_requests_new
  (id, created_at, updated_at, requested_by, op, parent_form_code, identity,
   target_form_code, definition_json, category, status, failed_stage, failure_reason,
   lease_owner, lease_at)
SELECT
  id, created_at, updated_at, requested_by, op, parent_form_code, identity,
  target_form_code, definition_json, NULL, status, failed_stage, failure_reason,
  lease_owner, lease_at
FROM publish_requests;

DROP TABLE publish_requests;
ALTER TABLE publish_requests_new RENAME TO publish_requests;

CREATE INDEX IF NOT EXISTS idx_publish_requests_status ON publish_requests(status);
CREATE INDEX IF NOT EXISTS idx_publish_requests_parent ON publish_requests(parent_form_code);
CREATE INDEX IF NOT EXISTS idx_publish_requests_created ON publish_requests(created_at);
