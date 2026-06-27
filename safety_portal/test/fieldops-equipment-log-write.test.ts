import { env, SELF } from "cloudflare:test";
import { describe, it, expect, beforeEach } from "vitest";

// ─────────────────────────────────────────────────────────────────────────────
// P2.3 Slice 5 — EQUIPMENT maintenance/fuel/hours LOG write. cap.equipment.field. Append-only
// integrity bar (server timestamps, amends_uuid edit chain, dual attribution, uuid-409, audit).
// (The inspection quick-log is deferred — see the PR note: needs the equipment-preinspection
//  forms catalog defined.)
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
async function provision(username: string, password: string, role: "submitter" | "admin"): Promise<void> {
  const res = await call("/api/internal/admin/users", { method: "POST", bearer: ADMIN_BEARER, body: JSON.stringify({ username, password, role }) });
  expect(res.status, await res.clone().text()).toBe(201);
}
async function login(username: string, password: string): Promise<string> {
  const res = await call("/api/login", { method: "POST", body: JSON.stringify({ username, password }) });
  expect(res.status, await res.clone().text()).toBe(200);
  return cookieFrom(res);
}
const p = (cookie: string, path: string, body?: unknown) =>
  call(path, { method: "POST", cookie, body: body === undefined ? undefined : JSON.stringify(body) });

async function seedEquipment(name: string): Promise<number> {
  await env.DB.prepare("INSERT INTO equipment (name, active) VALUES (?,1)").bind(name).run();
  return (await env.DB.prepare("SELECT id FROM equipment WHERE name=?").bind(name).first<{ id: number }>())!.id;
}
async function logRows(uuid: string) {
  return ((await env.DB.prepare("SELECT * FROM equipment_logs WHERE uuid=?").bind(uuid).all()).results as any[]);
}
async function audits(action: string) {
  return ((await env.DB.prepare("SELECT * FROM audit_log WHERE action=?").bind(action).all()).results as any[]);
}

let admin: string, submitter: string, eqId: number;
beforeEach(async () => {
  await env.DB.batch([
    env.DB.prepare("DELETE FROM users"),
    env.DB.prepare("DELETE FROM audit_log"),
    env.DB.prepare("DELETE FROM equipment_logs"),
    env.DB.prepare("DELETE FROM equipment"),
  ]);
  await provision("admin.one", "password123", "admin");
  await provision("submitter.jim", "password123", "submitter");
  admin = await login("admin.one", "password123");
  submitter = await login("submitter.jim", "password123");
  eqId = await seedEquipment("unit-a");
});

describe("POST /api/fieldops/equipment/:id/log", () => {
  it("gate: anon → 401, submitter → 201, admin → 201", async () => {
    expect((await call(`/api/fieldops/equipment/${eqId}/log`, { method: "POST", body: JSON.stringify({ uuid: "x", log_type: "fuel" }) })).status).toBe(401);
    expect((await p(submitter, `/api/fieldops/equipment/${eqId}/log`, { uuid: "l1", log_type: "fuel", value_num: 25 })).status).toBe(201);
    expect((await p(admin, `/api/fieldops/equipment/${eqId}/log`, { uuid: "l2", log_type: "hours" })).status).toBe(201);
  });

  it("creates a log row (server created_at) + audit", async () => {
    expect((await p(submitter, `/api/fieldops/equipment/${eqId}/log`, { uuid: "l1", log_type: "maintenance", detail: "oil change", performed_at: 1700000000 })).status).toBe(201);
    const row = (await logRows("l1"))[0];
    expect(row.log_type).toBe("maintenance");
    expect(row.detail).toBe("oil change");
    expect(row.performed_at).toBe(1700000000);
    expect(row.status_value).toBeNull();
    expect(row.created_at).toBeGreaterThan(1_000_000_000); // server DEFAULT
    expect(row.actor_username).toBe("submitter.jim");
    expect(await audits("equipment_log_create")).toHaveLength(1);
  });

  it("guards: bad log_type / missing uuid / over-bound detail → 400; unknown equipment → 404", async () => {
    expect((await p(submitter, `/api/fieldops/equipment/${eqId}/log`, { uuid: "x", log_type: "explode" })).status).toBe(400);
    expect((await p(submitter, `/api/fieldops/equipment/${eqId}/log`, { log_type: "fuel" })).status).toBe(400);
    expect((await p(submitter, `/api/fieldops/equipment/${eqId}/log`, { uuid: "x", log_type: "fuel", detail: "z".repeat(2001) })).status).toBe(400);
    expect((await p(submitter, "/api/fieldops/equipment/999999/log", { uuid: "x", log_type: "fuel" })).status).toBe(404);
  });

  it("append-only edit chain: amend is a NEW row (original untouched); uuid collision → 409 + rollback", async () => {
    await p(submitter, `/api/fieldops/equipment/${eqId}/log`, { uuid: "orig", log_type: "hours", value_num: 100 });
    expect((await p(submitter, `/api/fieldops/equipment/${eqId}/log`, { uuid: "amend", log_type: "hours", value_num: 110, amends_uuid: "orig" })).status).toBe(201);
    expect((await logRows("orig"))[0].value_num).toBe(100); // original untouched
    expect((await logRows("amend"))[0].amends_uuid).toBe("orig");
    expect(await audits("equipment_log_edit")).toHaveLength(1);
    // collision
    expect((await p(submitter, `/api/fieldops/equipment/${eqId}/log`, { uuid: "orig", log_type: "fuel" })).status).toBe(409);
    expect(await logRows("orig")).toHaveLength(1);
    expect((await logRows("orig"))[0].log_type).toBe("hours"); // unchanged
    expect(await audits("equipment_log_create")).toHaveLength(1); // no 2nd audit for the rolled-back collision
  });

  it("dual attribution: submitter submit-as → 403; admin submit-as to a real user → 201 (normalized)", async () => {
    expect((await p(submitter, `/api/fieldops/equipment/${eqId}/log`, { uuid: "x", log_type: "fuel", submitted_as: "admin.one" })).status).toBe(403);
    expect((await p(admin, `/api/fieldops/equipment/${eqId}/log`, { uuid: "y", log_type: "fuel", submitted_as: "Submitter.Jim" })).status).toBe(201);
    expect((await logRows("y"))[0].submitted_as).toBe("submitter.jim");
  });
});
