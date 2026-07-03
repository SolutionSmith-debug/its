import { env } from "cloudflare:test";
import { describe, it, expect } from "vitest";

// ─────────────────────────────────────────────────────────────────────────────
// SOP-content seed (migration 0028) — the 13-item Site-Supervisor-SOP daily_default (replacing the
// 0026 placeholder set) + the 6-template ER-Safety-Manual generic_inspection library.
// Asserts directly against the migrated D1 (no routes — this is a seed-content contract):
//   - the daily_default template carries exactly the 13 SOP items in seq order, spot-checking the
//     two count items (photos target 50, check-ins target 2) and the two form_linked items
//     (jha at seq 40, daily-report at seq 130 — the re-apply SENTINEL);
//   - the 6 library templates exist, each with at least one manual_attest item;
//   - IDEMPOTENCY: re-running every 0028 statement (twice) against the already-seeded DB changes
//     nothing — the sentinel guard skips the delete+reseed and the per-row NOT-EXISTS guards skip
//     every insert;
//   - the sentinel-gated RESEED path: with the sentinel removed (simulating a pre-0028 default) a
//     re-apply clears job_override suppression markers pointing at the outgoing default items (the
//     orphan cleanup) and restores the 13 items.
// Runs on Miniflare D1 with the real migrations auto-applied (apply-migrations.ts); storage is
// isolated per test, so each starts from the freshly-migrated schema.
// ─────────────────────────────────────────────────────────────────────────────

const EXPECTED_DAILY: { seq: number; item_type: string; label: string; form_code: string | null; target_count: number | null }[] = [
  { seq: 10, item_type: "manual_attest", label: "Pre-shift site walkthrough — overnight hazards, standing water, access clear", form_code: null, target_count: null },
  { seq: 20, item_type: "manual_attest", label: "All workers signed in & verified on approved roster", form_code: null, target_count: null },
  { seq: 30, item_type: "manual_attest", label: "PPE verified for all personnel on site", form_code: null, target_count: null },
  { seq: 40, item_type: "form_linked", label: "Daily JHA completed, walked through & signed by crew", form_code: "jha", target_count: null },
  { seq: 50, item_type: "manual_attest", label: "Visitor log current — all visitors signed in, PPE'd & escorted (or N/A today)", form_code: null, target_count: null },
  { seq: 60, item_type: "manual_attest", label: "Trench/excavation inspected by competent person before entry (or N/A today)", form_code: null, target_count: null },
  { seq: 70, item_type: "manual_attest", label: "OSHA walk — first aid stocked, fire extinguishers near hot work, heat plan if >80°F, housekeeping", form_code: null, target_count: null },
  { seq: 80, item_type: "manual_attest", label: "QC spot-checks documented — pile depth/plumb/spacing, torque, wiring; nothing covered before verified", form_code: null, target_count: null },
  { seq: 90, item_type: "count", label: "Site photos taken & uploaded", form_code: null, target_count: 50 },
  { seq: 100, item_type: "manual_attest", label: "Deliveries inspected & personally signed for (or N/A today)", form_code: null, target_count: null },
  { seq: 110, item_type: "count", label: "Construction Manager check-ins (morning + end-of-day)", form_code: null, target_count: 2 },
  { seq: 120, item_type: "manual_attest", label: "End-of-day site secure — workers signed out, gate locked, conduit capped, no exposed live conductors, trenches barricaded, docs filed", form_code: null, target_count: null },
  { seq: 130, item_type: "form_linked", label: "Daily Field Report filed", form_code: "daily-report", target_count: null },
];

const EXPECTED_LIBRARY_TITLES = [
  "Aerial Lift / MEWP Daily Check",
  "Crane & Rigging Daily Check",
  "Excavation / Trench Daily Inspection",
  "Hot-Work / Welding Daily Check",
  "Ladder & Fall-Gear Daily Inspection",
  "Scaffold Pre-Shift Inspection",
];

interface DailyRow { seq: number; item_type: string; label: string; form_code: string | null; target_count: number | null }

async function dailyItems(): Promise<DailyRow[]> {
  const r = await env.DB.prepare(
    "SELECT seq, item_type, label, form_code, target_count FROM checklist_items WHERE template_id=(SELECT id FROM checklist_templates WHERE kind='daily_default') AND suppresses_default_item_id IS NULL ORDER BY seq ASC",
  ).all<DailyRow>();
  return r.results ?? [];
}

// Re-execute every statement of the 0028 migration against the live test DB — the concrete
// idempotency check. TEST_MIGRATIONS is the readD1Migrations() array (name + queries per migration).
async function reapply0028(): Promise<void> {
  const m = env.TEST_MIGRATIONS.find((mig) => mig.name.startsWith("0028"));
  expect(m, "migration 0028 must be present in TEST_MIGRATIONS").toBeDefined();
  for (const q of m!.queries) await env.DB.prepare(q).run();
}

describe("0028 seed — the SOP daily_default content", () => {
  it("the daily_default template has EXACTLY the 13 SOP items in seq order (labels/types/form_codes/targets)", async () => {
    const items = await dailyItems();
    expect(items).toEqual(EXPECTED_DAILY);
    // Spot-checks called out by the slice: the count-50 photos item, the count-2 check-ins item,
    // and the two form_linked items (jha + daily-report).
    const photos = items.find((i) => i.label === "Site photos taken & uploaded")!;
    expect(photos).toMatchObject({ item_type: "count", target_count: 50 });
    const checkins = items.find((i) => i.label === "Construction Manager check-ins (morning + end-of-day)")!;
    expect(checkins).toMatchObject({ item_type: "count", target_count: 2 });
    expect(items.filter((i) => i.item_type === "form_linked").map((i) => i.form_code)).toEqual(["jha", "daily-report"]);
    // The 0026 placeholder set is gone.
    expect(items.some((i) => i.label === "File the Daily Field Report")).toBe(false);
    expect(items.some((i) => i.label === "Record site visitors")).toBe(false);
  });

  it("the 6 ER-Safety-Manual generic_inspection library templates exist, each with items", async () => {
    const tpls = await env.DB.prepare(
      "SELECT t.id, t.title, COUNT(ci.id) AS item_count FROM checklist_templates t LEFT JOIN checklist_items ci ON ci.template_id = t.id WHERE t.kind='generic_inspection' GROUP BY t.id ORDER BY t.title ASC",
    ).all<{ id: number; title: string; item_count: number }>();
    const rows = tpls.results ?? [];
    expect(rows.map((r) => r.title)).toEqual(EXPECTED_LIBRARY_TITLES);
    expect(rows.every((r) => r.item_count >= 3)).toBe(true);
    // Every library item is a plain manual_attest (no form_code / target_count).
    const bad = await env.DB.prepare(
      "SELECT COUNT(*) n FROM checklist_items ci JOIN checklist_templates t ON t.id=ci.template_id AND t.kind='generic_inspection' WHERE ci.item_type<>'manual_attest' OR ci.form_code IS NOT NULL OR ci.target_count IS NOT NULL",
    ).first<{ n: number }>();
    expect(bad!.n).toBe(0);
  });
});

describe("0028 seed — guard + idempotency", () => {
  it("re-applying every 0028 statement TWICE changes nothing (sentinel + NOT-EXISTS guards hold)", async () => {
    const before = await dailyItems();
    const beforeTpl = (await env.DB.prepare("SELECT COUNT(*) n FROM checklist_templates").first<{ n: number }>())!.n;
    const beforeItems = (await env.DB.prepare("SELECT COUNT(*) n FROM checklist_items").first<{ n: number }>())!.n;
    // Item ids must be STABLE across a guarded re-apply (a delete+reinsert would churn them and
    // strand any job_override suppression markers).
    const beforeIds = (await env.DB.prepare(
      "SELECT id FROM checklist_items WHERE template_id=(SELECT id FROM checklist_templates WHERE kind='daily_default') ORDER BY id",
    ).all<{ id: number }>()).results!.map((r) => r.id);

    await reapply0028();
    await reapply0028();

    expect(await dailyItems()).toEqual(before);
    expect((await env.DB.prepare("SELECT COUNT(*) n FROM checklist_templates").first<{ n: number }>())!.n).toBe(beforeTpl);
    expect((await env.DB.prepare("SELECT COUNT(*) n FROM checklist_items").first<{ n: number }>())!.n).toBe(beforeItems);
    const afterIds = (await env.DB.prepare(
      "SELECT id FROM checklist_items WHERE template_id=(SELECT id FROM checklist_templates WHERE kind='daily_default') ORDER BY id",
    ).all<{ id: number }>()).results!.map((r) => r.id);
    expect(afterIds).toEqual(beforeIds);
  });

  it("with the sentinel absent (pre-0028 state) a re-apply RESEEDS and clears orphaned suppression markers", async () => {
    // Simulate the pre-0028 world: drop the sentinel item and plant a job_override suppression
    // marker pointing at a surviving default item (as a per-job override against the OLD default).
    const dt = (await env.DB.prepare("SELECT id FROM checklist_templates WHERE kind='daily_default'").first<{ id: number }>())!.id;
    await env.DB.prepare("DELETE FROM checklist_items WHERE template_id=?1 AND label='Daily Field Report filed'").bind(dt).run();
    const victim = (await env.DB.prepare("SELECT id FROM checklist_items WHERE template_id=?1 ORDER BY seq LIMIT 1").bind(dt).first<{ id: number }>())!.id;
    await env.DB.prepare("INSERT INTO checklist_templates (kind, job_id, active) VALUES ('job_override','JOB-SEED',1)").run();
    const ot = (await env.DB.prepare("SELECT id FROM checklist_templates WHERE kind='job_override' AND job_id='JOB-SEED'").first<{ id: number }>())!.id;
    await env.DB.prepare("INSERT INTO checklist_items (template_id, seq, item_type, label, suppresses_default_item_id) VALUES (?1,0,'manual_attest','(suppressed)',?2)").bind(ot, victim).run();
    // A per-job ADDED item (no suppresses_default_item_id) must SURVIVE the reseed.
    await env.DB.prepare("INSERT INTO checklist_items (template_id, seq, item_type, label) VALUES (?1,999,'manual_attest','Job-added step')").bind(ot).run();

    await reapply0028();

    // The 13 items are back, the orphaned marker is gone, the job's own added item survived.
    expect(await dailyItems()).toEqual(EXPECTED_DAILY);
    const markers = (await env.DB.prepare("SELECT COUNT(*) n FROM checklist_items WHERE suppresses_default_item_id IS NOT NULL").first<{ n: number }>())!.n;
    expect(markers).toBe(0);
    const kept = (await env.DB.prepare("SELECT COUNT(*) n FROM checklist_items WHERE template_id=?1 AND label='Job-added step'").bind(ot).first<{ n: number }>())!.n;
    expect(kept).toBe(1);
  });
});
