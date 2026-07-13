-- Feature B (PO document attachments) — the D1 pool for draft-time PO attachments
-- (specs / drawings / supporting docs), the §34 Option-D pattern GENERALIZED from
-- photos to documents.
--
-- WHY A POOL: any portal-inbound file is UNTRUSTED (Invariant 2). The Worker only
-- bounds-gates (size / count / filename / declared-MIME allowlist / magic sniff) and
-- queues the bytes here SEND-FREE; the Mac daemon (po_materials/po_poll.py attachment
-- pass) pulls the bytes over the PO bearer tier, verifies the po-att:v1 HMAC + sha256,
-- runs the §34 doc screener (po_materials/po_attach_screen.py — magic/consistency →
-- PDF/OpenXML/image structural inspection → config-gated ClamAV), and only a CLEAN
-- file reaches Box / the PO_Log row. There is NO serving route back to the browser
-- beyond filename/size/status listing — bytes only ever flow Mac-ward (Option D).
--
-- LIFECYCLE: uploaded at DRAFT time (parent status='draft' enforced in-WHERE by the
-- upload route); rides the draft; serviced by the Mac AFTER the PO files (parent
-- reaches 'pending_review'+). status: pending → claimed (Mac claim-first marker) →
-- filed | refused. DELETE-ON-DISPOSITION: the chunks (the bytes) are deleted in the
-- SAME batch that applies a filed/refused result — D1 holds attachment bytes only
-- while status IN ('pending','claimed'). The byte-free po_attachments row remains as
-- the manifest / forensic marker.
--
-- CASCADE: the delete-draft route (POST /api/po/:id/delete, #560) and the prune.ts
-- stale-draft stage delete chunks + attachment rows in the SAME atomic batch as the
-- parent + line items (no ON DELETE CASCADE in this schema lineage — children are
-- deleted first, subquery-scoped to the guarded parent, mirroring po_line_items).
--
-- CHUNKING: mirrors filed_pdfs (0011) — base64 chunks of ≤ 1MB decoded each, keyed
-- (attachment_id, chunk_index); a 10MB file is ≤ 10 chunks. All chunks of one upload
-- land in ONE db.batch with the parent row + audit (W4).
--
-- att_uuid: the insert-then-resolve identity (the po_uuid pattern from 0043) — chunk
-- INSERTs resolve attachment_id via a scalar subquery on att_uuid, and the po-att:v1
-- HMAC binds to it so a signed attachment cannot be replayed onto another row.
--
-- APPLY BEFORE DEPLOY: run `npx wrangler d1 migrations apply its-safety-portal-db
-- --remote` BEFORE any Worker build that reads/writes these tables deploys — else the
-- attachment routes 500. Same rule as 0010/0033/0036/0043. (Always `git pull` ~/its
-- to latest main FIRST — the stale-migrations-list lockout class, forensic #2.)

CREATE TABLE IF NOT EXISTS po_attachments (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  att_uuid      TEXT    NOT NULL UNIQUE,             -- insert-then-resolve identity (HMAC-bound)
  po_id         INTEGER NOT NULL,                    -- FK-ish → purchase_orders.id (route-validated)
  filename      TEXT    NOT NULL,                    -- sanitized + bounded by the upload route
  declared_mime TEXT    NOT NULL,                    -- allowlisted at upload (PDF/JPEG/PNG/docx/xlsx)
  size_bytes    INTEGER NOT NULL,                    -- DECODED byte count, Worker-verified
  sha256        TEXT    NOT NULL,                    -- hex digest of the decoded bytes, Worker-computed
  status        TEXT    NOT NULL DEFAULT 'pending',  -- pending | claimed | filed | refused
  hmac          TEXT    NOT NULL,                    -- HMAC-SHA256 hex over the po-att:v1 canonical string
  uploaded_by   TEXT    NOT NULL,                    -- the AUTHENTICATED session username
  box_file_id   TEXT,                                -- set by the Mac 'filed' disposition (Box = the record)
  detail        TEXT,                                -- refused machine reason (never bytes)
  created_at    INTEGER NOT NULL DEFAULT (unixepoch()),
  screened_at   INTEGER                              -- stamped by the Mac disposition post-back
);
-- The builder list read + the per-PO count cap.
CREATE INDEX IF NOT EXISTS idx_po_attachments_po ON po_attachments(po_id);
-- The Mac attachment pass scans serviceable rows oldest-first.
CREATE INDEX IF NOT EXISTS idx_po_attachments_pending ON po_attachments(status, created_at);

CREATE TABLE IF NOT EXISTS po_attachment_chunks (
  attachment_id INTEGER NOT NULL,                    -- FK-ish → po_attachments.id
  chunk_index   INTEGER NOT NULL,
  chunk_total   INTEGER NOT NULL,
  chunk_b64     TEXT    NOT NULL,                    -- ≤ 1MB decoded per chunk (mirrors filed_pdfs)
  PRIMARY KEY (attachment_id, chunk_index)
);
