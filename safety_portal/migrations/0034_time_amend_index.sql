-- G2.3 — scoped crew EDIT/RETIRE + non-destructive time AMEND/VOID: the amend-chain index.
--
-- time_entries ALREADY carries the full append-only amend chain (0015: uuid TEXT PRIMARY KEY +
-- amends_uuid TEXT + dual attribution; 0016 added task_id) — G2.3 adds NO columns. The void
-- reason rides the existing `notes` column (a void IS an amend with hours = 0 + a required
-- reason; see SPEC.md §2.4), so there is nothing structural to add. What IS missing is an
-- index on amends_uuid: every G2.3 time read resolves to chain HEADS via a per-row
--   NOT EXISTS (SELECT 1 FROM time_entries x WHERE x.amends_uuid = t.uuid)
-- (NOT EXISTS, never NOT IN — a NULL amends_uuid in a NOT IN subquery poisons the whole
-- predicate; the 0033 prune fixed this exact class), and the amend route's head-only rule is
-- enforced by folding the same probe into the INSERT (INSERT … SELECT … WHERE NOT EXISTS —
-- atomic, so two concurrent amends of one head cannot fork the chain). Both probe
-- amends_uuid = <uuid>; without an index each probe is a full table scan per displayed row.
--
-- PARTIAL (WHERE amends_uuid IS NOT NULL): originals vastly outnumber amendments, and the
-- probe only ever looks for non-NULL pointers.
--
-- ORDER DEPENDENCY (activation): apply with `wrangler d1 migrations apply its-safety-portal-db
-- --remote` before the Worker deploy per the standing rule — but UNLIKE 0027/0030, a missed
-- apply here is NOT a lockout: no route structurally requires the index (the reads/amend-fold
-- run correct-but-slower as scans). Always `git pull` ~/its to latest main BEFORE
-- `wrangler d1 migrations apply` — the stale-migrations-list lockout class.

CREATE INDEX IF NOT EXISTS idx_time_entries_amends
  ON time_entries(amends_uuid) WHERE amends_uuid IS NOT NULL;
