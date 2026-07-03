import { env } from "cloudflare:test";
import { describe, it, expect, beforeEach } from "vitest";
import { provision, login, get, post, seedJob, seedPersonnel as seedPersonnelRow } from "./helpers";

// ─────────────────────────────────────────────────────────────────────────────
// R5 — assignment LIFECYCLE: the admin outstanding-assignments list + cancel.
//   - GET /api/fieldops/checklist/instances — inspection-kind ONLY (daily instances are
//     auto-generated noise), with assignee/job/title context + the done/total item aggregate;
//     ?status=open (default) | complete | all; bounded (LIMIT 300) newest-first.
//   - POST /api/fieldops/checklist/instance/:id/cancel — hard-deletes the instance + its item
//     states ATOMICALLY with a changes()-gated audit (the W4 pattern); 404 on unknown ids AND on
//     daily instances (wrong-kind is indistinguishable from absent); a cancelled assignment
//     disappears from the assignee's /checklist/assigned on their next load.
// Both routes are cap.checklist.manage (admin; submitter + manager 403). Runs against the REAL
// worker with Miniflare D1 (migrations auto-apply) — the same harness as
// fieldops-inspection-library.test.ts.
// ─────────────────────────────────────────────────────────────────────────────

const seedPersonnel = (name: string, username: string | null, currentJob: string | null, active = 1): Promise<number> =>
  seedPersonnelRow(name, username, currentJob, { active });
async function createTemplate(cookie: string, title: string): Promise<number> {
  const res = await post(cookie, "/api/fieldops/checklist/inspection", { title });
  expect(res.status, await res.clone().text()).toBe(201);
  return ((await res.json()) as { id: number }).id;
}
async function addItem(cookie: string, tplId: number, item: Record<string, unknown>): Promise<number> {
  const res = await post(cookie, `/api/fieldops/checklist/inspection/${tplId}/item`, item);
  expect(res.status, await res.clone().text()).toBe(201);
  return ((await res.json()) as { id: number }).id;
}
async function assign(cookie: string, body: Record<string, unknown>): Promise<number> {
  const res = await post(cookie, "/api/fieldops/checklist/assign", body);
  expect(res.status, await res.clone().text()).toBe(201);
  return ((await res.json()) as { instance_id: number }).instance_id;
}

interface InstanceRow {
  id: number;
  template_title: string | null;
  assignee_personnel_id: number | null;
  assignee_name: string | null;
  job_id: string | null;
  project_name: string | null;
  instance_date: string | null;
  status: string;
  created_at: number;
  items_total: number;
  items_done: number;
}
async function listInstances(cookie: string, status?: string): Promise<InstanceRow[]> {
  const res = await get(cookie, `/api/fieldops/checklist/instances${status ? `?status=${status}` : ""}`);
  expect(res.status, await res.clone().text()).toBe(200);
  return ((await res.json()) as { instances: InstanceRow[] }).instances;
}

interface AssignedResp {
  inspections: { instance: { id: number; status: string }; items: { id: number; status: string }[] }[];
}
async function assigned(cookie: string): Promise<AssignedResp> {
  const res = await get(cookie, "/api/fieldops/checklist/assigned");
  expect(res.status, await res.clone().text()).toBe(200);
  return (await res.json()) as AssignedResp;
}

// A daily-kind instance seeded straight into D1 (with one item state) — the noise the admin list
// must EXCLUDE and the cancel route must REFUSE.
async function seedDailyInstance(personnelId: number, jobId: string, date: string): Promise<number> {
  await env.DB.prepare(
    "INSERT INTO checklist_instances (kind, job_id, assignee_personnel_id, instance_date, status) VALUES ('daily', ?1, ?2, ?3, 'open')",
  ).bind(jobId, personnelId, date).run();
  const id = (await env.DB.prepare(
    "SELECT id FROM checklist_instances WHERE kind='daily' AND assignee_personnel_id=?1 AND instance_date=?2",
  ).bind(personnelId, date).first<{ id: number }>())!.id;
  await env.DB.prepare(
    "INSERT INTO checklist_item_states (instance_id, item_type, label, status) VALUES (?1, 'manual_attest', 'Walk the site', 'open')",
  ).bind(id).run();
  return id;
}

let admin: string, manager: string, submitter: string;
let subPersonId: number;

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
  await provision("mgr.mo", "password123", "manager");
  await provision("sub.sam", "password123", "submitter");
  admin = await login("admin.one", "password123");
  manager = await login("mgr.mo", "password123");
  submitter = await login("sub.sam", "password123");
  await seedJob("JOB-A");
  subPersonId = await seedPersonnel("Sam Sub", "sub.sam", "JOB-A");
});

describe("R5 lifecycle — capability gating", () => {
  it("both routes are cap.checklist.manage: admin passes, submitter + manager are 403", async () => {
    expect((await get(admin, "/api/fieldops/checklist/instances")).status).toBe(200);
    expect((await get(submitter, "/api/fieldops/checklist/instances")).status).toBe(403);
    expect((await get(manager, "/api/fieldops/checklist/instances")).status).toBe(403);
    expect((await post(submitter, "/api/fieldops/checklist/instance/1/cancel")).status).toBe(403);
    expect((await post(manager, "/api/fieldops/checklist/instance/1/cancel")).status).toBe(403);
  });
});

describe("R5 GET /checklist/instances — the admin outstanding-assignments list", () => {
  it("returns inspection-kind ONLY, with title/assignee/job context + the done/total aggregate", async () => {
    const t = await createTemplate(admin, "Fall protection");
    await addItem(admin, t, { item_type: "manual_attest", label: "Harness checked" });
    await addItem(admin, t, { item_type: "count", label: "Anchors", target_count: 2 });
    await assign(admin, { template_id: t, assignee_personnel_id: subPersonId, job_id: "JOB-A", due_date: "2026-07-10" });
    // A daily instance for the same person must NOT appear (auto-generated noise, not an assignment).
    await seedDailyInstance(subPersonId, "JOB-A", "2026-07-01");

    const rows = await listInstances(admin);
    expect(rows).toHaveLength(1);
    const r = rows[0];
    expect(r.template_title).toBe("Fall protection");
    expect(r.assignee_personnel_id).toBe(subPersonId);
    expect(r.assignee_name).toBe("Sam Sub");
    expect(r.job_id).toBe("JOB-A");
    expect(r.project_name).toBe("Project JOB-A");
    expect(r.instance_date).toBe("2026-07-10");
    expect(r.status).toBe("open");
    expect(r.items_total).toBe(2);
    expect(r.items_done).toBe(0);

    // The assignee completes one item → the aggregate reflects it on the next admin read.
    const mine = await assigned(submitter);
    const manual = mine.inspections[0].items[0];
    expect((await post(submitter, `/api/fieldops/checklist/item-state/${manual.id}/complete`, {})).status).toBe(200);
    const after = await listInstances(admin);
    expect(after[0].items_done).toBe(1);
    expect(after[0].items_total).toBe(2);
    expect(after[0].status).toBe("open"); // one item still open
  });

  it("?status filters: open is the default, complete shows finished ones, all shows both; bad value 400", async () => {
    const t = await createTemplate(admin, "One-check");
    await addItem(admin, t, { item_type: "manual_attest", label: "Only item" });
    // Instance 1: completed by the assignee. Instance 2 (no job/date → distinct): left open.
    await assign(admin, { template_id: t, assignee_personnel_id: subPersonId, job_id: "JOB-A", due_date: "2026-07-10" });
    const mine = await assigned(submitter);
    const stateId = mine.inspections[0].items[0].id;
    expect((await post(submitter, `/api/fieldops/checklist/item-state/${stateId}/complete`, {})).status).toBe(200);
    await assign(admin, { template_id: t, assignee_personnel_id: subPersonId });

    const open = await listInstances(admin); // default = open
    expect(open).toHaveLength(1);
    expect(open[0].status).toBe("open");
    const openExplicit = await listInstances(admin, "open");
    expect(openExplicit).toHaveLength(1);
    const complete = await listInstances(admin, "complete");
    expect(complete).toHaveLength(1);
    expect(complete[0].status).toBe("complete");
    expect(complete[0].items_done).toBe(1);
    const all = await listInstances(admin, "all");
    expect(all).toHaveLength(2);
    // Unknown filter values are refused, never silently coerced.
    expect((await get(admin, "/api/fieldops/checklist/instances?status=bogus")).status).toBe(400);
  });

  it("is bounded at 300 rows, newest (created_at) first", async () => {
    // Seed 305 inspection instances straight into D1 with ascending created_at.
    const stmts = [];
    for (let i = 0; i < 305; i++) {
      stmts.push(
        env.DB.prepare(
          "INSERT INTO checklist_instances (kind, job_id, assignee_personnel_id, instance_date, status, template_title, created_at) VALUES ('inspection', NULL, ?1, NULL, 'open', ?2, ?3)",
        ).bind(subPersonId, `Bulk ${i}`, 1_700_000_000 + i),
      );
    }
    await env.DB.batch(stmts);
    const rows = await listInstances(admin, "all");
    expect(rows).toHaveLength(300);
    expect(rows[0].template_title).toBe("Bulk 304"); // newest first
    expect(rows[0].created_at).toBe(1_700_000_000 + 304);
    expect(rows[299].created_at).toBeGreaterThan(1_700_000_000); // the 5 OLDEST fell off the end
  });
});

describe("R5 POST /checklist/instance/:id/cancel — revoke an assignment", () => {
  it("deletes the instance + its item states atomically, audits with full context, and the assignee's tab drops it", async () => {
    const t = await createTemplate(admin, "Fall protection");
    await addItem(admin, t, { item_type: "manual_attest", label: "Harness checked" });
    const instId = await assign(admin, { template_id: t, assignee_personnel_id: subPersonId, job_id: "JOB-A", due_date: "2026-07-10" });

    expect((await assigned(submitter)).inspections).toHaveLength(1);

    const res = await post(admin, `/api/fieldops/checklist/instance/${instId}/cancel`);
    expect(res.status, await res.clone().text()).toBe(200);

    // Instance + states are GONE (hard delete — no soft 'cancelled' state to leak into the dedupe key).
    expect(await env.DB.prepare("SELECT id FROM checklist_instances WHERE id=?").bind(instId).first()).toBeNull();
    expect((await env.DB.prepare("SELECT COUNT(*) n FROM checklist_item_states WHERE instance_id=?").bind(instId).first<{ n: number }>())!.n).toBe(0);

    // The audit row exists and names actor/instance/template/assignee (the forensic record survives the delete).
    const audit = await env.DB.prepare("SELECT actor_username, target_username, detail FROM audit_log WHERE action='checklist_inspection_cancel'").first<{ actor_username: string; target_username: string; detail: string }>();
    expect(audit).not.toBeNull();
    expect(audit!.actor_username).toBe("admin.one");
    expect(audit!.target_username).toBe(String(subPersonId));
    const detail = JSON.parse(audit!.detail) as Record<string, unknown>;
    expect(detail.instance_id).toBe(instId);
    expect(detail.template_title).toBe("Fall protection");
    expect(detail.assignee_name).toBe("Sam Sub");
    expect(detail.job_id).toBe("JOB-A");

    // The assignee's /checklist/assigned no longer returns it (it queries checklist_instances).
    expect((await assigned(submitter)).inspections).toHaveLength(0);

    // Cancelling a job+date assignment frees the UNIQUE key: the same (job, date) can be re-assigned.
    const again = await post(admin, "/api/fieldops/checklist/assign", { template_id: t, assignee_personnel_id: subPersonId, job_id: "JOB-A", due_date: "2026-07-10" });
    expect(again.status, await again.clone().text()).toBe(201);
  });

  it("404s on an unknown id, 400s on a non-numeric id, and a double-cancel audits only once", async () => {
    const t = await createTemplate(admin, "One-check");
    await addItem(admin, t, { item_type: "manual_attest", label: "x" });
    const instId = await assign(admin, { template_id: t, assignee_personnel_id: subPersonId });

    expect((await post(admin, "/api/fieldops/checklist/instance/999999/cancel")).status).toBe(404);
    expect((await post(admin, "/api/fieldops/checklist/instance/abc/cancel")).status).toBe(400);

    expect((await post(admin, `/api/fieldops/checklist/instance/${instId}/cancel`)).status).toBe(200);
    expect((await post(admin, `/api/fieldops/checklist/instance/${instId}/cancel`)).status).toBe(404);
    expect((await env.DB.prepare("SELECT COUNT(*) n FROM audit_log WHERE action='checklist_inspection_cancel'").first<{ n: number }>())!.n).toBe(1);
  });

  it("REFUSES a daily instance (404 — indistinguishable from absent) and leaves it untouched", async () => {
    const dailyId = await seedDailyInstance(subPersonId, "JOB-A", "2026-07-01");
    expect((await post(admin, `/api/fieldops/checklist/instance/${dailyId}/cancel`)).status).toBe(404);
    // The daily instance AND its item state survive.
    expect(await env.DB.prepare("SELECT id FROM checklist_instances WHERE id=?").bind(dailyId).first()).not.toBeNull();
    expect((await env.DB.prepare("SELECT COUNT(*) n FROM checklist_item_states WHERE instance_id=?").bind(dailyId).first<{ n: number }>())!.n).toBe(1);
    // And nothing was audited.
    expect((await env.DB.prepare("SELECT COUNT(*) n FROM audit_log WHERE action='checklist_inspection_cancel'").first<{ n: number }>())!.n).toBe(0);
  });
});
