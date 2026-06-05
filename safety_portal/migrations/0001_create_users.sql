-- Portal users (Phase 2). Real field PMs are provisioned via the Phase 7 /admin
-- route; Phase 2 seeds a single validation user (0002) to prove the auth path.
--
-- username = lastname.firstname (mission §3). password_hash = bcrypt cost 10 (Q2).
-- created_at = unix seconds (SQLite has no native datetime type).

CREATE TABLE IF NOT EXISTS users (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  username      TEXT    NOT NULL UNIQUE,
  password_hash TEXT    NOT NULL,
  created_at    INTEGER NOT NULL DEFAULT (unixepoch())
);
