-- ADR-0004 R1 (RFQ generator, po_materials sub-lane) — the D1 store for outbound Requests
-- for Quote: rfqs (the composer draft → queued → generated → sent lifecycle) +
-- rfq_line_items (PRICE-FREE — an RFQ asks vendors for prices, it never carries one) +
-- rfq_vendors (one row per addressed vendor: the per-vendor filing/send/response state).
--
-- SHAPE: deliberately the purchase_orders mirror (0043) so the operator's PO mental model
-- transfers — draft_version optimistic lock, NULL-until-generate number, HMAC at generate
-- (domain 'rfq:v1' — same secret, own domain: an RFQ signature can never replay as a PO or
-- submission), rfq_uuid as the insert-then-resolve batch-atomicity handle (line/vendor
-- INSERTs resolve rfq_id via a scalar subquery on rfq_uuid; last_insert_rowid() moves per
-- inserted row inside a batch and is deliberately avoided).
--
-- STATUS MACHINES:
--   rfqs:        draft → queued (generate: number allocated + hmac signed) → generated
--                (Mac daemon filed EVERY vendor's RFQ PDF + fillable form to Box) →
--                partially_sent → sent; closed (R4 round-trip end state) / canceled
--                off-path. Every transition is guarded in-WHERE (worker/rfq.ts).
--   rfq_vendors: pending → filed (mark-filed: box ids + review row recorded) → sent
--                (F22-approved send happened, Mac-side) → responded (the vendor's quote
--                came back through the estimate importer — responded_estimate_id links
--                the po_estimates row); canceled off-path. Forward-only, in-WHERE.
--   rfqs.status is DERIVED from the vendor rows at status-sync: all live vendors
--   sent/responded → 'sent'; some → 'partially_sent'.
--
-- NUMBERING — rfq_number = 'RFQ-{job_no}-{NNN}' (NNN = zero-padded per-job sequence),
-- NULL while draft. Allocation derives MAX(seq)+1 over the job's allocated numbers with
-- the UNIQUE index on rfq_number as the race backstop (two racing generates that read the
-- same MAX both build seq N; the loser hits UNIQUE → clean 409). A per-job counter table
-- was considered and REJECTED as the more complex option for zero gain at this volume
-- (Evergreen issues a handful of RFQs per job): a counter needs a seed migration + a
-- self-heal path past down-synced rows, while MAX+1 reads the same table it writes and
-- the UNIQUE backstop makes the race loser safe either way — the exact po_number
-- revision-allocation pattern already proven in worker/po.ts.
--
-- SHIP-TO SNAPSHOT: mirrors purchase_orders' block verbatim (frozen at draft time, never
-- re-resolved after generate) — the RFQ's delivery block prints on the outbound PDF and
-- must not drift under a later job edit.
--
-- APPLY BEFORE DEPLOY: run `npx wrangler d1 migrations apply its-safety-portal-db
-- --remote` BEFORE any Worker build that reads/writes these tables deploys — else the
-- /api/po/rfqs routes 500. Same rule as 0010/0033/0043/0053/0054. (Always `git pull`
-- ~/its to latest main FIRST — the stale-migrations-list lockout class, forensic #2.)

CREATE TABLE IF NOT EXISTS rfqs (
  id                     INTEGER PRIMARY KEY AUTOINCREMENT,
  rfq_uuid               TEXT    NOT NULL UNIQUE,           -- batch-atomicity handle (see header)
  rfq_number             TEXT    UNIQUE,                    -- 'RFQ-{job_no}-{NNN}'; NULL until generate
  job_no                 TEXT    NOT NULL,                  -- Evergreen '{YYYY.NNN}', route-validated
  job_name               TEXT    NOT NULL DEFAULT '',
  -- ship-to SNAPSHOT (mirror of purchase_orders' block; frozen at draft time)
  ship_to_name           TEXT    NOT NULL DEFAULT '',
  ship_to_address        TEXT    NOT NULL DEFAULT '',
  ship_to_city           TEXT    NOT NULL DEFAULT '',
  ship_to_state          TEXT    NOT NULL DEFAULT '',       -- 2-letter when present
  ship_to_zip            TEXT    NOT NULL DEFAULT '',
  delivery_contact_name  TEXT    NOT NULL DEFAULT '',
  delivery_contact_phone TEXT    NOT NULL DEFAULT '',
  delivery_contact_email TEXT    NOT NULL DEFAULT '',
  scope_text             TEXT    NOT NULL DEFAULT '',       -- the RFQ's scope-of-supply narrative
  due_date               TEXT,                              -- quote-due date, 'YYYY-MM-DD' (or NULL)
  status                 TEXT    NOT NULL DEFAULT 'draft'
    CHECK (status IN ('draft','queued','generated','partially_sent','sent','closed','canceled')),
  hmac                   TEXT,                              -- set at generate (domain 'rfq:v1')
  -- Monotonic draft-state version (the 0043 pattern): bumped by every draft update
  -- (parent + lines + vendor rows rewrite); generate() pins its status flip on the
  -- version it read so a concurrent edit inside the read→sign→commit window is a clean
  -- 'draft_changed' 409, never a queued row whose HMAC signed a stale snapshot.
  draft_version          INTEGER NOT NULL DEFAULT 0,
  created_by             TEXT    NOT NULL,                  -- authenticated session username
  created_at             INTEGER NOT NULL DEFAULT (unixepoch()),
  updated_at             INTEGER NOT NULL DEFAULT (unixepoch())
);

-- Queue drain (status='queued' oldest-first) + the tracker list read.
CREATE INDEX IF NOT EXISTS idx_rfqs_status ON rfqs(status, updated_at);

-- Line items — PRICE-FREE by design (NO unit_cost / extended / total columns anywhere in
-- this migration: prices enter the system only through the vendor's response, via the
-- estimate importer + the human disposition, ADR-0004 decision 2). position is
-- server-assigned (1-based array order); draft updates FULL-REPLACE the set (guarded
-- DELETE + re-INSERT in the parent's batch). qty is bounded to ≤3 decimal places at the
-- Worker so the canonical-JSON HMAC serialization is bit-stable across the JS/Python
-- recompute (shortest-roundtrip doubles agree on both sides).
CREATE TABLE IF NOT EXISTS rfq_line_items (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  rfq_id      INTEGER NOT NULL REFERENCES rfqs(id),
  position    INTEGER NOT NULL,                             -- 1-based, server-assigned
  part_number TEXT    NOT NULL DEFAULT '',
  description TEXT    NOT NULL,                             -- required, 1..512 (Worker-bounded)
  qty         REAL,                                         -- requested quantity (nullable — "quote per unit")
  unit        TEXT    NOT NULL DEFAULT '',
  line_note   TEXT    NOT NULL DEFAULT '',                  -- per-line note to the vendor
  UNIQUE (rfq_id, position)
);

-- One row per (rfq, vendor) — the fan-out unit the whole downstream lane keys on: the Mac
-- daemon files one RFQ PDF + one fillable quote form PER VENDOR (mark-filed records the
-- Box ids + the RFQ_Pending_Review row), the send lane sends per vendor (one review row
-- per vendor, ADR-0004 decision 12), and the R4 round-trip closes per vendor
-- (responded_estimate_id → the po_estimates row the vendor's quote landed as).
CREATE TABLE IF NOT EXISTS rfq_vendors (
  id                     INTEGER PRIMARY KEY AUTOINCREMENT,
  rfq_id                 INTEGER NOT NULL REFERENCES rfqs(id),
  vendor_key             TEXT    NOT NULL,                  -- soft ref → po_vendors.vendor_key
  status                 TEXT    NOT NULL DEFAULT 'pending'
    CHECK (status IN ('pending','filed','sent','responded','canceled')),
  box_pdf_file_id        TEXT,                              -- the vendor's rendered RFQ PDF in Box
  box_form_file_id       TEXT,                              -- the vendor's fillable .xlsx quote form in Box
  review_row_id          TEXT,                              -- the RFQ_Pending_Review Smartsheet row (send lane)
  responded_estimate_id  INTEGER,                           -- soft ref → po_estimates.id (R4 round-trip)
  sent_at                INTEGER,                           -- stamped by status-sync 'sent'
  UNIQUE (rfq_id, vendor_key)
);

-- The status-sync / mark-filed scans and the "what's still pending" derivations.
CREATE INDEX IF NOT EXISTS idx_rfq_vendors_status ON rfq_vendors(status);
