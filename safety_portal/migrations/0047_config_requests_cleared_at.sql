-- 0047: add config_requests.cleared_at — a forensic-SAFE soft-dismiss for the PO Config status
-- monitor. A terminal config request (live | archived | failed) can be "cleared" from the DEFAULT
-- monitor view WITHOUT deleting the row: the config_requests row is the §50 forensic record and is
-- NEVER hard-deleted. cleared_at is an ORTHOGONAL column, deliberately NOT a new `status` value — a
-- 'cleared' status would entangle the CHECK constraint, the Worker's LEGAL_PREDECESSORS state
-- machine, AND the actuator's stamp dispatch. Nothing internal reads cleared_at: the daemon
-- pending/claim/stamp/stuck routes filter on `status`, so a clear can never advance/wedge a request.
--
-- A nullable ADD COLUMN needs NO table rebuild (unlike 0046, which recreated the table to widen a
-- CHECK constraint) — the existing indexes (status / ws_artifact / created) are preserved untouched.
-- The status monitor query adds `AND cleared_at IS NULL` by default (a ?include_cleared=1 view shows
-- them). config_requests has NO foreign keys, so no FK toggling is needed.
--
-- DEPLOY-ORDER-CRITICAL: apply to the live D1 BEFORE the Worker that reads/writes cleared_at deploys.
-- `git pull` ~/its to latest main BEFORE `wrangler d1 migrations apply` (the stale-migrations-list
-- lockout class, forensic #2).

ALTER TABLE config_requests ADD COLUMN cleared_at INTEGER;
CREATE INDEX idx_config_requests_cleared ON config_requests(cleared_at);
