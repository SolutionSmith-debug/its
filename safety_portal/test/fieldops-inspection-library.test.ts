import { env } from "cloudflare:test";
import { describe, it, expect, beforeEach } from "vitest";
import { provision, login, get, post, seedJob, seedPersonnel as seedPersonnelRow } from "./helpers";

// ─────────────────────────────────────────────────────────────────────────────
// Assigned-Tasks tab (P4 field-ops feature) S6 — the generic-inspection LIBRARY + admin compose/assign.
//   - cap.checklist.manage gates the library CRUD + the assign route (admin holds it; submitter +
//     manager do NOT). A LIBRARY = MANY generic_inspection templates (generalizing the S2 single
//     daily_default item CRUD).
//   - POST /checklist/assign creates a kind='inspection' instance for a manager OR subcontractor and
//     SNAPSHOTS the template's items into checklist_item_states; validates template-kind + assignee.
//   - GET /checklist/assigned (cap.tasks.own) returns the actor's OWN assigned inspections; completion
//     reuses the EXISTING S3/S4 item-state routes (ownership-scoped, kind-agnostic).
// Runs against the REAL worker with Miniflare D1 (migrations incl. 0026 auto-apply).
// ─────────────────────────────────────────────────────────────────────────────

const seedPersonnel = (name: string, username: string | null, currentJob: string | null, active = 1): Promise<number> =>
  seedPersonnelRow(name, username, currentJob, { active });

// Create a generic_inspection library template as the admin; return its id.
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

interface AssignedResp {
  inspections: { instance: { id: number; job_id: string | null; instance_date: string | null; status: string; project_name: string | null }; items: { id: number; item_type: string; label: string | null; status: string }[] }[];
}
async function assigned(cookie: string): Promise<AssignedResp> {
  const res = await get(cookie, "/api/fieldops/checklist/assigned");
  expect(res.status, await res.clone().text()).toBe(200);
  return (await res.json()) as AssignedResp;
}

let admin: string, manager: string, submitter: string, submitter2: string;
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
  await provision("sub.sue", "password123", "submitter");
  admin = await login("admin.one", "password123");
  manager = await login("mgr.mo", "password123");
  submitter = await login("sub.sam", "password123");
  submitter2 = await login("sub.sue", "password123");
  await seedJob("JOB-A");
  subPersonId = await seedPersonnel("Sam Sub", "sub.sam", "JOB-A");
  await seedPersonnel("Sue Sub", "sub.sue", null);
});

describe("S6 library — capability gating", () => {
  it("admin can CRUD the library; submitter + manager are 403", async () => {
    expect((await get(admin, "/api/fieldops/checklist/inspections")).status).toBe(200);
    expect((await get(submitter, "/api/fieldops/checklist/inspections")).status).toBe(403);
    expect((await get(manager, "/api/fieldops/checklist/inspections")).status).toBe(403);
    expect((await post(submitter, "/api/fieldops/checklist/inspection", { title: "x" })).status).toBe(403);
    expect((await post(manager, "/api/fieldops/checklist/inspection", { title: "x" })).status).toBe(403);
    expect((await post(submitter, "/api/fieldops/checklist/assign", { template_id: 1, assignee_personnel_id: 1 })).status).toBe(403);
  });
});

describe("S6 library — MANY templates CRUD", () => {
  it("creates, lists, edits, and deletes multiple generic_inspection templates", async () => {
    const a = await createTemplate(admin, "Fall protection");
    const b = await createTemplate(admin, "Crane pre-lift");
    expect(a).not.toBe(b);

    // List shows both (item_count starts at 0).
    let list = (await (await get(admin, "/api/fieldops/checklist/inspections")).json()) as { templates: { id: number; title: string; item_count: number }[] };
    expect(list.templates.map((t) => t.title).sort()).toEqual(["Crane pre-lift", "Fall protection"]);
    expect(list.templates.every((t) => t.item_count === 0)).toBe(true);

    // Add items to A (a form_linked + a manual_attest + a count).
    await addItem(admin, a, { item_type: "form_linked", label: "File JHA", form_code: "jha" });
    await addItem(admin, a, { item_type: "manual_attest", label: "Harness checked" });
    const cnt = await addItem(admin, a, { item_type: "count", label: "Anchors", target_count: 3 });

    const detail = (await (await get(admin, `/api/fieldops/checklist/inspection/${a}`)).json()) as { template: { id: number }; items: { id: number; label: string; item_type: string }[] };
    expect(detail.items).toHaveLength(3);
    expect(detail.items.map((i) => i.label)).toContain("Harness checked");

    // Edit the count item; delete it.
    expect((await post(admin, `/api/fieldops/checklist/inspection/${a}/item/${cnt}/edit`, { item_type: "count", label: "Anchor points", target_count: 4 })).status).toBe(200);
    expect((await post(admin, `/api/fieldops/checklist/inspection/${a}/item/${cnt}/delete`)).status).toBe(200);
    const detail2 = (await (await get(admin, `/api/fieldops/checklist/inspection/${a}`)).json()) as { items: unknown[] };
    expect(detail2.items).toHaveLength(2);

    // Rename + deactivate B; then delete it.
    expect((await post(admin, `/api/fieldops/checklist/inspection/${b}/edit`, { title: "Crane pre-lift v2", active: false })).status).toBe(200);
    expect((await post(admin, `/api/fieldops/checklist/inspection/${b}/delete`)).status).toBe(200);
    list = (await (await get(admin, "/api/fieldops/checklist/inspections")).json()) as { templates: { id: number; title: string; item_count: number }[] };
    expect(list.templates).toHaveLength(1);
    expect(list.templates[0].id).toBe(a);
    expect(list.templates[0].item_count).toBe(2);
  });

  it("the inspection routes refuse a NON-generic template id (daily_default / unknown → 404)", async () => {
    const dailyId = (await env.DB.prepare("SELECT id FROM checklist_templates WHERE kind='daily_default'").first<{ id: number }>())!.id;
    expect((await get(admin, `/api/fieldops/checklist/inspection/${dailyId}`)).status).toBe(404);
    expect((await post(admin, `/api/fieldops/checklist/inspection/${dailyId}/item`, { item_type: "manual_attest", label: "x" })).status).toBe(404);
    expect((await get(admin, `/api/fieldops/checklist/inspection/999999`)).status).toBe(404);
  });

  it("rejects a blank/oversized title and a bad item", async () => {
    expect((await post(admin, "/api/fieldops/checklist/inspection", { title: "" })).status).toBe(400);
    expect((await post(admin, "/api/fieldops/checklist/inspection", { title: "x".repeat(300) })).status).toBe(400);
    const t = await createTemplate(admin, "Valid");
    expect((await post(admin, `/api/fieldops/checklist/inspection/${t}/item`, { item_type: "bogus", label: "x" })).status).toBe(400);
    expect((await post(admin, `/api/fieldops/checklist/inspection/${t}/item`, { item_type: "form_linked", label: "x" })).status).toBe(400); // form_code required
  });
});

describe("S6 assign — create instance + snapshot items + validate", () => {
  it("assigns a generic_inspection to a subcontractor, snapshotting its items into an instance", async () => {
    const t = await createTemplate(admin, "Fall protection");
    await addItem(admin, t, { item_type: "manual_attest", label: "Harness checked" });
    await addItem(admin, t, { item_type: "count", label: "Anchors", target_count: 2 });

    const res = await post(admin, "/api/fieldops/checklist/assign", { template_id: t, assignee_personnel_id: subPersonId });
    expect(res.status, await res.clone().text()).toBe(201);
    const body = (await res.json()) as { ok: boolean; instance_id: number; item_count: number };
    expect(body.item_count).toBe(2);

    // The instance is kind='inspection', assigned to the subcontractor, with 2 snapshotted item-states.
    const inst = await env.DB.prepare("SELECT kind, assignee_personnel_id, status FROM checklist_instances WHERE id=?").bind(body.instance_id).first<{ kind: string; assignee_personnel_id: number; status: string }>();
    expect(inst!.kind).toBe("inspection");
    expect(inst!.assignee_personnel_id).toBe(subPersonId);
    const states = await env.DB.prepare("SELECT COUNT(*) c FROM checklist_item_states WHERE instance_id=?").bind(body.instance_id).first<{ c: number }>();
    expect(states!.c).toBe(2);
    // (W4) the assign is audited ATOMICALLY with the instance creation (one batch) — a forensic record
    // of who assigned which template to whom exists the moment the instance row does.
    expect((await env.DB.prepare("SELECT COUNT(*) n FROM audit_log WHERE action='checklist_inspection_assign'").first<{ n: number }>())!.n).toBe(1);
  });

  it("validates template-kind, assignee existence/active, job_id, and due_date", async () => {
    const t = await createTemplate(admin, "Valid");
    await addItem(admin, t, { item_type: "manual_attest", label: "x" });
    const dailyId = (await env.DB.prepare("SELECT id FROM checklist_templates WHERE kind='daily_default'").first<{ id: number }>())!.id;

    // template must be generic_inspection.
    expect((await post(admin, "/api/fieldops/checklist/assign", { template_id: dailyId, assignee_personnel_id: subPersonId })).status).toBe(404);
    // assignee must exist + be active.
    expect((await post(admin, "/api/fieldops/checklist/assign", { template_id: t, assignee_personnel_id: 999999 })).status).toBe(404);
    const retired = await seedPersonnel("Retired Ray", "ray", null, 0);
    expect((await post(admin, "/api/fieldops/checklist/assign", { template_id: t, assignee_personnel_id: retired })).status).toBe(404);
    // job_id must exist when supplied.
    expect((await post(admin, "/api/fieldops/checklist/assign", { template_id: t, assignee_personnel_id: subPersonId, job_id: "NOPE" })).status).toBe(404);
    // due_date must be YYYY-MM-DD.
    expect((await post(admin, "/api/fieldops/checklist/assign", { template_id: t, assignee_personnel_id: subPersonId, due_date: "07/01/2026" })).status).toBe(400);
  });

  it("dedupes an exact (job + date) repeat (409) but ALLOWS repeats with a null date", async () => {
    const t = await createTemplate(admin, "Valid");
    await addItem(admin, t, { item_type: "manual_attest", label: "x" });

    // Two assigns with NO job/date → two distinct instances (NULLs are distinct in the UNIQUE key).
    expect((await post(admin, "/api/fieldops/checklist/assign", { template_id: t, assignee_personnel_id: subPersonId })).status).toBe(201);
    expect((await post(admin, "/api/fieldops/checklist/assign", { template_id: t, assignee_personnel_id: subPersonId })).status).toBe(201);

    // Same job + date twice → the second is deduped.
    const first = await post(admin, "/api/fieldops/checklist/assign", { template_id: t, assignee_personnel_id: subPersonId, job_id: "JOB-A", due_date: "2026-07-10" });
    expect(first.status).toBe(201);
    const dup = await post(admin, "/api/fieldops/checklist/assign", { template_id: t, assignee_personnel_id: subPersonId, job_id: "JOB-A", due_date: "2026-07-10" });
    expect(dup.status, await dup.clone().text()).toBe(409);
  });
});

describe("S6 assigned tab — the assignee sees only their own; completion reuses S3/S4", () => {
  it("a submitter with an assigned inspection sees it; another submitter does NOT", async () => {
    const t = await createTemplate(admin, "Fall protection");
    await addItem(admin, t, { item_type: "manual_attest", label: "Harness checked" });
    await post(admin, "/api/fieldops/checklist/assign", { template_id: t, assignee_personnel_id: subPersonId });

    const mine = await assigned(submitter);
    expect(mine.inspections).toHaveLength(1);
    expect(mine.inspections[0].items.some((i) => i.label === "Harness checked")).toBe(true);

    // A different submitter (sub.sue) sees none.
    const others = await assigned(submitter2);
    expect(others.inspections).toHaveLength(0);
  });

  it("also works for a MANAGER assignee", async () => {
    const mgrPerson = await seedPersonnel("Mo Manager", "mgr.mo", "JOB-A");
    const t = await createTemplate(admin, "Mgr inspection");
    await addItem(admin, t, { item_type: "manual_attest", label: "Walkthrough" });
    await post(admin, "/api/fieldops/checklist/assign", { template_id: t, assignee_personnel_id: mgrPerson });
    const mine = await assigned(manager);
    expect(mine.inspections).toHaveLength(1);
  });

  it("an assigned-inspection manual_attest item completes via the EXISTING /complete route (ownership-scoped)", async () => {
    const t = await createTemplate(admin, "Fall protection");
    await addItem(admin, t, { item_type: "manual_attest", label: "Harness checked" });
    await post(admin, "/api/fieldops/checklist/assign", { template_id: t, assignee_personnel_id: subPersonId });

    const mine = await assigned(submitter);
    const stateId = mine.inspections[0].items[0].id;

    // The OTHER submitter cannot complete it (ownership = instance.assignee, kind-agnostic).
    expect((await post(submitter2, `/api/fieldops/checklist/item-state/${stateId}/complete`, {})).status).toBe(403);

    // The assignee completes it → item done + the inspection instance flips to complete.
    const done = await post(submitter, `/api/fieldops/checklist/item-state/${stateId}/complete`, {});
    expect(done.status, await done.clone().text()).toBe(200);
    const after = await assigned(submitter);
    expect(after.inspections[0].items[0].status).toBe("done");
    expect(after.inspections[0].instance.status).toBe("complete");
  });

  it("a form_linked item in an assigned inspection auto-closes on a matching submission (loop-closure reuse)", async () => {
    const t = await createTemplate(admin, "JHA inspection");
    await addItem(admin, t, { item_type: "form_linked", label: "File JHA", form_code: "jha" });
    // Assign WITH a job + date so loop-closure has a (job, date) to match.
    await post(admin, "/api/fieldops/checklist/assign", { template_id: t, assignee_personnel_id: subPersonId, job_id: "JOB-A", due_date: "2026-07-10" });

    // Before the submission: pending.
    let mine = await assigned(submitter);
    expect(mine.inspections[0].items[0].status).toBe("open");

    // File a jha submission for (JOB-A, 2026-07-10) → the item auto-closes on the next assigned read.
    await env.DB.prepare(
      "INSERT INTO submissions (submission_uuid, job_id, form_code, work_date, payload_json, created_at) VALUES (?,?,?,?,?,?)",
    ).bind("uuid-jha-1", "JOB-A", "jha-v3", "2026-07-10", "{}", 1_700_000_100).run();

    mine = await assigned(submitter);
    expect(mine.inspections[0].items[0].status).toBe("done");
    expect(mine.inspections[0].instance.status).toBe("complete");
  });
});
