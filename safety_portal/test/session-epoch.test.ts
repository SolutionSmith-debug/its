import { env, SELF } from "cloudflare:test";
import { describe, it, expect, beforeEach } from "vitest";

// ─────────────────────────────────────────────────────────────────────────────
// Slice 8a — real session revocation via users.session_epoch (deferred audit #7).
// Real workerd + Miniflare D1 (migration 0009 applied by test/apply-migrations.ts).
//
// Covers (per brief B7 + C10):
//  - the epoch is embedded in the cookie at ISSUE (login);
//  - a STALE-epoch cookie (cookie.epoch < user.session_epoch) is REJECTED (401);
//  - LOGOUT bumps the epoch → the just-cleared cookie is dead on its next request;
//  - PASSWORD-CHANGE bumps the epoch (both the bearer reset + the in-app credentials
//    route) → outstanding cookies die;
//  - a NO-epoch cookie (a pre-#7 session) is treated as 0 and STILL WORKS (survival —
//    we must NOT mass-logout existing submitters).
// ─────────────────────────────────────────────────────────────────────────────

const BASE = "https://portal.test";
const ADMIN_BEARER = "test-admin-token"; // == PORTAL_ADMIN_API_TOKEN in vitest.config.ts
const SIGNING_SECRET = "test-session-signing-secret"; // == SESSION_SIGNING_SECRET in vitest.config.ts
const COOKIE = "its_portal_session";

type Init = RequestInit & { cookie?: string; bearer?: string };
function call(path: string, init: Init = {}): Promise<Response> {
  const headers = new Headers(init.headers);
  if (init.cookie) headers.set("Cookie", init.cookie);
  if (init.bearer) headers.set("Authorization", `Bearer ${init.bearer}`);
  if (init.body && !headers.has("content-type")) headers.set("content-type", "application/json");
  return SELF.fetch(BASE + path, { ...init, headers });
}

function cookieFrom(res: Response): string {
  return (res.headers.get("set-cookie") ?? "").split(";")[0]; // "its_portal_session=VALUE"
}

/** Extract + URL-decode the signed cookie VALUE (drops the trailing ".<sig>" so the
 *  JSON claims are readable). Mirrors what the Worker signs at login. */
function decodeClaims(cookie: string): Record<string, unknown> {
  const value = decodeURIComponent(cookie.slice(`${COOKIE}=`.length));
  const json = value.slice(0, value.lastIndexOf(".")); // strip the ".<base64sig>"
  return JSON.parse(json);
}

/**
 * Forge a Hono signed cookie byte-for-byte (the format requireSession verifies):
 *   <jsonValue>.<base64(HMAC-SHA256(jsonValue))>  →  encodeURIComponent
 * Mirrors hono/dist/utils/cookie.js makeSignature + serializeSigned. Used only for the
 * pre-#7 "no epoch claim" survival case — we can't get such a cookie from /api/login
 * (which now always embeds an epoch), so we mint one without the claim.
 */
async function signCookie(claims: Record<string, unknown>): Promise<string> {
  const value = JSON.stringify(claims);
  const key = await crypto.subtle.importKey(
    "raw", new TextEncoder().encode(SIGNING_SECRET), { name: "HMAC", hash: "SHA-256" }, false, ["sign"],
  );
  const sig = await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(value));
  const b64 = btoa(String.fromCharCode(...new Uint8Array(sig)));
  return `${COOKIE}=${encodeURIComponent(`${value}.${b64}`)}`;
}

async function provision(username: string, password: string, role: "submitter" | "admin" = "submitter") {
  const res = await call("/api/internal/admin/users", {
    method: "POST", bearer: ADMIN_BEARER, body: JSON.stringify({ username, password, role }),
  });
  expect(res.status, await res.clone().text()).toBe(201);
}

async function loginRaw(username: string, password: string): Promise<Response> {
  return call("/api/login", { method: "POST", body: JSON.stringify({ username, password }) });
}
async function login(username: string, password: string): Promise<string> {
  const res = await loginRaw(username, password);
  expect(res.status, await res.clone().text()).toBe(200);
  return cookieFrom(res);
}

async function epochOf(username: string): Promise<number> {
  const row = await env.DB
    .prepare("SELECT session_epoch FROM users WHERE username=?")
    .bind(username)
    .first<{ session_epoch: number }>();
  return row!.session_epoch;
}

beforeEach(async () => {
  await env.DB.batch([
    env.DB.prepare("DELETE FROM users"),
    env.DB.prepare("DELETE FROM audit_log"),
  ]);
});

describe("epoch embedded at issue", () => {
  it("a fresh user starts at session_epoch 0 (migration DEFAULT)", async () => {
    await provision("pm.bob", "password123");
    expect(await epochOf("pm.bob")).toBe(0);
  });

  it("login embeds the user's session_epoch in the signed cookie", async () => {
    await provision("pm.bob", "password123");
    // Bump the epoch to a non-zero value out-of-band so the test asserts the value
    // SNAPSHOTS the live DB epoch (not just a hard-coded 0).
    await env.DB.prepare("UPDATE users SET session_epoch=3 WHERE username=?").bind("pm.bob").run();
    const cookie = await login("pm.bob", "password123");
    expect(decodeClaims(cookie).epoch).toBe(3);
  });

  it("a cookie minted at the current epoch is accepted", async () => {
    await provision("pm.bob", "password123");
    const cookie = await login("pm.bob", "password123");
    expect((await call("/api/session", { cookie })).status).toBe(200);
  });
});

describe("stale-epoch rejection", () => {
  it("a cookie whose epoch is BEHIND the DB epoch is rejected (401 revoked)", async () => {
    await provision("pm.bob", "password123");
    const cookie = await login("pm.bob", "password123"); // epoch 0 in the cookie
    expect((await call("/api/session", { cookie })).status).toBe(200);
    // Revoke out-of-band: DB epoch now 1, the cookie still carries 0.
    await env.DB.prepare("UPDATE users SET session_epoch=1 WHERE username=?").bind("pm.bob").run();
    const res = await call("/api/session", { cookie });
    expect(res.status).toBe(401);
    expect(await res.json()).toMatchObject({ error: "revoked" });
  });

  it("an EQUAL epoch is accepted (not behind); a HIGHER cookie epoch is accepted too", async () => {
    await provision("pm.bob", "password123");
    await env.DB.prepare("UPDATE users SET session_epoch=2 WHERE username=?").bind("pm.bob").run();
    const cookie = await login("pm.bob", "password123"); // cookie epoch == 2 (equal)
    expect((await call("/api/session", { cookie })).status).toBe(200);
    // A cookie epoch AHEAD of the DB (only reachable via a key compromise) is NOT
    // "behind", so it is not rejected on epoch grounds — matches `< ` semantics.
    await env.DB.prepare("UPDATE users SET session_epoch=1 WHERE username=?").bind("pm.bob").run();
    expect((await call("/api/session", { cookie })).status).toBe(200);
  });
});

describe("logout bumps the epoch", () => {
  it("logout increments session_epoch and kills the old cookie", async () => {
    await provision("pm.bob", "password123");
    const cookie = await login("pm.bob", "password123");
    expect(await epochOf("pm.bob")).toBe(0);

    const out = await call("/api/logout", { method: "POST", cookie });
    expect(out.status).toBe(200);
    expect(await epochOf("pm.bob")).toBe(1); // bumped

    // The cleared cookie (still epoch 0) is now stale → rejected.
    const res = await call("/api/session", { cookie });
    expect(res.status).toBe(401);
    expect(await res.json()).toMatchObject({ error: "revoked" });

    // A FRESH login mints a cookie at the new epoch (1) and works again.
    const fresh = await login("pm.bob", "password123");
    expect(decodeClaims(fresh).epoch).toBe(1);
    expect((await call("/api/session", { cookie: fresh })).status).toBe(200);
  });

  it("logout with NO cookie still returns ok and bumps nothing", async () => {
    await provision("pm.bob", "password123");
    const out = await call("/api/logout", { method: "POST" });
    expect(out.status).toBe(200);
    expect(await epochOf("pm.bob")).toBe(0);
  });
});

describe("password-change bumps the epoch", () => {
  it("the bearer reset route bumps session_epoch and kills the old cookie", async () => {
    await provision("pm.bob", "password123");
    const cookie = await login("pm.bob", "password123");
    expect(await epochOf("pm.bob")).toBe(0);

    const reset = await call("/api/internal/admin/users/reset", {
      method: "POST", bearer: ADMIN_BEARER, body: JSON.stringify({ username: "pm.bob", password: "newpassword456" }),
    });
    expect(reset.status, await reset.clone().text()).toBe(200);
    expect(await epochOf("pm.bob")).toBe(1);

    const res = await call("/api/session", { cookie });
    expect(res.status).toBe(401);
    expect(await res.json()).toMatchObject({ error: "revoked" });
  });

  it("the in-app credentials route bumps the epoch on a password change (another user)", async () => {
    await provision("admin.one", "password123", "admin");
    await provision("pm.bob", "password123");
    const admin = await login("admin.one", "password123");
    const bobCookie = await login("pm.bob", "password123");

    const res = await call("/api/admin/users/credentials", {
      method: "POST", cookie: admin, body: JSON.stringify({ username: "pm.bob", new_password: "newpassword456" }),
    });
    expect(res.status, await res.clone().text()).toBe(200);
    expect(await epochOf("pm.bob")).toBe(1);

    // Bob's outstanding cookie is now revoked.
    const sess = await call("/api/session", { cookie: bobCookie });
    expect(sess.status).toBe(401);
    expect(await sess.json()).toMatchObject({ error: "revoked" });
  });

  it("a username-ONLY edit (no password) does NOT bump the epoch", async () => {
    await provision("admin.one", "password123", "admin");
    await provision("pm.bob", "password123");
    const admin = await login("admin.one", "password123");

    const res = await call("/api/admin/users/credentials", {
      method: "POST", cookie: admin, body: JSON.stringify({ username: "pm.bob", new_username: "bob.pm" }),
    });
    expect(res.status, await res.clone().text()).toBe(200);
    // Renamed → look up under the new username; epoch unchanged (still 0).
    expect(await epochOf("bob.pm")).toBe(0);
  });
});

describe("pre-#7 survival — a NO-epoch cookie is treated as 0 and still works", () => {
  it("a signed cookie with no epoch claim is accepted while the DB epoch is 0", async () => {
    await provision("pm.bob", "password123");
    const id = (await env.DB.prepare("SELECT id FROM users WHERE username=?").bind("pm.bob").first<{ id: number }>())!.id;
    // Mint a legacy-shaped cookie: valid signature, iat now, NO `epoch` claim.
    const legacy = await signCookie({ sub: id, username: "pm.bob", iat: Math.floor(Date.now() / 1000) });
    const res = await call("/api/session", { cookie: legacy });
    expect(res.status, await res.clone().text()).toBe(200);
    expect(await res.json()).toMatchObject({ user: { username: "pm.bob", role: "submitter" } });
  });

  it("the same no-epoch cookie IS revoked once the DB epoch advances past 0", async () => {
    await provision("pm.bob", "password123");
    const id = (await env.DB.prepare("SELECT id FROM users WHERE username=?").bind("pm.bob").first<{ id: number }>())!.id;
    const legacy = await signCookie({ sub: id, username: "pm.bob", iat: Math.floor(Date.now() / 1000) });
    // A real logout / password-change later moves the epoch to 1 → 0 < 1 → revoked.
    await env.DB.prepare("UPDATE users SET session_epoch=1 WHERE username=?").bind("pm.bob").run();
    expect((await call("/api/session", { cookie: legacy })).status).toBe(401);
  });
});
