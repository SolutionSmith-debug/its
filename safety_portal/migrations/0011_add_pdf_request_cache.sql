-- PR-4 Part A — request-driven canonical PDF download cache.
--
-- A field PM (or an admin/attributee) can ask the portal to "make my filed safety
-- report available for download." The canonical PDF is the Box-filed copy — the Worker
-- holds NO Box creds and is SEND-FREE, so the Mac-side portal_poll daemon fetches the
-- filed PDF from Box (by box_file_id), base64-chunks it, and POSTs the chunks into D1
-- here; the Worker's GET /api/submissions/:uuid/pdf reassembles the chunks and serves
-- the bytes (Content-Disposition: attachment). NOTHING is cached unless the user asks.
--
--   pdf_requested — 0/1 flag set to 1 by POST /api/submissions/:uuid/request-pdf
--                   ("the user wants it cached"). The Mac pass selects on this.
--   box_file_id   — the Box file id of the filed PDF, written at mark-filed time (the
--                   daemon downloads THIS id to chunk). NULL until filed.
--   pdf_ready_at  — epoch (seconds) when ALL chunks have been uploaded (cache ready).
--                   NULL = not cached. Used for serviceable / ready / expiry gating:
--                     serviceable (Mac picks up) = pdf_requested=1 AND pdf_ready_at IS NULL
--                                                  AND box_file_id IS NOT NULL
--                     ready (downloadable)       = pdf_ready_at IS NOT NULL AND chunks exist
--                     expiry                     = pdf_ready_at + 86400 (prune deletes chunks
--                                                  + resets pdf_requested=0, pdf_ready_at=NULL)
--
-- All three are additive: pdf_requested is NOT NULL DEFAULT 0 (a flag — backfills 0 on
-- existing rows); box_file_id / pdf_ready_at are plain nullable (NULL means "never
-- filed / never cached"; no sensible default to invent). Same additive-column rule as
-- 0005/0008. The canonical HMAC payload is UNCHANGED (these are not part of it).
--
-- filed_pdfs holds the base64 chunks of ONE filed PDF, keyed by (submission_uuid,
-- chunk_index). MAX_CHUNKS is enforced Worker-side (8); each chunk decodes ≤ 1MB. A
-- chunk row is transient cache only — pruned 24h past pdf_ready_at, or orphaned when its
-- parent submission is pruned. created_at is the standard epoch-seconds default.
--
-- ORDER DEPENDENCY (activation): apply this migration to the live D1 BEFORE the Worker
-- that READS/WRITES these columns + the filed_pdfs table deploys — a SELECT/INSERT
-- naming a not-yet-existing column/table errors. Additive + nullable, so applying it
-- ahead of the new Worker is safe (the old Worker never references them). Exact mirror
-- of the 0005/0006/0007/0008/0010 activation rule. See safety_portal/README.md "Deploy".
ALTER TABLE submissions ADD COLUMN pdf_requested INTEGER NOT NULL DEFAULT 0;
ALTER TABLE submissions ADD COLUMN box_file_id   TEXT;
ALTER TABLE submissions ADD COLUMN pdf_ready_at  INTEGER;

CREATE TABLE IF NOT EXISTS filed_pdfs (
  submission_uuid TEXT    NOT NULL,
  chunk_index     INTEGER NOT NULL,
  chunk_total     INTEGER NOT NULL,
  chunk_b64       TEXT    NOT NULL,
  created_at      INTEGER NOT NULL DEFAULT (unixepoch()),
  PRIMARY KEY (submission_uuid, chunk_index)
);
CREATE INDEX IF NOT EXISTS idx_filed_pdfs_uuid ON filed_pdfs(submission_uuid);
