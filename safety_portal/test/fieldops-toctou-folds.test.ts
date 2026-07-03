import { env } from "cloudflare:test";
import { describe, it, expect, beforeEach } from "vitest";
import { json, post, provision, login, seedJob, seedPersonnel } from "./helpers";

// ─────────────────────────────────────────────────────────────────────────────
// CS4 — the two accepted-fast-follow TOCTOU folds, race-shape assertions.
//
// The guards under test no longer live in pre-check SELECTs: they are folded INTO the mutating
// statement's WHERE (fieldops_task_write.ts: the status-ownership predicate + the W1 current-owner
// and target role predicates; fieldops_daily_requirements.ts: the REQUIREMENTS_LIMIT count as
// INSERT … SELECT … WHERE (SELECT COUNT(*) …) < limit). A concurrent role flip / reassign /
// parallel add can therefore never land between check and write — the statement itself refuses.
//
// What a single-connection test CAN prove about that shape (and asserts here):
//   • the refusal is ATOMIC — a refused write leaves the row byte-identical, inserts nothing,
//     and writes NO audit row (the audit rides changes()=1 in the same batch);
//   • the boundary is enforced BY THE STATEMENT at the exact limit (199 → 201, 200 → 409);
//   • the response codes are IDENTICAL to the old pre-checks' (the post-refusal diagnostic
//     re-reads in the old order) — the sibling suites fieldops-task-write / fieldops-task-
//     authority / fieldops-daily-requirements pass UNMODIFIED as the broader proof.
// Runs against the REAL worker with Miniflare D1 (migrations auto-apply).
// ─────────────────────────────────────────────────────────────────────────────

async function auditCount(action: string): Promise<number> {
  const row = await env.DB.prepare("SELECT COUNT(*) AS n FROM audit_log WHERE action = ?1")
    .bind(action)
    .first<{ n: number }>();
  return row?.n ?? 0;
}

interface TaskRow {
  personnel_id: number | null;
  status: string;
  assigned_by: string | null;
}
async function taskRow(id: number): Promise<TaskRow | null> {
  return env.DB.prepare("SELECT personnel_id, status, assigned_by FROM task_assignments WHERE id = ?1")
    .bind(id)
    .first<TaskRow>();
}

async function seedTask(jobId: string, personnelId: number | null, description: string): Promise<number> {
  await env.DB.prepare(
    "INSERT INTO task_assignments (job_id, personnel_id, description, status, assigned_by) VALUES (?,?,?,'open','seeder')",
  )
    .bind(jobId, personnelId, description)
    .run();
  return (await env.DB.prepare("SELECT id FROM task_assignments ORDER BY id DESC LIMIT 1").first<{ id: number }>())!.id;
}

let admin: string, manager: string, subA: string, subB: string;
let pSubA: number, pSubB: number, pMgr: number;

beforeEach(async () => {
  await env.DB.batch([
    env.DB.prepare("DELETE FROM task_assignments"),
    env.DB.prepare("DELETE FROM job_daily_requirements"),
    env.DB.prepare("DELETE FROM personnel"),
    env.DB.prepare("DELETE FROM users"),
    env.DB.prepare("DELETE FROM jobs"),
    env.DB.prepare("DELETE FROM audit_log"),
  ]);
  await provision("admin.one", "password123", "admin");
  await provision("manager.mo", "password123", "manager");
  await provision("sub.sam", "password123", "submitter");
  await provision("sub.bee", "password123", "submitter");
  admin = await login("admin.one", "password123");
  manager = await login("manager.mo", "password123");
  subA = await login("sub.sam", "password123");
  subB = await login("sub.bee", "password123");
  await seedJob("JOB-A");
  pSubA = await seedPersonnel("Sam Sub", "sub.sam", "JOB-A");
  pSubB = await seedPersonnel("Bee Sub", "sub.bee", "JOB-A");
  pMgr = await seedPersonnel("Mo Manager", "manager.mo", "JOB-A");
});

// ── Fold 1: task-status ownership lives in the UPDATE's WHERE ────────────────────────────────────
describe("status route — the ownership predicate refuses IN-STATEMENT (atomic, no audit, row untouched)", () => {
  it("an own-only actor flipping SOMEONE ELSE'S task: 403 forbidden_task, status unchanged, zero audit rows", async () => {
    const taskId = await seedTask("JOB-A", pSubA, "Sam's task");
    const res = await post(subB, `/api/fieldops/task/${taskId}/status`, { status: "done" });
    expect(res.status).toBe(403);
    expect((await json<{ error: string }>(res)).error).toBe("forbidden_task");
    expect((await taskRow(taskId))!.status).toBe("open"); // the refused UPDATE wrote nothing
    expect(await auditCount("task_status")).toBe(0); // and audited nothing (changes()=0)
  });

  it("the owner's own flip still lands: 200, status updated, exactly ONE audit row", async () => {
    const taskId = await seedTask("JOB-A", pSubA, "Sam's task");
    const res = await post(subA, `/api/fieldops/task/${taskId}/status`, { status: "done" });
    expect(res.status).toBe(200);
    expect((await taskRow(taskId))!.status).toBe("done");
    expect(await auditCount("task_status")).toBe(1);
  });

  it("a RETIRED owner link no longer owns: the former owner's flip is refused in-statement (403)", async () => {
    const taskId = await seedTask("JOB-A", pSubA, "Sam's task");
    // The mid-window race the fold closes, replayed sequentially: the actor's ownership evaporates
    // (roster row retired) before the write — the WHERE predicate, not a stale pre-check, decides.
    await env.DB.prepare("UPDATE personnel SET active = 0 WHERE id = ?1").bind(pSubA).run();
    const res = await post(subA, `/api/fieldops/task/${taskId}/status`, { status: "done" });
    expect(res.status).toBe(403);
    expect((await taskRow(taskId))!.status).toBe("open");
  });

  it("unknown task → 404 (no existence leak), zero audit rows", async () => {
    const res = await post(subA, "/api/fieldops/task/99999/status", { status: "done" });
    expect(res.status).toBe(404);
    expect(await auditCount("task_status")).toBe(0);
  });
});

// ── Fold 2: the W1 current-owner + target role predicates live in the assign UPDATE's WHERE ─────
describe("assign route — role predicates refuse IN-STATEMENT (atomic, no audit, row untouched)", () => {
  it("a manager reassigning a MANAGER-owned task: 403 forbidden_task; owner AND assigned_by untouched", async () => {
    const taskId = await seedTask("JOB-A", pMgr, "Mo's own task");
    const res = await post(manager, `/api/fieldops/task/${taskId}/assign`, { personnel_id: pSubA });
    expect(res.status).toBe(403);
    expect((await json<{ error: string }>(res)).error).toBe("forbidden_task");
    const row = (await taskRow(taskId))!;
    expect(row.personnel_id).toBe(pMgr); // the refused UPDATE wrote NEITHER column
    expect(row.assigned_by).toBe("seeder"); // (a partial re-stamp would betray a non-atomic guard)
    expect(await auditCount("task_assign")).toBe(0);
  });

  it("a manager targeting a MANAGER-linked destination: 403 forbidden_target, row untouched, no audit", async () => {
    const taskId = await seedTask("JOB-A", pSubA, "Sam's task");
    const res = await post(manager, `/api/fieldops/task/${taskId}/assign`, { personnel_id: pMgr });
    expect(res.status).toBe(403);
    expect((await json<{ error: string }>(res)).error).toBe("forbidden_target");
    const row = (await taskRow(taskId))!;
    expect(row.personnel_id).toBe(pSubA);
    expect(row.assigned_by).toBe("seeder");
    expect(await auditCount("task_assign")).toBe(0);
  });

  it("a mid-window role promotion is closed out: once the owner's account is promoted, the manager's touch refuses", async () => {
    const taskId = await seedTask("JOB-A", pSubA, "Sam's task");
    // Sequential replay of the tracked race: the owner's linked account flips submitter → manager
    // before the write lands. The folded WHERE sees the CURRENT role, not a pre-check snapshot.
    await env.DB.prepare("UPDATE users SET role = 'manager' WHERE username = 'sub.sam'").run();
    const res = await post(manager, `/api/fieldops/task/${taskId}/assign`, { personnel_id: null });
    expect(res.status).toBe(403);
    expect((await json<{ error: string }>(res)).error).toBe("forbidden_task");
    expect((await taskRow(taskId))!.personnel_id).toBe(pSubA); // the unassign never landed
  });

  it("an admin remains unrestricted by the ?4=0 branch (reassign manager-owned → 200 + audit)", async () => {
    const taskId = await seedTask("JOB-A", pMgr, "Mo's task");
    const res = await post(admin, `/api/fieldops/task/${taskId}/assign`, { personnel_id: pSubB });
    expect(res.status).toBe(200);
    expect((await taskRow(taskId))!.personnel_id).toBe(pSubB);
    expect(await auditCount("task_assign")).toBe(1);
  });
});

// ── Fold 3: the create-route target predicate lives in the INSERT's WHERE ───────────────────────
describe("create route — the target predicate refuses IN-STATEMENT (nothing inserted, no audit)", () => {
  it("a manager creating a task for a MANAGER-linked target: 403 forbidden_target, ZERO rows inserted, no audit", async () => {
    const res = await post(manager, "/api/fieldops/job/JOB-A/task", { description: "sneak", personnel_id: pMgr });
    expect(res.status).toBe(403);
    expect((await json<{ error: string }>(res)).error).toBe("forbidden_target");
    const n = await env.DB.prepare("SELECT COUNT(*) AS n FROM task_assignments").first<{ n: number }>();
    expect(n!.n).toBe(0);
    expect(await auditCount("task_create")).toBe(0);
  });

  it("a RETIRED target refuses with the old 422 unknown_personnel (admin path — the active predicate)", async () => {
    await env.DB.prepare("UPDATE personnel SET active = 0 WHERE id = ?1").bind(pSubA).run();
    const res = await post(admin, "/api/fieldops/job/JOB-A/task", { description: "for a ghost", personnel_id: pSubA });
    expect(res.status).toBe(422);
    expect((await json<{ error: string }>(res)).error).toBe("unknown_personnel");
    expect(await auditCount("task_create")).toBe(0);
  });

  it("a clean create still lands with its id + exactly one audit row (changes()=1 twin)", async () => {
    const res = await post(manager, "/api/fieldops/job/JOB-A/task", { description: "legit", personnel_id: pSubA });
    expect(res.status).toBe(201);
    const body = await json<{ ok: boolean; id: number | null }>(res);
    expect(body.id).not.toBeNull();
    expect(await auditCount("task_create")).toBe(1);
  });
});

// ── Fold 4: the D4 REQUIREMENTS_LIMIT count lives in the INSERT's WHERE ──────────────────────────
describe("daily-requirements add — the ceiling is enforced BY THE STATEMENT (exact boundary, atomic)", () => {
  const ADD = "/api/fieldops/daily-form/job/JOB-A/requirement";

  async function seedRequirements(count: number, active = 1): Promise<void> {
    await env.DB.prepare(
      `WITH RECURSIVE seq(n) AS (SELECT 1 UNION ALL SELECT n + 1 FROM seq WHERE n < ?1)
       INSERT INTO job_daily_requirements (job_id, seq, kind, label, active)
       SELECT 'JOB-A', n, 'note', 'seed ' || n, ?2 FROM seq`,
    )
      .bind(count, active)
      .run();
  }

  it("at 199 ACTIVE items the add lands (201); the very next add refuses 409 too_many_items — the exact boundary", async () => {
    await seedRequirements(199);
    const ok = await post(admin, ADD, { kind: "note", label: "the 200th" });
    expect(ok.status, await ok.clone().text()).toBe(201);
    const refused = await post(admin, ADD, { kind: "note", label: "the 201st" });
    expect(refused.status).toBe(409);
    expect((await json<{ error: string }>(refused)).error).toBe("too_many_items");
    // Atomic refusal: nothing inserted, nothing audited for the refused add.
    const n = await env.DB.prepare(
      "SELECT COUNT(*) AS n FROM job_daily_requirements WHERE job_id = 'JOB-A' AND active = 1",
    ).first<{ n: number }>();
    expect(n!.n).toBe(200);
    expect(await auditCount("daily_requirement_add")).toBe(1); // only the 200th's audit row
  });

  it("DEACTIVATED rows don't count toward the ceiling (the count predicate is active=1)", async () => {
    await seedRequirements(200, 0); // 200 soft-deleted rows
    const res = await post(admin, ADD, { kind: "note", label: "fits fine" });
    expect(res.status, await res.clone().text()).toBe(201);
  });
});
