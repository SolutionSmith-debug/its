import { env, SELF } from "cloudflare:test";
import { describe, it, expect, beforeEach } from "vitest";

// ─────────────────────────────────────────────────────────────────────────────
// P6 — GET /api/internal/progress-rollup (bearer-gated, send-free D1 aggregation).
// Runs against the REAL worker with Miniflare D1. Aggregates the structured field-ops tables
// for one job over the Sat→Fri epoch window: labor hours (amend-collapsed), DISTINCT equipment
// on site (windowed on recorded_at), open-tasks count (status != 'done', NOT windowed). NO
// progress-% (dropped by operator decision). Materials → null.
// ─────────────────────────────────────────────────────────────────────────────

const BASE = "https://portal.test";
const INTERNAL_BEARER = "test-internal-token";

// A comfortable Sat→Fri window (epoch seconds). Events INSIDE fall in [FROM, TO).
const FROM = 1_700_000_000;
const TO = FROM + 7 * 86400;

type RollupBody = {
  job_id: string;
  window: { from: number; to: number };
  labor_hours: number;
  equipment: { name: string; kind: string | null }[];
  open_tasks: number;
  materials: null;
  generated_at: number;
};

async function rollupJson(res: Response): Promise<RollupBody> {
  return (await res.json()) as RollupBody;
}
async function errJson(res: Response): Promise<{ error: string }> {
  return (await res.json()) as { error: string };
}

function call(path: string, bearer?: string): Promise<Response> {
  const headers = new Headers();
  if (bearer) headers.set("Authorization", `Bearer ${bearer}`);
  return SELF.fetch(BASE + path, { headers });
}

async function seedJob(jobId: string): Promise<void> {
  await env.DB.prepare(
    "INSERT INTO jobs (job_id, project_name, active, status, progress, created_at) VALUES (?,?,?,?,?,?)",
  ).bind(jobId, "Prog Job", 1, "active", 40, 1_600_000_000).run();
}
async function seedEquipment(name: string, kind: string): Promise<number> {
  await env.DB.prepare("INSERT INTO equipment (name, kind, active) VALUES (?,?,1)").bind(name, kind).run();
  return (await env.DB.prepare("SELECT id FROM equipment WHERE name=?").bind(name).first<{ id: number }>())!.id;
}
async function seedEquipLoc(equipId: number, jobId: string | null, recordedAt: number): Promise<void> {
  await env.DB.prepare(
    "INSERT INTO equipment_location (equipment_id, job_id, recorded_at) VALUES (?,?,?)",
  ).bind(equipId, jobId, recordedAt).run();
}
async function seedTime(
  jobId: string, uuid: string, hours: number,
  opts: { workStartedAt?: number | null; createdAt?: number; amendsUuid?: string | null } = {},
): Promise<void> {
  await env.DB.prepare(
    "INSERT INTO time_entries (uuid, job_id, work_started_at, hours, created_at, actor_username, amends_uuid) VALUES (?,?,?,?,?,?,?)",
  ).bind(
    uuid, jobId, opts.workStartedAt ?? null, hours, opts.createdAt ?? FROM + 3600,
    "admin.one", opts.amendsUuid ?? null,
  ).run();
}
async function seedTask(jobId: string, description: string, status: string): Promise<void> {
  await env.DB.prepare(
    "INSERT INTO task_assignments (job_id, description, status, created_at) VALUES (?,?,?,?)",
  ).bind(jobId, description, status, FROM + 100).run();
}

describe("GET /api/internal/progress-rollup", () => {
  beforeEach(async () => {
    // Clean slate per test (Miniflare D1 persists across tests in a file).
    await env.DB.batch([
      env.DB.prepare("DELETE FROM time_entries"),
      env.DB.prepare("DELETE FROM equipment_location"),
      env.DB.prepare("DELETE FROM equipment"),
      env.DB.prepare("DELETE FROM task_assignments"),
      env.DB.prepare("DELETE FROM jobs"),
    ]);
  });

  // ── auth (fail-closed) ──────────────────────────────────────────────────────
  it("401s without the internal bearer", async () => {
    const res = await call(`/api/internal/progress-rollup?job_id=JOB-1&from=${FROM}&to=${TO}`);
    expect(res.status).toBe(401);
  });

  it("401s with a wrong bearer", async () => {
    const res = await call(`/api/internal/progress-rollup?job_id=JOB-1&from=${FROM}&to=${TO}`, "wrong");
    expect(res.status).toBe(401);
  });

  // ── param validation (reject the whole request) ─────────────────────────────
  it("400s on a missing job_id", async () => {
    const res = await call(`/api/internal/progress-rollup?from=${FROM}&to=${TO}`, INTERNAL_BEARER);
    expect(res.status).toBe(400);
    expect((await errJson(res)).error).toBe("invalid_job_id");
  });

  it("400s on an over-long job_id (>64)", async () => {
    const res = await call(
      `/api/internal/progress-rollup?job_id=${"x".repeat(65)}&from=${FROM}&to=${TO}`, INTERNAL_BEARER);
    expect(res.status).toBe(400);
  });

  it.each(["from=abc", "from=-1", "from=1.5", "from=1e3", `from=${FROM}`])(
    "400s on a non-integer / missing window param (%s)", async (fromParam) => {
      // Pair with a bad/missing `to` so at least one epoch is invalid.
      const res = await call(
        `/api/internal/progress-rollup?job_id=JOB-1&${fromParam}&to=notanint`, INTERNAL_BEARER);
      expect(res.status).toBe(400);
      expect((await errJson(res)).error).toBe("invalid_window");
    },
  );

  it("400s when to <= from", async () => {
    const res = await call(
      `/api/internal/progress-rollup?job_id=JOB-1&from=${TO}&to=${FROM}`, INTERNAL_BEARER);
    expect(res.status).toBe(400);
    expect((await errJson(res)).error).toBe("invalid_window");
  });

  // ── graceful zeros ──────────────────────────────────────────────────────────
  it("returns graceful zeros for a job with no field-ops activity", async () => {
    await seedJob("JOB-EMPTY");
    const res = await call(
      `/api/internal/progress-rollup?job_id=JOB-EMPTY&from=${FROM}&to=${TO}`, INTERNAL_BEARER);
    expect(res.status).toBe(200);
    const body = await rollupJson(res);
    expect(body).toMatchObject({
      job_id: "JOB-EMPTY",
      window: { from: FROM, to: TO },
      labor_hours: 0,
      equipment: [],
      open_tasks: 0,
      materials: null,
    });
    expect(typeof body.generated_at).toBe("number");
    // NO progress-% field (operator decision 2026-06-30).
    expect(body).not.toHaveProperty("progress_pct");
    expect(body).not.toHaveProperty("progress");
  });

  // ── labor: window + amend-collapse ──────────────────────────────────────────
  it("sums in-window hours and EXCLUDES amended (superseded) rows", async () => {
    await seedJob("JOB-1");
    await seedTime("JOB-1", "t1", 8, { workStartedAt: FROM + 1000 }); // in-window, kept
    await seedTime("JOB-1", "t2", 5, { workStartedAt: FROM + 2000 }); // original, AMENDED away
    await seedTime("JOB-1", "t2b", 6, { workStartedAt: FROM + 2100, amendsUuid: "t2" }); // the amendment
    const res = await call(
      `/api/internal/progress-rollup?job_id=JOB-1&from=${FROM}&to=${TO}`, INTERNAL_BEARER);
    const body = await rollupJson(res);
    // 8 (t1) + 6 (t2b, the surviving amendment) — the superseded t2 (5) is collapsed out.
    expect(body.labor_hours).toBe(14);
  });

  it("windows labor by work_started_at, created_at fallback", async () => {
    await seedJob("JOB-1");
    await seedTime("JOB-1", "before", 3, { workStartedAt: FROM - 10 }); // event before window → out
    await seedTime("JOB-1", "after", 4, { workStartedAt: TO + 10 }); // event after window → out
    await seedTime("JOB-1", "nostart", 7, { workStartedAt: null, createdAt: FROM + 50 }); // fallback in
    const res = await call(
      `/api/internal/progress-rollup?job_id=JOB-1&from=${FROM}&to=${TO}`, INTERNAL_BEARER);
    expect((await rollupJson(res)).labor_hours).toBe(7);
  });

  // ── equipment: DISTINCT + window + job scope ────────────────────────────────
  it("lists DISTINCT in-window equipment for the job only", async () => {
    await seedJob("JOB-1");
    const skid = await seedEquipment("Skid Steer 3", "skid-steer");
    const tele = await seedEquipment("Telehandler A", "telehandler");
    const other = await seedEquipment("Barge", "barge");
    await seedEquipLoc(skid, "JOB-1", FROM + 10);
    await seedEquipLoc(skid, "JOB-1", FROM + 20); // duplicate read → still ONE row (DISTINCT)
    await seedEquipLoc(tele, "JOB-1", FROM + 30);
    await seedEquipLoc(other, "JOB-1", FROM - 5); // out of window → excluded
    await seedEquipLoc(skid, "JOB-OTHER", FROM + 40); // other job → excluded
    const res = await call(
      `/api/internal/progress-rollup?job_id=JOB-1&from=${FROM}&to=${TO}`, INTERNAL_BEARER);
    const body = await rollupJson(res);
    expect(body.equipment).toEqual([
      { name: "Skid Steer 3", kind: "skid-steer" },
      { name: "Telehandler A", kind: "telehandler" },
    ]);
  });

  // ── open tasks: status != done, NOT windowed ────────────────────────────────
  it("counts only NOT-done tasks, regardless of window", async () => {
    await seedJob("JOB-1");
    await seedTask("JOB-1", "dig", "open");
    await seedTask("JOB-1", "weld", "in_progress");
    await seedTask("JOB-1", "paint", "done"); // done → not counted
    await seedTask("JOB-OTHER", "haul", "open"); // other job → not counted
    const res = await call(
      `/api/internal/progress-rollup?job_id=JOB-1&from=${FROM}&to=${TO}`, INTERNAL_BEARER);
    expect((await rollupJson(res)).open_tasks).toBe(2);
  });
});
