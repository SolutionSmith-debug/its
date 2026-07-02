-- SOP-CONTENT SEED — replace the migration-0026 placeholder daily_default items with the 13-item
-- Site-Supervisor-SOP daily checklist, and seed the S6 inspection library with 6 generic_inspection
-- templates from the ER Safety Manual.
--
-- PROVENANCE (content source of truth — do not invent items beyond these):
--   • Daily default (Part 1): "SITE SUPERVISOR STANDARD OPERATING PROCEDURE, Evergreen Renewables,
--     LLC | Utility-Scale Solar Construction" (`Site_Supervisor_SOP 2.docx`, extracted 2026-07-02).
--     Each item cites its SOP section in a trailing comment. Conditional duties (visitors, trenching,
--     deliveries) are manual_attest phrased "…or N/A today" so a day without that condition doesn't
--     block completion.
--   • Inspection library (Part 2): "ER Safety Manual 2025.pdf" (Box file 2265234453251; internally
--     "Safety Policy & Hazcom Program", OSHA-1926-derived). Hazard-conditional daily inspection duties
--     — seeded as generic_inspection templates admins assign per-job/per-person, NOT in the default.
--   Form codes verified against catalog.json parents: 'jha', 'daily-report'.
--
-- ORDER DEPENDENCY (activation — same standing rule as 0007/0013/0023/0025/0026/0027): apply to the
-- live D1 with
--   wrangler d1 migrations apply its-safety-portal-db --remote
-- BEFORE the next Worker deploy. NOTE this migration is CONTENT-ONLY (no schema change, no new
-- routes): the already-deployed Worker renders the new rows exactly like the old ones, so either
-- order is LOW-RISK — keep apply-before-deploy anyway per the standing rule. (Always `git pull`
-- `~/its` to latest `main` BEFORE `wrangler d1 migrations apply` — the stale-migrations-list
-- lockout class.)
--
-- GUARD / IDEMPOTENCY: the daily_default replacement (the two DELETEs) runs ONLY while the template
-- does NOT already contain the item labeled 'Daily Field Report filed' (the SENTINEL — the new seed's
-- capstone item, a label the 0026 placeholder set never used). Once this migration has run, the
-- sentinel exists → a re-apply skips both DELETEs, and every INSERT below is NOT-EXISTS-guarded on
-- its own (template, label), so re-applying changes nothing. Already-generated daily instances keep
-- their checklist_item_states snapshot (by design — S3 snapshots at generation); the new default
-- takes effect on the NEXT day's roll.
--
-- ORPHAN CLEANUP: per-job overrides reference daily_default item ids via suppresses_default_item_id.
-- Replacing the default items would strand those markers, so — same pattern as the default-item
-- delete route (fieldops_checklist.ts, checklist_default_item_delete) — the markers pointing at the
-- outgoing items are deleted FIRST (while the old ids are still resolvable), then the items. Any
-- per-job suppressions authored against the OLD placeholder items are therefore cleared; per-job
-- ADDED items (suppresses_default_item_id IS NULL) are untouched.

-- ── Part 1a — orphan-marker cleanup (BEFORE the item delete, while old ids still resolve) ──────────
DELETE FROM checklist_items
WHERE suppresses_default_item_id IN (
        SELECT di.id
        FROM checklist_items di
        JOIN checklist_templates dt ON dt.id = di.template_id AND dt.kind = 'daily_default'
      )
  AND NOT EXISTS (
        SELECT 1
        FROM checklist_items s
        JOIN checklist_templates st ON st.id = s.template_id AND st.kind = 'daily_default'
        WHERE s.label = 'Daily Field Report filed'
      );

-- ── Part 1b — drop the 0026 placeholder items (sentinel-guarded; the template ROW is kept — its id
-- is resolved by kind, not hardcoded, and instances reference it only via snapshots) ────────────────
DELETE FROM checklist_items
WHERE template_id IN (SELECT id FROM checklist_templates WHERE kind = 'daily_default')
  AND NOT EXISTS (
        SELECT 1
        FROM checklist_items s
        JOIN checklist_templates st ON st.id = s.template_id AND st.kind = 'daily_default'
        WHERE s.label = 'Daily Field Report filed'
      );

-- ── Part 1c — the 13 SOP daily items. Each INSERT is guarded on (daily_default template, label) so a
-- partial/re-apply never duplicates. Types: manual_attest (check), form_linked (auto-closes on a
-- matching submission — S4), count (value ≥ target_count). ──────────────────────────────────────────

-- SOP "7:30 AM — Arrive On Site"
INSERT INTO checklist_items (template_id, seq, item_type, label, form_code, target_count)
SELECT t.id, 10, 'manual_attest', 'Pre-shift site walkthrough — overnight hazards, standing water, access clear', NULL, NULL
FROM checklist_templates t WHERE t.kind = 'daily_default'
  AND NOT EXISTS (SELECT 1 FROM checklist_items ci WHERE ci.template_id = t.id AND ci.label = 'Pre-shift site walkthrough — overnight hazards, standing water, access clear');

-- SOP A.1 Sign Workers In
INSERT INTO checklist_items (template_id, seq, item_type, label, form_code, target_count)
SELECT t.id, 20, 'manual_attest', 'All workers signed in & verified on approved roster', NULL, NULL
FROM checklist_templates t WHERE t.kind = 'daily_default'
  AND NOT EXISTS (SELECT 1 FROM checklist_items ci WHERE ci.template_id = t.id AND ci.label = 'All workers signed in & verified on approved roster');

-- SOP A.2 PPE Verification
INSERT INTO checklist_items (template_id, seq, item_type, label, form_code, target_count)
SELECT t.id, 30, 'manual_attest', 'PPE verified for all personnel on site', NULL, NULL
FROM checklist_templates t WHERE t.kind = 'daily_default'
  AND NOT EXISTS (SELECT 1 FROM checklist_items ci WHERE ci.template_id = t.id AND ci.label = 'PPE verified for all personnel on site');

-- SOP A.3 Daily JHA (form_linked → catalog parent 'jha'; auto-closes when a jha submission exists)
INSERT INTO checklist_items (template_id, seq, item_type, label, form_code, target_count)
SELECT t.id, 40, 'form_linked', 'Daily JHA completed, walked through & signed by crew', 'jha', NULL
FROM checklist_templates t WHERE t.kind = 'daily_default'
  AND NOT EXISTS (SELECT 1 FROM checklist_items ci WHERE ci.template_id = t.id AND ci.label = 'Daily JHA completed, walked through & signed by crew');

-- SOP A.4 Visitor Log (conditional — "or N/A today")
INSERT INTO checklist_items (template_id, seq, item_type, label, form_code, target_count)
SELECT t.id, 50, 'manual_attest', 'Visitor log current — all visitors signed in, PPE''d & escorted (or N/A today)', NULL, NULL
FROM checklist_templates t WHERE t.kind = 'daily_default'
  AND NOT EXISTS (SELECT 1 FROM checklist_items ci WHERE ci.template_id = t.id AND ci.label = 'Visitor log current — all visitors signed in, PPE''d & escorted (or N/A today)');

-- SOP B.5 Trenching & Excavation (conditional)
INSERT INTO checklist_items (template_id, seq, item_type, label, form_code, target_count)
SELECT t.id, 60, 'manual_attest', 'Trench/excavation inspected by competent person before entry (or N/A today)', NULL, NULL
FROM checklist_templates t WHERE t.kind = 'daily_default'
  AND NOT EXISTS (SELECT 1 FROM checklist_items ci WHERE ci.template_id = t.id AND ci.label = 'Trench/excavation inspected by competent person before entry (or N/A today)');

-- SOP B.6–7 Electrical/General OSHA
INSERT INTO checklist_items (template_id, seq, item_type, label, form_code, target_count)
SELECT t.id, 70, 'manual_attest', 'OSHA walk — first aid stocked, fire extinguishers near hot work, heat plan if >80°F, housekeeping', NULL, NULL
FROM checklist_templates t WHERE t.kind = 'daily_default'
  AND NOT EXISTS (SELECT 1 FROM checklist_items ci WHERE ci.template_id = t.id AND ci.label = 'OSHA walk — first aid stocked, fire extinguishers near hot work, heat plan if >80°F, housekeeping');

-- SOP C.8–11 Quality Control
INSERT INTO checklist_items (template_id, seq, item_type, label, form_code, target_count)
SELECT t.id, 80, 'manual_attest', 'QC spot-checks documented — pile depth/plumb/spacing, torque, wiring; nothing covered before verified', NULL, NULL
FROM checklist_templates t WHERE t.kind = 'daily_default'
  AND NOT EXISTS (SELECT 1 FROM checklist_items ci WHERE ci.template_id = t.id AND ci.label = 'QC spot-checks documented — pile depth/plumb/spacing, torque, wiring; nothing covered before verified');

-- SOP D.12 Photo Documentation ("minimum 50 photos per day") — count, target 50
INSERT INTO checklist_items (template_id, seq, item_type, label, form_code, target_count)
SELECT t.id, 90, 'count', 'Site photos taken & uploaded', NULL, 50
FROM checklist_templates t WHERE t.kind = 'daily_default'
  AND NOT EXISTS (SELECT 1 FROM checklist_items ci WHERE ci.template_id = t.id AND ci.label = 'Site photos taken & uploaded');

-- SOP D.13 Material & Equipment Deliveries (conditional)
INSERT INTO checklist_items (template_id, seq, item_type, label, form_code, target_count)
SELECT t.id, 100, 'manual_attest', 'Deliveries inspected & personally signed for (or N/A today)', NULL, NULL
FROM checklist_templates t WHERE t.kind = 'daily_default'
  AND NOT EXISTS (SELECT 1 FROM checklist_items ci WHERE ci.template_id = t.id AND ci.label = 'Deliveries inspected & personally signed for (or N/A today)');

-- SOP E. Check-Ins 2x Per Day — count, target 2
INSERT INTO checklist_items (template_id, seq, item_type, label, form_code, target_count)
SELECT t.id, 110, 'count', 'Construction Manager check-ins (morning + end-of-day)', NULL, 2
FROM checklist_templates t WHERE t.kind = 'daily_default'
  AND NOT EXISTS (SELECT 1 FROM checklist_items ci WHERE ci.template_id = t.id AND ci.label = 'Construction Manager check-ins (morning + end-of-day)');

-- SOP END OF DAY
INSERT INTO checklist_items (template_id, seq, item_type, label, form_code, target_count)
SELECT t.id, 120, 'manual_attest', 'End-of-day site secure — workers signed out, gate locked, conduit capped, no exposed live conductors, trenches barricaded, docs filed', NULL, NULL
FROM checklist_templates t WHERE t.kind = 'daily_default'
  AND NOT EXISTS (SELECT 1 FROM checklist_items ci WHERE ci.template_id = t.id AND ci.label = 'End-of-day site secure — workers signed out, gate locked, conduit capped, no exposed live conductors, trenches barricaded, docs filed');

-- SOP E/EOD reporting duty (the S5 rollup capstone; form_linked → catalog parent 'daily-report').
-- THE SENTINEL — this label is what gates the Part-1a/1b DELETEs on re-apply.
INSERT INTO checklist_items (template_id, seq, item_type, label, form_code, target_count)
SELECT t.id, 130, 'form_linked', 'Daily Field Report filed', 'daily-report', NULL
FROM checklist_templates t WHERE t.kind = 'daily_default'
  AND NOT EXISTS (SELECT 1 FROM checklist_items ci WHERE ci.template_id = t.id AND ci.label = 'Daily Field Report filed');

-- ── Part 2 — inspection-library seed (6 generic_inspection templates, ER Safety Manual). ───────────
-- Template rows are guarded NOT EXISTS on (kind, title) — a re-apply, or an admin-created template
-- with the same title, is never duplicated. Item rows are guarded on (that template, label). Items
-- are the source bullets split into individual short manual_attest clauses (citations stay here in
-- comments, out of the labels).

-- 1. Excavation / Trench Daily Inspection — competent person; before shift + after rain.
--    [ER Safety Manual p.69–71; SOP B.5]
INSERT INTO checklist_templates (kind, job_id, title, active)
SELECT 'generic_inspection', NULL, 'Excavation / Trench Daily Inspection', 1
WHERE NOT EXISTS (SELECT 1 FROM checklist_templates WHERE kind = 'generic_inspection' AND title = 'Excavation / Trench Daily Inspection');
INSERT INTO checklist_items (template_id, seq, item_type, label)
SELECT t.id, 10, 'manual_attest', 'Protective system in place for excavations 5 ft or deeper'
FROM checklist_templates t WHERE t.kind = 'generic_inspection' AND t.title = 'Excavation / Trench Daily Inspection'
  AND NOT EXISTS (SELECT 1 FROM checklist_items ci WHERE ci.template_id = t.id AND ci.label = 'Protective system in place for excavations 5 ft or deeper');
INSERT INTO checklist_items (template_id, seq, item_type, label)
SELECT t.id, 20, 'manual_attest', 'Spoil piles set back at least 2 ft from the edge'
FROM checklist_templates t WHERE t.kind = 'generic_inspection' AND t.title = 'Excavation / Trench Daily Inspection'
  AND NOT EXISTS (SELECT 1 FROM checklist_items ci WHERE ci.template_id = t.id AND ci.label = 'Spoil piles set back at least 2 ft from the edge');
INSERT INTO checklist_items (template_id, seq, item_type, label)
SELECT t.id, 30, 'manual_attest', 'Egress ladder within 25 ft of workers (excavations 4 ft or deeper)'
FROM checklist_templates t WHERE t.kind = 'generic_inspection' AND t.title = 'Excavation / Trench Daily Inspection'
  AND NOT EXISTS (SELECT 1 FROM checklist_items ci WHERE ci.template_id = t.id AND ci.label = 'Egress ladder within 25 ft of workers (excavations 4 ft or deeper)');
INSERT INTO checklist_items (template_id, seq, item_type, label)
SELECT t.id, 40, 'manual_attest', 'Soil classification verified'
FROM checklist_templates t WHERE t.kind = 'generic_inspection' AND t.title = 'Excavation / Trench Daily Inspection'
  AND NOT EXISTS (SELECT 1 FROM checklist_items ci WHERE ci.template_id = t.id AND ci.label = 'Soil classification verified');
INSERT INTO checklist_items (template_id, seq, item_type, label)
SELECT t.id, 50, 'manual_attest', 'Adjacent-structure support checked'
FROM checklist_templates t WHERE t.kind = 'generic_inspection' AND t.title = 'Excavation / Trench Daily Inspection'
  AND NOT EXISTS (SELECT 1 FROM checklist_items ci WHERE ci.template_id = t.id AND ci.label = 'Adjacent-structure support checked');

-- 2. Scaffold Pre-Shift Inspection — competent person. [ER Safety Manual p.83–85]
INSERT INTO checklist_templates (kind, job_id, title, active)
SELECT 'generic_inspection', NULL, 'Scaffold Pre-Shift Inspection', 1
WHERE NOT EXISTS (SELECT 1 FROM checklist_templates WHERE kind = 'generic_inspection' AND title = 'Scaffold Pre-Shift Inspection');
INSERT INTO checklist_items (template_id, seq, item_type, label)
SELECT t.id, 10, 'manual_attest', 'Base/footing sound'
FROM checklist_templates t WHERE t.kind = 'generic_inspection' AND t.title = 'Scaffold Pre-Shift Inspection'
  AND NOT EXISTS (SELECT 1 FROM checklist_items ci WHERE ci.template_id = t.id AND ci.label = 'Base/footing sound');
INSERT INTO checklist_items (template_id, seq, item_type, label)
SELECT t.id, 20, 'manual_attest', 'Fully planked'
FROM checklist_templates t WHERE t.kind = 'generic_inspection' AND t.title = 'Scaffold Pre-Shift Inspection'
  AND NOT EXISTS (SELECT 1 FROM checklist_items ci WHERE ci.template_id = t.id AND ci.label = 'Fully planked');
INSERT INTO checklist_items (template_id, seq, item_type, label)
SELECT t.id, 30, 'manual_attest', 'Guardrails complete'
FROM checklist_templates t WHERE t.kind = 'generic_inspection' AND t.title = 'Scaffold Pre-Shift Inspection'
  AND NOT EXISTS (SELECT 1 FROM checklist_items ci WHERE ci.template_id = t.id AND ci.label = 'Guardrails complete');
INSERT INTO checklist_items (template_id, seq, item_type, label)
SELECT t.id, 40, 'manual_attest', 'Access ladder in place'
FROM checklist_templates t WHERE t.kind = 'generic_inspection' AND t.title = 'Scaffold Pre-Shift Inspection'
  AND NOT EXISTS (SELECT 1 FROM checklist_items ci WHERE ci.template_id = t.id AND ci.label = 'Access ladder in place');
INSERT INTO checklist_items (template_id, seq, item_type, label)
SELECT t.id, 50, 'manual_attest', 'Tags current'
FROM checklist_templates t WHERE t.kind = 'generic_inspection' AND t.title = 'Scaffold Pre-Shift Inspection'
  AND NOT EXISTS (SELECT 1 FROM checklist_items ci WHERE ci.template_id = t.id AND ci.label = 'Tags current');

-- 3. Crane & Rigging Daily Check. [ER Safety Manual p.51, p.102]
INSERT INTO checklist_templates (kind, job_id, title, active)
SELECT 'generic_inspection', NULL, 'Crane & Rigging Daily Check', 1
WHERE NOT EXISTS (SELECT 1 FROM checklist_templates WHERE kind = 'generic_inspection' AND title = 'Crane & Rigging Daily Check');
INSERT INTO checklist_items (template_id, seq, item_type, label)
SELECT t.id, 10, 'manual_attest', 'Operator daily log signed'
FROM checklist_templates t WHERE t.kind = 'generic_inspection' AND t.title = 'Crane & Rigging Daily Check'
  AND NOT EXISTS (SELECT 1 FROM checklist_items ci WHERE ci.template_id = t.id AND ci.label = 'Operator daily log signed');
INSERT INTO checklist_items (template_id, seq, item_type, label)
SELECT t.id, 20, 'manual_attest', 'Pre-shift visual inspection done'
FROM checklist_templates t WHERE t.kind = 'generic_inspection' AND t.title = 'Crane & Rigging Daily Check'
  AND NOT EXISTS (SELECT 1 FROM checklist_items ci WHERE ci.template_id = t.id AND ci.label = 'Pre-shift visual inspection done');
INSERT INTO checklist_items (template_id, seq, item_type, label)
SELECT t.id, 30, 'manual_attest', 'Rigging inspected'
FROM checklist_templates t WHERE t.kind = 'generic_inspection' AND t.title = 'Crane & Rigging Daily Check'
  AND NOT EXISTS (SELECT 1 FROM checklist_items ci WHERE ci.template_id = t.id AND ci.label = 'Rigging inspected');
INSERT INTO checklist_items (template_id, seq, item_type, label)
SELECT t.id, 40, 'manual_attest', 'Swing radius barricaded'
FROM checklist_templates t WHERE t.kind = 'generic_inspection' AND t.title = 'Crane & Rigging Daily Check'
  AND NOT EXISTS (SELECT 1 FROM checklist_items ci WHERE ci.template_id = t.id AND ci.label = 'Swing radius barricaded');
INSERT INTO checklist_items (template_id, seq, item_type, label)
SELECT t.id, 50, 'manual_attest', 'Lift plan current for critical lifts'
FROM checklist_templates t WHERE t.kind = 'generic_inspection' AND t.title = 'Crane & Rigging Daily Check'
  AND NOT EXISTS (SELECT 1 FROM checklist_items ci WHERE ci.template_id = t.id AND ci.label = 'Lift plan current for critical lifts');

-- 4. Aerial Lift / MEWP Daily Check. [ER Safety Manual p.48, p.92]
INSERT INTO checklist_templates (kind, job_id, title, active)
SELECT 'generic_inspection', NULL, 'Aerial Lift / MEWP Daily Check', 1
WHERE NOT EXISTS (SELECT 1 FROM checklist_templates WHERE kind = 'generic_inspection' AND title = 'Aerial Lift / MEWP Daily Check');
INSERT INTO checklist_items (template_id, seq, item_type, label)
SELECT t.id, 10, 'manual_attest', 'Controls tested before use'
FROM checklist_templates t WHERE t.kind = 'generic_inspection' AND t.title = 'Aerial Lift / MEWP Daily Check'
  AND NOT EXISTS (SELECT 1 FROM checklist_items ci WHERE ci.template_id = t.id AND ci.label = 'Controls tested before use');
INSERT INTO checklist_items (template_id, seq, item_type, label)
SELECT t.id, 20, 'manual_attest', 'Harness + lanyard worn & tied off'
FROM checklist_templates t WHERE t.kind = 'generic_inspection' AND t.title = 'Aerial Lift / MEWP Daily Check'
  AND NOT EXISTS (SELECT 1 FROM checklist_items ci WHERE ci.template_id = t.id AND ci.label = 'Harness + lanyard worn & tied off');
INSERT INTO checklist_items (template_id, seq, item_type, label)
SELECT t.id, 30, 'manual_attest', 'Area overhead clear'
FROM checklist_templates t WHERE t.kind = 'generic_inspection' AND t.title = 'Aerial Lift / MEWP Daily Check'
  AND NOT EXISTS (SELECT 1 FROM checklist_items ci WHERE ci.template_id = t.id AND ci.label = 'Area overhead clear');

-- 5. Ladder & Fall-Gear Daily Inspection. [ER Safety Manual p.97, p.42]
INSERT INTO checklist_templates (kind, job_id, title, active)
SELECT 'generic_inspection', NULL, 'Ladder & Fall-Gear Daily Inspection', 1
WHERE NOT EXISTS (SELECT 1 FROM checklist_templates WHERE kind = 'generic_inspection' AND title = 'Ladder & Fall-Gear Daily Inspection');
INSERT INTO checklist_items (template_id, seq, item_type, label)
SELECT t.id, 10, 'manual_attest', 'Ladders visually inspected'
FROM checklist_templates t WHERE t.kind = 'generic_inspection' AND t.title = 'Ladder & Fall-Gear Daily Inspection'
  AND NOT EXISTS (SELECT 1 FROM checklist_items ci WHERE ci.template_id = t.id AND ci.label = 'Ladders visually inspected');
INSERT INTO checklist_items (template_id, seq, item_type, label)
SELECT t.id, 20, 'manual_attest', 'PFAS inspected before each use'
FROM checklist_templates t WHERE t.kind = 'generic_inspection' AND t.title = 'Ladder & Fall-Gear Daily Inspection'
  AND NOT EXISTS (SELECT 1 FROM checklist_items ci WHERE ci.template_id = t.id AND ci.label = 'PFAS inspected before each use');
INSERT INTO checklist_items (template_id, seq, item_type, label)
SELECT t.id, 30, 'manual_attest', 'Defective gear tagged out'
FROM checklist_templates t WHERE t.kind = 'generic_inspection' AND t.title = 'Ladder & Fall-Gear Daily Inspection'
  AND NOT EXISTS (SELECT 1 FROM checklist_items ci WHERE ci.template_id = t.id AND ci.label = 'Defective gear tagged out');

-- 6. Hot-Work / Welding Daily Check. [ER Safety Manual p.125–126; SOP B.7]
INSERT INTO checklist_templates (kind, job_id, title, active)
SELECT 'generic_inspection', NULL, 'Hot-Work / Welding Daily Check', 1
WHERE NOT EXISTS (SELECT 1 FROM checklist_templates WHERE kind = 'generic_inspection' AND title = 'Hot-Work / Welding Daily Check');
INSERT INTO checklist_items (template_id, seq, item_type, label)
SELECT t.id, 10, 'manual_attest', 'Hoses & torches inspected at shift start'
FROM checklist_templates t WHERE t.kind = 'generic_inspection' AND t.title = 'Hot-Work / Welding Daily Check'
  AND NOT EXISTS (SELECT 1 FROM checklist_items ci WHERE ci.template_id = t.id AND ci.label = 'Hoses & torches inspected at shift start');
INSERT INTO checklist_items (template_id, seq, item_type, label)
SELECT t.id, 20, 'manual_attest', 'Fire extinguisher within 100 ft'
FROM checklist_templates t WHERE t.kind = 'generic_inspection' AND t.title = 'Hot-Work / Welding Daily Check'
  AND NOT EXISTS (SELECT 1 FROM checklist_items ci WHERE ci.template_id = t.id AND ci.label = 'Fire extinguisher within 100 ft');
INSERT INTO checklist_items (template_id, seq, item_type, label)
SELECT t.id, 30, 'manual_attest', 'Combustibles cleared'
FROM checklist_templates t WHERE t.kind = 'generic_inspection' AND t.title = 'Hot-Work / Welding Daily Check'
  AND NOT EXISTS (SELECT 1 FROM checklist_items ci WHERE ci.template_id = t.id AND ci.label = 'Combustibles cleared');
