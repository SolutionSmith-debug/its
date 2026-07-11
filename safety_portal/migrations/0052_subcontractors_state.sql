-- 0052: subcontractors grouped by STATE, not region. Subcontracts are jurisdiction-specific (the
-- governing law + lien-waiver annexes are per-state), so the subcontractor registry's grouping/filter
-- dimension is the 2-letter USPS STATE (matching the subcontract's governing_law_state) rather than the
-- coarse West/Midwest/East/National region the po_vendors cache uses. Replaces the `region` column with
-- `state` and re-points the covering index.
--
-- TABLE-REBUILD (not ALTER … DROP COLUMN): the repo has NO precedent for DROP COLUMN and D1's support
-- for it is unreliable — the established, CI-proven idiom here (0032/0046/0048) is recreate-and-copy,
-- which D1 + the vitest Miniflare harness definitely accept. `state` is a REPLACEMENT, not a rename:
-- region values ('West'/'Midwest'/…) are NOT valid USPS states, so the copy drops region entirely and
-- `state` takes its blank DEFAULT (the seeder / the §51 down-sync fills it from ITS_Subcontractors).
-- The table ships DARK (no live rows yet), so INSERT … SELECT is a near-empty copy; it's included so
-- the migration is correct even if a dev DB holds rows. `subcontractor_counter` is a SEPARATE table —
-- untouched. subcontractors has NO foreign keys (it's a cache), so no FK toggling is needed.
--
-- The §51 bidirectional Smartsheet↔D1 sync (subcontracts/subcontractors.py) carries `state` both ways
-- automatically — a column-scoped mirror, so a state edit on either side reflects on the other, exactly
-- like the vendor region did.
--
-- DEPLOY-ORDER-CRITICAL: apply to the live D1 BEFORE the /api/subcontracts/* Worker (SC-S3c) that reads
-- `state` deploys. `git pull` ~/its to latest main BEFORE `wrangler d1 migrations apply` (the
-- stale-migrations-list lockout class).

CREATE TABLE subcontractors_new (
  sub_key               TEXT    PRIMARY KEY,                  -- 'SUB-######' bridge key (Smartsheet↔D1)
  sub_name              TEXT    NOT NULL,
  address               TEXT    NOT NULL DEFAULT '',
  contact_name          TEXT    NOT NULL DEFAULT '',
  contact_email         TEXT    NOT NULL DEFAULT '',
  contact_phone         TEXT    NOT NULL DEFAULT '',
  state                 TEXT    NOT NULL DEFAULT '',          -- 2-letter USPS (jurisdiction grouping)
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

-- Copy every column EXCEPT region (dropped; `state` takes its blank DEFAULT).
INSERT INTO subcontractors_new
  (sub_key, sub_name, address, contact_name, contact_email, contact_phone, trades,
   default_terms_profile, msa_reference, coi_reference, license_number, active, notes,
   origin, sync_state, mirror_version, mirrored_version, created_at, updated_at)
  SELECT sub_key, sub_name, address, contact_name, contact_email, contact_phone, trades,
   default_terms_profile, msa_reference, coi_reference, license_number, active, notes,
   origin, sync_state, mirror_version, mirrored_version, created_at, updated_at
  FROM subcontractors;

DROP TABLE subcontractors;
ALTER TABLE subcontractors_new RENAME TO subcontractors;

CREATE INDEX IF NOT EXISTS idx_subcontractors_active_state ON subcontractors(active, state);
