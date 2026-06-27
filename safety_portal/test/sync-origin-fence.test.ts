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
});
