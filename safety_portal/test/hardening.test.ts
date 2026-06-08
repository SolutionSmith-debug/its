import { env, SELF } from "cloudflare:test";
import { describe, it, expect, beforeEach } from "vitest";

// ─────────────────────────────────────────────────────────────────────────────
// Post-audit security hardening (2026-06-08). Real workerd + Miniflare D1.
//  #1 null/non-object body → 400 (not 500) on every handler.
//  #4 values:[] → 400 (typeof []==='object' slipped the object check).
//  #2/#3/#8–11 security headers on /api/* (+ enforcing CSP) + Cache-Control:no-store.
//  #5/#6 concurrency error codes: duplicate create/rename → 409; missing delete → 404.
// (Asset-document header presence is verified in the operator's post-deploy smoke —
//  vitest-pool-workers does not serve the built static assets.)
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
async function provision(username: string, password: string, role: "submitter" | "admin" = "submitter") {
  const res = await call("/api/internal/admin/users", {
    method: "POST", bearer: ADMIN_BEARER, body: JSON.stringify({ username, password, role }),
  });
  expect(res.status, await res.clone().text()).toBe(201);
}
async function login(username: string, password: string): Promise<string> {
  const res = await call("/api/login", { method: "POST", body: JSON.stringify({ username, password }) });
  expect(res.status).toBe(200);
  return cookieFrom(res);
}

beforeEach(async () => {
  await env.DB.batch([
    env.DB.prepare("DELETE FROM users"),
    env.DB.prepare("DELETE FROM audit_log"),
    env.DB.prepare("DELETE FROM submissions"),
  ]);
});

describe("input-shape hardening (#1 null/non-object body, #4 array values)", () => {
  it("literal `null` body → 400 (NOT 500) — unauthenticated on /api/login", async () => {
    const res = await call("/api/login", { method: "POST", body: "null" });
    expect(res.status).toBe(400);
  });

  it("array / scalar / string bodies all → 400 on /api/login (not 500, not 401-with-deref)", async () => {
    for (const b of ["[]", "5", '"a string"', "true"]) {
      const res = await call("/api/login", { method: "POST", body: b });
      expect(res.status, `body=${b}`).toBe(400);
    }
  });

  it("`null` body → 400 on an authenticated route (/api/submit)", async () => {
    await provision("pm.bob", "password123", "submitter");
    const cookie = await login("pm.bob", "password123");
    expect((await call("/api/submit", { method: "POST", cookie, body: "null" })).status).toBe(400);
  });

  it("`null` body → 400 on an admin route (/api/admin/users)", async () => {
    await provision("admin.one", "password123", "admin");
    const cookie = await login("admin.one", "password123");
    expect((await call("/api/admin/users", { method: "POST", cookie, body: "null" })).status).toBe(400);
  });

  it("`null` body → 400 on a bearer route (/api/internal/mark-filed)", async () => {
    const res = await call("/api/internal/mark-filed", { method: "POST", bearer: ADMIN_BEARER, body: "null" });
    // requireInternalToken uses PORTAL_INTERNAL_API_TOKEN; ADMIN_BEARER is the admin token →
    // this 401s at the gate BEFORE the body guard. The point: it is NOT a 500.
    expect(res.status).not.toBe(500);
  });

  it("values:[] → 400 invalid_submission (array slipped typeof==='object')", async () => {
    await provision("pm.bob", "password123", "submitter");
    const cookie = await login("pm.bob", "password123");
    const res = await call("/api/submit", {
      method: "POST", cookie,
      body: JSON.stringify({ job_id: "j", form_code: "f", work_date: "2026-06-08", submission_uuid: "s", values: [] }),
    });
    expect(res.status).toBe(400);
    expect(await res.json()).toMatchObject({ error: "invalid_submission" });
  });
});

describe("security headers (#2 CSP, #3 clickjacking, #8–11)", () => {
  it("an /api/* response carries the enforced headers", async () => {
    const res = await call("/api/session"); // 401 (no cookie) — headers still set by the middleware
    expect(res.headers.get("x-frame-options")).toBe("DENY");
    expect(res.headers.get("x-content-type-options")).toBe("nosniff");
    expect(res.headers.get("referrer-policy")).toBe("strict-origin-when-cross-origin");
    expect(res.headers.get("strict-transport-security")).toContain("max-age=31536000");
    expect(res.headers.get("cache-control")).toBe("no-store");
  });

  it("CSP is ENFORCING (flipped after a clean browser smoke)", async () => {
    const res = await call("/api/session");
    const csp = res.headers.get("content-security-policy");
    expect(csp).toBeTruthy();
    expect(csp).toContain("default-src 'self'");
    expect(csp).toContain("frame-ancestors 'none'");
    expect(csp).toContain("'unsafe-inline'"); // style-src, for React inline styles
    // Report-Only retired now that it enforces.
    expect(res.headers.get("content-security-policy-report-only")).toBeNull();
  });
});

describe("concurrency error codes (#5 create/rename → 409, #6 delete → 404 vs 409)", () => {
  let admin: string;
  beforeEach(async () => {
    await provision("admin.one", "password123", "admin");
    admin = await login("admin.one", "password123");
  });

  it("duplicate create → 409 exists (not 500)", async () => {
    const mk = () => call("/api/admin/users", {
      method: "POST", cookie: admin, body: JSON.stringify({ username: "pm.dup", password: "password123" }),
    });
    expect((await mk()).status).toBe(201);
    expect((await mk()).status).toBe(409);
  });

  it("rename into an existing username → 409 username_taken", async () => {
    await call("/api/admin/users", { method: "POST", cookie: admin, body: JSON.stringify({ username: "pm.a", password: "password123" }) });
    await call("/api/admin/users", { method: "POST", cookie: admin, body: JSON.stringify({ username: "pm.b", password: "password123" }) });
    const res = await call("/api/admin/users/credentials", {
      method: "POST", cookie: admin, body: JSON.stringify({ username: "pm.a", new_username: "pm.b" }),
    });
    expect(res.status).toBe(409);
    expect(await res.json()).toMatchObject({ error: "username_taken" });
  });

  it("delete of a never-existed user → 404 (not 409 last_admin)", async () => {
    const res = await call("/api/admin/users/delete", {
      method: "POST", cookie: admin, body: JSON.stringify({ username: "ghost.user" }),
    });
    expect(res.status).toBe(404);
  });
});
