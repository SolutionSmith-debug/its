-- Daily-report photo pool (DR-photo-pool Slice 1, operator directive 2026-07-03: "add more
-- photo holding sections … add as many of those as you need in the daily field report").
--
-- WHY A POOL: the inline `site_photos` field is payload-budgeted — CS2 set the client
-- per-photo budget at 280KB decoded × 4 photos ≈ 1.49MB base64, just under the Worker
-- SUBMIT_PAYLOAD_MAX/PAYLOAD_MAX = 1.8MB — so MORE inline photos structurally cannot ride
-- the submission payload. Each ADDITIONAL photo therefore uploads INDIVIDUALLY into this
-- pool (POST /api/fieldops/daily-photo, the G1 item-photo bounds verbatim: ≤400KB decoded
-- behind one derived body bound), and the SUBMISSION carries only tiny REFERENCES
-- (values.additional_photos = [{pool_id, caption?}]). At submit the Worker validates each
-- referenced row (exists / same job+date / uploaded by the acting session / not refused /
-- unclaimed-or-mine) and CLAIMS it: claimed_by_submission = the submission uuid, atomic
-- guard-in-WHERE — an already-claimed ref is a 409 (see fieldops_daily_photos.ts
-- claimAdditionalPhotos; claims land BEFORE the submission INSERT so a submission never
-- exists with unclaimed refs).
--
-- OPTION D POSTURE (inherited from item_photos/0036, RATIFIED 2026-07-03): record-only —
-- there is NO serving route, ever; no browser is ever served these bytes. DELETE-ON-SCREEN:
-- D1 holds photo bytes ONLY while status='pending'; the Slice-2 Mac screening pass (§34
-- photo_screen, byte-identical pipeline) NULLs photo_json on disposition — clean →
-- box_file_id set (Box is the permanent record) + screened_at stamped; refused → photo_json
-- NULLed + CRITICAL naming the account (the uploaded_by INSIDE photo_json is HMAC-covered).
-- The SPA renders STATUS ONLY (pending "Screening…" / clean "Photo on file ✓" / refused
-- retry copy — the G1 chip vocabulary), never an image.
--
-- CAPS (bounded growth, named constants in fieldops_daily_photos.ts): per-(job, work_date,
-- uploader) pool cap POOL_CAP_PER_DAY = 40 non-refused rows; pool-wide pending backstop
-- POOL_PENDING_GLOBAL_MAX = 200 (a dead screening loop pages via watchdog Check C /
-- ITS_Daemon_Health — the cap is the growth ceiling, not the alerting path).
--
-- LIFECYCLE / PRUNE (Slice 2): stuck-pending rows (>7d) and orphaned claims (claimed by a
-- submission uuid that never landed — the compensated-claim / crashed-insert tail) are
-- Slice-2 prune stages, mirroring the item_photos prune. Refused rows stay as byte-free
-- forensic markers. A claimed row is permanent record linkage (its submission is filed).
--
-- APPLY BEFORE DEPLOY: run `npx wrangler d1 migrations apply its-safety-portal-db --remote`
-- BEFORE any Worker build that reads/writes daily_photo_pool deploys — else the pool routes
-- and the /api/submit additional_photos claim 500. Same rule as 0010/0033/0036. (Always
-- `git pull` ~/its to latest main FIRST — the stale-migrations-list lockout class,
-- forensic #2.)

CREATE TABLE IF NOT EXISTS daily_photo_pool (
  id                    INTEGER PRIMARY KEY AUTOINCREMENT,
  job_id                TEXT    NOT NULL,                    -- FK-ish → jobs.job_id (route-validated: requireJob + placement scope)
  work_date             TEXT    NOT NULL,                    -- 'YYYY-MM-DD' (the daily report date the photo belongs to)
  uploaded_by           TEXT    NOT NULL,                    -- the AUTHENTICATED session username (also inside photo_json, HMAC-covered)
  status                TEXT    NOT NULL DEFAULT 'pending',  -- pending | clean | refused
  photo_json            TEXT,                                -- {data,name,taken_at,gps,uploaded_by} — NULLED on screen (delete-on-screen)
  hmac                  TEXT    NOT NULL,                    -- HMAC-SHA256 hex over the daily-photo canonical string ("daily_photo:v1")
  box_file_id           TEXT,                                -- set by the Slice-2 clean disposition (Box = the permanent record)
  created_at            INTEGER NOT NULL DEFAULT (unixepoch()),
  screened_at           INTEGER,                             -- stamped by the Slice-2 disposition post-back
  claimed_by_submission TEXT                                 -- the claiming submission_uuid; NULL = pre-submit pool
);
-- The per-(job, date, uploader) cap count + the SPA's own-photos list read.
CREATE INDEX IF NOT EXISTS idx_daily_photo_pool_owner
  ON daily_photo_pool(job_id, work_date, uploaded_by);
-- The Slice-2 screening pull scans pending rows oldest-first; the prune stage scans by age.
CREATE INDEX IF NOT EXISTS idx_daily_photo_pool_pending ON daily_photo_pool(status, created_at);
-- Slice-2 filing resolves a filed submission's pool photos by claim marker.
CREATE INDEX IF NOT EXISTS idx_daily_photo_pool_claimed
  ON daily_photo_pool(claimed_by_submission) WHERE claimed_by_submission IS NOT NULL;
