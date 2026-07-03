import { env, SELF } from "cloudflare:test";
import { describe, it, expect, beforeEach } from "vitest";
import { provision, login } from "./helpers";

// ─────────────────────────────────────────────────────────────────────────────
// GET /api/fieldops/checklist/assigned — PHASE-BATCHED shape (optimization slice 3, finding #9).
// The handler collapsed 3 sequential D1 round trips PER instance into one reconcile batch + one
// read batch (3N+2 → 4 round trips) with IDENTICAL SQL. These tests pin the behaviors the batching
// must preserve, on responses carrying SEVERAL instances at once:
//   • per-instance item lists stay correctly ALIGNED (the indexed read-back: items[2i] ↔ inst i);
//   • the S4 loop-closure reconcile still runs BEFORE the reads — a matching submission auto-closes
//     the form-bearing item AND the instance status returned in the SAME response is the fresh
//     post-reconcile one ('complete' when the auto-close finished the instance);
//   • instances WITHOUT a (job, date) get no reconcile but still return their items;
//   • the linked/empty contracts are unchanged ({inspections:[], linked:false|true}).
// Runs against the REAL worker with Miniflare D1 (migrations auto-apply); per-test isolation.
// ─────────────────────────────────────────────────────────────────────────────

const BASE = "https://portal.test";
type Init = RequestInit & { cookie?: string; bearer?: string };

function call(path: string, init: Init = {}): Promise<Response> {
  const headers = new Headers(init.headers);
  if (init.cookie) headers.set("Cookie", init.cookie);
  if (init.bearer) headers.set("Authorization", `Bearer ${init.bearer}`);
  if (init.body && !headers.has("content-type")) headers.set("content-type", "application/json");
  return SELF.fetch(BASE + path, { ...init, headers });
}
const get = (cookie: string, path: string) => call(path, { cookie });
const post = (cookie: string, path: string, body?: unknown) =>
  call(path, { method: "POST", cookie, body: body === undefined ? undefined : JSON.stringify(body) });

async function seedJob(jobId: string): Promise<void> {
  await env.DB.prepare("INSERT INTO jobs (job_id, project_name, active, status, created_at) VALUES (?,?,1,'active',?)")
    .bind(jobId, `Project ${jobId}`, 1_700_000_000).run();
}
async function seedPersonnel(name: string, username: string | null, currentJob: string | null): Promise<number> {
  await env.DB.prepare("INSERT INTO personnel (name, username, current_job, active) VALUES (?,?,?,1)")
    .bind(name, username, currentJob).run();
  return (await env.DB.prepare("SELECT id FROM personnel WHERE name=? ORDER BY id DESC LIMIT 1").bind(name).first<{ id: number }>())!.id;
}
async function seedSubmission(jobId: string, formCode: string, workDate: string): Promise<void> {
  await env.DB.prepare(
    "INSERT INTO submissions (submission_uuid, job_id, form_code, work_date, payload_json) VALUES (?,?,?,?,?)",
  ).bind(`sub-${jobId}-${formCode}-${workDate}-${Math.random()}`, jobId, formCode, workDate, "{}").run();
}
async function createTemplate(cookie: string, title: string): Promise<number> {
  const res = await post(cookie, "/api/fieldops/checklist/inspection", { title });
  expect(res.status, await res.clone().text()).toBe(201);
  return ((await res.json()) as { id: number }).id;
}
async function addItem(cookie: string, tplId: number, item: Record<string, unknown>): Promise<void> {
  const res = await post(cookie, `/api/fieldops/checklist/inspection/${tplId}/item`, item);
  expect(res.status, await res.clone().text()).toBe(201);
}
async function assign(cookie: string, body: Record<string, unknown>): Promise<void> {
  const res = await post(cookie, "/api/fieldops/checklist/assign", body);
  expect(res.status, await res.clone().text()).toBe(201);
}

interface ItemState { id: number; item_type: string; label: string | null; form_code: string | null; status: string; completed_by: string | null }
interface AssignedResp {
  inspections: {
    instance: { id: number; job_id: string | null; project_name: string | null; instance_date: string | null; status: string; template_title: string | null; created_at: number };
    items: ItemState[];
  }[];
  linked: boolean;
}
async function assigned(cookie: string): Promise<AssignedResp> {
  const res = await get(cookie, "/api/fieldops/checklist/assigned");
  expect(res.status, await res.clone().text()).toBe(200);
  return (await res.json()) as AssignedResp;
}

const DUE = "2026-07-10";
let admin: string;
let sub: string;
let subId: number;

beforeEach(async () => {
  await env.DB.batch([
    env.DB.prepare("DELETE FROM checklist_item_states"),
    env.DB.prepare("DELETE FROM checklist_instances"),
    env.DB.prepare("DELETE FROM checklist_items WHERE template_id IN (SELECT id FROM checklist_templates WHERE kind IN ('job_override','generic_inspection'))"),
    env.DB.prepare("DELETE FROM checklist_templates WHERE kind IN ('job_override','generic_inspection')"),
    env.DB.prepare("DELETE FROM submissions"),
    env.DB.prepare("DELETE FROM personnel"),
    env.DB.prepare("DELETE FROM users"),
    env.DB.prepare("DELETE FROM jobs"),
    env.DB.prepare("DELETE FROM audit_log"),
  ]);
  await provision("admin.one", "password123", "admin");
  await provision("sub.sam", "password123", "submitter");
  admin = await login("admin.one", "password123");
  sub = await login("sub.sam", "password123");
  await seedJob("JOB-A");
  await seedJob("JOB-B");
  subId = await seedPersonnel("Sam Sub", "sub.sam", "JOB-A");
});

describe("assigned — multi-instance alignment (the indexed read-back)", () => {
  it("returns each instance with ITS OWN items when several assignments coexist", async () => {
    const t1 = await createTemplate(admin, "Fall protection");
    await addItem(admin, t1, { item_type: "manual_attest", label: "Harness checked" });
    const t2 = await createTemplate(admin, "Scaffold audit");
    await addItem(admin, t2, { item_type: "manual_attest", label: "Base plates level" });
    await addItem(admin, t2, { item_type: "manual_attest", label: "Guardrails on" });
    const t3 = await createTemplate(admin, "Site walk");
    await addItem(admin, t3, { item_type: "manual_attest", label: "Perimeter clear" });

    // Mixed placements: one dated+jobbed, one job-less/date-less, one on a different job.
    await assign(admin, { template_id: t1, assignee_personnel_id: subId, job_id: "JOB-A", due_date: DUE });
    await assign(admin, { template_id: t2, assignee_personnel_id: subId });
    await assign(admin, { template_id: t3, assignee_personnel_id: subId, job_id: "JOB-B", due_date: DUE });

    const resp = await assigned(sub);
    expect(resp.linked).toBe(true);
    expect(resp.inspections.length).toBe(3);
    const byTitle = new Map(resp.inspections.map((i) => [i.instance.template_title, i]));
    expect([...byTitle.keys()].sort()).toEqual(["Fall protection", "Scaffold audit", "Site walk"]);
    // Alignment: each instance carries exactly its own snapshot items.
    expect(byTitle.get("Fall protection")!.items.map((i) => i.label)).toEqual(["Harness checked"]);
    expect(byTitle.get("Scaffold audit")!.items.map((i) => i.label)).toEqual(["Base plates level", "Guardrails on"]);
    expect(byTitle.get("Site walk")!.items.map((i) => i.label)).toEqual(["Perimeter clear"]);
    // The job-less instance still returned (no reconcile legs, reads intact).
    expect(byTitle.get("Scaffold audit")!.instance.job_id).toBeNull();
    expect(byTitle.get("Scaffold audit")!.instance.instance_date).toBeNull();
  });

  it("reconciles BEFORE reading: a matching submission auto-closes the item AND the SAME response carries the fresh 'complete' status — without touching a sibling instance", async () => {
    const t1 = await createTemplate(admin, "JHA check");
    await addItem(admin, t1, { item_type: "form_linked", label: "File the JHA", form_code: "jha" });
    const t2 = await createTemplate(admin, "Other job JHA");
    await addItem(admin, t2, { item_type: "form_linked", label: "File the JHA", form_code: "jha" });
    await assign(admin, { template_id: t1, assignee_personnel_id: subId, job_id: "JOB-A", due_date: DUE });
    await assign(admin, { template_id: t2, assignee_personnel_id: subId, job_id: "JOB-B", due_date: DUE });

    // Both open before any submission.
    let resp = await assigned(sub);
    for (const insp of resp.inspections) {
      expect(insp.instance.status).toBe("open");
      expect(insp.items[0].status).toBe("open");
    }

    // A JOB-A jha filing ON OR BEFORE the due date (inspection semantics) closes ONLY t1's item.
    await seedSubmission("JOB-A", "jha-v2", "2026-07-08");
    resp = await assigned(sub);
    const byTitle = new Map(resp.inspections.map((i) => [i.instance.template_title, i]));
    const closed = byTitle.get("JHA check")!;
    expect(closed.items[0].status).toBe("done");
    expect(closed.items[0].completed_by).toBe("(auto)");
    // Fresh post-reconcile instance status in the SAME response (the 2i+1 read leg).
    expect(closed.instance.status).toBe("complete");
    // The sibling on JOB-B is untouched by JOB-A's reconcile legs.
    const open = byTitle.get("Other job JHA")!;
    expect(open.items[0].status).toBe("open");
    expect(open.instance.status).toBe("open");
  });

  it("is idempotent: a second read after reconcile returns the same closed state (no duplicate work)", async () => {
    const t = await createTemplate(admin, "JHA check");
    await addItem(admin, t, { item_type: "form_linked", label: "File the JHA", form_code: "jha" });
    await assign(admin, { template_id: t, assignee_personnel_id: subId, job_id: "JOB-A", due_date: DUE });
    await seedSubmission("JOB-A", "jha", DUE);
    const first = await assigned(sub);
    const second = await assigned(sub);
    expect(second).toEqual(first);
    expect(second.inspections[0].instance.status).toBe("complete");
  });
});

describe("assigned — linked/empty contracts unchanged", () => {
  it("an account with no linked personnel row → { inspections: [], linked: false }", async () => {
    await provision("sub.solo", "password123", "submitter");
    const solo = await login("sub.solo", "password123");
    expect(await assigned(solo)).toEqual({ inspections: [], linked: false });
  });

  it("a linked person with no assignments → { inspections: [], linked: true }", async () => {
    expect(await assigned(sub)).toEqual({ inspections: [], linked: true });
  });
});
