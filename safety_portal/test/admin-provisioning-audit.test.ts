import { env, SELF } from "cloudflare:test";
import { describe, it, expect, beforeEach } from "vitest";

// The bearer-gated operator provisioning routes (/api/internal/admin/users*) mutate
// PRIVILEGE — they can mint an account at ANY role including admin, change a role,
// reset a password (revoking every live session) and disable an account. Every one of
// them wrote its mutation with a bare .run() and NO audit_log row, while their in-app
// /api/admin/* twins audited each equivalent action.
//
// audit_log is the portal's security event stream and the only cross-cutting record of
// who changed an account, so an actor holding PORTAL_ADMIN_API_TOKEN could mint an admin
// and leave nothing behind. These tests pin the W4 shape: mutation + audit in ONE batch,
// audit conditional on changes()=1 — so a real change always leaves a row and a no-op
// (404) never writes a lying one.
//
// The operator actions are namespaced `operator_user_*` deliberately: an account minted
// through the operator bearer must not be indistinguishable from one minted by a
// logged-in admin (which writes `user_create` with the admin's own username as actor).

const BASE = "https://portal.test";
const ADMIN_BEARER = "test-admin-token"; // == PORTAL_ADMIN_API_TOKEN in vitest.config.ts

type Init = RequestInit & { bearer?: string };
function call(path: string, init: Init = {}): Promise<Response> {
  const headers = new Headers(init.headers);
  if (init.bearer) headers.set("Authorization", `Bearer ${init.bearer}`);
  if (init.body && !headers.has("content-type")) headers.set("content-type", "application/json");
  return SELF.fetch(BASE + path, { ...init, headers });
}

async function audits(action: string) {
  const { results } = await env.DB
    .prepare("SELECT actor_username, action, target_username, detail FROM audit_log WHERE action=?")
    .bind(action)
    .all<{ actor_username: string; action: string; target_username: string; detail: string | null }>();
  return results;
}

const post = (path: string, body: unknown) =>
  call(path, { method: "POST", bearer: ADMIN_BEARER, body: JSON.stringify(body) });

beforeEach(async () => {
  await env.DB.batch([env.DB.prepare("DELETE FROM users"), env.DB.prepare("DELETE FROM audit_log")]);
});

describe("operator provisioning leaves an audit trail", () => {
  it("create → one operator_user_create naming actor, target and role", async () => {
    expect((await post("/api/internal/admin/users", { username: "pm.new", password: "password123", role: "admin" })).status).toBe(201);
    const rows = await audits("operator_user_create");
    expect(rows).toHaveLength(1);
    expect(rows[0]).toMatchObject({ actor_username: "operator-cli", target_username: "pm.new" });
    expect(JSON.parse(rows[0].detail!)).toMatchObject({ role: "admin" });
  });

  it("role change, password reset, disable and enable each audit exactly once", async () => {
    await post("/api/internal/admin/users", { username: "pm.bob", password: "password123" });
    expect((await post("/api/internal/admin/users/role", { username: "pm.bob", role: "admin" })).status).toBe(200);
    expect((await post("/api/internal/admin/users/reset", { username: "pm.bob", password: "newpassword1" })).status).toBe(200);
    expect((await post("/api/internal/admin/users/disable", { username: "pm.bob" })).status).toBe(200);
    expect((await post("/api/internal/admin/users/enable", { username: "pm.bob" })).status).toBe(200);

    expect(await audits("operator_user_role_change")).toHaveLength(1);
    expect(await audits("operator_user_password_reset")).toHaveLength(1);
    expect(await audits("operator_user_disable")).toHaveLength(1);
    expect(await audits("operator_user_enable")).toHaveLength(1);
  });

  it("a password reset NEVER records the plaintext or the hash", async () => {
    await post("/api/internal/admin/users", { username: "pm.bob", password: "password123" });
    await post("/api/internal/admin/users/reset", { username: "pm.bob", password: "reset-plain-text-value" });
    const blob = JSON.stringify(await audits("operator_user_password_reset"));
    expect(blob).not.toContain("reset-plain-text-value");
    expect(blob).not.toContain("$2"); // no bcrypt hash prefix
  });

  it("a 404 no-op writes NO audit row (changes()=1 guard, not a lying record)", async () => {
    expect((await post("/api/internal/admin/users/role", { username: "no.body", role: "admin" })).status).toBe(404);
    expect((await post("/api/internal/admin/users/reset", { username: "no.body", password: "password123" })).status).toBe(404);
    expect((await post("/api/internal/admin/users/disable", { username: "no.body" })).status).toBe(404);
    expect(await audits("operator_user_role_change")).toHaveLength(0);
    expect(await audits("operator_user_password_reset")).toHaveLength(0);
    expect(await audits("operator_user_disable")).toHaveLength(0);
  });

  it("a 409 duplicate create leaves the original's audit row alone", async () => {
    await post("/api/internal/admin/users", { username: "pm.bob", password: "password123" });
    expect((await post("/api/internal/admin/users", { username: "pm.bob", password: "password123" })).status).toBe(409);
    expect(await audits("operator_user_create")).toHaveLength(1); // not 2
  });
});
