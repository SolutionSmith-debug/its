import { env } from "cloudflare:test";
import { describe, it, expect, beforeEach } from "vitest";
import { call } from "./helpers";

// P7 Slice 1 — the field-ops Hours Log up-sync queue (/api/internal/fieldops/hours-*). The Mac
// hours pass reads unmirrored crew time entries (hours-pending), find-or-creates the job's per-job
// "Hours Log" sheet + upserts/supersedes the row, then commits (hours-mark-mirrored: idempotent
// mirrored_at stamp). Same field-ops token privilege separation as the job-mirror queue.

const FIELDOPS_BEARER = "test-fieldops-token"; // == PORTAL_FIELDOPS_API_TOKEN (vitest.config.ts)
const INTERNAL_BEARER = "test-internal-token"; // portal_poll's token — must be REJECTED here
const ADMIN_BEARER = "test-admin-token"; // operator token — must be REJECTED here

async function seedJob(jobId: string, project = "Job One") {
  await env.DB.prepare(
    "INSERT INTO jobs (job_id, project_name, active, origin, sync_state) VALUES (?1,?2,1,'portal','synced')",
  )
    .bind(jobId, project)
    .run();
}

async function seedPersonnel(name: string): Promise<number> {
  const r = await env.DB.prepare("INSERT INTO personnel (name) VALUES (?1)").bind(name).run();
  return Number(r.meta.last_row_id);
}

async function seedEntry(uuid: string, jobId: string, over: Partial<Record<string, unknown>> = {}) {
  await env.DB.prepare(
    `INSERT INTO time_entries (uuid, job_id, personnel_id, work_started_at, work_ended_at, hours,
        notes, actor_username, amends_uuid, mirrored_at)
     VALUES (?1,?2,?3,?4,?5,?6,?7,?8,?9,?10)`,
  )
    .bind(
      uuid,
      jobId,
      (over.personnel_id as number) ?? null,
      (over.work_started_at as number) ?? 1751000000,
      (over.work_ended_at as number) ?? 1751028800,
      (over.hours as number) ?? 8,
      (over.notes as string) ?? "poured footings",
      (over.actor_username as string) ?? "alice",
      (over.amends_uuid as string) ?? null,
      (over.mirrored_at as number) ?? null,
    )
    .run();
}

beforeEach(async () => {
  await env.DB.prepare("DELETE FROM time_entries").run();
  await env.DB.prepare("DELETE FROM jobs").run();
  await env.DB.prepare("DELETE FROM personnel").run();
});

describe("GET /api/internal/fieldops/hours-pending", () => {
  it("rejects the portal_poll + admin tokens and no token (privilege separation)", async () => {
    expect((await call("/api/internal/fieldops/hours-pending")).status).toBe(401);
    expect((await call("/api/internal/fieldops/hours-pending", { bearer: INTERNAL_BEARER })).status).toBe(401);
    expect((await call("/api/internal/fieldops/hours-pending", { bearer: ADMIN_BEARER })).status).toBe(401);
    expect((await call("/api/internal/fieldops/hours-pending", { bearer: FIELDOPS_BEARER })).status).toBe(200);
  });

  it("returns unmirrored entries with project_name, display-name personnel, hours + amend link", async () => {
    await seedJob("J1", "Job One");
    const pid = await seedPersonnel("Alice Crew");
    await seedEntry("T1", "J1", { personnel_id: pid, amends_uuid: "T0" });
    const res = await call("/api/internal/fieldops/hours-pending", { bearer: FIELDOPS_BEARER });
    expect(res.status).toBe(200);
    const { entries } = (await res.json()) as { entries: Array<Record<string, unknown>> };
    expect(entries).toHaveLength(1);
    const e = entries[0];
    expect(e.uuid).toBe("T1");
    expect(e.project_name).toBe("Job One");
    expect(e.personnel_name).toBe("Alice Crew"); // DISPLAY name, never actor_username
    expect(e.hours).toBe(8);
    expect(e.amends_uuid).toBe("T0");
  });

  it("excludes already-mirrored entries (mirrored_at set)", async () => {
    await seedJob("J1");
    await seedEntry("T-DONE", "J1", { mirrored_at: 1751099999 });
    await seedEntry("T-PENDING", "J1");
    const res = await call("/api/internal/fieldops/hours-pending", { bearer: FIELDOPS_BEARER });
    const { entries } = (await res.json()) as { entries: Array<{ uuid: string }> };
    expect(entries.map((e) => e.uuid)).toEqual(["T-PENDING"]);
  });

  it("drops an entry whose job row is missing (INNER JOIN — cannot be foldered)", async () => {
    await seedEntry("T-ORPHAN", "J-GONE"); // no matching jobs row
    await seedJob("J1");
    await seedEntry("T-OK", "J1");
    const res = await call("/api/internal/fieldops/hours-pending", { bearer: FIELDOPS_BEARER });
    const { entries } = (await res.json()) as { entries: Array<{ uuid: string }> };
    expect(entries.map((e) => e.uuid)).toEqual(["T-OK"]);
  });
});

describe("POST /api/internal/fieldops/hours-mark-mirrored", () => {
  it("rejects the portal_poll + admin tokens (privilege separation)", async () => {
    const body = JSON.stringify({ uuids: ["T1"] });
    expect((await call("/api/internal/fieldops/hours-mark-mirrored", { method: "POST", body })).status).toBe(401);
    expect((await call("/api/internal/fieldops/hours-mark-mirrored", { method: "POST", bearer: INTERNAL_BEARER, body })).status).toBe(401);
    expect((await call("/api/internal/fieldops/hours-mark-mirrored", { method: "POST", bearer: ADMIN_BEARER, body })).status).toBe(401);
  });

  it("stamps mirrored_at for the given uuids (removing them from the pending set)", async () => {
    await seedJob("J1");
    await seedEntry("T1", "J1");
    await seedEntry("T2", "J1");
    const res = await call("/api/internal/fieldops/hours-mark-mirrored", {
      method: "POST",
      bearer: FIELDOPS_BEARER,
      body: JSON.stringify({ uuids: ["T1"] }),
    });
    expect(res.status, await res.clone().text()).toBe(200);
    expect(((await res.json()) as { updated: number }).updated).toBe(1);
    const row = await env.DB.prepare("SELECT mirrored_at FROM time_entries WHERE uuid='T1'").first<{ mirrored_at: number | null }>();
    expect(row?.mirrored_at).not.toBeNull();
    const still = await env.DB.prepare("SELECT mirrored_at FROM time_entries WHERE uuid='T2'").first<{ mirrored_at: number | null }>();
    expect(still?.mirrored_at).toBeNull();
  });

  it("is idempotent — a re-mark never regresses an already-stamped mirrored_at", async () => {
    await seedJob("J1");
    await seedEntry("T1", "J1", { mirrored_at: 1751000123 });
    await call("/api/internal/fieldops/hours-mark-mirrored", {
      method: "POST",
      bearer: FIELDOPS_BEARER,
      body: JSON.stringify({ uuids: ["T1"] }),
    });
    const row = await env.DB.prepare("SELECT mirrored_at FROM time_entries WHERE uuid='T1'").first<{ mirrored_at: number }>();
    expect(row?.mirrored_at).toBe(1751000123); // WHERE mirrored_at IS NULL → no-op
  });

  it("rejects a malformed body (empty / non-array / bad element / non-json)", async () => {
    const bad = (b: string) =>
      call("/api/internal/fieldops/hours-mark-mirrored", { method: "POST", bearer: FIELDOPS_BEARER, body: b });
    expect((await bad(JSON.stringify({ uuids: [] }))).status).toBe(400);
    expect((await bad(JSON.stringify({ uuids: "T1" }))).status).toBe(400);
    expect((await bad(JSON.stringify({ uuids: [123] }))).status).toBe(400);
    expect((await bad("not json")).status).toBe(400);
  });
});
