import { env, SELF } from "cloudflare:test";
import { describe, it, expect, beforeEach } from "vitest";

// ─────────────────────────────────────────────────────────────────────────────
// P2.3 Slice 2 — JOB WRITE (create/close/progress). cap.jobtracker.manage (admin-only).
// Locks the 0017 origin fence (portal-created jobs are origin='portal'/sync_state='pending'),
// TOCTOU-safe close, progress clamp, and mutation+audit atomicity.
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
  const res = await call("/api/internal/admin/users", { method: "POST", bearer: ADMIN_BEARER, body: JSON.stringify({ username, password, role }) });
  expect(res.status, await res.clone().text()).toBe(201);
}
async function login(username: string, password: string): Promise<string> {
  const res = await call("/api/login", { method: "POST", body: JSON.stringify({ username, password }) });
  expect(res.status, await res.clone().text()).toBe(200);
  return cookieFrom(res);
}
const j = (cookie: string, path: string, body?: unknown) =>
  call(path, { method: "POST", cookie, body: body === undefined ? undefined : JSON.stringify(body) });

async function jobRow(jobId: string) {
  return await env.DB.prepare("SELECT * FROM jobs WHERE job_id=?").bind(jobId).first<any>();
}
async function seedJob(jobId: string, status: string) {
  await env.DB.prepare("INSERT INTO jobs (job_id, project_name, active, status, created_at, origin) VALUES (?,?,?,?,?,?)")
    .bind(jobId, `P ${jobId}`, status === "closed" ? 0 : 1, status, 1_700_000_000, "smartsheet").run();
}
async function audits(action: string) {
  return ((await env.DB.prepare("SELECT * FROM audit_log WHERE action=?").bind(action).all()).results as any[]);
}

let admin: string, submitter: string;
beforeEach(async () => {
  await env.DB.batch([
    env.DB.prepare("DELETE FROM users"),
    env.DB.prepare("DELETE FROM audit_log"),
    env.DB.prepare("DELETE FROM jobs"),
    env.DB.prepare("DELETE FROM clients"),
  ]);
  await provision("admin.one", "password123", "admin");
  await provision("submitter.jim", "password123", "submitter");
  admin = await login("admin.one", "password123");
  submitter = await login("submitter.jim", "password123");
});

describe("POST /api/fieldops/job (create)", () => {
  it("gate: anon → 401, submitter (no manage cap) → 403, admin → 201", async () => {
    expect((await call("/api/fieldops/job", { method: "POST", body: JSON.stringify({ job_id: "JOB-X", project_name: "X" }) })).status).toBe(401);
    expect((await j(submitter, "/api/fieldops/job", { job_id: "JOB-X", project_name: "X" })).status).toBe(403);
    expect((await j(admin, "/api/fieldops/job", { job_id: "JOB-X", project_name: "X" })).status).toBe(201);
  });

  it("stamps the 0017 portal-origin fence + server created_at, and audits", async () => {
    expect((await j(admin, "/api/fieldops/job", { job_id: "job-new", project_name: "New Job", progress: 40 })).status).toBe(201);
    const row = await jobRow("JOB-NEW"); // job_id upper-cased
    expect(row.origin).toBe("portal");
    expect(row.sync_state).toBe("pending");
    expect(row.canonical_job_id).toBeNull();
    expect(row.active).toBe(1);
    expect(row.status).toBe("active");
    expect(row.progress).toBe(40);
    expect(row.created_at).toBeGreaterThan(1_000_000_000); // server unixepoch(), not the ALTER default 0
    expect(await audits("job_create")).toHaveLength(1);
  });

  it("inline new_client writes a clients row linked to the job", async () => {
    expect((await j(admin, "/api/fieldops/job", { job_id: "JOB-C", project_name: "C", new_client: { name: "Acme Co", phone: "555" } })).status).toBe(201);
    const row = await jobRow("JOB-C");
    const client = await env.DB.prepare("SELECT * FROM clients WHERE id=?").bind(row.client_id).first<any>();
    expect(client.name).toBe("Acme Co");
  });

  it("client_id is verified (422 unknown_client) / linked when valid", async () => {
    expect((await j(admin, "/api/fieldops/job", { job_id: "JOB-Z", project_name: "Z", client_id: 99999 })).status).toBe(422);
    await env.DB.prepare("INSERT INTO clients (name) VALUES ('Real')").run();
    const cid = (await env.DB.prepare("SELECT id FROM clients WHERE name='Real'").first<{ id: number }>())!.id;
    expect((await j(admin, "/api/fieldops/job", { job_id: "JOB-Y", project_name: "Y", client_id: cid })).status).toBe(201);
    expect((await jobRow("JOB-Y")).client_id).toBe(cid);
  });

  it("duplicate job_id → 409 job_exists", async () => {
    await seedJob("JOB-A", "active");
    expect((await j(admin, "/api/fieldops/job", { job_id: "JOB-A", project_name: "dup" })).status).toBe(409);
  });

  it("body guards: bad job_id / missing project_name → 400", async () => {
    expect((await j(admin, "/api/fieldops/job", { job_id: "JOB A", project_name: "X" })).status).toBe(400); // space
    expect((await j(admin, "/api/fieldops/job", { job_id: "JOB-X", project_name: "" })).status).toBe(400);
  });

  it("new_client over-long fields → 400 (every body string reaching D1 is bounded)", async () => {
    const res = await j(admin, "/api/fieldops/job", { job_id: "JOB-LONG", project_name: "X", new_client: { name: "Acme Co", email: "x".repeat(321) } });
    expect(res.status).toBe(400);
  });
});

describe("POST /api/fieldops/job/:job_id/close", () => {
  it("closes an active job (200), audits, and is no longer active", async () => {
    await seedJob("JOB-A", "active");
    expect((await j(admin, "/api/fieldops/job/JOB-A/close")).status).toBe(200);
    const row = await jobRow("JOB-A");
    expect(row.status).toBe("closed");
    expect(row.active).toBe(0);
    expect(await audits("job_close")).toHaveLength(1);
  });
  it("TOCTOU: unknown → 404, already-closed → 409 not_active, and neither writes an audit row", async () => {
    expect((await j(admin, "/api/fieldops/job/NOPE/close")).status).toBe(404);
    await seedJob("JOB-Z", "closed");
    const res = await j(admin, "/api/fieldops/job/JOB-Z/close");
    expect(res.status).toBe(409);
    expect((await res.json() as any).error).toBe("not_active");
    expect(await audits("job_close")).toHaveLength(0); // no-op writes no audit (changes()=1 guard)
  });
  it("submitter → 403", async () => {
    await seedJob("JOB-A", "active");
    expect((await j(submitter, "/api/fieldops/job/JOB-A/close")).status).toBe(403);
  });
});

describe("POST /api/fieldops/job/:job_id/progress", () => {
  it("clamps 0–100 and audits", async () => {
    await seedJob("JOB-A", "active");
    expect((await j(admin, "/api/fieldops/job/JOB-A/progress", { progress: 150 })).status).toBe(200);
    expect((await jobRow("JOB-A")).progress).toBe(100);
    expect(await audits("job_progress")).toHaveLength(1);
  });
  it("unknown job → 404; invalid body → 400; submitter → 403", async () => {
    expect((await j(admin, "/api/fieldops/job/NOPE/progress", { progress: 50 })).status).toBe(404);
    await seedJob("JOB-A", "active");
    expect((await j(admin, "/api/fieldops/job/JOB-A/progress", { progress: "high" })).status).toBe(400);
    expect((await j(submitter, "/api/fieldops/job/JOB-A/progress", { progress: 50 })).status).toBe(403);
  });
});
