-- Phase 5 transport columns on `submissions` — the pull-model queue.
-- The Worker stores each submission (send-free); the Mac-side portal_poll daemon
-- drains it, verifies the HMAC, files it via intake, and POSTs the receipt back.

-- hmac: HMAC-SHA256 the Worker signs at /api/submit over the canonical payload
--   (submission_uuid \n job_id \n form_code \n work_date \n payload_json). portal_poll
--   verifies it before intake trusts the submission (downgrade defense: a row whose
--   HMAC doesn't verify is rejected + flagged, never filed).
ALTER TABLE submissions ADD COLUMN hmac TEXT;

-- The receipt: set by intake via POST /api/internal/mark-filed once it has filed the
-- submission to Smartsheet + Box. box_verified=0 = still in the queue (the portal shows
-- "submitted"); =1 = filed (the portal shows "received & filed").
ALTER TABLE submissions ADD COLUMN box_verified INTEGER NOT NULL DEFAULT 0;
ALTER TABLE submissions ADD COLUMN filed_at INTEGER;
ALTER TABLE submissions ADD COLUMN box_link TEXT;

-- The queue-drain index: oldest unfiled first.
CREATE INDEX IF NOT EXISTS idx_submissions_unfiled ON submissions(box_verified, created_at);
