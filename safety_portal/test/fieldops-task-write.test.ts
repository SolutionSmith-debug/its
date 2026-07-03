import { env } from "cloudflare:test";
import { describe, it, expect, beforeEach } from "vitest";
import { call, provision, login, p as j, seedJob as seedJobRow } from "./helpers";

// ─────────────────────────────────────────────────────────────────────────────
// P2.3 Slice 3 — TASK WRITE (add / status). MIXED CAP: add = cap.jobtracker.manage (admin),
// status = cap.tasks.own (submitter + admin). The key assertion is the split — a submitter can
// change a task's status but cannot create one.
// ─────────────────────────────────────────────────────────────────────────────

const seedJob = (jobId: string, status: string): Promise<void> => seedJobRow(jobId, { status, projectName: `P ${jobId}` });
async function seedPersonnel(name: string): Promise<number> {
  await env.DB.prepare("INSERT INTO personnel (name, active) VALUES (?,1)").bind(name).run();
  return (await env.DB.prepare("SELECT id FROM personnel WHERE name=?").bind(name).first<{ id: number }>())!.id;
}
async function seedTask(jobId: string): Promise<number> {
  await env.DB.prepare("INSERT INTO task_assignments (job_id, description, status, created_at) VALUES (?,?,?,?)")
    .bind(jobId, "Dig", "open", 1_700_000_000).run();
  return (await env.DB.prepare("SELECT id FROM task_assignments WHERE job_id=? ORDER BY id DESC LIMIT 1").bind(jobId).first<{ id: number }>())!.id;
}
async function audits(action: string) {
  return ((await env.DB.prepare("SELECT * FROM audit_log WHERE action=?").bind(action).all()).results as any[]);
}

let admin: string, submitter: string;
beforeEach(async () => {
  await env.DB.batch([
    env.DB.prepare("DELETE FROM users"),
    env.DB.prepare("DELETE FROM audit_log"),
    env.DB.prepare("DELETE FROM task_assignments"),
    env.DB.prepare("DELETE FROM personnel"),
    env.DB.prepare("DELETE FROM jobs"),
  ]);
  await provision("admin.one", "password123", "admin");
  await provision("submitter.jim", "password123", "submitter");
  admin = await login("admin.one", "password123");
  submitter = await login("submitter.jim", "password123");
  await seedJob("JOB-A", "active");
});

describe("POST /api/fieldops/job/:job_id/task (add — cap.jobtracker.manage)", () => {
  it("gate: anon → 401, submitter → 403, admin → 201 (+ returns the new id, audits)", async () => {
    expect((await call("/api/fieldops/job/JOB-A/task", { method: "POST", body: JSON.stringify({ description: "x" }) })).status).toBe(401);
    expect((await j(submitter, "/api/fieldops/job/JOB-A/task", { description: "x" })).status).toBe(403);
    const res = await j(admin, "/api/fieldops/job/JOB-A/task", { description: "Dig footings" });
    expect(res.status).toBe(201);
    expect(typeof (await res.json() as any).id).toBe("number");
    expect(await audits("task_create")).toHaveLength(1);
  });

  it("job must exist + be active (404 unknown, 409 closed); personnel verified (422)", async () => {
    expect((await j(admin, "/api/fieldops/job/NOPE/task", { description: "x" })).status).toBe(404);
    await seedJob("JOB-Z", "closed");
    expect((await j(admin, "/api/fieldops/job/JOB-Z/task", { description: "x" })).status).toBe(409);
    expect((await j(admin, "/api/fieldops/job/JOB-A/task", { description: "x", personnel_id: 9999 })).status).toBe(422);
    const pid = await seedPersonnel("Alice Chen");
    expect((await j(admin, "/api/fieldops/job/JOB-A/task", { description: "x", personnel_id: pid })).status).toBe(201);
    // A retired (active=0) roster member is not assignable → 422.
    const gone = await seedPersonnel("Gone Gwen");
    await env.DB.prepare("UPDATE personnel SET active = 0 WHERE id = ?").bind(gone).run();
    expect((await j(admin, "/api/fieldops/job/JOB-A/task", { description: "x", personnel_id: gone })).status).toBe(422);
  });

  it("description bounds → 400", async () => {
    expect((await j(admin, "/api/fieldops/job/JOB-A/task", { description: "" })).status).toBe(400);
    expect((await j(admin, "/api/fieldops/job/JOB-A/task", { description: "x".repeat(257) })).status).toBe(400);
  });
});

describe("POST /api/fieldops/task/:id/status (status — cap.tasks.own)", () => {
  it("MIXED CAP: a submitter CANNOT add but CAN change status of THEIR OWN task (200)", async () => {
    // (R1) the ownership guard means an own-only actor must be the task's assignee — link the
    // submitter to a personnel row and assign the task to it (the cap-split intent is unchanged).
    await env.DB.prepare("INSERT INTO personnel (name, username, active) VALUES ('Jim Sub','submitter.jim',1)").run();
    const pid = (await env.DB.prepare("SELECT id FROM personnel WHERE username='submitter.jim'").first<{ id: number }>())!.id;
    const id = await seedTask("JOB-A");
    await env.DB.prepare("UPDATE task_assignments SET personnel_id=? WHERE id=?").bind(pid, id).run();
    // proven 403 on add (above); here the same submitter succeeds on status
    const res = await j(submitter, `/api/fieldops/task/${id}/status`, { status: "in_progress" });
    expect(res.status).toBe(200);
    const row = await env.DB.prepare("SELECT status FROM task_assignments WHERE id=?").bind(id).first<{ status: string }>();
    expect(row!.status).toBe("in_progress");
    expect(await audits("task_status")).toHaveLength(1);
  });

  it("admin can change status too; enum is validated", async () => {
    const id = await seedTask("JOB-A");
    expect((await j(admin, `/api/fieldops/task/${id}/status`, { status: "done" })).status).toBe(200);
    expect((await j(admin, `/api/fieldops/task/${id}/status`, { status: "bogus" })).status).toBe(400);
  });

  it("non-object body (null / array) → 400, not a 500", async () => {
    const id = await seedTask("JOB-A");
    expect((await j(admin, `/api/fieldops/task/${id}/status`, null)).status).toBe(400);
    expect((await j(admin, `/api/fieldops/task/${id}/status`, [1])).status).toBe(400);
  });

  it("unknown task → 404 (no audit); non-integer id → 400; anon → 401", async () => {
    const res = await j(admin, "/api/fieldops/task/999999/status", { status: "done" });
    expect(res.status).toBe(404);
    expect(await audits("task_status")).toHaveLength(0); // no-op writes no audit
    expect((await j(admin, "/api/fieldops/task/notanumber/status", { status: "done" })).status).toBe(400);
    expect((await call("/api/fieldops/task/1/status", { method: "POST", body: JSON.stringify({ status: "done" }) })).status).toBe(401);
  });
});

describe("POST /api/fieldops/task/:id/assign (reassign — cap.jobtracker.manage)", () => {
  it("gate: anon → 401, submitter (cap.tasks.own, NOT jobtracker.manage) → 403, admin → 200 (+ audits)", async () => {
    const id = await seedTask("JOB-A");
    const alice = await seedPersonnel("Alice Chen");
    expect((await call(`/api/fieldops/task/${id}/assign`, { method: "POST", body: JSON.stringify({ personnel_id: alice }) })).status).toBe(401);
    expect((await j(submitter, `/api/fieldops/task/${id}/assign`, { personnel_id: alice })).status).toBe(403);
    const res = await j(admin, `/api/fieldops/task/${id}/assign`, { personnel_id: alice });
    expect(res.status, await res.clone().text()).toBe(200);
    expect((await res.json() as any).personnel_id).toBe(alice);
    const row = await env.DB.prepare("SELECT personnel_id FROM task_assignments WHERE id=?").bind(id).first<{ personnel_id: number }>();
    expect(row!.personnel_id).toBe(alice);
    expect(await audits("task_assign")).toHaveLength(1);
  });

  it("assign → reassign to another person → unassign (null): each 200 + persisted, 3 audits", async () => {
    const id = await seedTask("JOB-A");
    const alice = await seedPersonnel("Alice Chen");
    const bob = await seedPersonnel("Bob Ray");
    const cur = async () => (await env.DB.prepare("SELECT personnel_id FROM task_assignments WHERE id=?").bind(id).first<{ personnel_id: number | null }>())!.personnel_id;

    expect((await j(admin, `/api/fieldops/task/${id}/assign`, { personnel_id: alice })).status).toBe(200);
    expect(await cur()).toBe(alice);

    expect((await j(admin, `/api/fieldops/task/${id}/assign`, { personnel_id: bob })).status).toBe(200);
    expect(await cur()).toBe(bob);

    const un = await j(admin, `/api/fieldops/task/${id}/assign`, { personnel_id: null });
    expect(un.status).toBe(200);
    expect((await un.json() as any).personnel_id).toBeNull();
    expect(await cur()).toBeNull();
    expect(await audits("task_assign")).toHaveLength(3);
  });

  it("unknown personnel → 422; absent task → 404 (no audit); bad/absent personnel_id + id → 400", async () => {
    const id = await seedTask("JOB-A");
    const res422 = await j(admin, `/api/fieldops/task/${id}/assign`, { personnel_id: 99999 });
    expect(res422.status).toBe(422);
    expect((await res422.json() as any).error).toBe("unknown_personnel");

    const alice = await seedPersonnel("Alice Chen");
    const res404 = await j(admin, `/api/fieldops/task/999999/assign`, { personnel_id: alice });
    expect(res404.status).toBe(404);
    expect(await audits("task_assign")).toHaveLength(0); // no-op writes no audit

    // present-but-wrong-type / missing key → 400 invalid_personnel_id
    const bad = await j(admin, `/api/fieldops/task/${id}/assign`, { personnel_id: "5" });
    expect(bad.status).toBe(400);
    expect((await bad.json() as any).error).toBe("invalid_personnel_id");
    expect((await j(admin, `/api/fieldops/task/${id}/assign`, {})).status).toBe(400);
    // non-integer id → 400
    expect((await j(admin, "/api/fieldops/task/notanumber/assign", { personnel_id: alice })).status).toBe(400);
  });

  it("a RETIRED (active=0) roster member is not assignable → 422 unknown_personnel", async () => {
    const id = await seedTask("JOB-A");
    const gone = await seedPersonnel("Gone Gwen");
    await env.DB.prepare("UPDATE personnel SET active = 0 WHERE id = ?").bind(gone).run();
    const res = await j(admin, `/api/fieldops/task/${id}/assign`, { personnel_id: gone });
    expect(res.status).toBe(422);
    expect((await res.json() as any).error).toBe("unknown_personnel");
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// G2.6 — task DUE DATES (migration 0035: task_assignments.due_date, nullable 'YYYY-MM-DD').
// Create accepts an optional due_date (DUE_DATE_RE shape, the checklist-assign precedent);
// a reassign/unassign NEVER touches it (the deadline belongs to the work, not the holder).
// ─────────────────────────────────────────────────────────────────────────────
describe("G2.6 — due_date on task create + reassign-preserves", () => {
  const dueOf = async (id: number) =>
    (await env.DB.prepare("SELECT due_date FROM task_assignments WHERE id=?").bind(id).first<{ due_date: string | null }>())!.due_date;

  it("create WITH due_date → 201, round-trips to the row + rides the audit payload", async () => {
    const res = await j(admin, "/api/fieldops/job/JOB-A/task", { description: "Grade pad", due_date: "2026-07-10" });
    expect(res.status, await res.clone().text()).toBe(201);
    const id = (await res.json() as any).id as number;
    expect(await dueOf(id)).toBe("2026-07-10");
    const [audit] = await audits("task_create");
    expect(JSON.parse(audit.detail ?? "{}").due_date).toBe("2026-07-10");
  });

  it("absent / null / '' due_date all mean NO deadline → 201 with a NULL column (tri-state precedent)", async () => {
    for (const body of [{ description: "A" }, { description: "B", due_date: null }, { description: "C", due_date: "" }]) {
      const res = await j(admin, "/api/fieldops/job/JOB-A/task", body);
      expect(res.status, await res.clone().text()).toBe(201);
      expect(await dueOf((await res.json() as any).id)).toBeNull();
    }
  });

  it("malformed due_date → 400 invalid_due_date, nothing written", async () => {
    for (const bad of ["2026-7-4", "July 4 2026", "2026-07-04T00:00", 20260704, "2026/07/04"]) {
      const res = await j(admin, "/api/fieldops/job/JOB-A/task", { description: "x", due_date: bad });
      expect(res.status, `due_date=${JSON.stringify(bad)}`).toBe(400);
      expect((await res.json() as any).error).toBe("invalid_due_date");
    }
    expect((await env.DB.prepare("SELECT COUNT(*) n FROM task_assignments").first<{ n: number }>())!.n).toBe(0);
  });

  it("reassign → unassign → status change ALL preserve due_date (no route clears it)", async () => {
    const create = await j(admin, "/api/fieldops/job/JOB-A/task", { description: "Keep my date", due_date: "2026-07-10" });
    expect(create.status).toBe(201);
    const id = (await create.json() as any).id as number;
    const alice = await seedPersonnel("Alice Chen");

    expect((await j(admin, `/api/fieldops/task/${id}/assign`, { personnel_id: alice })).status).toBe(200);
    expect(await dueOf(id)).toBe("2026-07-10");

    expect((await j(admin, `/api/fieldops/task/${id}/assign`, { personnel_id: null })).status).toBe(200);
    expect(await dueOf(id)).toBe("2026-07-10");

    expect((await j(admin, `/api/fieldops/task/${id}/status`, { status: "done" })).status).toBe(200);
    expect(await dueOf(id)).toBe("2026-07-10");
  });
});
