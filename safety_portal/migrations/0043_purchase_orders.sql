-- PO workstream S2 (Aug-7 delivery program WS1) — purchase_orders + po_line_items: the
-- AUTHORITATIVE D1 store for the PO drafting/generation pipeline (PO_Log on Smartsheet is the
-- operator-visible LEDGER MIRROR of this table, written by the Mac daemon at filing — S1/S4).
--
-- MONEY IS INTEGER CENTS EVERYWHERE (D8). No floats touch a money column: unit costs arrive
-- from the client as cents integers; extended = round(qty × unit_cost_cents) (or the per-watt
-- integer math — see po_line_items below); subtotal = Σ extended; tax = round(subtotal ×
-- tax_rate_bp / 10000); total = subtotal + tax + shipping. The Worker recomputes ALL of it
-- server-side at generate and REJECTS a client whose displayed totals disagree (worker/po.ts).
--
-- D7 NUMBERING — po_number = '{job_no}.{site_phase}.{supersede_seq}.{revision}' where job_no is
-- the existing Evergreen '{YYYY.NNN}' job number (sourced from the job record, never folder
-- tags). po_number is NULL while status='draft' (nullable-until-allocated); generate allocates
-- revision = MAX(revision)+1 within the (job_no, site_phase, supersede_seq) family inside one
-- atomic batch, and the UNIQUE index below is the numbering-collision backstop: two racing
-- generates in the same family cannot both land (the loser's UPDATE hits UNIQUE → clean 409).
--
-- D7 STATUS MACHINE — draft → queued → pending_review → approved → sent, with superseded /
-- canceled off-path. 'queued' is the D1-ONLY transit state (generated + HMAC-signed, awaiting
-- the Mac daemon's pull/render/file); PO_Log's Status picklist deliberately omits it (the
-- ledger row is first written at filing, when status is already pending_review — see
-- shared/picklist_validation._PO_LOG_STATUS_VALUES). Every transition is guarded in-WHERE
-- (worker/po.ts), so a stale/replayed sync can never regress a status.
--
-- SUPERSESSION (D7): supersede clones a SENT PO into a new draft with supersede_seq+1 and
-- revision reset; the old PO is untouched until the successor reaches 'sent', at which point
-- the status-sync flips it to 'superseded' IN THE SAME BATCH (worker/po.ts status-sync).
--
-- HMAC (Invariant 2): generate signs "po:v1"\n<po_id>\n<po_number>\n<canonical_payload_json>
-- with the existing portal HMAC secret (HMAC_PAYLOAD_SECRET / Keychain ITS_PORTAL_HMAC_SECRET);
-- the Mac daemon (S4) recomputes it before rendering/filing — same pull-model trust chain as
-- submissions, new domain prefix so a PO signature can never be replayed as a submission.
--
-- po_uuid is internal plumbing: a client-independent unique handle minted at create so the
-- create/supersede batches can INSERT the parent + its line items + the audit row ATOMICALLY
-- (the line INSERTs resolve po_id via a scalar subquery on po_uuid — last_insert_rowid() is
-- NOT safe inside an INSERT..SELECT, it moves per inserted row).
--
-- ORDER DEPENDENCY (activation): apply to the live D1 BEFORE the /api/po/* Worker deploys
-- (the 0006/0007/0013 rule).
CREATE TABLE IF NOT EXISTS purchase_orders (
  id                     INTEGER PRIMARY KEY AUTOINCREMENT,
  po_uuid                TEXT    NOT NULL UNIQUE,           -- batch-atomicity handle (see header)
  po_number              TEXT    UNIQUE,                    -- NULL until allocated at generate
  job_no                 TEXT    NOT NULL,                  -- '{YYYY.NNN}' (D7)
  site_phase             INTEGER NOT NULL,
  supersede_seq          INTEGER NOT NULL DEFAULT 0,
  revision               INTEGER,                           -- NULL until allocated at generate
  job_id                 TEXT    NOT NULL DEFAULT '',       -- soft ref → jobs.job_id (JOB-######)
  job_name               TEXT    NOT NULL DEFAULT '',
  -- ship-to SNAPSHOT (frozen at draft time; never re-resolved after generate)
  ship_to_name           TEXT    NOT NULL DEFAULT '',
  ship_to_address        TEXT    NOT NULL DEFAULT '',
  ship_to_city           TEXT    NOT NULL DEFAULT '',
  ship_to_state          TEXT    NOT NULL DEFAULT '',       -- 2-letter; the 'auto' tax basis
  ship_to_zip            TEXT    NOT NULL DEFAULT '',
  delivery_contact_name  TEXT    NOT NULL DEFAULT '',
  delivery_contact_phone TEXT    NOT NULL DEFAULT '',
  delivery_contact_email TEXT    NOT NULL DEFAULT '',
  sow_text               TEXT    NOT NULL DEFAULT '',
  delivery_instructions  TEXT    NOT NULL DEFAULT '',
  payment_terms_text     TEXT    NOT NULL DEFAULT '',
  terms_profile_id       TEXT    NOT NULL DEFAULT '',       -- terms library pin (S3): id…
  terms_version          TEXT    NOT NULL DEFAULT '',       -- …+ immutable version
  subtotal_cents         INTEGER NOT NULL DEFAULT 0,
  tax_mode               TEXT    NOT NULL DEFAULT 'auto' CHECK (tax_mode IN ('auto','exempt','included','override')),
  tax_rate_bp            INTEGER NOT NULL DEFAULT 0,        -- RESOLVED basis points (auto → table value)
  tax_cents              INTEGER NOT NULL DEFAULT 0,
  shipping_cents         INTEGER NOT NULL DEFAULT 0,
  total_cents            INTEGER NOT NULL DEFAULT 0,
  line_column_variant    TEXT    NOT NULL DEFAULT 'default' CHECK (line_column_variant IN ('default','lump_sum','per_watt')),
  supersedes_po_id       INTEGER,                           -- soft ref → purchase_orders.id
  status                 TEXT    NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','queued','pending_review','approved','sent','superseded','canceled')),
  hmac                   TEXT,                              -- set at generate (domain 'po:v1')
  box_file_id            TEXT,                              -- set by mark-filed (the daemon's receipt)
  approver_name          TEXT    NOT NULL DEFAULT '',       -- D9: Purchaser-block autofill
  approver_title         TEXT    NOT NULL DEFAULT '',
  vendor_key             TEXT    NOT NULL,                  -- soft ref → po_vendors.vendor_key
  created_by             TEXT    NOT NULL,                  -- authenticated session username
  created_at             INTEGER NOT NULL DEFAULT (unixepoch()),
  updated_at             INTEGER NOT NULL DEFAULT (unixepoch()),
  -- Monotonic draft-state version: bumped by every draft update (parent+lines rewrite).
  -- generate() pins its final status-flip UPDATE on the version it read, so a concurrent
  -- edit landing inside generate's read→sign→commit window makes the flip a clean 0-row
  -- 'draft_changed' 409 instead of queueing a row whose HMAC signed a stale snapshot
  -- (PR #494 security-review finding W5/W8).
  draft_version          INTEGER NOT NULL DEFAULT 0
);

-- THE numbering-collision backstop (D7). Drafts carry revision NULL and are exempt (SQLite
-- UNIQUE treats NULLs as pairwise distinct); every ALLOCATED (job_no, site_phase,
-- supersede_seq, revision) tuple is unique — the second of two racing generates that read the
-- same MAX(revision) hits this index and maps to a clean 409 (worker/po.ts isUniqueViolation).
CREATE UNIQUE INDEX IF NOT EXISTS idx_po_family_revision
  ON purchase_orders(job_no, site_phase, supersede_seq, revision);

-- Queue drain (status='queued' oldest-first) + the tracker list read.
CREATE INDEX IF NOT EXISTS idx_purchase_orders_status ON purchase_orders(status, updated_at);

-- Line items. Structured free-form rows (D8): position is server-assigned (1-based array
-- order); draft updates FULL-REPLACE the set (guarded DELETE + re-INSERT in the parent's
-- batch). Money: unit_cost_cents is the cents-integer unit price; extended_cents is ALWAYS
-- server-computed — round(qty × unit_cost_cents), or, when the per-watt fields are present,
-- round(watts × price_per_watt_microcents / 1e6) (microcents = 1e-6 cents, so $0.325/W is
-- representable exactly as 32_500_000 — integer math end-to-end). qty is bounded to ≤3
-- decimal places at the Worker so the canonical-JSON serialization is bit-stable across the
-- JS/Python HMAC recompute (shortest-roundtrip doubles agree on both sides).
CREATE TABLE IF NOT EXISTS po_line_items (
  id                        INTEGER PRIMARY KEY AUTOINCREMENT,
  po_id                     INTEGER NOT NULL REFERENCES purchase_orders(id),
  position                  INTEGER NOT NULL,               -- 1-based, server-assigned
  part_number               TEXT    NOT NULL DEFAULT '',
  description               TEXT    NOT NULL DEFAULT '',
  qty                       REAL    NOT NULL DEFAULT 0,
  unit                      TEXT    NOT NULL DEFAULT '',
  unit_cost_cents           INTEGER,
  extended_cents            INTEGER NOT NULL DEFAULT 0,     -- server-computed, never client-trusted
  -- per-watt variant fields (D8 fast-follow surface; nullable on default/lump_sum lines)
  watts                     INTEGER,
  panels                    INTEGER,
  pallets                   INTEGER,
  price_per_watt_microcents INTEGER
);

CREATE INDEX IF NOT EXISTS idx_po_line_items_po ON po_line_items(po_id, position);
