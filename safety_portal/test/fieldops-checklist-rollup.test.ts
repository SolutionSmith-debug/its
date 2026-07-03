import { env } from "cloudflare:test";
import { describe, it, expect, beforeEach } from "vitest";
import { provision, login, get, post, seedJob as seedJobRow, seedPersonnel as seedPersonnelRow } from "./helpers";

// ─────────────────────────────────────────────────────────────────────────────
// Assigned-Tasks tab (P4 field-ops feature) S5 — auto-rollup → Daily Report.
//   - GET /api/fieldops/checklist/mine/rollup-draft assembles a best-effort Daily Report DRAFT from the
//     day's data (job/crew/equipment/date/manager + a factual checklist summary) — but ONLY when the
//     actor's daily instance is COMPLETE (else 409). Read-only. Ownership-scoped to the actor's own
//     placed job (resolveActorPersonnel).
//   - checklist_instances.rolled_up_submission_uuid is stamped by the /mine reconcile when a daily-report
//     (parent-family) submission exists for the instance's (job_id, instance_date) — the SAME
//     submission-existence pattern S4 uses for form_linked; NOT for another job or another date.
// Runs against the REAL worker with Miniflare D1 (migration 0026 daily_default seed auto-applies fresh
// per isolated test). NO send path — filing is the existing /api/submit (Invariant 1).
// ─────────────────────────────────────────────────────────────────────────────

const seedJob = (jobId: string, projectName: string): Promise<void> => seedJobRow(jobId, { projectName });
const seedPersonnel = (name: string, username: string | null, currentJob: string | null, trade: string | null = null): Promise<number> =>
  seedPersonnelRow(name, username, currentJob, { trade });
async function seedEquipmentOnJob(name: string, identifier: string, jobId: string): Promise<void> {
  await env.DB.prepare("INSERT INTO equipment (name, kind, identifier, active) VALUES (?,?,?,1)").bind(name, "skid-steer", identifier).run();
  const eid = (await env.DB.prepare("SELECT id FROM equipment WHERE name=? ORDER BY id DESC LIMIT 1").bind(name).first<{ id: number }>())!.id;
  await env.DB.prepare("INSERT INTO equipment_location (equipment_id, job_id, label, recorded_at) VALUES (?,?,?,?)")
    .bind(eid, jobId, "On site", 1_700_000_100).run();
}
async function seedSubmission(jobId: string, formCode: string, workDate: string): Promise<void> {
  await env.DB.prepare(
    "INSERT INTO submissions (submission_uuid, job_id, form_code, work_date, payload_json) VALUES (?,?,?,?,?)",
  ).bind(`sub-${jobId}-${formCode}-${workDate}-${Math.random()}`, jobId, formCode, workDate, "{}").run();
}

interface ItemState { id: number; item_type: string; status: string; target_count: number | null; }
interface MineResp { instance: { id: number; job_id: string; instance_date: string; status: string; rolled_up_submission_uuid: string | null } | null; items: ItemState[]; }
async function mine(cookie: string): Promise<MineResp> {
  const res = await get(cookie, "/api/fieldops/checklist/mine");
  expect(res.status, await res.clone().text()).toBe(200);
  return (await res.json()) as MineResp;
}
// Drive the seeded instance to COMPLETE (0028 SOP content): manually complete each manual_attest
// item, meet each count item's target, then file the jha + daily-report submissions that auto-close
// the two seeded form_linked items.
async function completeInstance(cookie: string, jobId: string): Promise<MineResp> {
  const before = await mine(cookie);
  for (const it of before.items.filter((i) => i.item_type === "manual_attest")) {
    expect((await post(cookie, `/api/fieldops/checklist/item-state/${it.id}/complete`)).status).toBe(200);
  }
  for (const it of before.items.filter((i) => i.item_type === "count")) {
    expect((await post(cookie, `/api/fieldops/checklist/item-state/${it.id}/complete`, { value_num: it.target_count ?? 1 })).status).toBe(200);
  }
  await seedSubmission(jobId, "jha-v3", before.instance!.instance_date);
  await seedSubmission(jobId, "daily-report-v1", before.instance!.instance_date);
  const after = await mine(cookie);
  expect(after.instance!.status).toBe("complete");
  return after;
}

let manager: string;

beforeEach(async () => {
  await env.DB.batch([
    env.DB.prepare("DELETE FROM checklist_item_states"),
    env.DB.prepare("DELETE FROM checklist_instances"),
    env.DB.prepare("DELETE FROM checklist_items WHERE template_id IN (SELECT id FROM checklist_templates WHERE kind='job_override')"),
    env.DB.prepare("DELETE FROM checklist_templates WHERE kind='job_override'"),
    env.DB.prepare("DELETE FROM submissions"),
    env.DB.prepare("DELETE FROM equipment_location"),
    env.DB.prepare("DELETE FROM equipment"),
    env.DB.prepare("DELETE FROM personnel"),
    env.DB.prepare("DELETE FROM users"),
    env.DB.prepare("DELETE FROM jobs"),
    env.DB.prepare("DELETE FROM audit_log"),
  ]);
  await provision("mgr.mo", "password123", "manager");
  manager = await login("mgr.mo", "password123");
  await seedJob("JOB-A", "North Ridge Solar");
  await seedPersonnel("Mo Manager", "mgr.mo", "JOB-A");
});

describe("checklist S5 — rollup-draft gating (only for a COMPLETE instance)", () => {
  it("409s while the daily instance is still open (nothing to roll up)", async () => {
    await mine(manager); // materialize an OPEN instance
    const res = await get(manager, "/api/fieldops/checklist/mine/rollup-draft");
    expect(res.status).toBe(409);
    expect(((await res.json()) as { error: string }).error).toBe("not_complete");
  });

  it("409s for a manager with no instance yet (never opened the tab)", async () => {
    const res = await get(manager, "/api/fieldops/checklist/mine/rollup-draft");
    expect(res.status).toBe(409);
  });

  it("returns a draft once the instance is COMPLETE", async () => {
    await completeInstance(manager, "JOB-A");
    const res = await get(manager, "/api/fieldops/checklist/mine/rollup-draft");
    expect(res.status, await res.clone().text()).toBe(200);
    const draft = (await res.json()) as { job_id: string; work_date: string; form_code: string; values: Record<string, unknown> };
    expect(draft.job_id).toBe("JOB-A");
    expect(draft.form_code).toBe("daily-report");
  });
});

describe("checklist S5 — the draft's structured fields reflect the job/crew/equipment/date/manager", () => {
  it("seeds job_name (project_name), report_date (instance date), prepared_by (manager), crew, equipment", async () => {
    await seedPersonnel("Casey Crew", "sub.casey", "JOB-A", "Ironworker");
    await seedPersonnel("Pat Painter", null, "JOB-A", null);
    await seedPersonnel("Off Job", "x.off", "JOB-B", null); // NOT on JOB-A → excluded
    await seedEquipmentOnJob("Bobcat S650", "U-17", "JOB-A");
    await seedEquipmentOnJob("Elsewhere Rig", "E-9", "JOB-B"); // different job → excluded

    const after = await completeInstance(manager, "JOB-A");
    const res = await get(manager, "/api/fieldops/checklist/mine/rollup-draft");
    expect(res.status, await res.clone().text()).toBe(200);
    const draft = (await res.json()) as { work_date: string; values: Record<string, unknown> };
    const v = draft.values;

    expect(v.job_name).toBe("North Ridge Solar");
    expect(v.report_date).toBe(after.instance!.instance_date);
    expect(draft.work_date).toBe(after.instance!.instance_date);
    expect(v.prepared_by).toBe("Mo Manager");

    // crew placed on JOB-A (manager + the two crew), NOT the JOB-B person.
    const crew = v.crew_progress as { crew_subcontractor: string }[];
    const crewNames = crew.map((r) => r.crew_subcontractor);
    expect(crewNames.some((n) => n.includes("Casey Crew") && n.includes("Ironworker"))).toBe(true);
    expect(crewNames.some((n) => n.includes("Pat Painter"))).toBe(true);
    expect(crewNames.some((n) => n.includes("Off Job"))).toBe(false);

    // equipment currently on JOB-A only.
    const equip = v.equipment_on_site as { equipment_type: string }[];
    const equipTypes = equip.map((r) => r.equipment_type);
    expect(equipTypes.some((t) => t.includes("Bobcat S650"))).toBe(true);
    expect(equipTypes.some((t) => t.includes("Elsewhere Rig"))).toBe(false);

    // Narrative header fields are LEFT BLANK (not fabricated).
    expect(v.weather ?? "").toBe(""); // omitted from payload → undefined; SPA fills the blank default
    // The factual comments summary names the job + the day's filed forms.
    expect(String(v.comments)).toContain("North Ridge Solar");
    expect(String(v.comments)).toContain("daily-report-v1");
  });
});

describe("checklist S5 — rolled_up_submission_uuid reconcile on /mine", () => {
  it("stays NULL with no daily-report submission for the job+date", async () => {
    const body = await mine(manager);
    expect(body.instance!.rolled_up_submission_uuid).toBeNull();
    // A wrong-date + wrong-job daily-report, and a same-day NON-daily-report — none should link.
    await seedSubmission("JOB-A", "daily-report", "1999-01-01");
    await seedSubmission("JOB-B", "daily-report", body.instance!.instance_date);
    await seedSubmission("JOB-A", "jha-v3", body.instance!.instance_date);
    expect((await mine(manager)).instance!.rolled_up_submission_uuid).toBeNull();
  });

  it("is stamped once a daily-report (family) submission exists for the instance's job+date", async () => {
    const body = await mine(manager);
    expect(body.instance!.rolled_up_submission_uuid).toBeNull();
    await seedSubmission("JOB-A", "daily-report-v1", body.instance!.instance_date);
    const after = await mine(manager);
    expect(after.instance!.rolled_up_submission_uuid).not.toBeNull();
    expect(after.instance!.rolled_up_submission_uuid).toContain("sub-JOB-A-daily-report-v1");
  });

  it("is stable once set (set-once; a later re-read does not change it)", async () => {
    const body = await mine(manager);
    await seedSubmission("JOB-A", "daily-report-v1", body.instance!.instance_date);
    const first = (await mine(manager)).instance!.rolled_up_submission_uuid;
    expect(first).not.toBeNull();
    // A second daily-report submission arrives; the link stays the first (set-once).
    await seedSubmission("JOB-A", "daily-report-v1", body.instance!.instance_date);
    expect((await mine(manager)).instance!.rolled_up_submission_uuid).toBe(first);
  });
});

describe("checklist S5 — ownership scoping", () => {
  it("a manager's rollup-draft resolves to THEIR OWN placed job, not another manager's", async () => {
    await provision("mgr.bo", "password123", "manager");
    const other = await login("mgr.bo", "password123");
    await seedJob("JOB-B", "South Field");
    await seedPersonnel("Bo Boss", "mgr.bo", "JOB-B");

    await completeInstance(manager, "JOB-A");
    await completeInstance(other, "JOB-B");

    const draftA = (await (await get(manager, "/api/fieldops/checklist/mine/rollup-draft")).json()) as { job_id: string; values: Record<string, unknown> };
    const draftB = (await (await get(other, "/api/fieldops/checklist/mine/rollup-draft")).json()) as { job_id: string; values: Record<string, unknown> };
    expect(draftA.job_id).toBe("JOB-A");
    expect(draftA.values.prepared_by).toBe("Mo Manager");
    expect(draftB.job_id).toBe("JOB-B");
    expect(draftB.values.prepared_by).toBe("Bo Boss");
  });

  it("409s for a manager who is not placed on any job", async () => {
    await provision("mgr.un", "password123", "manager");
    const unplaced = await login("mgr.un", "password123");
    await seedPersonnel("Una Unplaced", "mgr.un", null);
    const res = await get(unplaced, "/api/fieldops/checklist/mine/rollup-draft");
    expect(res.status).toBe(409);
  });
});
