-- ADR-0004 E1 (vendor-estimate importer, po_materials sub-lane) — the D1 upload pool for
-- office-uploaded vendor estimates/quotes, the §34 Option-D pattern (the po_attachments
-- shape, 0053) applied to the estimate lane.
--
-- WHY A POOL: any portal-inbound file is UNTRUSTED (Invariant 2). The Worker only
-- bounds-gates (size / filename / declared-MIME allowlist / magic sniff) and queues the
-- bytes here SEND-FREE; the Mac daemon (po_materials/estimate_poll.py) pulls the bytes
-- over its OWN bearer tier (PORTAL_ESTIMATE_API_TOKEN — ADR-0004 decision 4: the
-- highest-exposure process gets its own token, separate from PO and the future RFQ),
-- verifies the est:v1 HMAC + sha256, runs the §34 screen + doc-type classifier, and only
-- then files the ORIGINAL to Box "Vendor Quotes" + the Estimate_Log row. Bytes only ever
-- flow Mac-ward; the browser reads metadata + rendered PNG previews (0055), never the
-- original bytes.
--
-- LIFECYCLE (the ADR status machine): pending → claimed (Mac claim-first marker) →
-- (refused | needs_review | extracted) → (imported | rejected); plus superseded (a newer
-- revision in the same family_key). Chunks are deleted at refusal AND at disposition
-- (dispose deletes previews + chunks) — D1 holds estimate bytes only while the doc is
-- live in the pipeline.
--
-- DEDUPE (ADR decision 7, red-team #9): the PARTIAL UNIQUE index on sha256 over live rows
-- is the dedupe AUTHORITY — an exact-byte replay hits the constraint and the upload route
-- maps it to HTTP 409 duplicate_estimate (never a 500). Refused/rejected/superseded rows
-- leave the index, so a legitimately re-uploaded doc after a refusal is accepted.
-- family_key = normalize(vendor)|quote_number once body-derived (E4); sha256 fallback for
-- numberless docs — seeded sha256 at upload, refined at extraction.
--
-- est_uuid: the insert-then-resolve identity (the att_uuid pattern from 0053) — chunk
-- INSERTs resolve estimate_id via a scalar subquery on est_uuid, and the est:v1 HMAC
-- binds to it so a signed estimate cannot be replayed onto another row.
--
-- APPLY BEFORE DEPLOY: run `npx wrangler d1 migrations apply its-safety-portal-db
-- --remote` BEFORE any Worker build that reads/writes these tables deploys — else the
-- estimate routes 500. Same rule as 0010/0033/0043/0053. (Always `git pull` ~/its to
-- latest main FIRST — the stale-migrations-list lockout class, forensic #2.)

CREATE TABLE IF NOT EXISTS po_estimates (
  id                     INTEGER PRIMARY KEY AUTOINCREMENT,
  est_uuid               TEXT    NOT NULL UNIQUE,             -- insert-then-resolve identity (HMAC-bound)
  job_no                 TEXT    NOT NULL,                    -- Evergreen YYYY.NNN, route-validated
  job_name               TEXT,
  rfq_id                 INTEGER,                             -- future RFQ round-trip bind (R4) — NULL in E1
  rfq_vendor_key         TEXT,                                -- future rfq-form:v1 auto-bind — NULL in E1
  vendor_key             TEXT,                                -- optional office pick at upload; CONFIRMED at disposition
  filename               TEXT    NOT NULL,                    -- sanitized + bounded by the upload route
  declared_mime          TEXT    NOT NULL,                    -- allowlisted at upload (PDF/JPEG/PNG/docx/xlsx)
  size_bytes             INTEGER NOT NULL,                    -- DECODED byte count, Worker-verified
  sha256                 TEXT    NOT NULL,                    -- hex digest of the decoded bytes, Worker-computed
  status                 TEXT    NOT NULL DEFAULT 'pending'
    CHECK (status IN ('pending','claimed','refused','needs_review','extracted','imported','rejected','superseded')),
  doc_type               TEXT
    CHECK (doc_type IN ('quote','estimate','proposal','invoice','ap_report','filled_form','other')),
  detail                 TEXT,                                -- refusal / review machine reason (never bytes)
  hmac                   TEXT    NOT NULL,                    -- HMAC-SHA256 hex over the est:v1 canonical string
  uploaded_by            TEXT    NOT NULL,                    -- the AUTHENTICATED session username
  box_file_id            TEXT,                                -- set by the Mac result post (Box Vendor Quotes filing)
  family_key             TEXT,                                -- normalize(vendor)|quote_number (E4); sha256 fallback
  supersedes_estimate_id INTEGER,                             -- revision chain (E4) — NULL in E1
  po_id                  INTEGER,                             -- the draft PO minted at disposition (provenance)
  created_at             INTEGER NOT NULL DEFAULT (unixepoch()),
  screened_at            INTEGER,                             -- stamped by the Mac result post
  extracted_at           INTEGER,                             -- stamped when an extraction lands
  disposed_at            INTEGER                              -- stamped by the browser dispose
);
-- The Mac pending scan + the tracker list read oldest/newest-first.
CREATE INDEX IF NOT EXISTS idx_po_estimates_status ON po_estimates(status, created_at);
-- Revision-family lookups (E4 supersession).
CREATE INDEX IF NOT EXISTS idx_po_estimates_family ON po_estimates(family_key);
-- THE dedupe authority (ADR decision 7): live rows only — refused/rejected/superseded
-- byte-twins may re-enter.
CREATE UNIQUE INDEX IF NOT EXISTS idx_po_estimates_sha_live ON po_estimates(sha256)
  WHERE status NOT IN ('refused','rejected','superseded');

CREATE TABLE IF NOT EXISTS po_estimate_chunks (
  estimate_id INTEGER NOT NULL,                               -- FK-ish → po_estimates.id
  chunk_index INTEGER NOT NULL,
  chunk_total INTEGER NOT NULL,
  chunk_b64   TEXT    NOT NULL,                               -- ≤ 1MB decoded per chunk (mirrors 0053/filed_pdfs)
  PRIMARY KEY (estimate_id, chunk_index)
);
