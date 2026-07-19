-- ADR-0004 E1/E3 — the ADVISORY extraction store + rendered page previews for the
-- vendor-estimate importer, and the draft-import idempotency column (red-team #4).
--
-- ADVISORY, NEVER TRUSTED (ADR decision 2): rows here are what the Mac daemon POSTED
-- over its estimate bearer — attacker-influenceable document content. No dollar in these
-- tables ever reaches a PO except through the human disposition screen and the EXISTING
-- session-gated POST /api/po/drafts validators (parseDraftBody recomputes all money
-- server-side in integer cents). The single fidelity control is the human side-by-side
-- accept against the page previews below (ADR decision 3).
--
-- estimate_previews: PNG page renders (Quartz, subprocess-isolated on the Mac) so the
-- disposition screen shows the SOURCE next to the extracted lines without ever serving
-- the original untrusted bytes to a browser. ≤ 1MB decoded per page, Worker-bounded.
-- Deleted (with the chunks) at disposition.
--
-- purchase_orders.estimate_id (red-team #4): PROVENANCE ONLY — which estimate a draft
-- was imported from, so the draft route can refuse a second import of the same estimate
-- (409 estimate_already_imported over non-canceled rows). It is deliberately NOT part of
-- canonicalPoJson / the po:v1 HMAC string (the Python recompute in shared/portal_hmac.py
-- would break — store-only, never signed).
--
-- APPLY BEFORE DEPLOY, after 0054 (estimate_extractions FK-ish references po_estimates).

CREATE TABLE IF NOT EXISTS estimate_extractions (
  id               INTEGER PRIMARY KEY AUTOINCREMENT,
  estimate_id      INTEGER NOT NULL,                          -- FK-ish → po_estimates.id
  tier             INTEGER NOT NULL CHECK (tier BETWEEN 0 AND 3), -- 0 form / 1 deterministic / 2 local-LLM / 3 manual
  schema_version   TEXT    NOT NULL,                          -- schemas/vendor_estimate_extraction.json version
  doc_type         TEXT,                                      -- classifier / extractor doc_type
  vendor_name      TEXT,                                      -- body-derived (NEVER writes the vendor SoR — ADR decision 9)
  quote_number     TEXT,
  revision_label   TEXT,
  quote_date       TEXT,
  valid_until      TEXT,
  subtotal_cents   INTEGER,
  tax_cents        INTEGER,
  freight_cents    INTEGER,
  misc_cents       INTEGER,
  grand_total_cents INTEGER,
  math_ok          INTEGER NOT NULL DEFAULT 0,                -- deterministic cross-check verdict (0|1)
  confidence       REAL,                                      -- 0–1, advisory
  payload_json     TEXT    NOT NULL,                          -- the full schema-validated extraction document
  anomalies        TEXT,                                      -- anomaly_logger string-field findings (advisory)
  superseded       INTEGER NOT NULL DEFAULT 0,                -- a re-extraction replaces, never deletes
  created_at       INTEGER NOT NULL DEFAULT (unixepoch())
);
CREATE INDEX IF NOT EXISTS idx_estimate_extractions_estimate ON estimate_extractions(estimate_id);

CREATE TABLE IF NOT EXISTS estimate_extraction_lines (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  extraction_id INTEGER NOT NULL,                             -- FK-ish → estimate_extractions.id
  position      INTEGER NOT NULL,                             -- 1-based document order
  section       TEXT,                                         -- section-band header (Platt/OnPoint grouping)
  part_number   TEXT,
  description   TEXT    NOT NULL,
  qty           REAL,
  unit          TEXT,
  unit_cost_cents INTEGER,
  extended_cents  INTEGER,
  math_ok       INTEGER NOT NULL DEFAULT 0,                   -- qty×unit==extended verdict (0|1)
  line_note     TEXT,                                         -- per-line stock note / flag
  disposition   TEXT    NOT NULL DEFAULT 'pending'
    CHECK (disposition IN ('pending','accepted','rejected','edited')), -- the human color-coding replacement
  edited_json   TEXT,                                         -- the human's edited line (disposition='edited')
  UNIQUE (extraction_id, position)
);
CREATE INDEX IF NOT EXISTS idx_estimate_extraction_lines_extraction
  ON estimate_extraction_lines(extraction_id);

CREATE TABLE IF NOT EXISTS estimate_previews (
  estimate_id INTEGER NOT NULL,                               -- FK-ish → po_estimates.id
  page        INTEGER NOT NULL,                               -- 1-based
  png_b64     TEXT    NOT NULL,                               -- ≤ 1MB decoded, Worker-bounded
  PRIMARY KEY (estimate_id, page)
);

-- Draft-import provenance (red-team #4) — store-only, NEVER enters the po:v1 canonical.
ALTER TABLE purchase_orders ADD COLUMN estimate_id INTEGER;
CREATE INDEX IF NOT EXISTS idx_purchase_orders_estimate ON purchase_orders(estimate_id);
