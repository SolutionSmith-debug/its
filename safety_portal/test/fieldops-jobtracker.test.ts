import { env, SELF } from "cloudflare:test";
import { describe, it, expect, beforeEach } from "vitest";

// ─────────────────────────────────────────────────────────────────────────────
// BRIEF C — Job Tracker tab (cap.jobtracker.read, SUBMITTER + ADMIN).
// Runs against the REAL worker with Miniflare D1; SELF.fetch cookie-forwarding.
// F5: the LIST filters by a validated `status` param (not a hard active=1 gate); the per-job
// DETAIL serves any status and 404s only a truly unknown job_id.
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

// ── seed helpers ──────────────────────────────────────────────────────────────
async function seedClient(name: string): Promise<number> {
  await env.DB.prepare("INSERT INTO clients (name, contact, phone, email) VALUES (?,?,?,?)")
    .bind(name, "Pat Contact", "555-0100", "pat@example.com").run();
  return (await env.DB.prepare("SELECT id FROM clients WHERE name=?").bind(name).first<{ id: number }>())!.id;
}
async function seedJob(jobId: string, projectName: string, status: string, progress = 0, clientId: number | null = null): Promise<void> {
  await env.DB.prepare(
    "INSERT INTO jobs (job_id, project_name, active, status, progress, client_id, created_at) VALUES (?,?,?,?,?,?,?)",
  ).bind(jobId, projectName, status === "closed" ? 0 : 1, status, progress, clientId, 1_700_000_000).run();
}
async function seedPersonnel(name: string, trade: string): Promise<number> {
  await env.DB.prepare("INSERT INTO personnel (name, username, trade, active) VALUES (?,?,?,1)")
    .bind(name, name.toLowerCase().replace(/\s+/g, "."), trade).run();
  return (await env.DB.prepare("SELECT id FROM personnel WHERE name=?").bind(name).first<{ id: number }>())!.id;
}
// Crew is the people PLACED on a job (personnel.current_job, migration 0023) — set placement here.
// This is what the crew legs now read (converged onto placement); NULL = unplaced (not on any crew).
async function placePersonnel(personnelId: number, jobId: string): Promise<void> {
  await env.DB.prepare("UPDATE personnel SET current_job = ? WHERE id = ?").bind(jobId, personnelId).run();
}
async function seedTask(jobId: string, personnelId: number | null, description: string, status: string, createdAt: number): Promise<void> {
  await env.DB.prepare(
    "INSERT INTO task_assignments (job_id, personnel_id, description, status, created_at) VALUES (?,?,?,?,?)",
  ).bind(jobId, personnelId, description, status, createdAt).run();
}
async function seedTimeEntry(jobId: string, personnelId: number, uuid: string, createdAt: number): Promise<void> {
  await env.DB.prepare(
    "INSERT INTO time_entries (uuid, job_id, personnel_id, work_started_at, work_ended_at, hours, notes, created_at, actor_username) VALUES (?,?,?,?,?,?,?,?,?)",
  ).bind(uuid, jobId, personnelId, createdAt - 3600, createdAt, 8, "note", createdAt, "admin.one").run();
}
async function seedEquipment(name: string): Promise<number> {
  await env.DB.prepare("INSERT INTO equipment (name, kind, identifier, active) VALUES (?,?,?,1)")
    .bind(name, "skid-steer", name.toUpperCase()).run();
  return (await env.DB.prepare("SELECT id FROM equipment WHERE name=?").bind(name).first<{ id: number }>())!.id;
}
async function seedLocation(equipmentId: number, jobId: string, recordedAt: number): Promise<void> {
  await env.DB.prepare(
    "INSERT INTO equipment_location (equipment_id, job_id, label, lat, lon, read_at, recorded_at) VALUES (?,?,?,?,?,?,?)",
  ).bind(equipmentId, jobId, "Site", 1.0, 2.0, recordedAt, recordedAt).run();
}
async function seedInspection(jobId: string, equipmentId: number, uuid: string, createdAt: number): Promise<void> {
  await env.DB.prepare(
    "INSERT INTO inspections (uuid, job_id, equipment_id, form_code, version, payload_json, performed_at, created_at, actor_username) VALUES (?,?,?,?,?,?,?,?,?)",
  ).bind(uuid, jobId, equipmentId, "skid-daily", 1, "{}", createdAt, createdAt, "admin.one").run();
}

beforeEach(async () => {
  // 0004 dev-seeds jobs; clear everything for deterministic status-filter assertions.
  await env.DB.batch([
    env.DB.prepare("DELETE FROM users"),
    env.DB.prepare("DELETE FROM task_assignments"),
    env.DB.prepare("DELETE FROM time_entries"),
    env.DB.prepare("DELETE FROM inspections"),
    env.DB.prepare("DELETE FROM equipment_location"),
    env.DB.prepare("DELETE FROM equipment"),
    env.DB.prepare("DELETE FROM personnel"),
    env.DB.prepare("DELETE FROM jobs"),
    env.DB.prepare("DELETE FROM clients"),
  ]);
});

// ── GET /api/fieldops/jobs (list) ───────────────────────────────────────────────
describe("GET /api/fieldops/jobs", () => {
  beforeEach(async () => {
    await provision("admin.one", "password123", "admin");
    await provision("submitter.jim", "password123", "submitter");
  });

  it("no session → 401", async () => {
    expect((await call("/api/fieldops/jobs")).status).toBe(401);
  });

  it("submitter is allowed (cap.jobtracker.read is submitter + admin) → 200", async () => {
    const c = await login("submitter.jim", "password123");
    expect((await call("/api/fieldops/jobs", { cookie: c })).status).toBe(200);
  });

  it("empty list when no jobs", async () => {
    const c = await login("admin.one", "password123");
    const res = await call("/api/fieldops/jobs", { cookie: c });
    expect(res.status).toBe(200);
    const body = (await res.json()) as { jobs: any[]; next_cursor: string | null };
    expect(body.jobs).toEqual([]);
    expect(body.next_cursor).toBeNull();
  });

  it("F5: ?status filters; active excludes closed, closed returns closed, all returns both", async () => {
    await seedJob("JOB-A", "Alpha", "active");
    await seedJob("JOB-Z", "Zulu", "closed");
    const c = await login("admin.one", "password123");

    const active = (await (await call("/api/fieldops/jobs?status=active", { cookie: c })).json()) as { jobs: any[] };
    expect(active.jobs.map((j) => j.job_id)).toEqual(["JOB-A"]);

    const closed = (await (await call("/api/fieldops/jobs?status=closed", { cookie: c })).json()) as { jobs: any[] };
    expect(closed.jobs.map((j) => j.job_id)).toEqual(["JOB-Z"]);

    const all = (await (await call("/api/fieldops/jobs?status=all", { cookie: c })).json()) as { jobs: any[] };
    expect(all.jobs.map((j) => j.job_id).sort()).toEqual(["JOB-A", "JOB-Z"]);
  });

  it("invalid status falls back to active (not 400)", async () => {
    await seedJob("JOB-A", "Alpha", "active");
    await seedJob("JOB-Z", "Zulu", "closed");
    const c = await login("admin.one", "password123");
    const res = await call("/api/fieldops/jobs?status=bogus", { cookie: c });
    expect(res.status).toBe(200);
    const body = (await res.json()) as { jobs: any[] };
    expect(body.jobs.map((j) => j.job_id)).toEqual(["JOB-A"]);
  });

  it("returns client_name + crew + open_tasks per job (open excludes done)", async () => {
    const clientId = await seedClient("Acme Co");
    await seedJob("JOB-A", "Alpha", "active", 40, clientId);
    const pid = await seedPersonnel("Alice Chen", "operator");
    await placePersonnel(pid, "JOB-A"); // crew = placed personnel
    await seedTask("JOB-A", pid, "Dig footings", "open", 100);
    await seedTask("JOB-A", pid, "Finished item", "done", 90);
    const c = await login("admin.one", "password123");
    const body = (await (await call("/api/fieldops/jobs?status=active", { cookie: c })).json()) as { jobs: any[] };
    const job = body.jobs.find((j) => j.job_id === "JOB-A");
    expect(job.client_name).toBe("Acme Co");
    expect(job.crew.map((p: any) => p.name)).toContain("Alice Chen");
    expect(job.open_tasks).toHaveLength(1); // 'done' excluded
    expect(job.open_tasks[0].description).toBe("Dig footings");
  });

  it("crew = PLACED personnel: a task-assigned-but-unplaced person is NOT crew (convergence)", async () => {
    await seedJob("JOB-A", "Alpha", "active");
    const placed = await seedPersonnel("Placed Pat", "operator");
    await placePersonnel(placed, "JOB-A"); // on the crew
    const assignedOnly = await seedPersonnel("Task Tom", "laborer");
    await seedTask("JOB-A", assignedOnly, "Dig footings", "open", 100); // task, but NOT placed
    const c = await login("admin.one", "password123");
    const body = (await (await call("/api/fieldops/jobs?status=active", { cookie: c })).json()) as { jobs: any[] };
    const job = body.jobs.find((j) => j.job_id === "JOB-A");
    const crewNames = job.crew.map((p: any) => p.name);
    expect(crewNames).toContain("Placed Pat");
    expect(crewNames).not.toContain("Task Tom"); // assigned a task but not placed → not crew
    // The task assignment is unaffected: it still surfaces as an open task with its assignee.
    expect(job.open_tasks.map((t: any) => t.personnel_name)).toContain("Task Tom");
  });

  it("crew excludes a retired (inactive) placement and scopes to the right job", async () => {
    await seedJob("JOB-A", "Alpha", "active");
    await seedJob("JOB-B", "Bravo", "active");
    const onA = await seedPersonnel("Anna A", "operator");
    await placePersonnel(onA, "JOB-A");
    const onB = await seedPersonnel("Bob B", "operator");
    await placePersonnel(onB, "JOB-B");
    const retired = await seedPersonnel("Gone Gwen", "operator");
    await placePersonnel(retired, "JOB-A");
    await env.DB.prepare("UPDATE personnel SET active = 0 WHERE id = ?").bind(retired).run();
    const c = await login("admin.one", "password123");
    const body = (await (await call("/api/fieldops/jobs?status=all", { cookie: c })).json()) as { jobs: any[] };
    const jobA = body.jobs.find((j) => j.job_id === "JOB-A");
    const jobB = body.jobs.find((j) => j.job_id === "JOB-B");
    expect(jobA.crew.map((p: any) => p.name)).toEqual(["Anna A"]); // Bob is JOB-B, Gwen is inactive
    expect(jobB.crew.map((p: any) => p.name)).toEqual(["Bob B"]);
  });

  it("keyset walks page 2 with no overlap", async () => {
    for (let i = 0; i < 75; i++) {
      await seedJob(`JOB-${String(i).padStart(3, "0")}`, `Project ${String(i).padStart(3, "0")}`, "active");
    }
    const c = await login("admin.one", "password123");
    let body = (await (await call("/api/fieldops/jobs?status=active&limit=50", { cookie: c })).json()) as { jobs: any[]; next_cursor: string };
    expect(body.jobs).toHaveLength(50);
    expect(body.next_cursor).not.toBeNull();
    const page1 = new Set(body.jobs.map((j) => j.job_id));
    const body2 = (await (await call(`/api/fieldops/jobs?status=active&limit=50&cursor=${body.next_cursor}`, { cookie: c })).json()) as { jobs: any[]; next_cursor: string | null };
    expect(body2.jobs).toHaveLength(25);
    for (const j of body2.jobs) expect(page1.has(j.job_id)).toBe(false);
    expect(body2.next_cursor).toBeNull();
  });

  it("hostile non-primitive cursor → first page (200), never 500", async () => {
    await seedJob("JOB-A", "Alpha", "active");
    const c = await login("admin.one", "password123");
    const hostile = btoa(JSON.stringify({ p: {}, j: [] })).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
    const res = await call(`/api/fieldops/jobs?cursor=${hostile}`, { cookie: c });
    expect(res.status, await res.clone().text()).toBe(200);
    expect(((await res.json()) as { jobs: any[] }).jobs.length).toBeGreaterThan(0);
  });
});

// ── GET /api/fieldops/jobs/:job_id (detail) ─────────────────────────────────────
describe("GET /api/fieldops/jobs/:job_id", () => {
  beforeEach(async () => {
    await provision("admin.one", "password123", "admin");
    await provision("submitter.jim", "password123", "submitter");
  });

  it("no session → 401", async () => {
    expect((await call("/api/fieldops/jobs/JOB-A")).status).toBe(401);
  });

  it("submitter is allowed → 200", async () => {
    await seedJob("JOB-A", "Alpha", "active");
    const c = await login("submitter.jim", "password123");
    expect((await call("/api/fieldops/jobs/JOB-A", { cookie: c })).status).toBe(200);
  });

  it("unknown job_id → 404", async () => {
    const c = await login("admin.one", "password123");
    expect((await call("/api/fieldops/jobs/NOPE", { cookie: c })).status).toBe(404);
  });

  it("F5: detail of a CLOSED job → 200 (not 404)", async () => {
    await seedJob("JOB-Z", "Zulu", "closed");
    const c = await login("admin.one", "password123");
    const res = await call("/api/fieldops/jobs/JOB-Z", { cookie: c });
    expect(res.status).toBe(200);
    const body = (await res.json()) as { job: any };
    expect(body.job.job_id).toBe("JOB-Z");
    expect(body.job.status).toBe("closed");
  });

  it("returns header + client + crew + tasks + time + inspections", async () => {
    const clientId = await seedClient("Acme Co");
    await seedJob("JOB-A", "Alpha", "active", 60, clientId);
    const pid = await seedPersonnel("Alice Chen", "operator");
    await placePersonnel(pid, "JOB-A"); // crew = placed personnel
    await seedTask("JOB-A", pid, "Dig", "open", 100);
    await seedTimeEntry("JOB-A", pid, "te-1", 200);
    const eq = await seedEquipment("unit-a");
    await seedInspection("JOB-A", eq, "in-1", 150);
    const c = await login("admin.one", "password123");
    const body = (await (await call("/api/fieldops/jobs/JOB-A", { cookie: c })).json()) as { job: any; cursors: any };
    expect(body.job.client.name).toBe("Acme Co");
    expect(body.job.crew.map((p: any) => p.name)).toContain("Alice Chen");
    expect(body.job.tasks).toHaveLength(1);
    expect(body.job.time_entries).toHaveLength(1);
    expect(body.job.time_entries[0].recorded_at).toBe(200); // created_at AS recorded_at
    expect(body.job.inspections).toHaveLength(1);
    expect(body.cursors).toHaveProperty("tasks");
  });

  it("detail crew = PLACED personnel: task-assigned-but-unplaced excluded (convergence)", async () => {
    await seedJob("JOB-A", "Alpha", "active");
    const placed = await seedPersonnel("Placed Pat", "operator");
    await placePersonnel(placed, "JOB-A");
    const assignedOnly = await seedPersonnel("Task Tom", "laborer");
    await seedTask("JOB-A", assignedOnly, "Dig", "open", 100); // task, not placed
    const c = await login("admin.one", "password123");
    const body = (await (await call("/api/fieldops/jobs/JOB-A", { cookie: c })).json()) as { job: any };
    const crewNames = body.job.crew.map((p: any) => p.name);
    expect(crewNames).toContain("Placed Pat");
    expect(crewNames).not.toContain("Task Tom");
    // Tom's task still shows in the tasks leg with his name.
    expect(body.job.tasks.map((t: any) => t.personnel_name)).toContain("Task Tom");
  });

  it("equipment-on-site: includes a unit whose LATEST location is this job, excludes one moved away", async () => {
    await seedJob("JOB-A", "Alpha", "active");
    await seedJob("JOB-B", "Bravo", "active");
    const here = await seedEquipment("here-unit");
    await seedLocation(here, "JOB-A", 100);
    await seedLocation(here, "JOB-A", 200); // latest on JOB-A
    const moved = await seedEquipment("moved-unit");
    await seedLocation(moved, "JOB-A", 50); // was on JOB-A
    await seedLocation(moved, "JOB-B", 300); // latest on JOB-B → excluded from JOB-A
    const c = await login("admin.one", "password123");
    const body = (await (await call("/api/fieldops/jobs/JOB-A", { cookie: c })).json()) as { job: any };
    const names = body.job.equipment_on_site.map((e: any) => e.name);
    expect(names).toContain("here-unit");
    expect(names).not.toContain("moved-unit");
  });

  it("time-entries leg keyset paginates without overlap", async () => {
    await seedJob("JOB-A", "Alpha", "active");
    const pid = await seedPersonnel("Alice Chen", "operator");
    for (let i = 0; i < 75; i++) await seedTimeEntry("JOB-A", pid, `te-${String(i).padStart(3, "0")}`, 1000 - i);
    const c = await login("admin.one", "password123");
    let body = (await (await call("/api/fieldops/jobs/JOB-A?limit=50", { cookie: c })).json()) as { job: any; cursors: any };
    expect(body.job.time_entries).toHaveLength(50);
    expect(body.cursors.time).not.toBeNull();
    const page1 = new Set(body.job.time_entries.map((t: any) => t.uuid));
    const body2 = (await (await call(`/api/fieldops/jobs/JOB-A?limit=50&time_cursor=${body.cursors.time}`, { cookie: c })).json()) as { job: any };
    expect(body2.job.time_entries).toHaveLength(25);
    for (const t of body2.job.time_entries) expect(page1.has(t.uuid)).toBe(false);
  });
});
