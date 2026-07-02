-- R1 (Assigned-Tasks refinement) — snapshot the assigned template's TITLE onto the instance.
--
-- WHY: an assigned inspection instance carried no name — the UI could only render "Inspection #<id>"
-- (the A1/A3/A4 finding). checklist_instances has no template_id (deliberate: instances live on their
-- checklist_item_states SNAPSHOT, decoupled from later template edits/deletes — see 0026), so the
-- title is snapshotted the same way the items are: `template_title` is stamped by POST
-- /api/fieldops/checklist/assign at assign time (fieldops_checklist.ts) and returned by
-- GET /api/fieldops/checklist/assigned. Renaming or deleting the library template later never
-- mutates an in-flight instance — same lineage rule as the item snapshot.
--
-- BACKFILL (best-effort): existing kind='inspection' instances recover their title through the
-- item-snapshot lineage — item_states.source_item_id → checklist_items.template_id →
-- checklist_templates.title (kind='generic_inspection' only, so a daily instance can never pick up
-- a library title). Unresolvable rows (template or items since deleted, or a 0-item legacy assign)
-- stay NULL — the UI falls back to "Inspection #<id>" for those. Idempotent: the UPDATE only touches
-- template_title IS NULL rows, and re-running resolves to the same value (or NULL again).
--
-- ORDER DEPENDENCY (activation — the deploy-lockout class, same standing rule as
-- 0007/0013/0023/0025/0026/0027/0028): apply this migration to the live D1 with
--   wrangler d1 migrations apply its-safety-portal-db --remote
-- BEFORE the Worker that writes/reads template_title deploys — otherwise POST /checklist/assign and
-- GET /checklist/assigned 500 on the missing column. (Always `git pull` `~/its` to latest `main`
-- BEFORE `wrangler d1 migrations apply` — the stale-migrations-list lockout class.)

ALTER TABLE checklist_instances ADD COLUMN template_title TEXT;

UPDATE checklist_instances
SET template_title = (
  SELECT t.title
  FROM checklist_item_states s
  JOIN checklist_items ci ON ci.id = s.source_item_id
  JOIN checklist_templates t ON t.id = ci.template_id
  WHERE s.instance_id = checklist_instances.id
    AND t.kind = 'generic_inspection'
    AND t.title IS NOT NULL
  ORDER BY s.id ASC
  LIMIT 1
)
WHERE kind = 'inspection' AND template_title IS NULL;
