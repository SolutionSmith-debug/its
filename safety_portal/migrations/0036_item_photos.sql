-- G1 Slice 1 (checklist item-photo capture, Option D RATIFIED 2026-07-03) — the item_photos
-- pending queue.
--
-- WHY: checklist item states carry a photo_ref (0026) but no capture path existed — the R3
-- "render half only" gap. This table is the CAPTURE queue: the Worker accepts ONE bounds-gated
-- photo per item state (POST /api/fieldops/checklist/item-state/:id/photo, the verbatim
-- validatePhotoValues bounds), stores the base64 payload + meta here as `photo_json`, HMAC-signs
-- it (canonical string documented on the route), and stamps the owning item state's
-- photo_ref = 'pending:<id>' in the SAME atomic batch (W4).
--
-- OPTION D IS FINAL (operator ratification, 2026-07-03): there is NO serving route, ever — no
-- browser is ever served these bytes, screened or not. DELETE-ON-SCREEN supersedes retention:
-- D1 holds photo bytes ONLY while status='pending'; the Slice-2 Mac screening pass (§34
-- photo_screen, byte-identical pipeline) NULLs photo_json on disposition — clean → box_file_id
-- set (Box is the permanent record) + screened_at stamped; refused → photo_json NULLed, CRITICAL
-- naming the account, item completion stands. The SPA renders STATUS ONLY (pending "screening…" /
-- clean "photo on file ✓" / refused retry copy) — never an image.
--
-- ONE PHOTO PER ITEM STATE: the partial UNIQUE index makes the rule structural — at most one
-- pending|clean row per item_state_id (a second upload 409s, even under a lost check-then-act
-- race); a REFUSED row vacates the slot, so a retry after refusal inserts a fresh pending row
-- (refused rows stay as byte-free forensic markers until their item state is cancelled).
--
-- item_state_id is FK-ish — validated by the route (loadOwnedItemState: existence + assignee
-- ownership) rather than enforced by D1 (consistent with checklist_item_states.instance_id,
-- 0026). The instance-cancel route cascades this table; the prune stage 'item_photos' deletes
-- stuck-pending rows (>7d — screening loop dead; growth cap, NOT the alerting path: a dead
-- portal_poll pages via watchdog Check C / ITS_Daemon_Health within hours) and drops orphans.
--
-- APPLY BEFORE DEPLOY: run `npx wrangler d1 migrations apply its-safety-portal-db --remote`
-- BEFORE any Worker build that reads/writes item_photos deploys — else the photo route and the
-- extended /checklist/assigned read 500. Same rule as 0010/0033. (Always `git pull` ~/its to
-- latest main FIRST — the stale-migrations-list lockout class, forensic #2.)
-- NOTE: 0034/0035 belong to parallel slices — number gaps are expected and harmless
-- (wrangler applies by filename order; nothing indexes on contiguity).

CREATE TABLE IF NOT EXISTS item_photos (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  item_state_id INTEGER NOT NULL,                    -- FK-ish → checklist_item_states.id (route-validated)
  status        TEXT    NOT NULL DEFAULT 'pending',  -- pending | clean | refused
  photo_json    TEXT,                                -- {data,name,taken_at,gps,uploaded_by} — NULLED on screen (delete-on-screen)
  hmac          TEXT    NOT NULL,                    -- HMAC-SHA256 hex over the item-photo canonical string
  box_file_id   TEXT,                                -- set by the Slice-2 clean disposition (Box = the permanent record)
  created_at    INTEGER NOT NULL DEFAULT (unixepoch()),
  screened_at   INTEGER                              -- stamped by the Slice-2 disposition post-back
);
CREATE INDEX IF NOT EXISTS idx_item_photos_item_state ON item_photos(item_state_id);
-- The one-photo rule, structurally: at most ONE live (pending|clean) photo per item state.
CREATE UNIQUE INDEX IF NOT EXISTS idx_item_photos_one_live
  ON item_photos(item_state_id) WHERE status IN ('pending', 'clean');
-- The Slice-2 screening pull scans pending rows oldest-first; the prune stage scans by age.
CREATE INDEX IF NOT EXISTS idx_item_photos_pending ON item_photos(status, created_at);
