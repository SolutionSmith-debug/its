import { env, SELF } from "cloudflare:test";
import { describe, it, expect, beforeEach } from "vitest";

// ─────────────────────────────────────────────────────────────────────────────
// P2.3 Slice 1 — TIME-entry WRITE (cap.time.log, submitter + admin). Integrity-bar
// reference: server-authoritative timestamps, append-only edit chain, dual attribution,
// mutation+audit atomicity (W4). Real worker via cloudflare:test SELF.fetch + Miniflare D1.
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
async function provision(username: string, password: string, role: "submitter" | "admin"): Promise<void> {
  const res = await call("/api/internal/admin/users", {
    method: "POST",
    bearer: ADMIN_BEARER,
    body: JSON.stringify({ username, password, role }),
  });
  expect(res.status, await res.clone().text()).toBe(201);
}
async function login(username: string, password: string): Promise<string> {
  const res = await call("/api/login", { method: "POST", body: JSON.stringify({ username, password }) });
  expect(res.status, await res.clone().text()).toBe(200);
  return cookieFrom(res);
}
function post(cookie: string, body: unknown): Promise<Response> {
  return call("/api/fieldops/time-entry", { method: "POST", cookie, body: JSON.stringify(body) });
}

async function seedJob(jobId: string, status: string): Promise<void> {
  await env.DB.prepare("INSERT INTO jobs (job_id, project_name, active, status, created_at) VALUES (?,?,?,?,?)")
    .bind(jobId, `Project ${jobId}`, status === "closed" ? 0 : 1, status, 1_700_000_000)
    .run();
}
async function seedTask(jobId: string): Promise<number> {
  await env.DB.prepare("INSERT INTO task_assignments (job_id, description, status, created_at) VALUES (?,?,?,?)")
    .bind(jobId, "Dig", "open", 1_700_000_000)
    .run();
  return (await env.DB.prepare("SELECT id FROM task_assignments WHERE job_id=? ORDER BY id DESC LIMIT 1").bind(jobId).first<{ id: number }>())!.id;
}
async function rowsByUuid(uuid: string) {
  return (await env.DB.prepare("SELECT * FROM time_entries WHERE uuid=?").bind(uuid).all()).results as any[];
}
async function auditRows(action: string, uuidInDetail: string) {
  const all = (await env.DB.prepare("SELECT * FROM audit_log WHERE action=?").bind(action).all()).results as any[];
  return all.filter((r) => typeof r.detail === "string" && r.detail.includes(uuidInDetail));
}

beforeEach(async () => {
  await env.DB.batch([
    env.DB.prepare("DELETE FROM users"),
    env.DB.prepare("DELETE FROM audit_log"),
    env.DB.prepare("DELETE FROM time_entries"),
    env.DB.prepare("DELETE FROM task_assignments"),
    env.DB.prepare("DELETE FROM jobs"),
  ]);
  await provision("admin.one", "password123", "admin");
  await provision("submitter.jim", "password123", "submitter");
  await seedJob("JOB-A", "active");
});

describe("POST /api/fieldops/time-entry — gate", () => {
  it("no session → 401", async () => {
    const res = await call("/api/fieldops/time-entry", { method: "POST", body: JSON.stringify({ uuid: "t1", job_id: "JOB-A" }) });
    expect(res.status).toBe(401);
  });
  it("submitter has cap.time.log → 201", async () => {
    const c = await login("submitter.jim", "password123");
    const res = await post(c, { uuid: "t1", job_id: "JOB-A", hours: 8 });
    expect(res.status, await res.clone().text()).toBe(201);
  });
  it("admin → 201", async () => {
    const c = await login("admin.one", "password123");
    expect((await post(c, { uuid: "t2", job_id: "JOB-A" })).status).toBe(201);
  });
});

describe("POST /api/fieldops/time-entry — body guard", () => {
  it("malformed JSON → 400 bad_request", async () => {
    const c = await login("submitter.jim", "password123");
    const res = await call("/api/fieldops/time-entry", { method: "POST", cookie: c, body: "{not json", headers: { "content-type": "application/json" } });
    expect(res.status).toBe(400);
  });
  it("non-object body (array) → 400", async () => {
    const c = await login("submitter.jim", "password123");
    expect((await post(c, [1, 2])).status).toBe(400);
  });
  it("missing uuid → 400 invalid_uuid", async () => {
    const c = await login("submitter.jim", "password123");
    const res = await post(c, { job_id: "JOB-A" });
    expect(res.status).toBe(400);
    expect((await res.json() as any).error).toBe("invalid_uuid");
  });
  it("over-bound notes → 400 invalid_notes", async () => {
    const c = await login("submitter.jim", "password123");
    const res = await post(c, { uuid: "t1", job_id: "JOB-A", notes: "x".repeat(2001) });
    expect(res.status).toBe(400);
    expect((await res.json() as any).error).toBe("invalid_notes");
  });
});

describe("POST /api/fieldops/time-entry — referential", () => {
  it("unknown job_id → 422 unknown_job", async () => {
    const c = await login("submitter.jim", "password123");
    expect((await post(c, { uuid: "t1", job_id: "NOPE" })).status).toBe(422);
  });
  it("closed (inactive) job → 422 unknown_job", async () => {
    await seedJob("JOB-Z", "closed");
    const c = await login("submitter.jim", "password123");
    const res = await post(c, { uuid: "t1", job_id: "JOB-Z" });
    expect(res.status).toBe(422);
    expect((await res.json() as any).error).toBe("unknown_job");
  });
  it("task_id from a different job → 422 unknown_task", async () => {
    await seedJob("JOB-B", "active");
    const otherTask = await seedTask("JOB-B");
    const c = await login("submitter.jim", "password123");
    const res = await post(c, { uuid: "t1", job_id: "JOB-A", task_id: otherTask });
    expect(res.status).toBe(422);
    expect((await res.json() as any).error).toBe("unknown_task");
  });
});

describe("POST /api/fieldops/time-entry — integrity bar", () => {
  it("mutation + audit land atomically (W4): both rows exist with correct action/actor", async () => {
    const c = await login("submitter.jim", "password123");
    expect((await post(c, { uuid: "t1", job_id: "JOB-A", hours: 8 })).status).toBe(201);
    const rows = await rowsByUuid("t1");
    expect(rows).toHaveLength(1);
    expect(rows[0].actor_username).toBe("submitter.jim");
    expect(rows[0].submitted_as).toBe("submitter.jim"); // self-submit: equals actor
    const audits = await auditRows("time_entry_create", "t1");
    expect(audits).toHaveLength(1);
    expect(audits[0].actor_username).toBe("submitter.jim");
  });

  it("server-authoritative created_at: a forged body timestamp is ignored; event time stored verbatim", async () => {
    const c = await login("submitter.jim", "password123");
    await post(c, { uuid: "t1", job_id: "JOB-A", created_at: 100, edited_at: 100, work_started_at: 1700000000, work_ended_at: 1700003600 });
    const row = (await rowsByUuid("t1"))[0];
    expect(row.created_at).toBeGreaterThan(1_000_000_000); // server unixepoch(), not the forged 100
    expect(row.work_started_at).toBe(1700000000); // event claim stored verbatim
    expect(row.work_ended_at).toBe(1700003600);
  });

  it("append-only edit chain: an amend is a NEW row; the original is untouched", async () => {
    const c = await login("submitter.jim", "password123");
    await post(c, { uuid: "orig", job_id: "JOB-A", hours: 8, notes: "first" });
    const r1 = (await rowsByUuid("orig"))[0];
    const res = await post(c, { uuid: "amend", job_id: "JOB-A", hours: 9, notes: "corrected", amends_uuid: "orig" });
    expect(res.status).toBe(201);
    const orig = (await rowsByUuid("orig"))[0];
    expect(orig.hours).toBe(8); // original NEVER mutated
    expect(orig.notes).toBe("first");
    expect(orig.created_at).toBe(r1.created_at);
    const amend = (await rowsByUuid("amend"))[0];
    expect(amend.amends_uuid).toBe("orig");
    expect(amend.hours).toBe(9);
    expect(await auditRows("time_entry_edit", "amend")).toHaveLength(1); // amend logs an edit action
  });

  it("dual attribution: submit-as needs cap.submit_as + a real ENABLED, normalized target", async () => {
    // submitter lacks cap.submit_as → 403 (the cap check precedes the user lookup — no oracle)
    const sub = await login("submitter.jim", "password123");
    expect((await post(sub, { uuid: "t1", job_id: "JOB-A", submitted_as: "admin.one" })).status).toBe(403);

    const adm = await login("admin.one", "password123");
    // a phantom (well-formed but non-existent) target → 422; integrity bar rejects phantom attribution
    expect((await post(adm, { uuid: "t2", job_id: "JOB-A", submitted_as: "no.body" })).status).toBe(422);
    // a malformed username (no dot) → 400
    expect((await post(adm, { uuid: "t3", job_id: "JOB-A", submitted_as: "nodot" })).status).toBe(400);
    // a real enabled user (mixed-case) → 201, and the stored attribution is NORMALIZED
    expect((await post(adm, { uuid: "t4", job_id: "JOB-A", submitted_as: "Submitter.Jim" })).status).toBe(201);
    const row = (await rowsByUuid("t4"))[0];
    expect(row.actor_username).toBe("admin.one"); // the real actor
    expect(row.submitted_as).toBe("submitter.jim"); // normalized attributed account
  });

  it("uuid collision → 409 and the batch rolls back (no 2nd data row, no 2nd audit row)", async () => {
    const c = await login("submitter.jim", "password123");
    expect((await post(c, { uuid: "dup", job_id: "JOB-A", hours: 8 })).status).toBe(201);
    const res = await post(c, { uuid: "dup", job_id: "JOB-A", hours: 99 });
    expect(res.status).toBe(409);
    expect((await res.json() as any).error).toBe("uuid_conflict");
    expect(await rowsByUuid("dup")).toHaveLength(1); // INSERT rejected → still one row
    expect((await rowsByUuid("dup"))[0].hours).toBe(8); // the original, unchanged
    expect(await auditRows("time_entry_create", "dup")).toHaveLength(1); // audit rolled back with the failed INSERT
  });
});
