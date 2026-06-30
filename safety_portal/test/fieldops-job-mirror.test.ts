import { env, SELF } from "cloudflare:test";
import { describe, it, expect, beforeEach } from "vitest";

// P2.5 Slice 1 — the field-ops job-mirror queue (/api/internal/fieldops/*). The Mac-side mirror
// daemon reads dirty portal jobs (pending-jobs), find-or-creates a row in BOTH Active-Jobs sheets,
// then commits per-sheet (jobs-mark-mirrored: monotonic watermark advance + row-id cache +
// canonical writeback + the version-vector sync_state flip). These routes are gated by the
// daemon's OWN token (PORTAL_FIELDOPS_API_TOKEN), privilege-separated from the portal_poll +
// admin tokens.

const BASE = "https://portal.test";
const FIELDOPS_BEARER = "test-fieldops-token"; // == PORTAL_FIELDOPS_API_TOKEN (vitest.config.ts)
const INTERNAL_BEARER = "test-internal-token"; // portal_poll's token — must be REJECTED here
const ADMIN_BEARER = "test-admin-token"; // operator token — must be REJECTED here

function call(path: string, init: RequestInit & { bearer?: string } = {}): Promise<Response> {
  const headers = new Headers(init.headers);
  if (init.bearer) headers.set("Authorization", `Bearer ${init.bearer}`);
  if (init.body && !headers.has("content-type")) headers.set("content-type", "application/json");
  return SELF.fetch(BASE + path, { ...init, headers });
}

/** Seed a dirty portal job directly (the create route needs a session; the internal routes don't). */
async function seedPortalJob(jobId: string, over: Partial<Record<string, unknown>> = {}) {
  await env.DB.prepare(
    `INSERT INTO jobs (job_id, project_name, active, status, origin, sync_state, lifecycle,
       address, stakeholder_name, stakeholder_email, safety_contact_email, safety_cc,
       progress_contact_email, progress_cc, mirror_version, safety_mirrored_version, progress_mirrored_version)
     VALUES (?1, ?2, 1, 'active', 'portal', ?3, 'active',
       ?4, 'Stake Holder', 'stake@x.com', 'safety@x.com', '["sc1@x.com","sc2@x.com"]',
       'prog@x.com', '["pc1@x.com"]', ?5, ?6, ?7)`,
  )
    .bind(
      jobId,
      (over.project_name as string) ?? "Mirror Job",
      (over.sync_state as string) ?? "pending",
      (over.address as string) ?? "1 Solar Way",
      (over.mirror_version as number) ?? 1,
      (over.safety_mirrored_version as number) ?? 0,
      (over.progress_mirrored_version as number) ?? 0,
    )
    .run();
}

beforeEach(async () => {
  await env.DB.prepare("DELETE FROM jobs").run();
});

describe("GET /api/internal/fieldops/pending-jobs", () => {
  it("rejects the portal_poll + admin tokens and no token (privilege separation)", async () => {
    expect((await call("/api/internal/fieldops/pending-jobs")).status).toBe(401);
    expect((await call("/api/internal/fieldops/pending-jobs", { bearer: INTERNAL_BEARER })).status).toBe(401);
    expect((await call("/api/internal/fieldops/pending-jobs", { bearer: ADMIN_BEARER })).status).toBe(401);
    expect((await call("/api/internal/fieldops/pending-jobs", { bearer: FIELDOPS_BEARER })).status).toBe(200);
  });

  it("returns dirty portal jobs with the full SoR payload + parsed CC arrays + watermarks", async () => {
    await seedPortalJob("PJOB-1");
    const res = await call("/api/internal/fieldops/pending-jobs", { bearer: FIELDOPS_BEARER });
    expect(res.status).toBe(200);
    const { jobs } = (await res.json()) as { jobs: Array<Record<string, unknown>> };
    expect(jobs).toHaveLength(1);
    const j = jobs[0];
    expect(j.job_id).toBe("PJOB-1");
    expect(j.safety_contact_email).toBe("safety@x.com");
    expect(j.safety_cc).toEqual(["sc1@x.com", "sc2@x.com"]); // parsed JSON → string[]
    expect(j.progress_cc).toEqual(["pc1@x.com"]);
    expect(j.mirror_version).toBe(1);
    expect(j.safety_mirrored_version).toBe(0);
  });

  it("excludes synced portal jobs and smartsheet-origin jobs", async () => {
    await seedPortalJob("PJOB-DIRTY"); // pending
    await seedPortalJob("PJOB-CLEAN", { sync_state: "synced" }); // not dirty
    await env.DB.prepare(
      "INSERT INTO jobs (job_id, project_name, active, origin, sync_state) VALUES ('SS-1','SS',1,'smartsheet','pending')",
    ).run();
    const res = await call("/api/internal/fieldops/pending-jobs", { bearer: FIELDOPS_BEARER });
    const { jobs } = (await res.json()) as { jobs: Array<{ job_id: string }> };
    expect(jobs.map((j) => j.job_id)).toEqual(["PJOB-DIRTY"]);
  });
});

describe("POST /api/internal/fieldops/jobs-mark-mirrored", () => {
  it("rejects the portal_poll + admin tokens (privilege separation)", async () => {
    const body = JSON.stringify({ updates: [{ job_id: "X", sheet: "safety", mirrored_version: 1, row_id: 9 }] });
    expect((await call("/api/internal/fieldops/jobs-mark-mirrored", { method: "POST", body })).status).toBe(401);
    expect((await call("/api/internal/fieldops/jobs-mark-mirrored", { method: "POST", bearer: INTERNAL_BEARER, body })).status).toBe(401);
    expect((await call("/api/internal/fieldops/jobs-mark-mirrored", { method: "POST", bearer: ADMIN_BEARER, body })).status).toBe(401);
  });

  it("safety mirror advances the safety watermark + caches row_id + writes canonical, but stays pending until progress catches up", async () => {
    await seedPortalJob("PJOB-1"); // mirror_version=1, both watermarks 0
    const res = await call("/api/internal/fieldops/jobs-mark-mirrored", {
      method: "POST",
      bearer: FIELDOPS_BEARER,
      body: JSON.stringify({ updates: [{ job_id: "PJOB-1", sheet: "safety", mirrored_version: 1, row_id: 5001, canonical_job_id: "JOB-42" }] }),
    });
    expect(res.status, await res.clone().text()).toBe(200);
    const row = await env.DB.prepare(
      "SELECT safety_mirrored_version, progress_mirrored_version, safety_row_id, canonical_job_id, sync_state FROM jobs WHERE job_id='PJOB-1'",
    ).first<Record<string, unknown>>();
    expect(row?.safety_mirrored_version).toBe(1);
    expect(row?.progress_mirrored_version).toBe(0);
    expect(row?.safety_row_id).toBe(5001);
    expect(row?.canonical_job_id).toBe("JOB-42");
    expect(row?.sync_state).toBe("pending"); // progress still behind → version vector keeps it dirty
  });

  it("flips sync_state to synced only when BOTH sheets reach mirror_version", async () => {
    await seedPortalJob("PJOB-1", { safety_mirrored_version: 1 }); // safety already done; progress behind
    const res = await call("/api/internal/fieldops/jobs-mark-mirrored", {
      method: "POST",
      bearer: FIELDOPS_BEARER,
      body: JSON.stringify({ updates: [{ job_id: "PJOB-1", sheet: "progress", mirrored_version: 1, row_id: 7001 }] }),
    });
    expect(res.status).toBe(200);
    const row = await env.DB.prepare("SELECT sync_state, progress_row_id FROM jobs WHERE job_id='PJOB-1'").first<Record<string, unknown>>();
    expect(row?.progress_row_id).toBe(7001);
    expect(row?.sync_state).toBe("synced"); // both watermarks now >= mirror_version
  });

  it("advances the watermark MONOTONICALLY (a stale/replayed lower version never regresses it)", async () => {
    await seedPortalJob("PJOB-1", { mirror_version: 3, safety_mirrored_version: 3 });
    await call("/api/internal/fieldops/jobs-mark-mirrored", {
      method: "POST",
      bearer: FIELDOPS_BEARER,
      body: JSON.stringify({ updates: [{ job_id: "PJOB-1", sheet: "safety", mirrored_version: 1, row_id: 5001 }] }),
    });
    const row = await env.DB.prepare("SELECT safety_mirrored_version FROM jobs WHERE job_id='PJOB-1'").first<{ safety_mirrored_version: number }>();
    expect(row?.safety_mirrored_version).toBe(3); // MAX(3,1) — never regressed
  });

  it("rejects a malformed update (bad sheet / missing row_id)", async () => {
    await seedPortalJob("PJOB-1");
    const bad1 = await call("/api/internal/fieldops/jobs-mark-mirrored", {
      method: "POST", bearer: FIELDOPS_BEARER,
      body: JSON.stringify({ updates: [{ job_id: "PJOB-1", sheet: "bogus", mirrored_version: 1, row_id: 5 }] }),
    });
    expect(bad1.status).toBe(400);
    const bad2 = await call("/api/internal/fieldops/jobs-mark-mirrored", {
      method: "POST", bearer: FIELDOPS_BEARER,
      body: JSON.stringify({ updates: [{ job_id: "PJOB-1", sheet: "safety", mirrored_version: 1 }] }),
    });
    expect(bad2.status).toBe(400);
  });
});
