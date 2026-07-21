import { env, SELF } from "cloudflare:test";
import { describe, it, expect, beforeEach } from "vitest";

// ─────────────────────────────────────────────────────────────────────────────
// Admin dashboard (Phase 1) — role foundation + /api/admin/* account management.
// Runs against the REAL worker in workerd with a Miniflare D1 (migrations applied
// by test/apply-migrations.ts). Cookies are forwarded by hand (SELF.fetch is
// stateless per call), mirroring a browser's same-origin session.
// ─────────────────────────────────────────────────────────────────────────────

const BASE = "https://portal.test";
const ADMIN_BEARER = "test-admin-token"; // == PORTAL_ADMIN_API_TOKEN in vitest.config.ts

type Init = RequestInit & { cookie?: string; bearer?: string };

function call(path: string, init: Init = {}): Promise<Response> {
  const headers = new Headers(init.headers);
  if (init.cookie) headers.set("Cookie", init.cookie);
  if (init.bearer) headers.set("Authorization", `Bearer ${init.bearer}`);
  if (init.body && !headers.has("content-type")) headers.set("content-type", "application/json");
  return SELF.fetch(BASE + path, { ...init, headers });
}

function cookieFrom(res: Response): string {
  // Only the session cookie is ever set, so the first Set-Cookie is it.
  return (res.headers.get("set-cookie") ?? "").split(";")[0]; // "its_portal_session=VALUE"
}

/** Provision a user via the bearer operator route (the real create+hash path). */
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

// Empty the tables before each test (isolated storage keeps migrations but a clean
// slate makes admin-count assertions deterministic).
beforeEach(async () => {
  await env.DB.batch([
    env.DB.prepare("DELETE FROM users"),
    env.DB.prepare("DELETE FROM audit_log"),
  ]);
});

describe("role foundation", () => {
  it("login + /api/session expose the role + capabilities", async () => {
    await provision("admin.one", "password123", "admin");
    const res = await call("/api/login", {
      method: "POST",
      body: JSON.stringify({ username: "admin.one", password: "password123" }),
    });
    type UserBody = { user: { username: string; role: string; capabilities: string[] } };
    const loginBody = (await res.json()) as UserBody;
    expect(loginBody.user).toMatchObject({ username: "admin.one", role: "admin" });
    // admin resolves the full grant set (migration 0013) — assert a representative cap
    // rather than the exact list so the grant matrix can evolve without breaking this.
    expect(loginBody.user.capabilities).toContain("cap.admin.accounts");
    const cookie = cookieFrom(res);
    const sess = await call("/api/session", { cookie });
    const sessBody = (await sess.json()) as UserBody;
    expect(sessBody.user).toMatchObject({ username: "admin.one", role: "admin" });
    expect(sessBody.user.capabilities).toContain("cap.admin.accounts");
  });

  it("a DISABLED user cannot log in (PR-4 — validateUser rejects disabled, not just requireSession)", async () => {
    await provision("pm.bob", "password123", "submitter");
    expect((await call("/api/login", { method: "POST", body: JSON.stringify({ username: "pm.bob", password: "password123" }) })).status).toBe(200);
    await env.DB.prepare("UPDATE users SET disabled=1 WHERE username=?").bind("pm.bob").run();
    const res = await call("/api/login", { method: "POST", body: JSON.stringify({ username: "pm.bob", password: "password123" }) });
    expect(res.status).toBe(401);
    expect(await res.json()).toMatchObject({ error: "invalid_credentials" });
  });

  it("a submitter session is 403 on the admin surface; no session is 401", async () => {
    await provision("pm.bob", "password123", "submitter");
    const cookie = await login("pm.bob", "password123");
    expect((await call("/api/admin/users", { cookie })).status).toBe(403);
    expect((await call("/api/admin/users")).status).toBe(401);
  });

  it("an admin session reaches the admin surface", async () => {
    await provision("admin.one", "password123", "admin");
    const cookie = await login("admin.one", "password123");
    const res = await call("/api/admin/users", { cookie });
    expect(res.status).toBe(200);
    const { users } = (await res.json()) as { users: { username: string; role: string }[] };
    expect(users.find((u) => u.username === "admin.one")?.role).toBe("admin");
  });

  it("role is re-read per request — a demoted admin loses the surface immediately", async () => {
    await provision("admin.one", "password123", "admin");
    await provision("admin.two", "password123", "admin");
    const cookie = await login("admin.two", "password123");
    expect((await call("/api/admin/users", { cookie })).status).toBe(200);
    // Demote admin.two out-of-band via the bearer route (no re-login).
    await call("/api/internal/admin/users/role", {
      method: "POST",
      bearer: ADMIN_BEARER,
      body: JSON.stringify({ username: "admin.two", role: "submitter" }),
    });
    expect((await call("/api/admin/users", { cookie })).status).toBe(403);
  });
});

describe("account create (/api/admin/users)", () => {
  let admin: string;
  beforeEach(async () => {
    await provision("admin.one", "password123", "admin");
    admin = await login("admin.one", "password123");
  });

  it("creates a submitter and an admin", async () => {
    const r1 = await call("/api/admin/users", {
      method: "POST",
      cookie: admin,
      body: JSON.stringify({ username: "pm.new", password: "password123" }),
    });
    expect(r1.status).toBe(201);
    expect(await r1.json()).toMatchObject({ ok: true, username: "pm.new", role: "submitter" });

    const r2 = await call("/api/admin/users", {
      method: "POST",
      cookie: admin,
      body: JSON.stringify({ username: "admin.two", password: "password123", role: "admin" }),
    });
    expect(r2.status).toBe(201);
    // The newly-created admin can actually use the admin surface.
    const c2 = await login("admin.two", "password123");
    expect((await call("/api/admin/users", { cookie: c2 })).status).toBe(200);
  });

  it("rejects duplicate (409), bad username (400), bad role (400), short password (400)", async () => {
    await call("/api/admin/users", {
      method: "POST",
      cookie: admin,
      body: JSON.stringify({ username: "pm.dup", password: "password123" }),
    });
    const dup = await call("/api/admin/users", {
      method: "POST",
      cookie: admin,
      body: JSON.stringify({ username: "pm.dup", password: "password123" }),
    });
    expect(dup.status).toBe(409);

    const badName = await call("/api/admin/users", {
      method: "POST",
      cookie: admin,
      body: JSON.stringify({ username: "nodothere", password: "password123" }),
    });
    expect(badName.status).toBe(400);
    expect(await badName.json()).toMatchObject({ error: "invalid_username" });

    const badRole = await call("/api/admin/users", {
      method: "POST",
      cookie: admin,
      body: JSON.stringify({ username: "pm.role", password: "password123", role: "superadmin" }),
    });
    expect(badRole.status).toBe(400);
    expect(await badRole.json()).toMatchObject({ error: "invalid_role" });

    const shortPw = await call("/api/admin/users", {
      method: "POST",
      cookie: admin,
      body: JSON.stringify({ username: "pm.short", password: "short" }),
    });
    expect(shortPw.status).toBe(400);
  });

  it("a submitter cannot create accounts (403, before any write)", async () => {
    await provision("pm.bob", "password123", "submitter");
    const bob = await login("pm.bob", "password123");
    const res = await call("/api/admin/users", {
      method: "POST",
      cookie: bob,
      body: JSON.stringify({ username: "pm.sneaky", password: "password123", role: "admin" }),
    });
    expect(res.status).toBe(403);
    const exists = await env.DB.prepare("SELECT 1 FROM users WHERE username='pm.sneaky'").first();
    expect(exists).toBeNull();
  });
});

describe("credentials edit (/api/admin/users/credentials)", () => {
  let admin: string;
  beforeEach(async () => {
    await provision("admin.one", "password123", "admin");
    admin = await login("admin.one", "password123");
    await provision("pm.bob", "password123", "submitter");
  });

  it("changes another user's password (old fails, new works)", async () => {
    const res = await call("/api/admin/users/credentials", {
      method: "POST",
      cookie: admin,
      body: JSON.stringify({ username: "pm.bob", new_password: "newpassword456" }),
    });
    expect(res.status).toBe(200);
    expect((await call("/api/login", { method: "POST", body: JSON.stringify({ username: "pm.bob", password: "password123" }) })).status).toBe(401);
    expect((await call("/api/login", { method: "POST", body: JSON.stringify({ username: "pm.bob", password: "newpassword456" }) })).status).toBe(200);
  });

  it("renames a user; collision is 409", async () => {
    const ok = await call("/api/admin/users/credentials", {
      method: "POST",
      cookie: admin,
      body: JSON.stringify({ username: "pm.bob", new_username: "bob.renamed" }),
    });
    expect(ok.status).toBe(200);
    expect((await call("/api/login", { method: "POST", body: JSON.stringify({ username: "bob.renamed", password: "password123" }) })).status).toBe(200);

    await provision("pm.carol", "password123");
    const collide = await call("/api/admin/users/credentials", {
      method: "POST",
      cookie: admin,
      body: JSON.stringify({ username: "pm.carol", new_username: "bob.renamed" }),
    });
    expect(collide.status).toBe(409);
    expect(await collide.json()).toMatchObject({ error: "username_taken" });
  });

  it("self username-change re-auths AND invalidates the old cookie", async () => {
    const res = await call("/api/admin/users/credentials", {
      method: "POST",
      cookie: admin,
      body: JSON.stringify({ username: "admin.one", new_username: "admin.renamed" }),
    });
    expect(res.status).toBe(200);
    expect(await res.json()).toMatchObject({ reauth: true });
    // The old cookie's username no longer exists → requireSession 401s it.
    expect((await call("/api/session", { cookie: admin })).status).toBe(401);
    expect((await call("/api/login", { method: "POST", body: JSON.stringify({ username: "admin.renamed", password: "password123" }) })).status).toBe(200);
  });

  it("no-op edit is 400 no_changes", async () => {
    const res = await call("/api/admin/users/credentials", {
      method: "POST",
      cookie: admin,
      body: JSON.stringify({ username: "pm.bob" }),
    });
    expect(res.status).toBe(400);
    expect(await res.json()).toMatchObject({ error: "no_changes" });
  });
});

describe("role change + last-admin guard (/api/admin/users/role)", () => {
  it("promotes and demotes when another admin exists", async () => {
    await provision("admin.one", "password123", "admin");
    await provision("admin.two", "password123", "admin");
    await provision("pm.bob", "password123", "submitter");
    const admin = await login("admin.one", "password123");

    const promote = await call("/api/admin/users/role", {
      method: "POST",
      cookie: admin,
      body: JSON.stringify({ username: "pm.bob", role: "admin" }),
    });
    expect(promote.status).toBe(200);
    expect((await call("/api/admin/users", { cookie: await login("pm.bob", "password123") })).status).toBe(200);

    const demote = await call("/api/admin/users/role", {
      method: "POST",
      cookie: admin,
      body: JSON.stringify({ username: "admin.two", role: "submitter" }),
    });
    expect(demote.status).toBe(200);
  });

  it("blocks demoting the only enabled admin (last_admin), writes NO audit row, role unchanged", async () => {
    await provision("admin.one", "password123", "admin");
    const admin = await login("admin.one", "password123");
    const res = await call("/api/admin/users/role", {
      method: "POST",
      cookie: admin,
      body: JSON.stringify({ username: "admin.one", role: "submitter" }),
    });
    expect(res.status).toBe(409);
    expect(await res.json()).toMatchObject({ error: "last_admin" });
    // The atomic in-WHERE guard blocked the UPDATE, so the changes()-conditional
    // audit did NOT fire (guard + audit are bound to the same mutation).
    const audit = await env.DB
      .prepare("SELECT COUNT(*) AS n FROM audit_log WHERE action='role_change' AND target_username='admin.one'")
      .first<{ n: number }>();
    expect(audit?.n).toBe(0);
    const row = await env.DB.prepare("SELECT role FROM users WHERE username='admin.one'").first<{ role: string }>();
    expect(row?.role).toBe("admin");
  });

  it("counts only ENABLED admins — a disabled second admin doesn't satisfy the guard", async () => {
    await provision("admin.one", "password123", "admin");
    await provision("admin.two", "password123", "admin");
    // Disable admin.two via the operator route → only admin.one is an active admin.
    await call("/api/internal/admin/users/disable", {
      method: "POST",
      bearer: ADMIN_BEARER,
      body: JSON.stringify({ username: "admin.two" }),
    });
    const admin = await login("admin.one", "password123");
    const res = await call("/api/admin/users/role", {
      method: "POST",
      cookie: admin,
      body: JSON.stringify({ username: "admin.one", role: "submitter" }),
    });
    expect(res.status).toBe(409);
  });

  it("self-demote re-auths when another admin remains", async () => {
    await provision("admin.one", "password123", "admin");
    await provision("admin.two", "password123", "admin");
    const admin = await login("admin.one", "password123");
    const res = await call("/api/admin/users/role", {
      method: "POST",
      cookie: admin,
      body: JSON.stringify({ username: "admin.one", role: "submitter" }),
    });
    expect(res.status).toBe(200);
    expect(await res.json()).toMatchObject({ reauth: true });
  });
});

describe("delete (/api/admin/users/delete)", () => {
  it("deletes a submitter; blocks deleting the only admin", async () => {
    await provision("admin.one", "password123", "admin");
    await provision("pm.bob", "password123", "submitter");
    const admin = await login("admin.one", "password123");

    const del = await call("/api/admin/users/delete", {
      method: "POST",
      cookie: admin,
      body: JSON.stringify({ username: "pm.bob" }),
    });
    expect(del.status).toBe(200);
    expect(await env.DB.prepare("SELECT 1 FROM users WHERE username='pm.bob'").first()).toBeNull();

    const last = await call("/api/admin/users/delete", {
      method: "POST",
      cookie: admin,
      body: JSON.stringify({ username: "admin.one" }),
    });
    expect(last.status).toBe(409);
    expect(await last.json()).toMatchObject({ error: "last_admin" });
    // The blocked delete wrote no audit row for admin.one and left the row intact.
    const audit = await env.DB
      .prepare("SELECT COUNT(*) AS n FROM audit_log WHERE action='user_delete' AND target_username='admin.one'")
      .first<{ n: number }>();
    expect(audit?.n).toBe(0);
    expect(await env.DB.prepare("SELECT 1 FROM users WHERE username='admin.one'").first()).not.toBeNull();
  });

  it("deletes an admin when another remains", async () => {
    await provision("admin.one", "password123", "admin");
    await provision("admin.two", "password123", "admin");
    const admin = await login("admin.one", "password123");
    const res = await call("/api/admin/users/delete", {
      method: "POST",
      cookie: admin,
      body: JSON.stringify({ username: "admin.two" }),
    });
    expect(res.status).toBe(200);
  });
});

describe("bearer operator role routes (break-glass, NOT last-admin-guarded)", () => {
  it("creates an admin and changes role via the bearer routes", async () => {
    await provision("admin.boot", "password123", "admin");
    // set-role: demote the only admin via bearer — must SUCCEED (recovery path).
    const demote = await call("/api/internal/admin/users/role", {
      method: "POST",
      bearer: ADMIN_BEARER,
      body: JSON.stringify({ username: "admin.boot", role: "submitter" }),
    });
    expect(demote.status).toBe(200);
    // 404 for an unknown user.
    const missing = await call("/api/internal/admin/users/role", {
      method: "POST",
      bearer: ADMIN_BEARER,
      body: JSON.stringify({ username: "no.body", role: "admin" }),
    });
    expect(missing.status).toBe(404);
  });

  it("the bearer role route rejects a non-bearer caller (401)", async () => {
    const res = await call("/api/internal/admin/users/role", {
      method: "POST",
      body: JSON.stringify({ username: "x.y", role: "admin" }),
    });
    expect(res.status).toBe(401);
  });

  it("privilege separation: the poller's INTERNAL token is rejected by the admin routes (401)", async () => {
    // PORTAL_INTERNAL_API_TOKEN (the portal_poll daemon's token) must NOT authorize
    // user provisioning — that is the requireAdminToken/requireInternalToken split.
    const res = await call("/api/internal/admin/users", {
      method: "POST",
      bearer: "test-internal-token", // == PORTAL_INTERNAL_API_TOKEN, NOT the admin token
      body: JSON.stringify({ username: "x.y", password: "password123" }),
    });
    expect(res.status).toBe(401);
  });

  it("privilege separation: an admin SESSION cookie does not authorize the bearer-only routes (401)", async () => {
    await provision("admin.one", "password123", "admin");
    const cookie = await login("admin.one", "password123");
    const res = await call("/api/internal/admin/users", {
      method: "POST",
      cookie, // a valid admin session — but /api/internal/admin/* requires the bearer, not a cookie
      body: JSON.stringify({ username: "x.y", password: "password123" }),
    });
    expect(res.status).toBe(401);
  });
});

describe("audit_log", () => {
  it("records create, role_change, and delete with actor + detail", async () => {
    await provision("admin.one", "password123", "admin");
    await provision("admin.two", "password123", "admin");
    const admin = await login("admin.one", "password123");

    await call("/api/admin/users", {
      method: "POST",
      cookie: admin,
      body: JSON.stringify({ username: "pm.bob", password: "password123" }),
    });
    await call("/api/admin/users/role", {
      method: "POST",
      cookie: admin,
      body: JSON.stringify({ username: "admin.two", role: "submitter" }),
    });
    await call("/api/admin/users/delete", {
      method: "POST",
      cookie: admin,
      body: JSON.stringify({ username: "pm.bob" }),
    });

    const { results } = await env.DB
      .prepare("SELECT actor_username, action, target_username, detail FROM audit_log ORDER BY id")
      .all<{ actor_username: string; action: string; target_username: string; detail: string }>();
    const actions = results.map((r) => r.action);
    expect(actions).toContain("user_create");
    expect(actions).toContain("role_change");
    expect(actions).toContain("user_delete");
    // Every IN-APP row records the acting admin. The `operator-cli` rows come from the
    // bearer-gated /api/internal/admin/* provisioning this fixture uses to seed the two
    // admins: that surface now leaves its OWN trail under distinct `operator_user_*`
    // actions, so the two privilege paths stay tellable apart in the stream (an account
    // minted by the operator token must not read like one minted by a logged-in admin).
    // Filter it out rather than asserting across both, and assert it is really there.
    const inApp = results.filter((r) => r.actor_username !== "operator-cli");
    expect(inApp.every((r) => r.actor_username === "admin.one")).toBe(true);
    expect(actions).toContain("operator_user_create");
    const roleRow = results.find((r) => r.action === "role_change");
    expect(JSON.parse(roleRow!.detail)).toMatchObject({ from: "admin", to: "submitter" });
  });
});
