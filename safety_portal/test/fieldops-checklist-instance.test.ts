import { env, SELF } from "cloudflare:test";
import { describe, it, expect, beforeEach } from "vitest";

// ─────────────────────────────────────────────────────────────────────────────
// Assigned-Tasks tab (P4 field-ops feature) S3 — daily-instance GENERATION (Worker-on-read) +
// tab SURFACING (GET /checklist/mine) + manual_attest COMPLETION (+ un-complete).
//   - Generation is MANAGER-ONLY + placed-only (personnel.current_job set) + idempotent on the
//     checklist_instances UNIQUE key; the effective (default⊕override merged) items are snapshotted
//     into checklist_item_states on FIRST creation only.
//   - Completion is cap.tasks.own, ownership-scoped (only the actor's OWN daily instance), and in S3
//     gated to item_type='manual_attest' (form_linked/count/inspection → 400).
//   - Instance flips to 'complete' when every item is done, back to 'open' otherwise.
// Runs against the REAL worker with Miniflare D1 (migrations incl. 0026 auto-apply).
// ─────────────────────────────────────────────────────────────────────────────

const BASE = "https://portal.test";
const ADMIN_BEARER = "test-admin-token";
type Init = RequestInit & { cookie?: string; bearer?: string };

function call(path: string, init: Init = {}): Promise<Response> {
  const headers = new Headers(init.headers);
  if (init.cookie) headers.set("Cookie", init.cookie);
  if (init.bearer) headers.set("Authorization", `Bearer ${init.bearer}`);
  if (init.body && !headers.has("content-type")) headers.set("content-type", "application/json");
  return SELF.fetch(BASE + path, { ...init, headers });
}
function cookieFrom(res: Response): string {
  return (res.headers.get("set-cookie") ?? "").split(";")[0];
}
async function provision(username: string, password: string, role: "submitter" | "manager" | "admin"): Promise<void> {
  const res = await call("/api/internal/admin/users", { method: "POST", bearer: ADMIN_BEARER, body: JSON.stringify({ username, password, role }) });
  expect(res.status, await res.clone().text()).toBe(201);
}
async function login(username: string, password: string): Promise<string> {
  const res = await call("/api/login", { method: "POST", body: JSON.stringify({ username, password }) });
  expect(res.status, await res.clone().text()).toBe(200);
  return cookieFrom(res);
}
const get = (cookie: string, path: string) => call(path, { cookie });
const post = (cookie: string, path: string, body?: unknown) =>
  call(path, { method: "POST", cookie, body: body === undefined ? undefined : JSON.stringify(body) });

async function seedJob(jobId: string): Promise<void> {
  await env.DB.prepare("INSERT INTO jobs (job_id, project_name, active, status, created_at) VALUES (?,?,1,'active',?)")
    .bind(jobId, `Project ${jobId}`, 1_700_000_000).run();
}
// A roster person linked to `username`, optionally placed on `currentJob` (personnel.current_job).
async function seedPersonnel(name: string, username: string | null, currentJob: string | null): Promise<number> {
  await env.DB.prepare("INSERT INTO personnel (name, username, current_job, active) VALUES (?,?,?,1)")
    .bind(name, username, currentJob).run();
  return (await env.DB.prepare("SELECT id FROM personnel WHERE name=? ORDER BY id DESC LIMIT 1").bind(name).first<{ id: number }>())!.id;
}

interface ItemState { id: number; item_type: string; label: string | null; status: string; source_item_id: number | null; }
interface MineResp { instance: { id: number; job_id: string; instance_date: string; status: string } | null; items: ItemState[]; }
async function mine(cookie: string): Promise<MineResp> {
  const res = await get(cookie, "/api/fieldops/checklist/mine");
  expect(res.status, await res.clone().text()).toBe(200);
  return (await res.json()) as MineResp;
}

let manager: string, manager2: string, submitter: string;
let mgrPersonId: number;

beforeEach(async () => {
  await env.DB.batch([
    env.DB.prepare("DELETE FROM checklist_item_states"),
    env.DB.prepare("DELETE FROM checklist_instances"),
    env.DB.prepare("DELETE FROM checklist_items WHERE template_id IN (SELECT id FROM checklist_templates WHERE kind='job_override')"),
    env.DB.prepare("DELETE FROM checklist_templates WHERE kind='job_override'"),
    env.DB.prepare("DELETE FROM task_assignments"),
    env.DB.prepare("DELETE FROM personnel"),
    env.DB.prepare("DELETE FROM users"),
    env.DB.prepare("DELETE FROM jobs"),
    env.DB.prepare("DELETE FROM audit_log"),
  ]);
  await provision("mgr.mo", "password123", "manager");
  await provision("mgr.two", "password123", "manager");
  await provision("sub.sam", "password123", "submitter");
  manager = await login("mgr.mo", "password123");
  manager2 = await login("mgr.two", "password123");
  submitter = await login("sub.sam", "password123");
  await seedJob("JOB-A");
  await seedJob("JOB-B");
  // mgr.mo is a placed manager on JOB-A; mgr.two is placed on JOB-B; sub.sam is placed but a submitter.
  mgrPersonId = await seedPersonnel("Mo Manager", "mgr.mo", "JOB-A");
  await seedPersonnel("Two Manager", "mgr.two", "JOB-B");
  await seedPersonnel("Sam Sub", "sub.sam", "JOB-A");
});

describe("checklist S3 — generation (manager-only, placed-only, idempotent)", () => {
  it("a PLACED MANAGER gets a generated daily instance snapshotting the effective default items", async () => {
    const body = await mine(manager);
    expect(body.instance).not.toBeNull();
    expect(body.instance!.job_id).toBe("JOB-A");
    expect(body.instance!.status).toBe("open");
    expect(body.instance!.instance_date).toMatch(/^\d{4}-\d{2}-\d{2}$/);
    // Snapshot captured the migration-0026 effective default items (a form_linked + manual_attest set).
    expect(body.items.length).toBeGreaterThanOrEqual(2);
    expect(body.items.some((i) => i.item_type === "form_linked")).toBe(true);
    expect(body.items.some((i) => i.item_type === "manual_attest")).toBe(true);
    expect(body.items.every((i) => i.status === "open")).toBe(true);
  });

  it("the snapshot reflects the job's default⊕override MERGE (suppress a default + add a job item)", async () => {
    const dt = (await env.DB.prepare("SELECT id FROM checklist_templates WHERE kind='daily_default'").first<{ id: number }>())!.id;
    // Suppress the first default item for JOB-A and add a job-specific manual_attest item.
    const victim = (await env.DB.prepare("SELECT id, label FROM checklist_items WHERE template_id=? AND suppresses_default_item_id IS NULL ORDER BY seq LIMIT 1").bind(dt).first<{ id: number; label: string }>())!;
    expect((await post(manager, `/api/fieldops/checklist/job/JOB-A/item`, undefined)).status).toBeGreaterThanOrEqual(400); // manager can't author (cap.checklist.manage) — sanity
    // Author the override directly in D1 (S2 route is admin-only; we test the MERGE snapshot, not authoring).
    await env.DB.prepare("INSERT INTO checklist_templates (kind, job_id, active) VALUES ('job_override','JOB-A',1)").run();
    const ot = (await env.DB.prepare("SELECT id FROM checklist_templates WHERE kind='job_override' AND job_id='JOB-A'").first<{ id: number }>())!.id;
    await env.DB.prepare("INSERT INTO checklist_items (template_id, seq, item_type, label, suppresses_default_item_id) VALUES (?,0,'manual_attest','(suppressed)',?)").bind(ot, victim.id).run();
    await env.DB.prepare("INSERT INTO checklist_items (template_id, seq, item_type, label) VALUES (?, 999, 'manual_attest', 'Job-specific step')").bind(ot).run();

    const body = await mine(manager);
    const labels = body.items.map((i) => i.label);
    expect(labels).not.toContain(victim.label); // suppressed default not snapshotted
    expect(labels).toContain("Job-specific step"); // override addition snapshotted
  });

  it("is IDEMPOTENT — a second read the same day returns the SAME instance with NO duplicate states", async () => {
    const first = await mine(manager);
    const second = await mine(manager);
    expect(second.instance!.id).toBe(first.instance!.id);
    expect(second.items.length).toBe(first.items.length);
    const instCount = (await env.DB.prepare("SELECT COUNT(*) n FROM checklist_instances WHERE assignee_personnel_id=?").bind(mgrPersonId).first<{ n: number }>())!.n;
    expect(instCount).toBe(1);
    const stateCount = (await env.DB.prepare("SELECT COUNT(*) n FROM checklist_item_states WHERE instance_id=?").bind(first.instance!.id).first<{ n: number }>())!.n;
    expect(stateCount).toBe(first.items.length);
  });

  it("a SUBMITTER (even if placed) gets NO instance (null)", async () => {
    const body = await mine(submitter);
    expect(body.instance).toBeNull();
    expect(body.items).toEqual([]);
  });

  it("an UNPLACED manager (no current_job) gets NO instance (null)", async () => {
    await env.DB.prepare("UPDATE personnel SET current_job=NULL WHERE username='mgr.mo'").run();
    const body = await mine(manager);
    expect(body.instance).toBeNull();
  });

  it("a manager with NO linked personnel row gets NO instance (null)", async () => {
    await env.DB.prepare("DELETE FROM personnel WHERE username='mgr.mo'").run();
    const body = await mine(manager);
    expect(body.instance).toBeNull();
  });
});

describe("checklist S3 — completion (ownership-scoped, manual_attest-only, status recompute)", () => {
  async function manualItemId(cookie: string): Promise<number> {
    const body = await mine(cookie);
    return body.items.find((i) => i.item_type === "manual_attest")!.id;
  }

  it("completes a manual_attest item on the actor's OWN instance (+ un-completes)", async () => {
    const id = await manualItemId(manager);
    const done = await post(manager, `/api/fieldops/checklist/item-state/${id}/complete`, { note: "walked it" });
    expect(done.status, await done.clone().text()).toBe(200);
    let body = await mine(manager);
    expect(body.items.find((i) => i.id === id)!.status).toBe("done");
    // Un-complete toggles back to open.
    const undo = await post(manager, `/api/fieldops/checklist/item-state/${id}/uncomplete`);
    expect(undo.status).toBe(200);
    body = await mine(manager);
    expect(body.items.find((i) => i.id === id)!.status).toBe("open");
  });

  it("ANOTHER manager cannot complete an item on someone else's instance → 403", async () => {
    const id = await manualItemId(manager); // mgr.mo's item
    await mine(manager2); // generate mgr.two's own instance too (irrelevant target)
    const res = await post(manager2, `/api/fieldops/checklist/item-state/${id}/complete`);
    expect(res.status).toBe(403);
    // Unchanged.
    const body = await mine(manager);
    expect(body.items.find((i) => i.id === id)!.status).toBe("open");
  });

  it("a SUBMITTER (no ownership) cannot complete an item → 403", async () => {
    const id = await manualItemId(manager);
    const res = await post(submitter, `/api/fieldops/checklist/item-state/${id}/complete`);
    expect(res.status).toBe(403);
  });

  it("a manual complete on a form_linked item is REJECTED (auto-close only, 400)", async () => {
    // The seeded form_linked item closes via a matching SUBMISSION (S4 loop-closure), never a manual
    // action, so a direct /complete is refused. (S4 full loop-closure coverage lives in
    // fieldops-checklist-loop-closure.test.ts.)
    const body = await mine(manager);
    const formLinked = body.items.find((i) => i.item_type === "form_linked");
    expect(formLinked).toBeDefined();
    const res = await post(manager, `/api/fieldops/checklist/item-state/${formLinked!.id}/complete`);
    expect(res.status).toBe(400);
    expect(((await res.json()) as { error: string }).error).toBe("auto_close_only");
  });

  it("instance flips to 'complete' when ALL items done, back to 'open' when one is undone", async () => {
    // Reduce the effective default to a single manual_attest item so "all done" is one action.
    const dt = (await env.DB.prepare("SELECT id FROM checklist_templates WHERE kind='daily_default'").first<{ id: number }>())!.id;
    await env.DB.prepare("DELETE FROM checklist_items WHERE template_id=?").bind(dt).run();
    await env.DB.prepare("INSERT INTO checklist_items (template_id, seq, item_type, label) VALUES (?,10,'manual_attest','Only step')").bind(dt).run();

    const body = await mine(manager);
    expect(body.items.length).toBe(1);
    const id = body.items[0].id;
    expect(body.instance!.status).toBe("open");

    const done = await post(manager, `/api/fieldops/checklist/item-state/${id}/complete`);
    expect(((await done.json()) as { instance_status: string }).instance_status).toBe("complete");
    expect((await mine(manager)).instance!.status).toBe("complete");

    const undo = await post(manager, `/api/fieldops/checklist/item-state/${id}/uncomplete`);
    expect(((await undo.json()) as { instance_status: string }).instance_status).toBe("open");
    expect((await mine(manager)).instance!.status).toBe("open");
  });

  it("completing an unknown item-state id → 404", async () => {
    expect((await post(manager, `/api/fieldops/checklist/item-state/99999999/complete`)).status).toBe(404);
  });

  it("cap gate: an unauthenticated request to /checklist/mine → 401/403", async () => {
    const res = await get("", "/api/fieldops/checklist/mine");
    expect([401, 403]).toContain(res.status);
  });
});
