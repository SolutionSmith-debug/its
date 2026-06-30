import { env, SELF } from "cloudflare:test";
import { describe, it, expect, beforeEach } from "vitest";

// ─────────────────────────────────────────────────────────────────────────────
// P2.3 Slice 2 + P2.5 Slice 1/6 — JOB WRITE (create/close/progress/lifecycle/contacts).
// cap.jobtracker.manage (admin-only). Locks the 0017 origin fence (portal-created jobs are
// origin='portal'/sync_state='pending'), the version vector, the W5 cross-origin scope, and
// Slice 6 — the PORTAL ASSIGNS the canonical JOB-###### from the job_counter (the client no
// longer supplies a job_id; the server returns the assigned one + sets canonical_job_id == job_id).
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

// Slice 6: the portal assigns the Job ID. Create a job and return the SERVER-assigned JOB-######.
async function createOk(cookie: string, body: Record<string, unknown>): Promise<string> {
  const res = await j(cookie, "/api/fieldops/job", body);
  expect(res.status, await res.clone().text()).toBe(201);
  return ((await res.json()) as { job_id: string }).job_id;
}

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
    // Reset the Slice-6 allocator to its 0022 seed so each test's first create is JOB-000017.
    // CREATE IF NOT EXISTS + INSERT OR REPLACE self-heal even if a test DROPPED or emptied the
    // table (see the two counter_unavailable cases).
    env.DB.prepare("CREATE TABLE IF NOT EXISTS job_counter (id INTEGER PRIMARY KEY CHECK (id = 1), last_value INTEGER NOT NULL)"),
    env.DB.prepare("INSERT OR REPLACE INTO job_counter (id, last_value) VALUES (1, 16)"),
  ]);
  await provision("admin.one", "password123", "admin");
  await provision("submitter.jim", "password123", "submitter");
  admin = await login("admin.one", "password123");
  submitter = await login("submitter.jim", "password123");
});

describe("POST /api/fieldops/job (create)", () => {
  it("gate: anon → 401, submitter (no manage cap) → 403, admin → 201", async () => {
    expect((await call("/api/fieldops/job", { method: "POST", body: JSON.stringify({ project_name: "X" }) })).status).toBe(401);
    expect((await j(submitter, "/api/fieldops/job", { project_name: "X" })).status).toBe(403);
    expect((await j(admin, "/api/fieldops/job", { project_name: "X" })).status).toBe(201);
  });

  it("Slice 6: assigns sequential JOB-###### from the counter (seed 16 → first JOB-000017)", async () => {
    const a = await createOk(admin, { project_name: "A" });
    const b = await createOk(admin, { project_name: "B" });
    expect(a).toBe("JOB-000017");
    expect(b).toBe("JOB-000018");
  });

  it("Slice 6: ignores any client-supplied job_id — the portal assigns it", async () => {
    const id = await createOk(admin, { job_id: "CLIENT-CHOSEN", project_name: "X" });
    expect(id).toMatch(/^JOB-\d{6}$/); // server-assigned shape, not the client's string
    expect(await jobRow("CLIENT-CHOSEN")).toBeNull(); // the client's id never reaches D1
  });

  it("stamps the 0017 portal-origin fence + server created_at + canonical=job_id, and audits", async () => {
    const id = await createOk(admin, { project_name: "New Job", progress: 40 });
    expect(id).toMatch(/^JOB-\d{6}$/);
    const row = await jobRow(id);
    expect(row.origin).toBe("portal");
    expect(row.sync_state).toBe("pending");
    expect(row.canonical_job_id).toBe(id); // Slice 6: portal owns the number from birth (not NULL)
    expect(row.active).toBe(1);
    expect(row.status).toBe("active");
    expect(row.progress).toBe(40);
    expect(row.created_at).toBeGreaterThan(1_000_000_000); // server unixepoch(), not the ALTER default 0
    expect(await audits("job_create")).toHaveLength(1);
  });

  it("inline new_client writes a clients row linked to the job", async () => {
    const id = await createOk(admin, { project_name: "C", new_client: { name: "Acme Co", phone: "555" } });
    const row = await jobRow(id);
    const client = await env.DB.prepare("SELECT * FROM clients WHERE id=?").bind(row.client_id).first<any>();
    expect(client.name).toBe("Acme Co");
  });

  it("client_id is verified (422 unknown_client) / linked when valid", async () => {
    expect((await j(admin, "/api/fieldops/job", { project_name: "Z", client_id: 99999 })).status).toBe(422);
    await env.DB.prepare("INSERT INTO clients (name) VALUES ('Real')").run();
    const cid = (await env.DB.prepare("SELECT id FROM clients WHERE name='Real'").first<{ id: number }>())!.id;
    const id = await createOk(admin, { project_name: "Y", client_id: cid });
    expect((await jobRow(id)).client_id).toBe(cid);
  });

  it("body guard: missing project_name → 400 (no number is burned)", async () => {
    expect((await j(admin, "/api/fieldops/job", { project_name: "" })).status).toBe(400);
    expect((await j(admin, "/api/fieldops/job", {})).status).toBe(400);
    // A rejected create never advances the counter — the first valid create is still JOB-000017.
    expect(await createOk(admin, { project_name: "ok" })).toBe("JOB-000017");
  });

  it("new_client over-long fields → 400 (every body string reaching D1 is bounded)", async () => {
    const res = await j(admin, "/api/fieldops/job", { project_name: "X", new_client: { name: "Acme Co", email: "x".repeat(321) } });
    expect(res.status).toBe(400);
  });

  it("counter_unavailable → 500 fail-closed when the job_counter ROW is missing (no malformed id)", async () => {
    await env.DB.prepare("DELETE FROM job_counter").run(); // seed row gone, table present
    const res = await j(admin, "/api/fieldops/job", { project_name: "X" });
    expect(res.status).toBe(500);
    expect(((await res.json()) as any).error).toBe("counter_unavailable");
    expect(await env.DB.prepare("SELECT COUNT(*) AS n FROM jobs").first<{ n: number }>()).toMatchObject({ n: 0 });
    // beforeEach's INSERT OR REPLACE restores the row for the next test.
  });

  it("counter_unavailable → 500 when the job_counter TABLE is missing (0022 not applied before deploy)", async () => {
    // The literal deploy-order fault: D1 throws "no such table" — allocateJobNumber catches it and
    // collapses to the SAME clean counter_unavailable (not an opaque internal_error). Fail-closed.
    await env.DB.prepare("DROP TABLE job_counter").run();
    const res = await j(admin, "/api/fieldops/job", { project_name: "X" });
    expect(res.status).toBe(500);
    expect(((await res.json()) as any).error).toBe("counter_unavailable");
    expect(await env.DB.prepare("SELECT COUNT(*) AS n FROM jobs").first<{ n: number }>()).toMatchObject({ n: 0 });
    // beforeEach's CREATE TABLE IF NOT EXISTS + INSERT OR REPLACE restores it for later tests.
  });
});

describe("POST /api/fieldops/job/:job_id/close (P2.5: thin lifecycle='inactive' alias)", () => {
  it("closes an active PORTAL job (200) → lifecycle='inactive', active=0, status='closed', audits job_lifecycle", async () => {
    // /close is origin='portal'-scoped (W5), so the job must be portal-created (assigned id).
    const id = await createOk(admin, { project_name: "X" });
    expect((await j(admin, `/api/fieldops/job/${id}/close`)).status).toBe(200);
    const row = await jobRow(id);
    expect(row.lifecycle).toBe("inactive");
    expect(row.status).toBe("closed");
    expect(row.active).toBe(0);
    // P2.5: /close routes through the shared lifecycle setter → audits 'job_lifecycle' (not 'job_close').
    expect(await audits("job_lifecycle")).toHaveLength(1);
    expect(await audits("job_close")).toHaveLength(0);
  });
  it("unknown job → 404; an already-inactive portal job is an idempotent 200 (not active-guarded)", async () => {
    expect((await j(admin, "/api/fieldops/job/NOPE/close")).status).toBe(404);
    const id = await createOk(admin, { project_name: "X" });
    expect((await j(admin, `/api/fieldops/job/${id}/close`)).status).toBe(200); // first close
    // P2.5: the lifecycle model makes set-inactive idempotent — re-closing returns 200 (re-dirties
    // + bumps the mirror version), NOT the old 409 not_active (that active-guard was TOCTOU-era).
    const res = await j(admin, `/api/fieldops/job/${id}/close`);
    expect(res.status).toBe(200);
    const row = await jobRow(id);
    expect(row.lifecycle).toBe("inactive");
    expect(row.sync_state).toBe("pending");
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

// ─────────────────────────────────────────────────────────────────────────────
// P2.5 Slice 1 — SoR routing fields on create + the lifecycle / contacts routes with the mirror
// version-vector dirty-flag. (Slice 6: the client-supplied job_id is ignored; the server assigns it.)
// ─────────────────────────────────────────────────────────────────────────────
describe("P2.5 — SoR create + lifecycle + contacts (version vector)", () => {
  const FULL = {
    project_name: "Solar Ridge",
    address: "1 Solar Way",
    stakeholder_name: "Stake Holder",
    stakeholder_email: "stake@x.com",
    safety_contact_name: "Sam Safety",
    safety_contact_email: "safety@x.com",
    safety_cc: ["sc1@x.com", "sc2@x.com"],
    progress_contact_name: "Pat Progress",
    progress_contact_email: "prog@x.com",
    progress_cc: ["pc1@x.com"],
  };

  it("persists the full routing SoR + lifecycle='active' + mirror_version=1 + dirty", async () => {
    const id = await createOk(admin, FULL);
    const row = await jobRow(id);
    expect(row.address).toBe("1 Solar Way");
    expect(row.safety_contact_email).toBe("safety@x.com");
    expect(JSON.parse(row.safety_cc)).toEqual(["sc1@x.com", "sc2@x.com"]);
    expect(JSON.parse(row.progress_cc)).toEqual(["pc1@x.com"]);
    expect(row.lifecycle).toBe("active");
    expect(row.mirror_version).toBe(1);
    expect(row.sync_state).toBe("pending");
  });

  it("rejects a malformed CC (not email-shaped) and an over-cap CC array", async () => {
    expect((await j(admin, "/api/fieldops/job", { ...FULL, safety_cc: ["not-an-email"] })).status).toBe(400);
    expect((await j(admin, "/api/fieldops/job", { ...FULL, progress_cc: ["a@x.com", "b@x.com", "c@x.com", "d@x.com", "e@x.com", "f@x.com"] })).status).toBe(400);
  });

  it("/lifecycle sets lifecycle + derived active + bumps the mirror version + re-dirties", async () => {
    const id = await createOk(admin, FULL); // mirror_version=1, sync_state pending
    // Simulate the daemon having mirrored it clean, then change lifecycle.
    await env.DB.prepare("UPDATE jobs SET sync_state='synced', safety_mirrored_version=1, progress_mirrored_version=1 WHERE job_id=?").bind(id).run();
    const res = await j(admin, `/api/fieldops/job/${id}/lifecycle`, { lifecycle: "archived" });
    expect(res.status, await res.clone().text()).toBe(200);
    const row = await jobRow(id);
    expect(row.lifecycle).toBe("archived");
    expect(row.active).toBe(0); // only 'active' lifecycle keeps active=1
    expect(row.mirror_version).toBe(2); // bumped
    expect(row.sync_state).toBe("pending"); // re-dirtied for the daemon
    expect((await audits("job_lifecycle")).length).toBe(1);
  });

  it("/lifecycle rejects an invalid value; /close is a thin inactive alias", async () => {
    const id = await createOk(admin, FULL);
    expect((await j(admin, `/api/fieldops/job/${id}/lifecycle`, { lifecycle: "bogus" })).status).toBe(400);
    expect((await j(admin, `/api/fieldops/job/${id}/close`)).status).toBe(200);
    const row = await jobRow(id);
    expect(row.lifecycle).toBe("inactive");
    expect(row.active).toBe(0);
  });

  it("/contacts edits routing + bumps the mirror version (job_id/lifecycle untouched)", async () => {
    const id = await createOk(admin, FULL);
    await env.DB.prepare("UPDATE jobs SET sync_state='synced' WHERE job_id=?").bind(id).run();
    const res = await j(admin, `/api/fieldops/job/${id}/contacts`, { ...FULL, progress_contact_email: "newprog@x.com" });
    expect(res.status, await res.clone().text()).toBe(200);
    const row = await jobRow(id);
    expect(row.progress_contact_email).toBe("newprog@x.com");
    expect(row.lifecycle).toBe("active"); // untouched
    expect(row.mirror_version).toBe(2);
    expect(row.sync_state).toBe("pending");
  });

  it("/lifecycle + /contacts gate on cap.jobtracker.manage (submitter → 403)", async () => {
    const id = await createOk(admin, FULL);
    expect((await j(submitter, `/api/fieldops/job/${id}/lifecycle`, { lifecycle: "inactive" })).status).toBe(403);
    expect((await j(submitter, `/api/fieldops/job/${id}/contacts`, FULL)).status).toBe(403);
  });

  it("W5: edit routes REFUSE a smartsheet-origin job (no cross-origin corruption)", async () => {
    await seedJob("SS-9", "active"); // origin='smartsheet'
    expect((await j(admin, "/api/fieldops/job/SS-9/lifecycle", { lifecycle: "archived" })).status).toBe(404);
    expect((await j(admin, "/api/fieldops/job/SS-9/close")).status).toBe(404);
    expect((await j(admin, "/api/fieldops/job/SS-9/contacts", FULL)).status).toBe(404);
    const row = await jobRow("SS-9");
    expect(row.lifecycle).toBe("active"); // untouched (the origin='portal' scope refused every edit)
    expect(row.address).toBe("");
    expect(row.mirror_version).toBe(0);
  });

  it("W2: /lifecycle with a null JSON body → 400 (not a 500)", async () => {
    const id = await createOk(admin, FULL);
    expect((await j(admin, `/api/fieldops/job/${id}/lifecycle`, null)).status).toBe(400);
  });
});
