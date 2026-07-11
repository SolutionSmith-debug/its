-- Subcontracts workstream S1 — `subcontractors` + `subcontractor_counter`: the D1 CACHE of the
-- ITS_Subcontractors Smartsheet SoR (§51 bidirectional sync), a 1:1 fork of po_vendors (migration
-- 0042). Smartsheet ITS_Subcontractors is the SYSTEM OF RECORD; this table is a synced cache read
-- at render time for the subcontractor snapshot + the send-time recipient. Polarity is deliberate:
-- for the PARTY registry Smartsheet is authoritative and D1 mirrors it (opposite of subcontracts,
-- where D1 is authoritative and Subcontract_Log mirrors D1).
--
-- DELTA vs po_vendors: vendor_key→sub_key ('SUB-######' bridge key), vendor_name→sub_name,
-- supply_categories→trades (the 8 canonical trade slots + specialty, JSON array), gtc_reference→
-- msa_reference (a negotiated Master Subcontract Agreement pointer, the attach-not-generate role),
-- plus license_number + coi_reference. NOTE coi_reference is a POINTER only — the insurance/COI
-- evidence lives OUTSIDE the corpus (an unseen source-of-truth); there is deliberately NO
-- compliance-blocking gate built against it (the "don't build against an unseen SoR" rule). The
-- origin / sync_state / mirror_version / mirrored_version §51 watermark machinery and the
-- deactivate-not-delete `active` lifecycle are carried over verbatim.
--
-- ORDER DEPENDENCY (activation): apply to the live D1 BEFORE the /api/subcontracts/* Worker deploys.

CREATE TABLE IF NOT EXISTS subcontractors (
  sub_key               TEXT    PRIMARY KEY,                  -- 'SUB-######' bridge key (Smartsheet↔D1)
  sub_name              TEXT    NOT NULL,
  address               TEXT    NOT NULL DEFAULT '',
  contact_name          TEXT    NOT NULL DEFAULT '',
  contact_email         TEXT    NOT NULL DEFAULT '',
  contact_phone         TEXT    NOT NULL DEFAULT '',
  region                TEXT    NOT NULL DEFAULT '',
  trades                TEXT    NOT NULL DEFAULT '[]',        -- JSON array of canonical trade slots
  default_terms_profile TEXT    NOT NULL DEFAULT '',          -- subcontract_body profile pin
  msa_reference         TEXT    NOT NULL DEFAULT '',          -- negotiated MSA pointer (attach-not-generate)
  coi_reference         TEXT    NOT NULL DEFAULT '',          -- insurance-cert POINTER only (no gate — unseen SoR)
  license_number        TEXT    NOT NULL DEFAULT '',
  active                INTEGER NOT NULL DEFAULT 1,           -- deactivate-not-delete lifecycle
  notes                 TEXT    NOT NULL DEFAULT '',
  origin                TEXT    NOT NULL DEFAULT 'smartsheet' CHECK (origin IN ('smartsheet','portal')),
  sync_state            TEXT    NOT NULL DEFAULT 'synced' CHECK (sync_state IN ('synced','pending')),
  mirror_version        INTEGER NOT NULL DEFAULT 0,           -- bumped by a portal edit (up-sync watermark)
  mirrored_version      INTEGER NOT NULL DEFAULT 0,           -- last version pushed to the SoR
  created_at            INTEGER NOT NULL DEFAULT (unixepoch()),
  updated_at            INTEGER NOT NULL DEFAULT (unixepoch())
);

CREATE INDEX IF NOT EXISTS idx_subcontractors_active_region ON subcontractors(active, region);

-- Single-row allocator for portal-minted SUB- keys (verbatim po_vendor_counter / 0022 job_counter
-- pattern; the minter self-heals to MAX(counter, max-suffix-seen) so a hand-issued key can't collide).
CREATE TABLE IF NOT EXISTS subcontractor_counter (
  id         INTEGER PRIMARY KEY CHECK (id = 1),
  last_value INTEGER NOT NULL DEFAULT 0
);
INSERT OR IGNORE INTO subcontractor_counter (id, last_value) VALUES (1, 0);
