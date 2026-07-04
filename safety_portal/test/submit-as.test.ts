import { env, SELF } from "cloudflare:test";
import { describe, it, expect, beforeEach } from "vitest";

// ─────────────────────────────────────────────────────────────────────────────
// Admin dashboard (Phase 1) — submit-as ("filled out as") dual-attribution.
// Runs against the REAL worker in workerd with a Miniflare D1 (migrations applied
// by test/apply-migrations.ts, incl. 0008's actor_username/submitted_as columns).
// Same harness as admin.test.ts: SELF.fetch is stateless, so cookies are forwarded
// by hand to mirror a browser's same-origin session.
//
// What this locks:
//   - a submitter forging submitted_as is REJECTED 403 (server is the gate);
//   - an admin submit-as a valid enabled user records BOTH parties + an audit row;
//   - unknown / disabled attributed user → 422;
//   - a normal self-submit attributes to self and writes NO submit_as audit row;
//   - REGRESSION: the stored HMAC is byte-identical to the canonical-payload HMAC
//     (submit-as does NOT change signing) and /api/internal/pending returns the
//     FIXED column set WITHOUT actor_username/submitted_as (downstream unchanged).
// ─────────────────────────────────────────────────────────────────────────────

const BASE = "https://portal.test";
const ADMIN_BEARER = "test-admin-token"; // == PORTAL_ADMIN_API_TOKEN in vitest.config.ts
const INTERNAL_BEARER = "test-internal-token"; // == PORTAL_INTERNAL_API_TOKEN
const HMAC_SECRET = "test-hmac-payload-secret"; // == HMAC_PAYLOAD_SECRET in vitest.config.ts
const JOB = "JOB-SUBMITAS";

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

async function provision(username: string, password: string, role: "submitter" | "admin" = "submitter") {
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

function submitBody(extra: Record<string, unknown> = {}) {
  return JSON.stringify({
    job_id: JOB,
    form_code: "jha",
    work_date: "2026-06-08",
    submission_uuid: crypto.randomUUID(),
    values: { hazards: "none" },
    ...extra,
  });
}

/** Recompute the canonical HMAC exactly as the Worker does (mirror of
 *  worker/index.ts canonicalPayload + hmacHex), to assert submit-as did not change it. */
async function canonicalHmac(p: {
  submission_uuid: string; job_id: string; form_code: string; work_date: string; payload_json: string;
}): Promise<string> {
  const message = [p.submission_uuid, p.job_id, p.form_code, p.work_date, p.payload_json].join("\n");
  const key = await crypto.subtle.importKey(
    "raw", new TextEncoder().encode(HMAC_SECRET), { name: "HMAC", hash: "SHA-256" }, false, ["sign"],
  );
  const sig = await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(message));
  return [...new Uint8Array(sig)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

// Clean slate + a known active job before each test.
beforeEach(async () => {
  await env.DB.batch([
    env.DB.prepare("DELETE FROM users"),
    env.DB.prepare("DELETE FROM audit_log"),
    env.DB.prepare("DELETE FROM submissions"),
    env.DB.prepare("INSERT OR REPLACE INTO jobs (job_id, project_name, active) VALUES (?,?,1)").bind(JOB, "Submit-As Test"),
  ]);
});

describe("submit-as — submitter forging submitted_as", () => {
  it("a submitter forging submitted_as is rejected 403, with NO attributed row", async () => {
    await provision("pm.bob", "password123", "submitter");
    await provision("pm.carol", "password123", "submitter");
    const bob = await login("pm.bob", "password123");

    const res = await call("/api/submit", {
      method: "POST",
      cookie: bob,
      body: submitBody({ submitted_as: "pm.carol" }),
    });
    expect(res.status).toBe(403);
    expect(await res.json()).toMatchObject({ error: "forbidden" });

    // Nothing was written attributed to the target (the whole request was rejected).
    const row = await env.DB
      .prepare("SELECT COUNT(*) AS n FROM submissions WHERE submitted_as='pm.carol'")
      .first<{ n: number }>();
    expect(row?.n).toBe(0);
    // And no submissions row was created at all.
    const all = await env.DB.prepare("SELECT COUNT(*) AS n FROM submissions").first<{ n: number }>();
    expect(all?.n).toBe(0);
  });
});

describe("submit M1 — uuid overwrite guard (PR-4)", () => {
  const FIXED = "11111111-1111-1111-1111-111111111111";
  it("a DIFFERENT actor reusing a uuid → 409 uuid_conflict; the prior row is intact", async () => {
    await provision("pm.bob", "password123", "submitter");
    await provision("pm.carol", "password123", "submitter");
    const bob = await login("pm.bob", "password123");
    const carol = await login("pm.carol", "password123");
    expect((await call("/api/submit", { method: "POST", cookie: bob, body: submitBody({ submission_uuid: FIXED }) })).status).toBe(200);
    const res = await call("/api/submit", { method: "POST", cookie: carol, body: submitBody({ submission_uuid: FIXED }) });
    expect(res.status).toBe(409);
    expect(await res.json()).toMatchObject({ error: "uuid_conflict" });
    const row = await env.DB.prepare("SELECT actor_username FROM submissions WHERE submission_uuid=?").bind(FIXED).first<{ actor_username: string }>();
    expect(row!.actor_username).toBe("pm.bob");
  });
  it("a same-actor re-submit with CHANGED values writes a submission_replace audit", async () => {
    await provision("pm.bob", "password123", "submitter");
    const bob = await login("pm.bob", "password123");
    await call("/api/submit", { method: "POST", cookie: bob, body: submitBody({ submission_uuid: FIXED, values: { hazards: "one" } }) });
    await call("/api/submit", { method: "POST", cookie: bob, body: submitBody({ submission_uuid: FIXED, values: { hazards: "TWO" } }) });
    const n = (await env.DB.prepare("SELECT COUNT(*) n FROM audit_log WHERE action='submission_replace'").first<{ n: number }>())!.n;
    expect(n).toBe(1);
  });
});

describe("submit-as — admin attribution", () => {
  beforeEach(async () => {
    await provision("admin.one", "password123", "admin");
    await provision("pm.bob", "password123", "submitter");
  });

  it("admin submit-as a valid enabled user: 200, dual-attribution row + submit_as audit", async () => {
    const admin = await login("admin.one", "password123");
    const res = await call("/api/submit", {
      method: "POST",
      cookie: admin,
      body: submitBody({ submitted_as: "pm.bob" }),
    });
    expect(res.status, await res.clone().text()).toBe(200);

    const row = await env.DB
      .prepare("SELECT actor_username, submitted_as FROM submissions LIMIT 1")
      .first<{ actor_username: string; submitted_as: string }>();
    expect(row?.actor_username).toBe("admin.one");
    expect(row?.submitted_as).toBe("pm.bob");

    const audit = await env.DB
      .prepare("SELECT actor_username, action, target_username, detail FROM audit_log WHERE action='submit_as'")
      .first<{ actor_username: string; action: string; target_username: string; detail: string }>();
    expect(audit?.actor_username).toBe("admin.one");
    expect(audit?.target_username).toBe("pm.bob");
    expect(JSON.parse(audit!.detail)).toMatchObject({ job_id: JOB });
    expect(JSON.parse(audit!.detail).submission_uuid).toBeTruthy();
  });

  it("admin submit-as an UNKNOWN user → 422, nothing written", async () => {
    const admin = await login("admin.one", "password123");
    const res = await call("/api/submit", {
      method: "POST",
      cookie: admin,
      body: submitBody({ submitted_as: "no.body" }),
    });
    expect(res.status).toBe(422);
    expect(await res.json()).toMatchObject({ error: "unknown_attributed_user" });
    const all = await env.DB.prepare("SELECT COUNT(*) AS n FROM submissions").first<{ n: number }>();
    expect(all?.n).toBe(0);
  });

  it("admin submit-as a DISABLED user → 422", async () => {
    // Disable pm.bob via the operator route.
    await call("/api/internal/admin/users/disable", {
      method: "POST",
      bearer: ADMIN_BEARER,
      body: JSON.stringify({ username: "pm.bob" }),
    });
    const admin = await login("admin.one", "password123");
    const res = await call("/api/submit", {
      method: "POST",
      cookie: admin,
      body: submitBody({ submitted_as: "pm.bob" }),
    });
    expect(res.status).toBe(422);
    expect(await res.json()).toMatchObject({ error: "unknown_attributed_user" });
  });

  it("admin submit-as a malformed username → 422 (normalizeUsername rejects)", async () => {
    const admin = await login("admin.one", "password123");
    const res = await call("/api/submit", {
      method: "POST",
      cookie: admin,
      body: submitBody({ submitted_as: "nodothere" }),
    });
    // !== actor (an admin), so it's treated as submit-as → normalize fails → 422.
    expect(res.status).toBe(422);
    expect(await res.json()).toMatchObject({ error: "unknown_attributed_user" });
  });
});

describe("submit-as — normal self-submit (no impersonation)", () => {
  it("self-submit (no submitted_as): 200, actor == submitted_as == self, NO submit_as audit", async () => {
    await provision("admin.one", "password123", "admin");
    const admin = await login("admin.one", "password123");
    const res = await call("/api/submit", { method: "POST", cookie: admin, body: submitBody() });
    expect(res.status).toBe(200);

    const row = await env.DB
      .prepare("SELECT actor_username, submitted_as FROM submissions LIMIT 1")
      .first<{ actor_username: string; submitted_as: string }>();
    expect(row?.actor_username).toBe("admin.one");
    expect(row?.submitted_as).toBe("admin.one");

    const audit = await env.DB
      .prepare("SELECT COUNT(*) AS n FROM audit_log WHERE action='submit_as'")
      .first<{ n: number }>();
    expect(audit?.n).toBe(0);
  });

  it("submitted_as === self is a normal self-submit (no 403, no audit)", async () => {
    await provision("pm.bob", "password123", "submitter");
    const bob = await login("pm.bob", "password123");
    const res = await call("/api/submit", {
      method: "POST",
      cookie: bob,
      body: submitBody({ submitted_as: "pm.bob" }),
    });
    expect(res.status).toBe(200);
    const row = await env.DB
      .prepare("SELECT actor_username, submitted_as FROM submissions LIMIT 1")
      .first<{ actor_username: string; submitted_as: string }>();
    expect(row?.actor_username).toBe("pm.bob");
    expect(row?.submitted_as).toBe("pm.bob");
    const audit = await env.DB
      .prepare("SELECT COUNT(*) AS n FROM audit_log WHERE action='submit_as'")
      .first<{ n: number }>();
    expect(audit?.n).toBe(0);
  });
});

describe("submit-as — downstream is byte-unchanged (regression)", () => {
  it("stored hmac for a submit-as equals the canonical-payload HMAC (signing unchanged)", async () => {
    await provision("admin.one", "password123", "admin");
    await provision("pm.bob", "password123", "submitter");
    const admin = await login("admin.one", "password123");
    const uuid = crypto.randomUUID();
    const res = await call("/api/submit", {
      method: "POST",
      cookie: admin,
      body: submitBody({ submission_uuid: uuid, submitted_as: "pm.bob" }),
    });
    expect(res.status).toBe(200);

    const row = await env.DB
      .prepare("SELECT submission_uuid, job_id, form_code, work_date, payload_json, hmac FROM submissions WHERE submission_uuid=?")
      .bind(uuid)
      .first<{ submission_uuid: string; job_id: string; form_code: string; work_date: string; payload_json: string; hmac: string }>();
    const expected = await canonicalHmac({
      submission_uuid: row!.submission_uuid,
      job_id: row!.job_id,
      form_code: row!.form_code,
      work_date: row!.work_date,
      payload_json: row!.payload_json,
    });
    // The attribution columns are NOT part of the canonical payload, so the HMAC is
    // identical to a normal submit — portal_poll's recompute still verifies.
    expect(row!.hmac).toBe(expected);
  });

  it("/api/internal/pending returns the FIXED column set WITHOUT actor_username/submitted_as", async () => {
    await provision("admin.one", "password123", "admin");
    await provision("pm.bob", "password123", "submitter");
    const admin = await login("admin.one", "password123");
    await call("/api/submit", {
      method: "POST",
      cookie: admin,
      body: submitBody({ submitted_as: "pm.bob" }),
    });

    const res = await call("/api/internal/pending", { bearer: INTERNAL_BEARER });
    expect(res.status).toBe(200);
    const { pending } = (await res.json()) as { pending: Record<string, unknown>[] };
    expect(pending.length).toBe(1);
    // The exact column set the Mac-side portal_poll daemon consumes. `daily_photos`
    // is the DR-photo-pool Slice-2 claim manifest (server-resolved, NOT HMAC-covered
    // — intake consumes it only for HMAC-covered refs); everything else unchanged.
    expect(Object.keys(pending[0]).sort()).toEqual(
      ["amends_uuid", "created_at", "daily_photos", "form_code", "hmac", "job_id", "payload_json", "submission_uuid", "work_date"].sort(),
    );
    // The attribution columns must NOT leak into the downstream payload.
    expect(pending[0]).not.toHaveProperty("actor_username");
    expect(pending[0]).not.toHaveProperty("submitted_as");
  });
});
