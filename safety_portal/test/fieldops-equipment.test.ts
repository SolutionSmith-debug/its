import { env } from "cloudflare:test";
import { describe, it, expect, beforeEach, afterAll } from "vitest";
import { call, provision, login } from "./helpers";

// ─────────────────────────────────────────────────────────────────────────────
// BRIEF B — Equipment tab (cap.equipment.field, SUBMITTER + ADMIN)
// Runs against the REAL worker with Miniflare D1; SELF.fetch cookie-forwarding.
// ─────────────────────────────────────────────────────────────────────────────


// Seed equipment + location/inspections/logs
async function seedEquipment(): Promise<{ unitAId: number; unitBId: number }> {
  // Equipment columns: id (auto), name, kind, identifier, active, created_at
  await env.DB.prepare("INSERT INTO equipment (name, kind, identifier, active) VALUES (?,?,?,1)")
    .bind("Unit Alpha", "skid-steer", "SK-001")
    .run();
  const unitAId = (await env.DB.prepare("SELECT id FROM equipment WHERE name='Unit Alpha'").first<{id:number}>())!.id;
  
  await env.DB.prepare("INSERT INTO equipment (name, kind, identifier, active) VALUES (?,?,?,1)")
    .bind("Unit Beta", "telehandler", "TH-002")
    .run();
  const unitBId = (await env.DB.prepare("SELECT id FROM equipment WHERE name='Unit Beta'").first<{id:number}>())!.id;
  
  return { unitAId, unitBId };
}

async function seedLocation(equipmentId: number, label: string | null): Promise<void> {
  await env.DB.prepare(
    "INSERT INTO equipment_location (equipment_id, job_id, lat, lon, label, recorded_at, actor_username) VALUES (?,?,?,?,?,?,?)",
  )
    .bind(equipmentId, "JOB-A", 37.7749, -122.4194, label, Math.floor(Date.now() / 1000), "admin.one")
    .run();
}

async function seedInspection(equipmentId: number): Promise<void> {
  await env.DB.prepare(
    "INSERT INTO inspections (uuid, job_id, equipment_id, form_code, version, payload_json, performed_at, actor_username, submitted_as) VALUES (?,?,?,?,?,?,?,?,?)",
  )
    .bind(`insp-${equipmentId}`, "JOB-A", equipmentId, "skid-daily", 1, "{}", Math.floor(Date.now() / 1000) - 3600, "admin.one", "admin.one")
    .run();
}

async function seedLogs(equipmentId: number, count: number): Promise<void> {
  const now = Math.floor(Date.now() / 1000);
  for (let i = 0; i < count; i++) {
    await env.DB.prepare(
      "INSERT INTO equipment_logs (uuid, equipment_id, log_type, value_num, detail, performed_at, actor_username, submitted_as) VALUES (?,?,?,?,?,?,?,?)",
    )
      .bind(`log-${equipmentId}-${i}`, equipmentId, "fuel", 50 + i, `Fuel top-up ${i}`, now - i * 3600, "admin.one", "admin.one")
      .run();
  }
}

beforeEach(async () => {
  await env.DB.batch([
    env.DB.prepare("DELETE FROM users"),
    // Delete in reverse dependency order
    env.DB.prepare("DELETE FROM equipment_logs"),
    env.DB.prepare("DELETE FROM inspections"),
    env.DB.prepare("DELETE FROM equipment_location"),
    env.DB.prepare("DELETE FROM equipment"),
  ]);
});

// ── GET /api/fieldops/equipment (list) ────────────────────────────────────────
describe("GET /api/fieldops/equipment", () => {
  beforeEach(async () => {
    await provision("admin.one", "password123", "admin");
    await provision("submitter.jim", "password123", "submitter");
  });

  it("no session → 401 (requireSession)", async () => {
    const res = await call("/api/fieldops/equipment");
    expect(res.status).toBe(401);
  });

  it("submitter is allowed — cap.equipment.field is submitter + admin (not admin-only) → 200", async () => {
    // Unlike Brief A's admin-only personnel, equipment grants cap.equipment.field to submitter
    // too (migration 0013), so there is no missing-cap 403 case here.
    const c = await login("submitter.jim", "password123");
    const res = await call("/api/fieldops/equipment", { cookie: c });
    expect(res.status).toBe(200);
  });

  it("admin — empty list when no equipment", async () => {
    const c = await login("admin.one", "password123");
    const res = await call("/api/fieldops/equipment", { cookie: c });
    expect(res.status).toBe(200);
    const body = (await res.json()) as { equipment: any[]; next_cursor: string | null };
    expect(body.equipment).toEqual([]);
    expect(body.next_cursor).toBeNull();
  });

  it("admin — returns fleet + latest location/inspection/logs per unit", async () => {
    const { unitAId } = await seedEquipment();
    await seedLocation(unitAId, "Job Site A");
    await seedInspection(unitAId);
    await seedLogs(unitAId, 2); // 2 logs (under the 5/unit limit)
    
    const c = await login("admin.one", "password123");
    const res = await call("/api/fieldops/equipment", { cookie: c });
    expect(res.status).toBe(200);
    const body = (await res.json()) as { equipment: any[]; next_cursor: string | null };
    expect(body.equipment).toHaveLength(2);
    expect(body.equipment.map((e) => e.name)).toEqual(["Unit Alpha", "Unit Beta"]);
    
    // Unit Alpha should have the seeded data
    const unitA = body.equipment.find((e) => e.id === unitAId);
    expect(unitA).not.toBeNull();
    expect(unitA!.location).not.toBeNull();
    expect(unitA!.latest_inspection).not.toBeNull();
    expect(unitA!.recent_logs).toHaveLength(2);
    
    expect(body.next_cursor).toBeNull(); // less than limit (2 < 50)
  });

  it("honors limit + next_cursor walks page 2 with no overlap", async () => {
    // Seed > limit equipment
    for (let i = 0; i < 75; i++) {
      await env.DB.prepare("INSERT INTO equipment (name, kind, identifier, active) VALUES (?,?,?,1)")
        .bind(`Unit ${i}`, "vehicle", `UNIT-${i}`)
        .run();
    }
    const c = await login("admin.one", "password123");

    // Page 1
    let res = await call("/api/fieldops/equipment?limit=50", { cookie: c });
    expect(res.status).toBe(200);
    let body = (await res.json()) as { equipment: any[]; next_cursor: string | null };
    expect(body.equipment).toHaveLength(50);
    expect(body.next_cursor).not.toBeNull();

    const page1Ids = body.equipment.map((e) => e.id);

    // Page 2
    const cursor = body.next_cursor!;
    res = await call(`/api/fieldops/equipment?limit=50&cursor=${cursor}`, { cookie: c });
    expect(res.status).toBe(200);
    body = (await res.json()) as { equipment: any[]; next_cursor: string | null };
    expect(body.equipment).toHaveLength(25); // 75 total, 50 on page 1
    const page2Ids = body.equipment.map((e) => e.id);

    // No overlap
    for (const id of page2Ids) {
      expect(page1Ids.includes(id)).toBe(false);
    }
    expect(body.next_cursor).toBeNull(); // last page
  });

  it("hostile non-primitive cursor → first page (200), never 500", async () => {
    await seedEquipment();
    const c = await login("admin.one", "password123");
    const hostile = btoa(JSON.stringify({ n: {}, i: [] }))
      .replace(/\+/g, "-")
      .replace(/\//g, "_")
      .replace(/=+$/, "");
    const res = await call(`/api/fieldops/equipment?cursor=${hostile}`, { cookie: c });
    expect(res.status, await res.clone().text()).toBe(200);
    const body = (await res.json()) as { equipment: any[] };
    expect(body.equipment.length).toBeGreaterThan(0); // served the first page
  });

  it("returns exactly 5 logs per unit (windowed batch)", async () => {
    const { unitAId } = await seedEquipment();
    await seedLogs(unitAId, 12); // more than the 5/unit limit
    await seedLocation(unitAId, "Site A");
    
    const c = await login("admin.one", "password123");
    const res = await call("/api/fieldops/equipment", { cookie: c });
    expect(res.status).toBe(200);
    const body = (await res.json()) as { equipment: any[] };
    
    const unitA = body.equipment.find((e) => e.id === unitAId);
    expect(unitA!.recent_logs).toHaveLength(5); // exactly 5, not 12
  });
});

// ── GET /api/fieldops/equipment/:id (detail) ───────────────────────────────────
describe("GET /api/fieldops/equipment/:id", () => {
  beforeEach(async () => {
    await provision("admin.one", "password123", "admin");
    await provision("submitter.jim", "password123", "submitter");
    const { unitAId } = await seedEquipment();
    (globalThis as any).__TEST_UNIT_A_ID__ = unitAId;
  });

  afterAll(() => {
    delete (globalThis as any).__TEST_UNIT_A_ID__;
  });

  it("no session → 401", async () => {
    const res = await call("/api/fieldops/equipment/1");
    expect(res.status).toBe(401);
  });

  it("submitter is allowed on detail (submitter + admin) → 200", async () => {
    const unitAId = (globalThis as any).__TEST_UNIT_A_ID__;
    const c = await login("submitter.jim", "password123");
    const res = await call(`/api/fieldops/equipment/${unitAId}`, { cookie: c });
    expect(res.status).toBe(200);
  });

  it("non-integer id → 400", async () => {
    const c = await login("admin.one", "password123");
    const res = await call("/api/fieldops/equipment/notanumber", { cookie: c });
    expect(res.status).toBe(400);
  });

  it("unknown id → 404", async () => {
    const c = await login("admin.one", "password123");
    const res = await call("/api/fieldops/equipment/999999", { cookie: c });
    expect(res.status).toBe(404);
  });

  it("valid id returns header with snapshot columns + empty history when none", async () => {
    const unitAId = (globalThis as any).__TEST_UNIT_A_ID__;
    const c = await login("admin.one", "password123");
    const res = await call(`/api/fieldops/equipment/${unitAId}`, { cookie: c });
    expect(res.status).toBe(200);
    const body = (await res.json()) as { equipment: { header: any; locations: any[]; inspections: any[]; logs: any[] }; cursors: any };
    expect(body.equipment.header.id).toBe(unitAId);
    expect(body.equipment.header.name).toBe("Unit Alpha");
    expect(body.equipment.header.status).toBe("fmc"); // default from 0016
    expect(body.equipment.locations).toEqual([]);
    expect(body.equipment.inspections).toEqual([]);
    expect(body.equipment.logs).toEqual([]);
    expect(body.cursors.loc).toBeNull();
    expect(body.cursors.insp).toBeNull();
    expect(body.cursors.log).toBeNull();
  });

  it("returns header + history with cursors for each leg", async () => {
    const unitAId = (globalThis as any).__TEST_UNIT_A_ID__;
    await seedLocation(unitAId, "Site A");
    await seedInspection(unitAId);
    await seedLogs(unitAId, 3);
    
    const c = await login("admin.one", "password123");
    const res = await call(`/api/fieldops/equipment/${unitAId}`, { cookie: c });
    expect(res.status).toBe(200);
    const body = (await res.json()) as { equipment: { header: any; locations: any[]; inspections: any[]; logs: any[] }; cursors: any };

    expect(body.equipment.header.id).toBe(unitAId);
    expect(body.equipment.locations).toHaveLength(1);
    expect(body.equipment.inspections).toHaveLength(1);
    expect(body.equipment.logs).toHaveLength(3);
  });

  it("independent cursors paginate each leg without overlap", async () => {
    const unitAId = (globalThis as any).__TEST_UNIT_A_ID__;
    // Seed > limit logs for this test
    await seedLogs(unitAId, 75);
    
    const c = await login("admin.one", "password123");

    // Page 1 of logs
    let res = await call(`/api/fieldops/equipment/${unitAId}?log_cursor=&limit=50`, { cookie: c });
    expect(res.status).toBe(200);
    let body = (await res.json()) as { equipment: { logs: any[] }; cursors: any };
    expect(body.equipment.logs).toHaveLength(50);
    expect(body.cursors.log).not.toBeNull();

    const page1Ids = body.equipment.logs.map((l: any) => l.uuid);

    // Page 2 of logs
    const cursor = body.cursors.log!;
    res = await call(`/api/fieldops/equipment/${unitAId}?log_cursor=${cursor}&limit=50`, { cookie: c });
    expect(res.status).toBe(200);
    body = (await res.json()) as { equipment: { logs: any[] }; cursors: any };
    expect(body.equipment.logs).toHaveLength(25); // 75 total, 50 on page 1
    const page2Ids = body.equipment.logs.map((l: any) => l.uuid);

    for (const id of page2Ids) {
      expect(page1Ids.includes(id)).toBe(false);
    }
  });

});
