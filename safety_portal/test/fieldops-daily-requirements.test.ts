import { env } from "cloudflare:test";
import { describe, it, expect, beforeEach } from "vitest";
import { call, provision, login, seedJob, seedPersonnel } from "./helpers";

// ─────────────────────────────────────────────────────────────────────────────
// SOP daily form slice D4 — per-job daily-form requirements (migration 0030
// job_daily_requirements; worker/fieldops_daily_requirements.ts).
//   • Admin CRUD (cap.checklist.manage): add / edit (reorder = a seq edit) / deactivate —
//     mutation + audit in ONE batch (W4); the conditional audit rides changes()=1, so a failed
//     mutation writes NO audit row and a successful one writes EXACTLY one.
//   • form_link validation: form_code must be a REAL catalog parent (422 unknown_form_code) and
//     NOT a launch:"daily-tab" parent (422 daily_tab_form_code — the daily form deep-linking back
//     into itself would be circular). Non-link kinds store form_code NULL.
//   • The tab read (GET /api/fieldops/daily-form/requirements): cap.tasks.own + the SAME per-job
//     ownership scope as /daily-form/status — a non-admin actor only their OWN placement (403
//     forbidden_job), admins any job. Active items only, seq order, bounded shape.
// Runs against the REAL worker with Miniflare D1 (migrations auto-apply); per-test isolation.
// ─────────────────────────────────────────────────────────────────────────────

interface ReqItem { id: number; seq: number; kind: string; label: string; form_code: string | null }
async function list(cookie: string, jobId: string): Promise<ReqItem[]> {
  const res = await call(`/api/fieldops/daily-form/requirements?job_id=${encodeURIComponent(jobId)}`, { cookie });
  expect(res.status, await res.clone().text()).toBe(200);
  return ((await res.json()) as { items: ReqItem[] }).items;
}
function add(cookie: string, jobId: string, body: unknown): Promise<Response> {
  return call(`/api/fieldops/daily-form/job/${encodeURIComponent(jobId)}/requirement`, {
    method: "POST", cookie, body: JSON.stringify(body),
  });
}
function edit(cookie: string, jobId: string, id: number, body: unknown): Promise<Response> {
  return call(`/api/fieldops/daily-form/job/${encodeURIComponent(jobId)}/requirement/${id}/edit`, {
    method: "POST", cookie, body: JSON.stringify(body),
  });
}
function deactivate(cookie: string, jobId: string, id: number): Promise<Response> {
  return call(`/api/fieldops/daily-form/job/${encodeURIComponent(jobId)}/requirement/${id}/deactivate`, {
    method: "POST", cookie, body: "{}",
  });
}
async function auditCount(action: string): Promise<number> {
  const row = await env.DB.prepare("SELECT COUNT(*) n FROM audit_log WHERE action=?1").bind(action).first<{ n: number }>();
  return row!.n;
}

let admin: string;
let manager: string;

beforeEach(async () => {
  await env.DB.batch([
    env.DB.prepare("DELETE FROM job_daily_requirements"),
    env.DB.prepare("DELETE FROM audit_log"),
    env.DB.prepare("DELETE FROM personnel"),
    env.DB.prepare("DELETE FROM users"),
    env.DB.prepare("DELETE FROM jobs"),
  ]);
  await provision("adm.a", "password123", "admin");
  await provision("mgr.mo", "password123", "manager");
  admin = await login("adm.a", "password123");
  manager = await login("mgr.mo", "password123");
  await seedJob("JOB-A");
  await seedJob("JOB-B");
  // The default querying manager is PLACED on JOB-A (the ownership-scope fixture).
  await seedPersonnel("Mo Manager", "mgr.mo", "JOB-A");
});

describe("daily requirements — admin CRUD + audit atomicity (W4)", () => {
  it("add → 201 with the new id, listed in seq order, and EXACTLY one audit row in the same batch", async () => {
    const r1 = await add(admin, "JOB-A", { kind: "confirm", label: "Badge in at the client gate", seq: 20 });
    expect(r1.status, await r1.clone().text()).toBe(201);
    const r2 = await add(admin, "JOB-A", { kind: "note", label: "Client requires FR clothing", seq: 10 });
    expect(r2.status).toBe(201);
    const items = await list(admin, "JOB-A");
    expect(items.map((i) => i.label)).toEqual(["Client requires FR clothing", "Badge in at the client gate"]);
    expect(items.map((i) => i.kind)).toEqual(["note", "confirm"]);
    expect(items.every((i) => i.form_code === null)).toBe(true);
    expect(await auditCount("daily_requirement_add")).toBe(2);
  });

  it("edit replaces every field (reorder = a seq edit) and audits once; an unknown id 404s with NO audit row", async () => {
    const created = (await (await add(admin, "JOB-A", { kind: "text", label: "Gate code", seq: 10 })).json()) as { id: number };
    const ok = await edit(admin, "JOB-A", created.id, { kind: "confirm", label: "Gate code obtained", seq: 30 });
    expect(ok.status, await ok.clone().text()).toBe(200);
    const items = await list(admin, "JOB-A");
    expect(items).toHaveLength(1);
    expect(items[0]).toMatchObject({ kind: "confirm", label: "Gate code obtained", seq: 30 });
    expect(await auditCount("daily_requirement_edit")).toBe(1);
    // Unknown id → 404 and the conditional audit (changes()=1) writes NOTHING.
    const missing = await edit(admin, "JOB-A", 99_999, { kind: "note", label: "x" });
    expect(missing.status).toBe(404);
    expect(await auditCount("daily_requirement_edit")).toBe(1);
  });

  it("an item is scoped to ITS job: editing/deactivating it through another job's route 404s (no cross-job write)", async () => {
    const created = (await (await add(admin, "JOB-A", { kind: "note", label: "A-only" })).json()) as { id: number };
    expect((await edit(admin, "JOB-B", created.id, { kind: "note", label: "hijack" })).status).toBe(404);
    expect((await deactivate(admin, "JOB-B", created.id)).status).toBe(404);
    expect((await list(admin, "JOB-A"))[0].label).toBe("A-only"); // untouched
  });

  it("deactivate soft-deletes (gone from the list), 404s on repeat, audits exactly once", async () => {
    const created = (await (await add(admin, "JOB-A", { kind: "note", label: "Old rule" })).json()) as { id: number };
    expect((await deactivate(admin, "JOB-A", created.id)).status).toBe(200);
    expect(await list(admin, "JOB-A")).toEqual([]);
    expect((await deactivate(admin, "JOB-A", created.id)).status).toBe(404); // already inactive
    expect(await auditCount("daily_requirement_deactivate")).toBe(1);
    // The row survives in D1 (soft delete — historical filed answers stay explainable).
    const row = await env.DB.prepare("SELECT active FROM job_daily_requirements WHERE id=?1").bind(created.id).first<{ active: number }>();
    expect(row!.active).toBe(0);
  });

  it("writes are cap.checklist.manage-gated: a manager (cap.tasks.own only) is 403 on all three", async () => {
    const created = (await (await add(admin, "JOB-A", { kind: "note", label: "seed" })).json()) as { id: number };
    expect((await add(manager, "JOB-A", { kind: "note", label: "nope" })).status).toBe(403);
    expect((await edit(manager, "JOB-A", created.id, { kind: "note", label: "nope" })).status).toBe(403);
    expect((await deactivate(manager, "JOB-A", created.id)).status).toBe(403);
    expect(await auditCount("daily_requirement_add")).toBe(1); // only the admin's
  });

  it("bounds: bad kind / empty / oversize label / negative seq / non-object body → 400; unknown job → 404", async () => {
    expect((await add(admin, "JOB-A", { kind: "checkbox", label: "x" })).status).toBe(400);
    expect((await add(admin, "JOB-A", { kind: "note", label: "" })).status).toBe(400);
    expect((await add(admin, "JOB-A", { kind: "note", label: "x".repeat(257) })).status).toBe(400);
    expect((await add(admin, "JOB-A", { kind: "note", label: "x", seq: -1 })).status).toBe(400);
    expect((await add(admin, "JOB-A", [1, 2])).status).toBe(400);
    expect((await add(admin, "JOB-NOPE", { kind: "note", label: "x" })).status).toBe(404);
  });
});

describe("daily requirements — form_link catalog validation", () => {
  it("a REAL catalog parent (jha) is accepted and stored", async () => {
    const res = await add(admin, "JOB-A", { kind: "form_link", label: "File the client JHA", form_code: "jha" });
    expect(res.status, await res.clone().text()).toBe(201);
    expect((await list(admin, "JOB-A"))[0].form_code).toBe("jha");
  });

  it("an unknown code → 422 unknown_form_code (a typo'd link would be dead)", async () => {
    const res = await add(admin, "JOB-A", { kind: "form_link", label: "x", form_code: "not-a-form" });
    expect(res.status).toBe(422);
    expect(((await res.json()) as { error: string }).error).toBe("unknown_form_code");
  });

  it("a launch:'daily-tab' parent (daily-report) → 422 daily_tab_form_code (circular link refused)", async () => {
    const res = await add(admin, "JOB-A", { kind: "form_link", label: "x", form_code: "daily-report" });
    expect(res.status).toBe(422);
    expect(((await res.json()) as { error: string }).error).toBe("daily_tab_form_code");
    // The EDIT route runs the same validation (shared parseRequirement).
    const created = (await (await add(admin, "JOB-A", { kind: "note", label: "seed" })).json()) as { id: number };
    const viaEdit = await edit(admin, "JOB-A", created.id, { kind: "form_link", label: "x", form_code: "daily-report" });
    expect(viaEdit.status).toBe(422);
  });

  it("a missing form_code on form_link → 400; form_code on a non-link kind is ignored (stored null)", async () => {
    expect((await add(admin, "JOB-A", { kind: "form_link", label: "x" })).status).toBe(400);
    const res = await add(admin, "JOB-A", { kind: "confirm", label: "x", form_code: "jha" });
    expect(res.status).toBe(201);
    expect((await list(admin, "JOB-A"))[0].form_code).toBeNull();
  });
});

describe("daily requirements — the tab read (ownership scope + shape)", () => {
  beforeEach(async () => {
    await add(admin, "JOB-A", { kind: "confirm", label: "Badge in", seq: 10 });
    await add(admin, "JOB-B", { kind: "note", label: "B-only rule", seq: 10 });
  });

  it("a manager placed on JOB-A reads JOB-A (200, active items, seq order) but is 403 forbidden_job for JOB-B", async () => {
    const items = await list(manager, "JOB-A");
    expect(items).toHaveLength(1);
    expect(items[0]).toMatchObject({ kind: "confirm", label: "Badge in", seq: 10, form_code: null });
    expect(typeof items[0].id).toBe("number");
    const res = await call(`/api/fieldops/daily-form/requirements?job_id=JOB-B`, { cookie: manager });
    expect(res.status).toBe(403);
    expect(((await res.json()) as { error: string }).error).toBe("forbidden_job");
  });

  it("an UNPLACED actor (no linked personnel) is 403 even for a real job", async () => {
    await provision("sam.sub", "password123", "submitter");
    const sub = await login("sam.sub", "password123");
    const res = await call(`/api/fieldops/daily-form/requirements?job_id=JOB-A`, { cookie: sub });
    expect(res.status).toBe(403);
  });

  it("an admin (cap.checklist.manage / cap.jobtracker.manage) may read ANY job", async () => {
    expect((await list(admin, "JOB-B"))[0].label).toBe("B-only rule");
  });

  it("401 unauthenticated; 404 unknown job; 400 oversize/absent job_id", async () => {
    expect((await call(`/api/fieldops/daily-form/requirements?job_id=JOB-A`)).status).toBe(401);
    expect((await call(`/api/fieldops/daily-form/requirements?job_id=JOB-NOPE`, { cookie: manager })).status).toBe(404);
    expect((await call(`/api/fieldops/daily-form/requirements?job_id=${"x".repeat(65)}`, { cookie: manager })).status).toBe(400);
    expect((await call(`/api/fieldops/daily-form/requirements`, { cookie: manager })).status).toBe(400);
  });

  it("deactivated items are excluded from the read (new renders only)", async () => {
    const items = await list(admin, "JOB-A");
    await deactivate(admin, "JOB-A", items[0].id);
    expect(await list(manager, "JOB-A")).toEqual([]);
  });
});
