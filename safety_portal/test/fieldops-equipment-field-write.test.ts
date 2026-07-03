import { env } from "cloudflare:test";
import { describe, it, expect, beforeEach } from "vitest";
import { call, provision, login, p, seedJob as seedJobRow } from "./helpers";

// ─────────────────────────────────────────────────────────────────────────────
// P2.3 Slice 4 — EQUIPMENT FIELD WRITE (status + location). cap.equipment.field (submitter+admin).
// Headline: the status DUAL WRITE (append equipment_logs row + update the equipment snapshot) is
// atomic — both land, or a failure rolls back both.
// ─────────────────────────────────────────────────────────────────────────────

async function seedEquipment(name: string): Promise<number> {
  await env.DB.prepare("INSERT INTO equipment (name, active) VALUES (?,1)").bind(name).run();
  return (await env.DB.prepare("SELECT id FROM equipment WHERE name=?").bind(name).first<{ id: number }>())!.id;
}
const seedJob = (jobId: string, status: string): Promise<void> => seedJobRow(jobId, { status, projectName: `P ${jobId}` });
async function eqRow(id: number) {
  return await env.DB.prepare("SELECT * FROM equipment WHERE id=?").bind(id).first<any>();
}
async function logRows(uuid: string) {
  return ((await env.DB.prepare("SELECT * FROM equipment_logs WHERE uuid=?").bind(uuid).all()).results as any[]);
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
    env.DB.prepare("DELETE FROM equipment_location"),
    env.DB.prepare("DELETE FROM equipment"),
    env.DB.prepare("DELETE FROM jobs"),
  ]);
  await provision("admin.one", "password123", "admin");
  await provision("submitter.jim", "password123", "submitter");
  admin = await login("admin.one", "password123");
  submitter = await login("submitter.jim", "password123");
});

describe("POST /api/fieldops/equipment/:id/status", () => {
  it("gate: anon → 401, submitter → 201 (has cap.equipment.field), admin → 201", async () => {
    const id = await seedEquipment("unit-a");
    expect((await call(`/api/fieldops/equipment/${id}/status`, { method: "POST", body: JSON.stringify({ uuid: "x", status: "down" }) })).status).toBe(401);
    expect((await p(submitter, `/api/fieldops/equipment/${id}/status`, { uuid: "s1", status: "degraded" })).status).toBe(201);
    expect((await p(admin, `/api/fieldops/equipment/${id}/status`, { uuid: "a1", status: "fmc" })).status).toBe(201);
  });

  it("DUAL WRITE: appends a log row AND updates the snapshot, with the audit, atomically", async () => {
    const id = await seedEquipment("unit-a");
    expect((await p(submitter, `/api/fieldops/equipment/${id}/status`, { uuid: "s1", status: "degraded", status_note: "brake wear" })).status).toBe(201);
    const log = (await logRows("s1"))[0];
    expect(log.log_type).toBe("status");
    expect(log.status_value).toBe("degraded");
    expect(log.detail).toBe("brake wear");
    expect(log.actor_username).toBe("submitter.jim");
    expect(log.created_at).toBeGreaterThan(1_000_000_000); // server DEFAULT
    const eq = await eqRow(id);
    expect(eq.status).toBe("degraded");
    expect(eq.status_note).toBe("brake wear");
    expect(eq.status_actor).toBe("submitter.jim");
    expect(eq.status_changed_at).toBeGreaterThan(1_000_000_000);
    expect(await audits("equipment_status")).toHaveLength(1);
  });

  it("uuid collision → 409 and BOTH writes roll back (snapshot unchanged, no 2nd log/audit)", async () => {
    const id = await seedEquipment("unit-a");
    expect((await p(submitter, `/api/fieldops/equipment/${id}/status`, { uuid: "dup", status: "down" })).status).toBe(201);
    expect((await eqRow(id)).status).toBe("down");
    expect((await p(submitter, `/api/fieldops/equipment/${id}/status`, { uuid: "dup", status: "degraded" })).status).toBe(409);
    expect((await eqRow(id)).status).toBe("down"); // the 2nd batch rolled back → snapshot NOT changed to degraded
    expect(await logRows("dup")).toHaveLength(1);
    expect(await audits("equipment_status")).toHaveLength(1);
  });

  it("append-only edit chain: an amend is a NEW log row (original untouched)", async () => {
    const id = await seedEquipment("unit-a");
    await p(submitter, `/api/fieldops/equipment/${id}/status`, { uuid: "orig", status: "degraded", status_note: "first" });
    expect((await p(submitter, `/api/fieldops/equipment/${id}/status`, { uuid: "amend", status: "down", status_note: "worse", amends_uuid: "orig" })).status).toBe(201);
    expect((await logRows("orig"))[0].status_value).toBe("degraded"); // original log row untouched
    expect((await logRows("amend"))[0].amends_uuid).toBe("orig");
    expect(await audits("equipment_status_edit")).toHaveLength(1);
  });

  it("guards: bad status enum / missing uuid → 400; unknown or retired equipment → 404", async () => {
    const id = await seedEquipment("unit-a");
    expect((await p(submitter, `/api/fieldops/equipment/${id}/status`, { uuid: "x", status: "broken" })).status).toBe(400);
    expect((await p(submitter, `/api/fieldops/equipment/${id}/status`, { status: "down" })).status).toBe(400);
    expect((await p(submitter, `/api/fieldops/equipment/999999/status`, { uuid: "x", status: "down" })).status).toBe(404);
  });

  it("dual attribution: submitter submit-as → 403; admin submit-as to a real user → 201 (normalized)", async () => {
    const id = await seedEquipment("unit-a");
    expect((await p(submitter, `/api/fieldops/equipment/${id}/status`, { uuid: "x", status: "down", submitted_as: "admin.one" })).status).toBe(403);
    expect((await p(admin, `/api/fieldops/equipment/${id}/status`, { uuid: "y", status: "down", submitted_as: "Submitter.Jim" })).status).toBe(201);
    expect((await logRows("y"))[0].submitted_as).toBe("submitter.jim");
  });
});

describe("POST /api/fieldops/equipment/:id/location", () => {
  it("appends a location with server recorded_at vs field read_at; audits", async () => {
    const id = await seedEquipment("unit-a");
    await seedJob("JOB-A", "active");
    expect((await p(submitter, `/api/fieldops/equipment/${id}/location`, { job_id: "JOB-A", label: "Site A", read_at: 1700000000 })).status).toBe(201);
    const loc = await env.DB.prepare("SELECT * FROM equipment_location WHERE equipment_id=?").bind(id).first<any>();
    expect(loc.job_id).toBe("JOB-A");
    expect(loc.label).toBe("Site A");
    expect(loc.read_at).toBe(1700000000); // field claim verbatim
    expect(loc.recorded_at).toBeGreaterThan(1_000_000_000); // server DEFAULT
    expect(loc.actor_username).toBe("submitter.jim");
    expect(await audits("equipment_move")).toHaveLength(1);
  });

  it("unknown/closed job → 422; unknown equipment → 404; anon → 401", async () => {
    const id = await seedEquipment("unit-a");
    expect((await p(submitter, `/api/fieldops/equipment/${id}/location`, { job_id: "NOPE" })).status).toBe(422);
    await seedJob("JOB-Z", "closed");
    expect((await p(submitter, `/api/fieldops/equipment/${id}/location`, { job_id: "JOB-Z" })).status).toBe(422);
    await seedJob("JOB-A", "active");
    expect((await p(submitter, "/api/fieldops/equipment/999999/location", { job_id: "JOB-A" })).status).toBe(404);
    expect((await call(`/api/fieldops/equipment/${id}/location`, { method: "POST", body: JSON.stringify({ job_id: "JOB-A" }) })).status).toBe(401);
  });
});
