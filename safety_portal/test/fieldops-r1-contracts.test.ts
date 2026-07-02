import { env, SELF } from "cloudflare:test";
import { describe, it, expect, beforeEach } from "vitest";

// ─────────────────────────────────────────────────────────────────────────────
// R1 — Worker contract & correctness fixes (Assigned-Tasks refinement spec, slice R1):
//   1. OWNERSHIP (security): an own-only actor (cap.tasks.own without cap.jobtracker.manage /
//      cap.tasks.assign) may only change status of a task assigned to THEIR linked personnel →
//      403 forbidden_task otherwise. Managers/admins unrestricted.
//   2. ORDERING: /tasks/mine + /checklist/assigned return OPEN work first (status CASE), newest
//      first within a band — no more lexicographic done-first.
//   3. template_title (migration 0029): snapshotted at assign, returned by /checklist/assigned,
//      backfilled for legacy instances via the item-snapshot lineage.
//   4. ASSIGN-TIME VALIDATION: 0-item template → 422 empty_template; form-bearing template without
//      BOTH job + due date → 422 job_and_date_required; item writes 422 unknown_form_code on a
//      form code not in the catalog parent list.
//   5. BELOW-TARGET ACKNOWLEDGE: { value_num, acknowledge_below_target: true, note } completes a
//      count item below target (note REQUIRED); distinct audit action.
//   6. EMPTY-STATE REASONS: /checklist/mine returns reason (not_manager | no_personnel_link |
//      not_placed); /tasks/mine + /checklist/assigned return linked.
//   7. Q3 DUE-DATE SEMANTICS: inspection instances reconcile on work_date <= due date (early filing
//      closes); daily stays exact-date (regression-guarded in fieldops-checklist-loop-closure too).
//   8. filed_by ATTRIBUTION: auto-closed items + the rolled-up daily instance carry WHO filed the
//      closing submission (personnel display name, fallback raw account).
//   9. CONTEXT: /tasks/mine returns assigned_by; /checklist/mine instance carries project_name.
// Runs against the REAL worker with Miniflare D1 (migrations incl. 0029 auto-apply).
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
async function seedPersonnel(name: string, username: string | null, currentJob: string | null): Promise<number> {
  await env.DB.prepare("INSERT INTO personnel (name, username, current_job, active) VALUES (?,?,?,1)")
    .bind(name, username, currentJob).run();
  return (await env.DB.prepare("SELECT id FROM personnel WHERE name=? ORDER BY id DESC LIMIT 1").bind(name).first<{ id: number }>())!.id;
}
async function seedTask(jobId: string, personnelId: number | null, description: string, status: string, createdAt: number): Promise<number> {
  await env.DB.prepare("INSERT INTO task_assignments (job_id, personnel_id, description, status, created_at) VALUES (?,?,?,?,?)")
    .bind(jobId, personnelId, description, status, createdAt).run();
  return (await env.DB.prepare("SELECT id FROM task_assignments WHERE description=? ORDER BY id DESC LIMIT 1").bind(description).first<{ id: number }>())!.id;
}
async function seedSubmission(jobId: string, formCode: string, workDate: string, submittedAs: string | null, actor = "actor.acct"): Promise<string> {
  const uuid = `sub-${formCode}-${workDate}-${Math.random()}`;
  await env.DB.prepare(
    "INSERT INTO submissions (submission_uuid, job_id, form_code, work_date, payload_json, submitted_as, actor_username) VALUES (?,?,?,?,?,?,?)",
  ).bind(uuid, jobId, formCode, workDate, "{}", submittedAs, actor).run();
  return uuid;
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
async function auditCount(action: string): Promise<number> {
  return (await env.DB.prepare("SELECT COUNT(*) n FROM audit_log WHERE action=?").bind(action).first<{ n: number }>())!.n;
}

interface AssignedResp {
  inspections: {
    instance: { id: number; job_id: string | null; instance_date: string | null; status: string; template_title: string | null; created_at: number };
    items: { id: number; item_type: string; label: string | null; status: string; value_num: number | null; note: string | null; completed_by: string | null; filed_by: string | null }[];
  }[];
  linked: boolean;
}
async function assigned(cookie: string): Promise<AssignedResp> {
  const res = await get(cookie, "/api/fieldops/checklist/assigned");
  expect(res.status, await res.clone().text()).toBe(200);
  return (await res.json()) as AssignedResp;
}

interface MineResp {
  instance: { id: number; job_id: string; project_name: string | null; instance_date: string; status: string; rolled_up_submission_uuid: string | null; rolled_up_by: string | null } | null;
  items: { id: number; item_type: string; form_code: string | null; status: string; completed_by: string | null; filed_by: string | null }[];
  reason: string | null;
}
async function mine(cookie: string): Promise<MineResp> {
  const res = await get(cookie, "/api/fieldops/checklist/mine");
  expect(res.status, await res.clone().text()).toBe(200);
  return (await res.json()) as MineResp;
}

let admin: string, manager: string, subSam: string;
let pSam: number, pSue: number;

beforeEach(async () => {
  await env.DB.batch([
    env.DB.prepare("DELETE FROM checklist_item_states"),
    env.DB.prepare("DELETE FROM checklist_instances"),
    env.DB.prepare("DELETE FROM checklist_items WHERE template_id IN (SELECT id FROM checklist_templates WHERE kind IN ('job_override','generic_inspection'))"),
    env.DB.prepare("DELETE FROM checklist_templates WHERE kind IN ('job_override','generic_inspection')"),
    env.DB.prepare("DELETE FROM submissions"),
    env.DB.prepare("DELETE FROM task_assignments"),
    env.DB.prepare("DELETE FROM personnel"),
    env.DB.prepare("DELETE FROM users"),
    env.DB.prepare("DELETE FROM jobs"),
    env.DB.prepare("DELETE FROM audit_log"),
  ]);
  await provision("admin.one", "password123", "admin");
  await provision("mgr.mo", "password123", "manager");
  await provision("sub.sam", "password123", "submitter");
  await provision("sub.sue", "password123", "submitter");
  admin = await login("admin.one", "password123");
  manager = await login("mgr.mo", "password123");
  subSam = await login("sub.sam", "password123");
  await seedJob("JOB-A");
  pSam = await seedPersonnel("Sam Sub", "sub.sam", "JOB-A");
  pSue = await seedPersonnel("Sue Sub", "sub.sue", null);
});

// ── 1. Task-status OWNERSHIP (the A3 security blocker) ─────────────────────────────────────────────
describe("R1 — POST /task/:id/status ownership guard", () => {
  it("an own-only actor CANNOT flip another person's task → 403 forbidden_task (status unchanged, no audit)", async () => {
    const id = await seedTask("JOB-A", pSue, "Sue's task", "open", 100);
    const res = await post(subSam, `/api/fieldops/task/${id}/status`, { status: "done" });
    expect(res.status).toBe(403);
    expect(((await res.json()) as { error: string }).error).toBe("forbidden_task");
    const row = await env.DB.prepare("SELECT status FROM task_assignments WHERE id=?").bind(id).first<{ status: string }>();
    expect(row!.status).toBe("open");
    expect(await auditCount("task_status")).toBe(0);
  });

  it("an own-only actor CAN flip THEIR OWN task → 200", async () => {
    const id = await seedTask("JOB-A", pSam, "Sam's task", "open", 100);
    const res = await post(subSam, `/api/fieldops/task/${id}/status`, { status: "in_progress" });
    expect(res.status, await res.clone().text()).toBe(200);
  });

  it("an UNASSIGNED task is nobody's → 403 for an own-only actor", async () => {
    const id = await seedTask("JOB-A", null, "Orphan task", "open", 100);
    expect((await post(subSam, `/api/fieldops/task/${id}/status`, { status: "done" })).status).toBe(403);
  });

  it("an own-only actor with NO linked personnel → 403 on any task", async () => {
    await provision("lonely.lou", "password123", "submitter");
    const lou = await login("lonely.lou", "password123");
    const id = await seedTask("JOB-A", pSue, "Sue's task", "open", 100);
    expect((await post(lou, `/api/fieldops/task/${id}/status`, { status: "done" })).status).toBe(403);
  });

  it("a MANAGER (cap.tasks.assign) and an ADMIN stay unrestricted → 200 on someone else's task", async () => {
    const a = await seedTask("JOB-A", pSue, "Sue task A", "open", 100);
    const b = await seedTask("JOB-A", pSue, "Sue task B", "open", 100);
    expect((await post(manager, `/api/fieldops/task/${a}/status`, { status: "done" })).status).toBe(200);
    expect((await post(admin, `/api/fieldops/task/${b}/status`, { status: "done" })).status).toBe(200);
  });

  it("an unknown task id stays 404 for an own-only actor (not 403)", async () => {
    expect((await post(subSam, "/api/fieldops/task/999999/status", { status: "done" })).status).toBe(404);
  });
});

// ── 2 + 6 + 9. /tasks/mine ordering + linked + assigned_by ─────────────────────────────────────────
describe("R1 — GET /tasks/mine contract", () => {
  it("orders OPEN first, then in_progress, then done; created_at DESC within a band", async () => {
    await seedTask("JOB-A", pSam, "T-open-old", "open", 50);
    await seedTask("JOB-A", pSam, "T-open-new", "open", 100);
    await seedTask("JOB-A", pSam, "T-inprog", "in_progress", 200);
    await seedTask("JOB-A", pSam, "T-done", "done", 300); // newest — must still sort LAST
    const res = await get(subSam, "/api/fieldops/tasks/mine");
    expect(res.status).toBe(200);
    const body = (await res.json()) as { tasks: { description: string }[]; linked: boolean };
    expect(body.tasks.map((t) => t.description)).toEqual(["T-open-new", "T-open-old", "T-inprog", "T-done"]);
    expect(body.linked).toBe(true);
  });

  it("linked:false for a session with no ACTIVE linked personnel row", async () => {
    await provision("lonely.lou", "password123", "submitter");
    const lou = await login("lonely.lou", "password123");
    const body = (await (await get(lou, "/api/fieldops/tasks/mine")).json()) as { tasks: unknown[]; linked: boolean };
    expect(body.tasks).toEqual([]);
    expect(body.linked).toBe(false);
  });

  it("returns assigned_by — stamped by the create route, re-stamped by the assign route", async () => {
    const add = await post(admin, "/api/fieldops/job/JOB-A/task", { description: "Grade the pad", personnel_id: pSam });
    expect(add.status, await add.clone().text()).toBe(201);
    const id = ((await add.json()) as { id: number }).id;
    let body = (await (await get(subSam, "/api/fieldops/tasks/mine")).json()) as { tasks: { id: number; assigned_by: string | null; created_at: number }[] };
    expect(body.tasks.find((t) => t.id === id)!.assigned_by).toBe("admin.one");
    // manager reassigns (to a submitter-linked target, per the W1 guard) → assigned_by re-stamped.
    expect((await post(manager, `/api/fieldops/task/${id}/assign`, { personnel_id: pSam })).status).toBe(200);
    body = (await (await get(subSam, "/api/fieldops/tasks/mine")).json()) as { tasks: { id: number; assigned_by: string | null; created_at: number }[] };
    expect(body.tasks.find((t) => t.id === id)!.assigned_by).toBe("mgr.mo");
  });
});

// ── 4. Assign-time validation + catalog form_code ──────────────────────────────────────────────────
describe("R1 — assign-time validation", () => {
  it("assigning a 0-item template → 422 empty_template (no instance created)", async () => {
    const t = await createTemplate(admin, "Empty one");
    const res = await post(admin, "/api/fieldops/checklist/assign", { template_id: t, assignee_personnel_id: pSam });
    expect(res.status).toBe(422);
    expect(((await res.json()) as { error: string }).error).toBe("empty_template");
    expect((await env.DB.prepare("SELECT COUNT(*) n FROM checklist_instances WHERE kind='inspection'").first<{ n: number }>())!.n).toBe(0);
  });

  it("a form-bearing template needs BOTH job and due date → 422 job_and_date_required (each partial combination)", async () => {
    const t = await createTemplate(admin, "JHA check");
    await addItem(admin, t, { item_type: "form_linked", label: "File JHA", form_code: "jha" });
    for (const body of [
      { template_id: t, assignee_personnel_id: pSam },
      { template_id: t, assignee_personnel_id: pSam, job_id: "JOB-A" },
      { template_id: t, assignee_personnel_id: pSam, due_date: "2026-07-10" },
    ]) {
      const res = await post(admin, "/api/fieldops/checklist/assign", body);
      expect(res.status).toBe(422);
      expect(((await res.json()) as { error: string }).error).toBe("job_and_date_required");
    }
    // BOTH supplied → 201.
    expect((await post(admin, "/api/fieldops/checklist/assign", { template_id: t, assignee_personnel_id: pSam, job_id: "JOB-A", due_date: "2026-07-10" })).status).toBe(201);
  });

  it("a manual/count-only template still assigns WITHOUT job or date", async () => {
    const t = await createTemplate(admin, "Manual only");
    await addItem(admin, t, { item_type: "manual_attest", label: "Walkthrough" });
    expect((await post(admin, "/api/fieldops/checklist/assign", { template_id: t, assignee_personnel_id: pSam })).status).toBe(201);
  });

  it("item writes reject a form_code not in the catalog parent list → 422 unknown_form_code; a real parent passes", async () => {
    // library item route
    const t = await createTemplate(admin, "Codes");
    let res = await post(admin, `/api/fieldops/checklist/inspection/${t}/item`, { item_type: "form_linked", label: "x", form_code: "not-a-real-form" });
    expect(res.status).toBe(422);
    expect(((await res.json()) as { error: string }).error).toBe("unknown_form_code");
    // default-template item route
    res = await post(admin, "/api/fieldops/checklist/default/item", { item_type: "inspection", label: "x", form_code: "bogus-form" });
    expect(res.status).toBe(422);
    // per-job item route
    res = await post(admin, "/api/fieldops/checklist/job/JOB-A/item", { item_type: "form_linked", label: "x", form_code: "typo-daily-reprot" });
    expect(res.status).toBe(422);
    // real catalog parents pass on both form-bearing types
    expect((await post(admin, `/api/fieldops/checklist/inspection/${t}/item`, { item_type: "form_linked", label: "ok", form_code: "jha" })).status).toBe(201);
    expect((await post(admin, "/api/fieldops/checklist/default/item", { item_type: "inspection", label: "ok", form_code: "equipment-preinspection" })).status).toBe(201);
  });
});

// ── 3. template_title (0029) ───────────────────────────────────────────────────────────────────────
describe("R1 — template_title snapshot + backfill (migration 0029)", () => {
  it("assign snapshots the title; /checklist/assigned returns it; a later template rename does NOT mutate it", async () => {
    const t = await createTemplate(admin, "Fall protection");
    await addItem(admin, t, { item_type: "manual_attest", label: "Harness checked" });
    expect((await post(admin, "/api/fieldops/checklist/assign", { template_id: t, assignee_personnel_id: pSam })).status).toBe(201);
    let mineA = await assigned(subSam);
    expect(mineA.inspections[0].instance.template_title).toBe("Fall protection");
    // Rename the library template — the snapshot must not move (same lineage rule as the items).
    expect((await post(admin, `/api/fieldops/checklist/inspection/${t}/edit`, { title: "Renamed" })).status).toBe(200);
    mineA = await assigned(subSam);
    expect(mineA.inspections[0].instance.template_title).toBe("Fall protection");
  });

  it("the 0029 backfill resolves a legacy NULL title through the item-snapshot lineage; unresolvable stays NULL", async () => {
    const t = await createTemplate(admin, "Legacy title");
    await addItem(admin, t, { item_type: "manual_attest", label: "Old item" });
    expect((await post(admin, "/api/fieldops/checklist/assign", { template_id: t, assignee_personnel_id: pSam })).status).toBe(201);
    // Simulate a pre-0029 instance (the column existed only after the migration).
    await env.DB.prepare("UPDATE checklist_instances SET template_title = NULL WHERE kind='inspection'").run();

    // Re-run the REAL backfill statement from the applied migration (TEST_MIGRATIONS carries the
    // migration files' queries verbatim; the ALTER can't re-run, the UPDATE is idempotent).
    const mig = env.TEST_MIGRATIONS.find((m) => m.name.startsWith("0029"));
    expect(mig).toBeDefined();
    const backfills = mig!.queries.filter((q) => q.includes("UPDATE checklist_instances"));
    expect(backfills.length).toBe(1);
    await env.DB.prepare(backfills[0]).run();

    const row = await env.DB.prepare("SELECT template_title FROM checklist_instances WHERE kind='inspection'").first<{ template_title: string | null }>();
    expect(row!.template_title).toBe("Legacy title");

    // Unresolvable lineage (template + items deleted) → stays NULL, no error.
    await env.DB.prepare("UPDATE checklist_instances SET template_title = NULL WHERE kind='inspection'").run();
    expect((await post(admin, `/api/fieldops/checklist/inspection/${t}/delete`)).status).toBe(200);
    await env.DB.prepare(backfills[0]).run();
    const after = await env.DB.prepare("SELECT template_title FROM checklist_instances WHERE kind='inspection'").first<{ template_title: string | null }>();
    expect(after!.template_title).toBeNull();
  });
});

// ── 2. /checklist/assigned ordering ────────────────────────────────────────────────────────────────
describe("R1 — /checklist/assigned orders open first", () => {
  it("a COMPLETE inspection sorts after OPEN ones (no more lexicographic complete-first)", async () => {
    const t1 = await createTemplate(admin, "First");
    await addItem(admin, t1, { item_type: "manual_attest", label: "A" });
    const t2 = await createTemplate(admin, "Second");
    await addItem(admin, t2, { item_type: "manual_attest", label: "B" });
    expect((await post(admin, "/api/fieldops/checklist/assign", { template_id: t1, assignee_personnel_id: pSam })).status).toBe(201);
    expect((await post(admin, "/api/fieldops/checklist/assign", { template_id: t2, assignee_personnel_id: pSam })).status).toBe(201);

    // Complete the FIRST instance's item → that instance flips to complete.
    const before = await assigned(subSam);
    const first = before.inspections.find((i) => i.instance.template_title === "First")!;
    expect((await post(subSam, `/api/fieldops/checklist/item-state/${first.items[0].id}/complete`, {})).status).toBe(200);

    const after = await assigned(subSam);
    expect(after.inspections.map((i) => [i.instance.template_title, i.instance.status])).toEqual([
      ["Second", "open"],
      ["First", "complete"],
    ]);
    expect(after.linked).toBe(true);
  });

  it("linked:false for an unlinked session", async () => {
    await provision("lonely.lou", "password123", "submitter");
    const lou = await login("lonely.lou", "password123");
    const body = await assigned(lou);
    expect(body.inspections).toEqual([]);
    expect(body.linked).toBe(false);
  });
});

// ── 5. Below-target acknowledge ────────────────────────────────────────────────────────────────────
describe("R1 — count acknowledge-below-target", () => {
  async function seedCountInstance(): Promise<number> {
    const t = await createTemplate(admin, "Counted");
    await addItem(admin, t, { item_type: "count", label: "Anchors", target_count: 5 });
    expect((await post(admin, "/api/fieldops/checklist/assign", { template_id: t, assignee_personnel_id: pSam })).status).toBe(201);
    const m = await assigned(subSam);
    return m.inspections[0].items[0].id;
  }

  it("below target WITHOUT acknowledge stays 400 below_target with the value recorded (unchanged behavior)", async () => {
    const stateId = await seedCountInstance();
    const res = await post(subSam, `/api/fieldops/checklist/item-state/${stateId}/complete`, { value_num: 3 });
    expect(res.status).toBe(400);
    expect(((await res.json()) as { error: string }).error).toBe("below_target");
    const row = await env.DB.prepare("SELECT status, value_num FROM checklist_item_states WHERE id=?").bind(stateId).first<{ status: string; value_num: number | null }>();
    expect(row).toMatchObject({ status: "open", value_num: 3 });
  });

  it("acknowledge WITHOUT a note → 400 note_required (nothing completed)", async () => {
    const stateId = await seedCountInstance();
    for (const body of [
      { value_num: 3, acknowledge_below_target: true },
      { value_num: 3, acknowledge_below_target: true, note: "   " },
    ]) {
      const res = await post(subSam, `/api/fieldops/checklist/item-state/${stateId}/complete`, body);
      expect(res.status).toBe(400);
      expect(((await res.json()) as { error: string }).error).toBe("note_required");
    }
    const row = await env.DB.prepare("SELECT status FROM checklist_item_states WHERE id=?").bind(stateId).first<{ status: string }>();
    expect(row!.status).toBe("open");
  });

  it("acknowledge WITH a note completes below target, stores value + note, audits the DISTINCT action", async () => {
    const stateId = await seedCountInstance();
    const res = await post(subSam, `/api/fieldops/checklist/item-state/${stateId}/complete`, {
      value_num: 3,
      acknowledge_below_target: true,
      note: "Supplier shorted the delivery",
    });
    expect(res.status, await res.clone().text()).toBe(200);
    const body = (await res.json()) as { status: string; value_num: number; instance_status: string; acknowledged_below_target: boolean };
    expect(body).toMatchObject({ status: "done", value_num: 3, instance_status: "complete", acknowledged_below_target: true });
    const row = await env.DB.prepare("SELECT status, value_num, note, completed_by FROM checklist_item_states WHERE id=?").bind(stateId)
      .first<{ status: string; value_num: number; note: string; completed_by: string }>();
    expect(row).toMatchObject({ status: "done", value_num: 3, note: "Supplier shorted the delivery", completed_by: "sub.sam" });
    expect(await auditCount("checklist_item_complete_below_target")).toBe(1);
    expect(await auditCount("checklist_item_complete")).toBe(0); // the normal action was NOT used
  });

  it("meeting the target is unchanged: normal complete audit, acknowledged flag false", async () => {
    const stateId = await seedCountInstance();
    const res = await post(subSam, `/api/fieldops/checklist/item-state/${stateId}/complete`, { value_num: 6 });
    expect(res.status).toBe(200);
    expect(((await res.json()) as { acknowledged_below_target: boolean }).acknowledged_below_target).toBe(false);
    expect(await auditCount("checklist_item_complete")).toBe(1);
    expect(await auditCount("checklist_item_complete_below_target")).toBe(0);
  });
});

// ── 6 + 9. /checklist/mine reason codes + project_name ─────────────────────────────────────────────
describe("R1 — /checklist/mine empty-state reasons", () => {
  it("a submitter → reason 'not_manager'", async () => {
    const body = await mine(subSam);
    expect(body.instance).toBeNull();
    expect(body.reason).toBe("not_manager");
  });

  it("a manager with NO linked personnel → 'no_personnel_link'", async () => {
    const body = await mine(manager); // mgr.mo has no personnel row in this suite's seed
    expect(body.instance).toBeNull();
    expect(body.reason).toBe("no_personnel_link");
  });

  it("a linked but UNPLACED manager → 'not_placed'", async () => {
    await seedPersonnel("Mo Manager", "mgr.mo", null);
    const body = await mine(manager);
    expect(body.instance).toBeNull();
    expect(body.reason).toBe("not_placed");
  });

  it("a placed manager → reason null + instance carrying the job's project_name", async () => {
    await seedPersonnel("Mo Manager", "mgr.mo", "JOB-A");
    const body = await mine(manager);
    expect(body.reason).toBeNull();
    expect(body.instance).not.toBeNull();
    expect(body.instance!.project_name).toBe("Project JOB-A");
  });
});

// ── 7. Q3 due-date semantics ───────────────────────────────────────────────────────────────────────
describe("R1 — Q3: inspection reconcile matches work_date <= due date", () => {
  async function assignJha(dueDate: string): Promise<void> {
    const t = await createTemplate(admin, `JHA due ${dueDate}`);
    await addItem(admin, t, { item_type: "form_linked", label: "File JHA", form_code: "jha" });
    expect((await post(admin, "/api/fieldops/checklist/assign", { template_id: t, assignee_personnel_id: pSam, job_id: "JOB-A", due_date: dueDate })).status).toBe(201);
  }

  it("a filing BEFORE the due date closes the inspection item (early filing satisfies it)", async () => {
    await assignJha("2026-07-10");
    let m = await assigned(subSam);
    expect(m.inspections[0].items[0].status).toBe("open");
    await seedSubmission("JOB-A", "jha-v3", "2026-07-05", "sub.sam");
    m = await assigned(subSam);
    expect(m.inspections[0].items[0].status).toBe("done");
    expect(m.inspections[0].items[0].completed_by).toBe("(auto)");
    expect(m.inspections[0].instance.status).toBe("complete");
  });

  it("a filing AFTER the due date does NOT close it (<= is not <>)", async () => {
    await assignJha("2026-07-10");
    await seedSubmission("JOB-A", "jha-v3", "2026-07-12", "sub.sam");
    const m = await assigned(subSam);
    expect(m.inspections[0].items[0].status).toBe("open");
  });

  it("DAILY stays exact-date: an earlier-dated submission does NOT close today's daily form_linked item", async () => {
    await seedPersonnel("Mo Manager", "mgr.mo", "JOB-A");
    const before = await mine(manager);
    const fl = before.items.find((i) => i.item_type === "form_linked" && i.form_code === "daily-report")!;
    expect(fl.status).toBe("open");
    await seedSubmission("JOB-A", "daily-report", "2020-01-01", "mgr.mo"); // long before today
    const after = await mine(manager);
    expect(after.items.find((i) => i.id === fl.id)!.status).toBe("open");
  });
});

// ── 8. filed_by attribution ────────────────────────────────────────────────────────────────────────
describe("R1 — filed_by attribution on auto-closed items + the rolled-up daily", () => {
  it("an auto-closed inspection item carries the filer's personnel display name (submitted_as → personnel.name)", async () => {
    const t = await createTemplate(admin, "JHA attributed");
    await addItem(admin, t, { item_type: "form_linked", label: "File JHA", form_code: "jha" });
    expect((await post(admin, "/api/fieldops/checklist/assign", { template_id: t, assignee_personnel_id: pSam, job_id: "JOB-A", due_date: "2026-07-10" })).status).toBe(201);
    await seedSubmission("JOB-A", "jha-v3", "2026-07-10", "sub.sam");
    const m = await assigned(subSam);
    expect(m.inspections[0].items[0].status).toBe("done");
    expect(m.inspections[0].items[0].filed_by).toBe("Sam Sub"); // display name via personnel.username link
  });

  it("filed_by is NULL (never a raw account id) when no personnel row matches; NULL on open / manual items", async () => {
    // (W9) display-name-only attribution: an account with no personnel row must NOT leak its raw
    // username to the assignee — the item still auto-closes, just without a name caption.
    const t = await createTemplate(admin, "JHA fallback");
    await addItem(admin, t, { item_type: "form_linked", label: "File JHA", form_code: "jha" });
    await addItem(admin, t, { item_type: "manual_attest", label: "Walk" });
    expect((await post(admin, "/api/fieldops/checklist/assign", { template_id: t, assignee_personnel_id: pSam, job_id: "JOB-A", due_date: "2026-07-11" })).status).toBe(201);
    await seedSubmission("JOB-A", "jha-v3", "2026-07-11", "ghost.acct");
    const m = await assigned(subSam);
    const formItem = m.inspections[0].items.find((i) => i.item_type === "form_linked")!;
    const manualItem = m.inspections[0].items.find((i) => i.item_type === "manual_attest")!;
    expect(formItem.status).toBe("done"); // still auto-closed
    expect(formItem.filed_by).toBeNull();
    expect(manualItem.filed_by).toBeNull();
    // A manually-completed item is attributed by completed_by, not filed_by.
    expect((await post(subSam, `/api/fieldops/checklist/item-state/${manualItem.id}/complete`, {})).status).toBe(200);
    const m2 = await assigned(subSam);
    expect(m2.inspections.flatMap((i) => i.items).find((i) => i.id === manualItem.id)!.filed_by).toBeNull();
  });

  it("the rolled-up daily instance carries rolled_up_by (who filed the Daily Report)", async () => {
    await seedPersonnel("Mo Manager", "mgr.mo", "JOB-A");
    const before = await mine(manager);
    expect(before.instance!.rolled_up_by).toBeNull();
    await seedSubmission("JOB-A", "daily-report-v1", before.instance!.instance_date, "mgr.mo");
    const after = await mine(manager);
    expect(after.instance!.rolled_up_submission_uuid).not.toBeNull();
    expect(after.instance!.rolled_up_by).toBe("Mo Manager");
  });
});
