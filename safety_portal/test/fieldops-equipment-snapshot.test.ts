import { env } from "cloudflare:test";
import { describe, it, expect, beforeEach } from "vitest";
import { call, seedJob } from "./helpers";

// P7 Slice 2 — the field-ops Equipment Status & Location snapshot route
// (GET /api/internal/fieldops/equipment-snapshot). A SNAPSHOT (re-projected each cycle) of the
// CURRENT on-active-job equipment: one row per equipment whose LATEST equipment_location sits on
// an ACTIVE job. No watermark, no mark-mirrored. Same field-ops token privilege separation as the
// job/hours mirror queues.

const FIELDOPS_BEARER = "test-fieldops-token"; // == PORTAL_FIELDOPS_API_TOKEN (vitest.config.ts)
const INTERNAL_BEARER = "test-internal-token"; // portal_poll's token — must be REJECTED here
const ADMIN_BEARER = "test-admin-token"; // operator token — must be REJECTED here

const PATH = "/api/internal/fieldops/equipment-snapshot";

interface SnapshotRow {
  equipment_id: number;
  job_id: string;
  project_name: string;
  name: string;
  kind: string | null;
  identifier: string | null;
  status: string;
  status_note: string | null;
  status_changed_at: number | null;
  location_label: string | null;
  lat: number | null;
  lon: number | null;
  read_at: number | null;
  recorded_at: number;
}

async function seedEquipment(
  name: string,
  opts: { kind?: string; identifier?: string; active?: 0 | 1; status?: string; statusNote?: string } = {},
): Promise<number> {
  await env.DB.prepare(
    "INSERT INTO equipment (name, kind, identifier, active, status, status_note, status_changed_at) VALUES (?,?,?,?,?,?,?)",
  )
    .bind(
      name,
      opts.kind ?? "skid-steer",
      opts.identifier ?? "SK-001",
      opts.active ?? 1,
      opts.status ?? "fmc",
      opts.statusNote ?? null,
      1751000000,
    )
    .run();
  return (await env.DB.prepare("SELECT id FROM equipment WHERE name=? ORDER BY id DESC LIMIT 1")
    .bind(name)
    .first<{ id: number }>())!.id;
}

async function seedLocation(
  equipmentId: number,
  jobId: string | null,
  recordedAt: number,
  opts: { label?: string; lat?: number; lon?: number; readAt?: number } = {},
): Promise<void> {
  await env.DB.prepare(
    "INSERT INTO equipment_location (equipment_id, job_id, lat, lon, label, read_at, recorded_at) VALUES (?,?,?,?,?,?,?)",
  )
    .bind(
      equipmentId,
      jobId,
      opts.lat ?? 37.7749,
      opts.lon ?? -122.4194,
      opts.label ?? "North lot",
      opts.readAt ?? recordedAt - 100,
      recordedAt,
    )
    .run();
}

beforeEach(async () => {
  await env.DB.batch([
    env.DB.prepare("DELETE FROM equipment_location"),
    env.DB.prepare("DELETE FROM equipment"),
    env.DB.prepare("DELETE FROM jobs"),
  ]);
});

describe("GET /api/internal/fieldops/equipment-snapshot", () => {
  it("rejects the portal_poll + admin tokens and no token (privilege separation)", async () => {
    expect((await call(PATH)).status).toBe(401);
    expect((await call(PATH, { bearer: INTERNAL_BEARER })).status).toBe(401);
    expect((await call(PATH, { bearer: ADMIN_BEARER })).status).toBe(401);
    expect((await call(PATH, { bearer: FIELDOPS_BEARER })).status).toBe(200);
  });

  it("projects the full field set for equipment on an active job", async () => {
    await seedJob("JOB-A", { projectName: "Job Alpha", status: "active" });
    const eq = await seedEquipment("Unit Alpha", {
      kind: "telehandler",
      identifier: "TH-9",
      status: "degraded",
      statusNote: "hydraulic leak",
    });
    await seedLocation(eq, "JOB-A", 1751002000, { label: "South yard", lat: 30.1, lon: -80.2, readAt: 1751001900 });

    const res = await call(PATH, { bearer: FIELDOPS_BEARER });
    expect(res.status, await res.clone().text()).toBe(200);
    const { equipment } = (await res.json()) as { equipment: SnapshotRow[] };
    expect(equipment).toHaveLength(1);
    const r = equipment[0];
    expect(r).toMatchObject({
      equipment_id: eq,
      job_id: "JOB-A",
      project_name: "Job Alpha",
      name: "Unit Alpha",
      kind: "telehandler",
      identifier: "TH-9",
      status: "degraded",
      status_note: "hydraulic leak",
      status_changed_at: 1751000000,
      location_label: "South yard",
      lat: 30.1,
      lon: -80.2,
      read_at: 1751001900,
      recorded_at: 1751002000,
    });
  });

  it("excludes equipment whose LATEST location is on a non-active (closed/on_hold) job", async () => {
    await seedJob("JOB-ACTIVE", { projectName: "Active Job", status: "active" });
    await seedJob("JOB-CLOSED", { projectName: "Closed Job", status: "closed" });
    // Latest location is on the CLOSED job (even though an older one was on the active job).
    const eq = await seedEquipment("Unit Closed");
    await seedLocation(eq, "JOB-ACTIVE", 1751000000);
    await seedLocation(eq, "JOB-CLOSED", 1751005000); // newer → wins the window → dropped by INNER JOIN

    const res = await call(PATH, { bearer: FIELDOPS_BEARER });
    const { equipment } = (await res.json()) as { equipment: SnapshotRow[] };
    expect(equipment).toHaveLength(0);
  });

  it("uses ONLY the latest location — an item that moved from a closed to an active job is included on the active job", async () => {
    await seedJob("JOB-CLOSED", { projectName: "Closed Job", status: "closed" });
    await seedJob("JOB-ACTIVE-B", { projectName: "Active Job B", status: "active" });
    const eq = await seedEquipment("Unit Moved");
    await seedLocation(eq, "JOB-CLOSED", 1751000000); // older
    await seedLocation(eq, "JOB-ACTIVE-B", 1751009000); // newer → wins → included on the active job

    const res = await call(PATH, { bearer: FIELDOPS_BEARER });
    const { equipment } = (await res.json()) as { equipment: SnapshotRow[] };
    expect(equipment).toHaveLength(1);
    expect(equipment[0]).toMatchObject({ equipment_id: eq, job_id: "JOB-ACTIVE-B", project_name: "Active Job B" });
  });

  it("breaks a recorded_at tie by highest location id (ORDER BY recorded_at DESC, id DESC)", async () => {
    await seedJob("JOB-1", { projectName: "One", status: "active" });
    await seedJob("JOB-2", { projectName: "Two", status: "active" });
    const eq = await seedEquipment("Unit Tie");
    await seedLocation(eq, "JOB-1", 1751000000); // lower id
    await seedLocation(eq, "JOB-2", 1751000000); // same recorded_at, higher id → wins

    const res = await call(PATH, { bearer: FIELDOPS_BEARER });
    const { equipment } = (await res.json()) as { equipment: SnapshotRow[] };
    expect(equipment).toHaveLength(1);
    expect(equipment[0].job_id).toBe("JOB-2");
  });

  it("excludes retired equipment (active=0) even when on an active job", async () => {
    await seedJob("JOB-A", { projectName: "Job Alpha", status: "active" });
    const eq = await seedEquipment("Unit Retired", { active: 0 });
    await seedLocation(eq, "JOB-A", 1751002000);

    const res = await call(PATH, { bearer: FIELDOPS_BEARER });
    const { equipment } = (await res.json()) as { equipment: SnapshotRow[] };
    expect(equipment).toHaveLength(0);
  });

  it("excludes equipment whose latest location has a NULL job_id (not on any job)", async () => {
    await seedJob("JOB-A", { projectName: "Job Alpha", status: "active" });
    const eq = await seedEquipment("Unit Unassigned");
    await seedLocation(eq, null, 1751002000);

    const res = await call(PATH, { bearer: FIELDOPS_BEARER });
    const { equipment } = (await res.json()) as { equipment: SnapshotRow[] };
    expect(equipment).toHaveLength(0);
  });

  it("returns all active-job equipment ordered by project_name then name", async () => {
    await seedJob("JOB-B", { projectName: "Bravo", status: "active" });
    await seedJob("JOB-A", { projectName: "Alpha", status: "active" });
    const e1 = await seedEquipment("Zeta", { identifier: "Z" });
    const e2 = await seedEquipment("Apex", { identifier: "A" });
    const e3 = await seedEquipment("Nova", { identifier: "N" });
    await seedLocation(e1, "JOB-B", 1751000000); // Bravo / Zeta
    await seedLocation(e2, "JOB-A", 1751000000); // Alpha / Apex
    await seedLocation(e3, "JOB-A", 1751000000); // Alpha / Nova

    const res = await call(PATH, { bearer: FIELDOPS_BEARER });
    const { equipment } = (await res.json()) as { equipment: SnapshotRow[] };
    expect(equipment.map((r) => `${r.project_name}/${r.name}`)).toEqual([
      "Alpha/Apex",
      "Alpha/Nova",
      "Bravo/Zeta",
    ]);
  });
});
