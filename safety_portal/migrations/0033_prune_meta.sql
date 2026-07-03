-- GS2 (unbounded-growth audit, Slice 2) — prune observability: the one-row prune_meta record.
--
-- WHY: the daily D1 prune cron was a single point of SILENT failure (audit time-bomb #4) —
-- no last-run record, no failure flag, success only a console.log nobody tails. A dead prune
-- at 20×20 scale is a 10 GB D1 wall (every INSERT fails → /api/submit 500s → total
-- field-capture outage) in 7–17 weeks. This table is the durable heartbeat: the scheduled
-- handler UPSERTs id=1 after EVERY prune run (success or fail), and the Mac watchdog reads it
-- back over the bearer-gated GET /api/internal/prune-status (Check V): WARN when last_run_at
-- goes >48h stale, CRITICAL on failed_stages non-empty or db_size_bytes over the 6 GB
-- threshold (the previously console-only WARN, now recorded in size_warn).
--
-- ONE ROW BY DESIGN (id INTEGER PRIMARY KEY CHECK (id = 1)): this is a heartbeat, not a
-- history — history rows would themselves be unbounded growth (the exact disease this slice
-- treats). counters_json is the per-stage delete-count dict; failed_stages_json is the JSON
-- array of stage names whose fenced try/catch caught a throw (empty array = clean run).
--
-- APPLY BEFORE DEPLOY: run `npx wrangler d1 migrations apply its-safety-portal-db --remote`
-- BEFORE any Worker build that writes/reads prune_meta deploys — else the scheduled prune's
-- meta write fails (fenced — the prune itself still runs) and GET /api/internal/prune-status
-- 500s. Same rule as 0010. (Always `git pull` ~/its to latest main FIRST — the
-- stale-migrations-list lockout class, forensic #2.)

CREATE TABLE IF NOT EXISTS prune_meta (
  id                 INTEGER PRIMARY KEY CHECK (id = 1),  -- one-row table
  last_run_at        INTEGER NOT NULL,                    -- unix seconds of the last prune run
  db_size_bytes      INTEGER NOT NULL DEFAULT 0,          -- sampled D1 size (PRAGMA page_count × page_size)
  size_warn          INTEGER NOT NULL DEFAULT 0,          -- 1 when db_size_bytes exceeded the 6 GB WARN threshold
  counters_json      TEXT    NOT NULL DEFAULT '{}',       -- per-stage delete counts (JSON object)
  failed_stages_json TEXT    NOT NULL DEFAULT '[]'        -- stage names that threw (JSON array; [] = clean)
);
