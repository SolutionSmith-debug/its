import { env, SELF } from "cloudflare:test";
import { describe, it, expect, beforeEach } from "vitest";

// Slice 8b — admin 5-minute idle timeout (a SLIDING server-side cookie window, C10).
// Admins: a cookie idle past ADMIN_IDLE_S is 401'd (captured-cookie kill); an active
// request slides it. Submitters keep the 90-day session. Real workerd + Miniflare D1.

const BASE = "https://portal.test";
const ADMIN_BEARER = "test-admin-token";
const SIGNING_SECRET = "test-session-signing-secret";
const COOKIE = "its_portal_session";
const ADMIN_IDLE_S = 5 * 60;

type Init = RequestInit & { cookie?: string; bearer?: string };
function call(path: string, init: Init = {}): Promise<Response> {
  const headers = new Headers(init.headers);
  if (init.cookie) headers.set("Cookie", init.cookie);
  if (init.bearer) headers.set("Authorization", `Bearer ${init.bearer}`);
  if (init.body && !headers.has("content-type")) headers.set("content-type", "application/json");
  return SELF.fetch(BASE + path, { ...init, headers });
}
const cookieFrom = (res: Response) => (res.headers.get("set-cookie") ?? "").split(";")[0];
const setCookie = (res: Response) => res.headers.get("set-cookie") ?? "";
const claimsOf = (cookie: string): Record<string, unknown> => {
  const v = decodeURIComponent(cookie.slice(`${COOKIE}=`.length));
  return JSON.parse(v.slice(0, v.lastIndexOf(".")));
};

/** Forge a Hono signed cookie with arbitrary claims (e.g. a stale iat). */
async function signCookie(claims: Record<string, unknown>): Promise<string> {
  const value = JSON.stringify(claims);
  const key = await crypto.subtle.importKey(
    "raw", new TextEncoder().encode(SIGNING_SECRET), { name: "HMAC", hash: "SHA-256" }, false, ["sign"],
  );
  const sig = await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(value));
  const b64 = btoa(String.fromCharCode(...new Uint8Array(sig)));
  return `${COOKIE}=${encodeURIComponent(`${value}.${b64}`)}`;
}

async function provision(username: string, role: "submitter" | "admin"): Promise<number> {
  const r = await call("/api/internal/admin/users", {
    method: "POST", bearer: ADMIN_BEARER, body: JSON.stringify({ username, password: "password123", role }),
  });
  expect(r.status, await r.clone().text()).toBe(201);
  const row = await env.DB.prepare("SELECT id FROM users WHERE username=?").bind(username).first<{ id: number }>();
  return row!.id;
}
async function login(username: string): Promise<Response> {
  return call("/api/login", { method: "POST", body: JSON.stringify({ username, password: "password123" }) });
}
const now = () => Math.floor(Date.now() / 1000);

beforeEach(async () => {
  await env.DB.batch([env.DB.prepare("DELETE FROM users"), env.DB.prepare("DELETE FROM audit_log")]);
});

describe("login cookie lifetime is role-scoped", () => {
  it("an admin login issues a 5-minute (300s) Max-Age cookie", async () => {
    await provision("admin.one", "admin");
    expect(setCookie(await login("admin.one"))).toMatch(/max-age=300\b/i);
  });
  it("a submitter login keeps the 90-day cookie", async () => {
    await provision("pm.bob", "submitter");
    expect(setCookie(await login("pm.bob"))).toMatch(/max-age=7776000\b/i);
  });
});

describe("admin idle window (server-side, sliding)", () => {
  it("a fresh admin cookie is accepted AND re-issued (the window slides)", async () => {
    await provision("admin.one", "admin");
    const cookie = cookieFrom(await login("admin.one"));
    const res = await call("/api/session", { cookie });
    expect(res.status).toBe(200);
    // Active request slides: a new cookie is set with a fresh iat.
    const slid = setCookie(res);
    expect(slid).toMatch(/max-age=300\b/i);
    expect((claimsOf(slid).iat as number)).toBeGreaterThanOrEqual(now() - 2);
  });

  it("an admin cookie idle past 5 min is rejected (401 idle)", async () => {
    const id = await provision("admin.one", "admin");
    const stale = await signCookie({ sub: id, username: "admin.one", iat: now() - (ADMIN_IDLE_S + 60), epoch: 0 });
    const res = await call("/api/session", { cookie: stale });
    expect(res.status).toBe(401);
    expect(await res.json()).toMatchObject({ error: "idle" });
  });

  it("a SUBMITTER cookie idle past 5 min still works (no admin idle window)", async () => {
    const id = await provision("pm.bob", "submitter");
    const stale = await signCookie({ sub: id, username: "pm.bob", iat: now() - (ADMIN_IDLE_S + 60), epoch: 0 });
    const res = await call("/api/session", { cookie: stale });
    expect(res.status, await res.clone().text()).toBe(200);
    expect(await res.json()).toMatchObject({ user: { username: "pm.bob", role: "submitter" } });
  });

  it("a slid admin cookie keeps working (the re-issued iat passes the next check)", async () => {
    await provision("admin.one", "admin");
    let cookie = cookieFrom(await login("admin.one"));
    for (let i = 0; i < 3; i++) {
      const res = await call("/api/session", { cookie });
      expect(res.status).toBe(200);
      cookie = cookieFrom(res); // ride the slid cookie forward
    }
  });
});
