import { env, SELF } from "cloudflare:test";
import { describe, it, expect, beforeEach } from "vitest";

// Migration 0017 split-brain fence. /api/internal/sync is the Smartsheet full-replace
// down-sync: it upserts the payload jobs and DEACTIVATES any active job absent from the
// payload. The fence scopes that deactivation to origin='smartsheet', so a portal-CREATED
// job (origin='portal') — which does not exist in ITS_Active_Jobs yet — is NEVER deactivated
// by the sync. This is the fix for the P0 risk-register split-brain finding.

const BASE = "https://portal.test";
const INTERNAL_BEARER = "test-internal-token"; // == PORTAL_INTERNAL_API_TOKEN (see admin.test.ts)

function call(path: string, init: RequestInit & { bearer?: string } = {}): Promise<Response> {
  const headers = new Headers(init.headers);
  if (init.bearer) headers.set("Authorization", `Bearer ${init.bearer}`);
  if (init.body && !headers.has("content-type")) headers.set("content-type", "application/json");
  return SELF.fetch(BASE + path, { ...init, headers });
}

beforeEach(async () => {
  await env.DB.prepare("DELETE FROM jobs").run();
});

describe("/api/internal/sync — origin fence (migration 0017)", () => {
  it("deactivates a smartsheet-origin absent job, but NEVER a portal-origin job", async () => {
    await env.DB.batch([
      // smartsheet-origin (the down-synced set) — absent from the payload below ⇒ should deactivate
      env.DB.prepare(
        "INSERT INTO jobs (job_id, project_name, active, origin) VALUES ('SS-1','Smartsheet Job',1,'smartsheet')",
      ),
      // portal-CREATED — absent from the payload, but the fence must keep it active
      env.DB.prepare(
        "INSERT INTO jobs (job_id, project_name, active, origin) VALUES ('PJOB-1','Portal Job',1,'portal')",
      ),
    ]);

    // The Smartsheet payload carries a DIFFERENT job; both SS-1 and PJOB-1 are absent from it.
    const res = await call("/api/internal/sync", {
      method: "POST",
      bearer: INTERNAL_BEARER,
      body: JSON.stringify({ jobs: [{ job_id: "SS-2", project_name: "Other Job", active: 1 }] }),
    });
    expect(res.status, await res.clone().text()).toBe(200);

    const ss1 = await env.DB.prepare("SELECT active FROM jobs WHERE job_id='SS-1'").first<{ active: number }>();
    const pjob = await env.DB.prepare("SELECT active FROM jobs WHERE job_id='PJOB-1'").first<{ active: number }>();
    expect(ss1?.active).toBe(0); // smartsheet-origin + absent ⇒ deactivated
    expect(pjob?.active).toBe(1); // portal-origin ⇒ fence keeps it active (the split-brain fix)
  });

  it("P2.5 canonical pre-pass: a promoted portal job's JOB-#### in the payload is NOT re-inserted as a duplicate", async () => {
    // A portal job already promoted: its D1 row is origin='portal' keyed by the typed id, with the
    // safety sheet's read-back JOB-99 in canonical_job_id. list_all_jobs() will push JOB-99.
    await env.DB.prepare(
      "INSERT INTO jobs (job_id, project_name, active, origin, sync_state, canonical_job_id) " +
        "VALUES ('PJOB-1','Promoted Portal Job',1,'portal','synced','JOB-99')",
    ).run();

    const res = await call("/api/internal/sync", {
      method: "POST",
      bearer: INTERNAL_BEARER,
      body: JSON.stringify({
        jobs: [
          { job_id: "JOB-99", project_name: "Promoted Portal Job", active: 1 }, // the canonical dup
          { job_id: "SS-2", project_name: "Real Smartsheet Job", active: 1 }, // a genuine smartsheet job
        ],
      }),
    });
    const out = (await res.json()) as { ok: boolean; upserted: number };
    expect(res.status).toBe(200);
    expect(out.upserted).toBe(1); // only SS-2 upserted; JOB-99 dropped by the pre-pass

    // No ghost origin='smartsheet' row was created for the canonical id.
    const ghost = await env.DB.prepare("SELECT COUNT(*) AS n FROM jobs WHERE job_id='JOB-99'").first<{ n: number }>();
    expect(ghost?.n).toBe(0); // the portal row (PJOB-1) carries JOB-99 as canonical, not as a row id
    // The portal row is untouched + still active (fence + pre-pass).
    const pjob = await env.DB.prepare("SELECT active, origin FROM jobs WHERE job_id='PJOB-1'").first<{ active: number; origin: string }>();
    expect(pjob?.active).toBe(1);
    expect(pjob?.origin).toBe("portal");
    // The genuine smartsheet job did upsert.
    const ss2 = await env.DB.prepare("SELECT project_name FROM jobs WHERE job_id='SS-2'").first<{ project_name: string }>();
    expect(ss2?.project_name).toBe("Real Smartsheet Job");
  });

  it("C1: stores the job address from the sync payload; a row that omits address defaults to ''", async () => {
    const res = await call("/api/internal/sync", {
      method: "POST",
      bearer: INTERNAL_BEARER,
      body: JSON.stringify({
        jobs: [
          { job_id: "SS-A", project_name: "Job A", active: 1, address: "100 Array Rd, Rockford IL" },
          { job_id: "SS-B", project_name: "Job B", active: 1 }, // address omitted → "" (older daemon)
        ],
      }),
    });
    expect(res.status, await res.clone().text()).toBe(200);
    const a = await env.DB.prepare("SELECT address FROM jobs WHERE job_id='SS-A'").first<{ address: string }>();
    const b = await env.DB.prepare("SELECT address FROM jobs WHERE job_id='SS-B'").first<{ address: string }>();
    expect(a?.address).toBe("100 Array Rd, Rockford IL");
    expect(b?.address).toBe("");
  });

  it("C1: an ON CONFLICT re-sync updates a smartsheet job's address", async () => {
    await env.DB.prepare(
      "INSERT INTO jobs (job_id, project_name, active, address, origin) VALUES ('SS-C','Job C',1,'old addr','smartsheet')",
    ).run();
    const res = await call("/api/internal/sync", {
      method: "POST",
      bearer: INTERNAL_BEARER,
      body: JSON.stringify({ jobs: [{ job_id: "SS-C", project_name: "Job C", active: 1, address: "new addr" }] }),
    });
    expect(res.status, await res.clone().text()).toBe(200);
    const c = await env.DB.prepare("SELECT address FROM jobs WHERE job_id='SS-C'").first<{ address: string }>();
    expect(c?.address).toBe("new addr");
  });

  it("C1: rejects the whole batch on an over-length address (invalid_row, >512)", async () => {
    const res = await call("/api/internal/sync", {
      method: "POST",
      bearer: INTERNAL_BEARER,
      body: JSON.stringify({ jobs: [{ job_id: "SS-A", project_name: "Job A", active: 1, address: "x".repeat(513) }] }),
    });
    expect(res.status).toBe(400);
    expect(await res.json()).toEqual({ error: "invalid_row" });
  });
});
