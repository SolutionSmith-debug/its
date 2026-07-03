import { env } from "cloudflare:test";
import { describe, it, expect, beforeEach } from "vitest";
import { call, provision, login, post, seedJob, seedPersonnel } from "./helpers";

// ─────────────────────────────────────────────────────────────────────────────
// Assigned-Tasks tab (P4 field-ops feature) S1 — the auth-boundary change:
//   1. GET /api/fieldops/tasks/mine (cap.tasks.own) — a person's own assigned tasks,
//      resolved via the personnel↔account link (personnel.username == users.username);
//      an unlinked session sees an empty list, not an error.
//   2. The re-gated task write routes — POST /job/:id/task + POST /task/:id/assign now
//      accept cap.jobtracker.manage OR cap.tasks.assign (migration 0025), with a
//      SUBCONTRACTOR-TARGET GUARD: a cap.tasks.assign-only actor (a manager, no
//      cap.jobtracker.manage) may only target a personnel whose linked account role is
//      'submitter'. An admin (holds cap.jobtracker.manage) is unrestricted.
// Runs against the REAL worker with Miniflare D1 (migrations incl. 0025 auto-apply).
// ─────────────────────────────────────────────────────────────────────────────

async function seedTask(jobId: string, personnelId: number | null, description: string, createdAt: number): Promise<void> {
  await env.DB.prepare("INSERT INTO task_assignments (job_id, personnel_id, description, status, created_at) VALUES (?,?,?,'open',?)")
    .bind(jobId, personnelId, description, createdAt).run();
}

let admin: string, manager: string, submitter: string;
let pSub: number, pMgr: number, pAdmin: number, pUnlinked: number;

beforeEach(async () => {
  await env.DB.batch([
    env.DB.prepare("DELETE FROM task_assignments"),
    env.DB.prepare("DELETE FROM personnel"),
    env.DB.prepare("DELETE FROM users"),
    env.DB.prepare("DELETE FROM jobs"),
    env.DB.prepare("DELETE FROM audit_log"),
  ]);
  await provision("admin.one", "password123", "admin");
  await provision("manager.mo", "password123", "manager");
  await provision("sub.sam", "password123", "submitter");
  admin = await login("admin.one", "password123");
  manager = await login("manager.mo", "password123");
  submitter = await login("sub.sam", "password123");

  await seedJob("JOB-A");
  await seedJob("JOB-B");

  // Personnel linked to accounts of each role, + one unlinked.
  pSub = await seedPersonnel("Sam Sub", "sub.sam"); // submitter-linked → valid manager target
  pMgr = await seedPersonnel("Mo Manager", "manager.mo"); // manager-linked → invalid manager target
  pAdmin = await seedPersonnel("Ann Admin", "admin.one"); // admin-linked → invalid manager target
  pUnlinked = await seedPersonnel("Uma Unlinked", null); // unlinked → invalid manager target
});

// ── A. GET /api/fieldops/tasks/mine ────────────────────────────────────────────────────
describe("GET /api/fieldops/tasks/mine (cap.tasks.own)", () => {
  it("a linked person sees ONLY their own tasks, across jobs, with project_name", async () => {
    // Two tasks for Sam (across JOB-A + JOB-B) + one for someone else (must not appear).
    await seedTask("JOB-A", pSub, "Sam task A", 100);
    await seedTask("JOB-B", pSub, "Sam task B", 200);
    await seedTask("JOB-A", pMgr, "Not Sam's", 300);

    const res = await call("/api/fieldops/tasks/mine", { cookie: submitter });
    expect(res.status, await res.clone().text()).toBe(200);
    const body = (await res.json()) as { tasks: { id: number; job_id: string; project_name: string | null; description: string; status: string; created_at: number }[] };
    expect(body.tasks).toHaveLength(2);
    const descs = body.tasks.map((t) => t.description).sort();
    expect(descs).toEqual(["Sam task A", "Sam task B"]);
    // project_name resolved via the LEFT JOIN to jobs.
    const byDesc = new Map(body.tasks.map((t) => [t.description, t]));
    expect(byDesc.get("Sam task A")!.project_name).toBe("Project JOB-A");
    expect(byDesc.get("Sam task B")!.project_name).toBe("Project JOB-B");
    expect(byDesc.get("Sam task A")!.job_id).toBe("JOB-A");
  });

  it("a session with NO linked personnel → empty list (200, not an error)", async () => {
    // admin.one IS linked to pAdmin here; make an account with no personnel row.
    await provision("lonely.lou", "password123", "submitter");
    const lou = await login("lonely.lou", "password123");
    await seedTask("JOB-A", pSub, "Sam task", 100); // tasks exist, but none for Lou
    const res = await call("/api/fieldops/tasks/mine", { cookie: lou });
    expect(res.status).toBe(200);
    expect(((await res.json()) as { tasks: unknown[] }).tasks).toHaveLength(0);
  });

  it("anon → 401", async () => {
    expect((await call("/api/fieldops/tasks/mine")).status).toBe(401);
  });

  it("a session WITHOUT cap.tasks.own → 403 (gate fail-closed)", async () => {
    // Strip the grant from submitter to prove the gate; restore it so sibling tests are unaffected.
    await env.DB.prepare("DELETE FROM role_capabilities WHERE role_key='submitter' AND capability_key='cap.tasks.own'").run();
    const res = await call("/api/fieldops/tasks/mine", { cookie: submitter });
    expect(res.status).toBe(403);
    await env.DB.prepare("INSERT OR IGNORE INTO role_capabilities (role_key, capability_key) VALUES ('submitter','cap.tasks.own')").run();
  });
});

// ── B. Re-gated task CREATE (POST /job/:id/task) ────────────────────────────────────────
describe("POST /api/fieldops/job/:job_id/task — re-gate + subcontractor-target guard", () => {
  it("a manager (cap.tasks.assign) CAN create an unassigned task → 201", async () => {
    const res = await post(manager, "/api/fieldops/job/JOB-A/task", { description: "Dig footings" });
    expect(res.status, await res.clone().text()).toBe(201);
  });

  it("a manager CAN create a task targeting a SUBMITTER-linked personnel → 201", async () => {
    const res = await post(manager, "/api/fieldops/job/JOB-A/task", { description: "Frame wall", personnel_id: pSub });
    expect(res.status, await res.clone().text()).toBe(201);
  });

  it("a manager targeting a MANAGER-linked personnel → 403 forbidden_target (nothing created)", async () => {
    const res = await post(manager, "/api/fieldops/job/JOB-A/task", { description: "Nope", personnel_id: pMgr });
    expect(res.status).toBe(403);
    expect(((await res.json()) as { error: string }).error).toBe("forbidden_target");
    expect((await env.DB.prepare("SELECT COUNT(*) n FROM task_assignments").first<{ n: number }>())!.n).toBe(0);
  });

  it("a manager targeting an ADMIN-linked personnel → 403 forbidden_target", async () => {
    const res = await post(manager, "/api/fieldops/job/JOB-A/task", { description: "Nope", personnel_id: pAdmin });
    expect(res.status).toBe(403);
    expect(((await res.json()) as { error: string }).error).toBe("forbidden_target");
  });

  it("a manager targeting an UNLINKED personnel → 403 forbidden_target", async () => {
    const res = await post(manager, "/api/fieldops/job/JOB-A/task", { description: "Nope", personnel_id: pUnlinked });
    expect(res.status).toBe(403);
    expect(((await res.json()) as { error: string }).error).toBe("forbidden_target");
  });

  it("an ADMIN is unrestricted — can target a manager-linked personnel → 201", async () => {
    const res = await post(admin, "/api/fieldops/job/JOB-A/task", { description: "Admin assigns mgr", personnel_id: pMgr });
    expect(res.status, await res.clone().text()).toBe(201);
  });

  it("a plain submitter still CANNOT create a task → 403", async () => {
    expect((await post(submitter, "/api/fieldops/job/JOB-A/task", { description: "x" })).status).toBe(403);
  });
});

// ── C. Re-gated task REASSIGN (POST /task/:id/assign) ───────────────────────────────────
describe("POST /api/fieldops/task/:id/assign — re-gate + subcontractor-target guard", () => {
  async function seedOpenTask(jobId: string): Promise<number> {
    await seedTask(jobId, null, "Assignable", 100);
    return (await env.DB.prepare("SELECT id FROM task_assignments ORDER BY id DESC LIMIT 1").first<{ id: number }>())!.id;
  }

  it("a manager CAN reassign a task to a SUBMITTER-linked personnel → 200", async () => {
    const id = await seedOpenTask("JOB-A");
    const res = await post(manager, `/api/fieldops/task/${id}/assign`, { personnel_id: pSub });
    expect(res.status, await res.clone().text()).toBe(200);
    expect((await env.DB.prepare("SELECT personnel_id FROM task_assignments WHERE id=?").bind(id).first<{ personnel_id: number }>())!.personnel_id).toBe(pSub);
  });

  it("a manager reassigning to a MANAGER-linked personnel → 403 forbidden_target (unchanged)", async () => {
    const id = await seedOpenTask("JOB-A");
    const res = await post(manager, `/api/fieldops/task/${id}/assign`, { personnel_id: pMgr });
    expect(res.status).toBe(403);
    expect((await env.DB.prepare("SELECT personnel_id FROM task_assignments WHERE id=?").bind(id).first<{ personnel_id: number | null }>())!.personnel_id).toBeNull();
  });

  it("a manager CAN unassign (personnel_id null) → 200 (no target to guard)", async () => {
    const id = await seedOpenTask("JOB-A");
    // place then clear
    expect((await post(manager, `/api/fieldops/task/${id}/assign`, { personnel_id: pSub })).status).toBe(200);
    const res = await post(manager, `/api/fieldops/task/${id}/assign`, { personnel_id: null });
    expect(res.status).toBe(200);
    expect((await env.DB.prepare("SELECT personnel_id FROM task_assignments WHERE id=?").bind(id).first<{ personnel_id: number | null }>())!.personnel_id).toBeNull();
  });

  it("an ADMIN is unrestricted — can reassign to a manager-linked personnel → 200", async () => {
    const id = await seedOpenTask("JOB-A");
    const res = await post(admin, `/api/fieldops/task/${id}/assign`, { personnel_id: pMgr });
    expect(res.status, await res.clone().text()).toBe(200);
  });
});

// ── D. (W1) current-owner guard — a manager may only touch a task currently unassigned or submitter-held ──
describe("POST /api/fieldops/task/:id/assign — current-owner guard (W1)", () => {
  async function seedTaskOwnedBy(jobId: string, ownerPersonnelId: number | null): Promise<number> {
    await seedTask(jobId, ownerPersonnelId, "Owned", 100);
    return (await env.DB.prepare("SELECT id FROM task_assignments ORDER BY id DESC LIMIT 1").first<{ id: number }>())!.id;
  }

  it("a manager CANNOT reassign a task currently held by a MANAGER-linked personnel → 403 forbidden_task", async () => {
    const id = await seedTaskOwnedBy("JOB-A", pMgr);
    const res = await post(manager, `/api/fieldops/task/${id}/assign`, { personnel_id: pSub });
    expect(res.status).toBe(403);
    expect(((await res.json()) as { error: string }).error).toBe("forbidden_task");
    expect((await env.DB.prepare("SELECT personnel_id FROM task_assignments WHERE id=?").bind(id).first<{ personnel_id: number }>())!.personnel_id).toBe(pMgr);
  });

  it("a manager CANNOT unassign a task currently held by an ADMIN-linked personnel → 403 forbidden_task", async () => {
    const id = await seedTaskOwnedBy("JOB-A", pAdmin);
    const res = await post(manager, `/api/fieldops/task/${id}/assign`, { personnel_id: null });
    expect(res.status).toBe(403);
    expect(((await res.json()) as { error: string }).error).toBe("forbidden_task");
  });

  it("a manager CAN unassign a task currently held by a SUBMITTER → 200", async () => {
    const id = await seedTaskOwnedBy("JOB-A", pSub);
    const res = await post(manager, `/api/fieldops/task/${id}/assign`, { personnel_id: null });
    expect(res.status, await res.clone().text()).toBe(200);
  });

  it("an ADMIN is unrestricted — can reassign a manager-owned task → 200", async () => {
    const id = await seedTaskOwnedBy("JOB-A", pMgr);
    const res = await post(admin, `/api/fieldops/task/${id}/assign`, { personnel_id: pSub });
    expect(res.status, await res.clone().text()).toBe(200);
  });
});
