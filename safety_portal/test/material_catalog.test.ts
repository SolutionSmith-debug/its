import { env, SELF } from "cloudflare:test";
import { describe, it, expect, beforeEach } from "vitest";

// ─────────────────────────────────────────────────────────────────────────────
// P3 Materials (M1) — material_catalog CRUD + READ. cap.materials.manage (admin-only) gates the
// writes; cap.materials.receive (submitter + admin) gates the read so a field PM can browse the
// type vocabulary. Retire is a soft-delete (active=0) so a receipt/incident referencing a
// catalog_id keeps its target; idempotent. Migration 0019 seeds 36 approved types.
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
const g = (cookie: string, path: string) => call(path, { cookie });

// eslint-disable-next-line @typescript-eslint/no-explicit-any
async function catRow(id: number): Promise<any> {
  return await env.DB.prepare("SELECT * FROM material_catalog WHERE id=?").bind(id).first();
}
async function audits(action: string): Promise<unknown[]> {
  return (await env.DB.prepare("SELECT * FROM audit_log WHERE action=?").bind(action).all()).results;
}

let admin: string, submitter: string;
beforeEach(async () => {
  // Preserve the 0019 material_catalog seed; reset only users + audit_log so each test starts clean.
  await env.DB.batch([env.DB.prepare("DELETE FROM users"), env.DB.prepare("DELETE FROM audit_log")]);
  await provision("admin.one", "password123", "admin");
  await provision("submitter.jim", "password123", "submitter");
  admin = await login("admin.one", "password123");
  submitter = await login("submitter.jim", "password123");
});

describe("migration 0019 seed", () => {
  it("seeds the 36 approved types (a known model present; ≥36 active)", async () => {
    const known = await env.DB.prepare("SELECT id FROM material_catalog WHERE model_id=?").bind("Q.PEAK_DUO_XL-G11.3_BFG").first();
    expect(known).not.toBeNull();
    const n = (await env.DB.prepare("SELECT COUNT(*) n FROM material_catalog WHERE active=1").first<{ n: number }>())!.n;
    expect(n).toBeGreaterThanOrEqual(36);
  });
});

describe("POST /api/fieldops/material (create)", () => {
  it("gate: anon → 401, submitter (no manage cap) → 403, admin → 201 (+ id, audit, row)", async () => {
    expect((await call("/api/fieldops/material", { method: "POST", body: JSON.stringify({ model_id: "X", category: "module" }) })).status).toBe(401);
    expect((await p(submitter, "/api/fieldops/material", { model_id: "X", category: "module" })).status).toBe(403);
    const res = await p(admin, "/api/fieldops/material", { model_id: "TEST-MOD-1", manufacturer: "Acme", category: "module", key_specs: "500W", unit_cost: 210.5 });
    expect(res.status).toBe(201);
    const id = (await res.json() as { id: number }).id;
    expect(typeof id).toBe("number");
    const row = await catRow(id);
    expect(row.model_id).toBe("TEST-MOD-1");
    expect(row.category).toBe("module");
    expect(row.unit_cost).toBe(210.5);
    expect(row.active).toBe(1);
    expect(row.source_files).toBe("[]"); // default when none provided
    expect(await audits("material_catalog_create")).toHaveLength(1);
  });

  it("bounds → 400 (missing model_id / category, oversize, bad unit_cost)", async () => {
    expect((await p(admin, "/api/fieldops/material", { model_id: "", category: "module" })).status).toBe(400);
    expect((await p(admin, "/api/fieldops/material", { model_id: "X", category: "" })).status).toBe(400);
    expect((await p(admin, "/api/fieldops/material", { model_id: "x".repeat(129), category: "module" })).status).toBe(400);
    expect((await p(admin, "/api/fieldops/material", { model_id: "X", category: "module", unit_cost: -5 })).status).toBe(400);
    expect((await p(admin, "/api/fieldops/material", { model_id: "X", category: "module", unit_cost: "lots" })).status).toBe(400);
  });
});

describe("POST /api/fieldops/material/:id/update", () => {
  it("admin updates (200, audit, row); submitter → 403; unknown → 404", async () => {
    const id = (await p(admin, "/api/fieldops/material", { model_id: "Old", category: "module" }).then((r) => r.json()) as { id: number }).id;
    expect((await p(submitter, `/api/fieldops/material/${id}/update`, { model_id: "New", category: "inverter" })).status).toBe(403);
    expect((await p(admin, `/api/fieldops/material/${id}/update`, { model_id: "New Model", category: "inverter", unit_cost: 99 })).status).toBe(200);
    const row = await catRow(id);
    expect(row.model_id).toBe("New Model");
    expect(row.category).toBe("inverter");
    expect(row.unit_cost).toBe(99);
    expect(await audits("material_catalog_update")).toHaveLength(1);
    expect((await p(admin, "/api/fieldops/material/999999/update", { model_id: "X", category: "module" })).status).toBe(404);
  });
});

describe("POST /api/fieldops/material/:id/delete (soft-retire)", () => {
  it("soft-retires (active=0), is idempotent, 404s unknown, and the read excludes retired by default", async () => {
    const id = (await p(admin, "/api/fieldops/material", { model_id: "Doomed", category: "module" }).then((r) => r.json()) as { id: number }).id;
    expect((await p(submitter, `/api/fieldops/material/${id}/delete`)).status).toBe(403);

    const r1 = await p(admin, `/api/fieldops/material/${id}/delete`);
    expect(r1.status).toBe(200);
    expect((await catRow(id)).active).toBe(0); // soft — row kept
    expect(await audits("material_catalog_retire")).toHaveLength(1);

    // idempotent: second retire → 200 already_retired, no second audit
    const r2 = await p(admin, `/api/fieldops/material/${id}/delete`);
    expect(r2.status).toBe(200);
    expect((await r2.json() as { already_retired?: boolean }).already_retired).toBe(true);
    expect(await audits("material_catalog_retire")).toHaveLength(1);

    expect((await p(admin, "/api/fieldops/material/999999/delete")).status).toBe(404);
  });
});

describe("GET /api/fieldops/materials (read)", () => {
  it("submitter CAN read (cap.materials.receive); returns the seed; ?all=1 includes retired", async () => {
    // anon → 401
    expect((await call("/api/fieldops/materials")).status).toBe(401);
    // submitter reads the catalog (200)
    const res = await g(submitter, "/api/fieldops/materials?limit=200");
    expect(res.status).toBe(200);
    const body = await res.json() as { materials: { model_id: string; active: number }[]; next_cursor: string | null };
    expect(Array.isArray(body.materials)).toBe(true);
    expect(body.materials.length).toBeGreaterThanOrEqual(36);
    expect(body.materials.every((m) => m.active === 1)).toBe(true); // active-only by default

    // create + retire one, then prove default read excludes it and ?all=1 includes it
    const id = (await p(admin, "/api/fieldops/material", { model_id: "ZZZ-RETIRE-ME", category: "other" }).then((r) => r.json()) as { id: number }).id;
    await p(admin, `/api/fieldops/material/${id}/delete`);
    const def = await (await g(submitter, "/api/fieldops/materials?all=0&limit=200")).json() as { materials: { id: number }[] };
    expect(def.materials.some((m) => m.id === id)).toBe(false);
    const all = await (await g(admin, "/api/fieldops/materials?all=1&limit=200")).json() as { materials: { id: number }[] };
    expect(all.materials.some((m) => m.id === id)).toBe(true);
  });
});
