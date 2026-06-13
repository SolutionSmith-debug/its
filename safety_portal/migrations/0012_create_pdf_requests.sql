-- ORDER DEPENDENCY (activation): apply this migration to the live D1 BEFORE deploying the
-- Worker code that reads/writes pdf_requests, else the SELECT/INSERT errors (fail-closed).
-- PR-5: one row per (submission, requester) — downloads are requester-bound, 24h.
CREATE TABLE IF NOT EXISTS pdf_requests (
  submission_uuid TEXT    NOT NULL,
  account         TEXT    NOT NULL,
  requested_at    INTEGER NOT NULL DEFAULT (unixepoch()),
  ready_at        INTEGER,
  PRIMARY KEY (submission_uuid, account)
);
CREATE INDEX IF NOT EXISTS idx_pdf_requests_account ON pdf_requests(account, requested_at);
CREATE INDEX IF NOT EXISTS idx_pdf_requests_submission ON pdf_requests(submission_uuid);
