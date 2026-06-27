import { env, SELF } from "cloudflare:test";
import { describe, it, expect, beforeEach } from "vitest";

// ─────────────────────────────────────────────────────────────────────────────
// P2.3 Slice 6 — EQUIPMENT ROSTER CRUD (create / update / retire). cap.equipment.manage
// (admin-only — distinct from cap.equipment.field which submitters have). Retire is a soft-delete
// (active=0) so history (logs/locations/inspections) is preserved; idempotent.
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
async function eqRow(id: number) {
  return await env.DB.prepare("SELECT * FROM equipment WHERE id=?").bind(id).first<any>();
}
async function audits(action: string) {
  return ((await env.DB.prepare("SELECT * FROM audit_log WHERE action=?").bind(action).all()).results as any[]);
}

let admin: string, submitter: string;
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
});

describe("POST /api/fieldops/equipment (create)", () => {
  it("gate: anon → 401, submitter (no manage cap) → 403, admin → 201 (+ id, audit)", async () => {
    expect((await call("/api/fieldops/equipment", { method: "POST", body: JSON.stringify({ name: "X" }) })).status).toBe(401);
    expect((await p(submitter, "/api/fieldops/equipment", { name: "X" })).status).toBe(403);
    const res = await p(admin, "/api/fieldops/equipment", { name: "Skid 1", kind: "skid-steer", identifier: "SK-001" });
    expect(res.status).toBe(201);
    const id = (await res.json() as any).id;
    expect(typeof id).toBe("number");
    const row = await eqRow(id);
    expect(row.name).toBe("Skid 1");
    expect(row.status).toBe("fmc"); // default
    expect(row.active).toBe(1);
    expect(row.status_actor).toBe("admin.one");
    expect(await audits("equipment_create")).toHaveLength(1);
  });

  it("bounds + status enum → 400", async () => {
    expect((await p(admin, "/api/fieldops/equipment", { name: "" })).status).toBe(400);
    expect((await p(admin, "/api/fieldops/equipment", { name: "x".repeat(129) })).status).toBe(400);
    expect((await p(admin, "/api/fieldops/equipment", { name: "X", status: "exploded" })).status).toBe(400);
  });
});

describe("POST /api/fieldops/equipment/:id/update", () => {
  it("admin updates name/kind/identifier (200, audit); submitter → 403; unknown → 404", async () => {
    const id = (await p(admin, "/api/fieldops/equipment", { name: "Old" }).then((r) => r.json()) as any).id;
    expect((await p(submitter, `/api/fieldops/equipment/${id}/update`, { name: "New" })).status).toBe(403);
    expect((await p(admin, `/api/fieldops/equipment/${id}/update`, { name: "New Name", kind: "telehandler" })).status).toBe(200);
    expect((await eqRow(id)).name).toBe("New Name");
    expect(await audits("equipment_update")).toHaveLength(1);
    expect((await p(admin, "/api/fieldops/equipment/999999/update", { name: "X" })).status).toBe(404);
  });
});

describe("POST /api/fieldops/equipment/:id/delete (soft-retire)", () => {
  it("soft-retires (active=0), preserves history, is idempotent, and 404s an unknown id", async () => {
    const id = (await p(admin, "/api/fieldops/equipment", { name: "Doomed" }).then((r) => r.json()) as any).id;
    // give it a log row to prove retire preserves history
    await env.DB.prepare("INSERT INTO equipment_logs (uuid, equipment_id, log_type, actor_username) VALUES (?,?,?,?)").bind("lg", id, "fuel", "admin.one").run();

    expect((await p(submitter, `/api/fieldops/equipment/${id}/delete`)).status).toBe(403);

    const r1 = await p(admin, `/api/fieldops/equipment/${id}/delete`);
    expect(r1.status).toBe(200);
    expect((await eqRow(id)).active).toBe(0); // soft, row + history kept
    expect((await env.DB.prepare("SELECT COUNT(*) n FROM equipment_logs WHERE equipment_id=?").bind(id).first<{ n: number }>())!.n).toBe(1);
    expect(await audits("equipment_retire")).toHaveLength(1);

    // idempotent: second retire → 200 (already_retired), no second audit
    const r2 = await p(admin, `/api/fieldops/equipment/${id}/delete`);
    expect(r2.status).toBe(200);
    expect((await r2.json() as any).already_retired).toBe(true);
    expect(await audits("equipment_retire")).toHaveLength(1);

    expect((await p(admin, "/api/fieldops/equipment/999999/delete")).status).toBe(404);
  });
});
