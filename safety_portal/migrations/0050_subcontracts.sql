-- Subcontracts workstream S1 — `subcontracts` + `sov_lines`: the AUTHORITATIVE D1 store for the
-- subcontract drafting/generation pipeline (Subcontract_Log on Smartsheet is the operator-visible
-- LEDGER MIRROR, written by the Mac daemon at filing). A 1:1 fork of purchase_orders (0043).
--
-- MONEY IS INTEGER CENTS EVERYWHERE. A subcontract is a lump-sum CONTRACT PRICE (no tax table, no
-- shipping, no per-watt lines — the PO tax/shipping/per-watt columns are DROPPED). contract_price_cents
-- is the source of truth (§2.1 of the agreement); subtotal_cents = Σ sov_lines.extended_cents and MUST
-- equal contract_price_cents (the SOV-sums-to-price gate). retainage_bp captures the §2.5 retention
-- (default 1000 bp = 10% progress-payment retention, reduced to 5% at 50% completion — that reduction
-- is payment-application logic, NOT stored here). price_basis selects the §2.1 lead-in ('fixed' vs
-- 'not to exceed', both attested in the corpus). The Worker recomputes all money server-side at
-- generate and REJECTS a client whose displayed subtotal disagrees, AND asserts the spelled-out §2.1
-- price WORDS (derived deterministically from contract_price_cents via num2words) match the figure —
-- the value-add over the manual corpus, which shipped a real "nine cents / $…00" words↔figure mismatch.
--
-- NUMBERING — sc_number = '{job_no}.{site_phase}.{supersede_seq}.{revision}' (the exact PO 5-segment
-- job-derived scheme). NULL while status='draft'; generate allocates revision = MAX(revision)+1 within
-- the (job_no, site_phase, supersede_seq) family in one atomic batch, with the UNIQUE index as the
-- race backstop.
--
-- STATUS MACHINE — draft → queued → pending_review → approved → sent → executed, with superseded /
-- canceled off-path. 'executed' is NEW vs PO: a subcontract is a WET-SIGNATURE instrument (both parties
-- sign; §27 signature block), so 'executed' is the countersigned terminal after 'sent' (the corpus '_FE'
-- Fully-Executed marker). 'queued' is the D1-only transit state (generated + HMAC-signed, awaiting the
-- Mac daemon's pull/render/file); Subcontract_Log's Status picklist omits it (the ledger row is first
-- written at filing, status already pending_review).
--
-- OWNER-ENTITY FAN-OUT (the corpus's #1 complexity multiplier): one subcontractor gets one subcontract
-- PER owner-entity/SPV. owner_entity is the Evergreen-side contracting SPV (e.g. 'Bonacci 1, LLC'); the
-- generator emits one row per (sub_key × owner_entity). project/site are frozen-at-draft snapshots.
--
-- GOVERNING LAW — governing_law_state is PARAMETERIZED (default 'VA' per the corpus's hard-coded
-- Virginia/Fairfax venue), NOT hardcoded in the body, because the per-state lien-waiver annexes prove
-- jurisdiction varies by project. This is a legal decision surfaced to the operator, not auto-filled.
--
-- HMAC (Invariant 2): generate signs under domain 'sub:v1' (new prefix — a subcontract signature can
-- never replay as a PO or a submission). template_family selects the long-form (27-article, dominant)
-- vs the short-form (KSI monthly) contract shell; short-form is a future slice (default long_form).
--
-- ORDER DEPENDENCY (activation): apply to the live D1 BEFORE the /api/subcontracts/* Worker deploys.

CREATE TABLE IF NOT EXISTS subcontracts (
  id                     INTEGER PRIMARY KEY AUTOINCREMENT,
  sc_uuid                TEXT    NOT NULL UNIQUE,             -- batch-atomicity handle
  sc_number              TEXT    UNIQUE,                      -- NULL until allocated at generate
  job_no                 TEXT    NOT NULL,                    -- '{YYYY.NNN}'
  site_phase             INTEGER NOT NULL DEFAULT 0,
  supersede_seq          INTEGER NOT NULL DEFAULT 0,
  revision               INTEGER,                             -- NULL until allocated at generate
  job_id                 TEXT    NOT NULL DEFAULT '',         -- soft ref → jobs.job_id
  job_name               TEXT    NOT NULL DEFAULT '',
  -- project / owner-SPV / site SNAPSHOT (frozen at draft; the 3-tier fan-out)
  project_name           TEXT    NOT NULL DEFAULT '',
  owner_entity           TEXT    NOT NULL DEFAULT '',         -- the SPV (e.g. 'Bonacci 1, LLC')
  prime_contractor       TEXT    NOT NULL DEFAULT '',         -- e.g. 'Evergreen Renewables of Virginia LLC'
  site_name              TEXT    NOT NULL DEFAULT '',
  site_address           TEXT    NOT NULL DEFAULT '',
  governing_law_state    TEXT    NOT NULL DEFAULT 'VA',       -- parameterized jurisdiction (legal decision)
  -- subcontractor (soft ref → subcontractors.sub_key; snapshot resolved at render from the SoR)
  sub_key                TEXT    NOT NULL,
  trade                  TEXT    NOT NULL DEFAULT '',         -- one of the canonical trade slots
  -- Exhibit A: deterministic spine (Articles I/III/IV/VI filled from fields) + operator-authored Art II
  exhibit_a_template_id  TEXT    NOT NULL DEFAULT '',         -- trade scope-template pin (versioned)
  exhibit_a_template_version TEXT NOT NULL DEFAULT '',
  exhibit_a_work_text    TEXT    NOT NULL DEFAULT '',         -- Article II "The Work" — OPERATOR-authored (no AI)
  scope_summary          TEXT    NOT NULL DEFAULT '',
  -- money (integer cents; NO tax/shipping/per-watt — a subcontract is a lump-sum contract price)
  price_basis            TEXT    NOT NULL DEFAULT 'fixed' CHECK (price_basis IN ('fixed','not_to_exceed')),
  contract_price_cents   INTEGER NOT NULL DEFAULT 0,          -- §2.1 source of truth
  retainage_bp           INTEGER NOT NULL DEFAULT 1000,       -- §2.5 retention (bp); 1000 = 10%
  subtotal_cents         INTEGER NOT NULL DEFAULT 0,          -- Σ sov_lines; MUST == contract_price_cents
  -- schedule
  start_date             TEXT    NOT NULL DEFAULT '',
  completion_date        TEXT    NOT NULL DEFAULT '',         -- Contract Time / Substantial Completion
  -- terms pin (the 27-clause subcontract body, versioned + legal-gated)
  terms_profile_id       TEXT    NOT NULL DEFAULT '',
  terms_version          TEXT    NOT NULL DEFAULT '',
  template_family        TEXT    NOT NULL DEFAULT 'long_form' CHECK (template_family IN ('long_form','short_form')),
  supersedes_sc_id       INTEGER,                             -- soft ref → subcontracts.id
  status                 TEXT    NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','queued','pending_review','approved','sent','executed','superseded','canceled')),
  hmac                   TEXT,                                -- set at generate (domain 'sub:v1')
  box_file_id            TEXT,                                -- set by mark-filed (the daemon's receipt)
  approver_name          TEXT    NOT NULL DEFAULT '',
  approver_title         TEXT    NOT NULL DEFAULT '',
  created_by             TEXT    NOT NULL,
  created_at             INTEGER NOT NULL DEFAULT (unixepoch()),
  updated_at             INTEGER NOT NULL DEFAULT (unixepoch()),
  draft_version          INTEGER NOT NULL DEFAULT 0           -- concurrency guard (generate pins its flip on it)
);

-- Numbering-collision backstop (drafts carry revision NULL, exempt via SQLite NULL-distinct semantics).
CREATE UNIQUE INDEX IF NOT EXISTS idx_sc_family_revision
  ON subcontracts(job_no, site_phase, supersede_seq, revision);

-- Queue drain (status='queued' oldest-first) + the tracker list read.
CREATE INDEX IF NOT EXISTS idx_subcontracts_status ON subcontracts(status, updated_at);

-- Schedule-of-Values lines (mirror po_line_items; DROP the per-watt module fields). extended_cents is
-- ALWAYS server-computed = round(qty × unit_price_cents). The corpus's degenerate case is a single line
-- whose value == the whole contract price; the table structurally supports N lines. qty ≤3 decimal places
-- (bounded at the Worker) so the canonical-JSON HMAC recompute is bit-stable across JS/Python.
CREATE TABLE IF NOT EXISTS sov_lines (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  subcontract_id  INTEGER NOT NULL REFERENCES subcontracts(id),
  position        INTEGER NOT NULL,                           -- 1-based, server-assigned
  item_number     TEXT    NOT NULL DEFAULT '',
  description     TEXT    NOT NULL DEFAULT '',                -- SOV scope element
  qty             REAL    NOT NULL DEFAULT 1,
  unit            TEXT    NOT NULL DEFAULT '',
  unit_price_cents INTEGER,
  extended_cents  INTEGER NOT NULL DEFAULT 0                  -- server-computed, never client-trusted
);

CREATE INDEX IF NOT EXISTS idx_sov_lines_sc ON sov_lines(subcontract_id, position);
