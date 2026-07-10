-- PO workstream S2 (Aug-7 delivery program WS1) — po_vendors: the D1 PORTAL CACHE of the
-- ITS_Vendors Smartsheet sheet (the SOLE vendor SoR, S1/D4), plus the portal-side vendor-key
-- allocator.
--
-- D4 (bidirectional §51 rider): the vendor list is editable BOTH sides.
--   * DOWN-SYNC (Smartsheet → D1): the Mac daemon POSTs the full ITS_Vendors set to
--     /api/po/internal/vendors/sync — a full-replace UPSERT with a DIRTY-ROW FENCE: any row
--     whose sync_state='pending' (an un-mirrored portal edit) is SKIPPED, never clobbered.
--     Refuses an empty payload (a Smartsheet read-miss must never wipe the cache). NEVER
--     deletes — a vendor retired in the sheet arrives with active=0 (deactivate-not-delete).
--   * UP-SYNC (D1 → Smartsheet): portal creates/edits set sync_state='pending' and bump
--     mirror_version (the version vector, the jobs-mirror 0021 pattern); the daemon reads
--     /api/po/internal/vendors/pending, bridge-key find-or-creates the sheet row by
--     vendor_key, then /api/po/internal/vendors/mark-mirrored flips pending→synced ONLY if
--     mirror_version is unchanged (the watermark guard — an edit racing the mirror keeps the
--     row dirty and it re-syncs next cycle).
--
-- vendor_key 'VEN-######' is the cross-system bridge key (S1: the ITS_Vendors "Vendor Key"
-- column; seeded rows already hold VEN-000001..N sheet-side). Portal-created vendors allocate
-- from po_vendor_counter (the 0022 job_counter pattern), with a MAX(counter, max-suffix-seen)
-- self-heal folded into the single atomic allocation UPDATE so a portal-minted key can never
-- collide with a key that arrived via down-sync (see worker/po.ts allocateVendorKey).
--
-- origin records which side AUTHORED the current cached version ('portal' = an un-mirrored or
-- last-authored-here edit; 'smartsheet' = the down-synced sheet state). Unlike jobs.origin
-- (creation provenance, permanent), vendor origin flips with authorship: a portal edit sets
-- 'portal'; a down-sync overwrite (only ever of a NON-dirty row) resets it to 'smartsheet'.
--
-- supply_categories is a JSON TEXT array (mirrors the S1 MULTI_PICKLIST "Supply Categories"
-- column) — same storage shape as jobs.safety_cc.
--
-- ORDER DEPENDENCY (activation): apply to the live D1 BEFORE the Worker that registers
-- /api/po/* deploys (the 0006/0007/0013 rule) — a Worker deployed ahead of this migration
-- 500s every po_vendors read/write.
CREATE TABLE IF NOT EXISTS po_vendors (
  vendor_key            TEXT    PRIMARY KEY,               -- 'VEN-######' bridge key
  vendor_name           TEXT    NOT NULL,
  address               TEXT    NOT NULL DEFAULT '',
  contact_name          TEXT    NOT NULL DEFAULT '',
  contact_email         TEXT    NOT NULL DEFAULT '',
  contact_phone         TEXT    NOT NULL DEFAULT '',
  region                TEXT    NOT NULL DEFAULT '',
  supply_categories     TEXT    NOT NULL DEFAULT '[]',     -- JSON text array
  default_terms_profile TEXT    NOT NULL DEFAULT '',       -- terms library profile id (S3)
  gtc_reference         TEXT    NOT NULL DEFAULT '',       -- negotiated-GTC pointer (attach-not-generate, D6)
  active                INTEGER NOT NULL DEFAULT 1,        -- deactivate-not-delete
  notes                 TEXT    NOT NULL DEFAULT '',
  origin                TEXT    NOT NULL DEFAULT 'smartsheet' CHECK (origin IN ('smartsheet','portal')),
  sync_state            TEXT    NOT NULL DEFAULT 'synced'     CHECK (sync_state IN ('synced','pending')),
  mirror_version        INTEGER NOT NULL DEFAULT 0,        -- bumped by every portal create/edit
  mirrored_version      INTEGER NOT NULL DEFAULT 0,        -- the up-sync watermark
  created_at            INTEGER NOT NULL DEFAULT (unixepoch()),
  updated_at            INTEGER NOT NULL DEFAULT (unixepoch())
);

-- The vendor-picker read path (active vendors, region-chip filtered).
CREATE INDEX IF NOT EXISTS idx_po_vendors_active_region ON po_vendors(active, region);

-- Single-row allocator for PORTAL-created vendor keys (the 0022 job_counter shape).
-- Seed 0: sheet-side seeded keys (VEN-000001..N, seed_its_vendors.py) reach D1 via down-sync,
-- and the allocation UPDATE self-heals past the max suffix seen, so 0 is always a safe seed.
-- INSERT OR IGNORE keeps a re-apply from resetting a counter that has handed out keys.
CREATE TABLE IF NOT EXISTS po_vendor_counter (
  id         INTEGER PRIMARY KEY CHECK (id = 1),
  last_value INTEGER NOT NULL
);
INSERT OR IGNORE INTO po_vendor_counter (id, last_value) VALUES (1, 0);
