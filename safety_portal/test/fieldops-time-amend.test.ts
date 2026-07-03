import { env } from "cloudflare:test";
import { describe, it, expect, beforeEach } from "vitest";
import { call, provision, login, seedJob as seedJobRow, seedPersonnel } from "./helpers";

// ─────────────────────────────────────────────────────────────────────────────
// G2.3 — POST /api/fieldops/time-entry/:uuid/amend — NON-DESTRUCTIVE amend/void
// (SPEC.md §2.3–2.4 / §4.4) + the heads-only reads (§2.5 / §4.5).
//
// The scoping matrix (recorder / other-sub / manager / admin), the chain rules
// (head-only enforced ATOMICALLY in the INSERT's WHERE NOT EXISTS; the original
// row never mutated), the void rule (hours 0 + required reason), inheritance
// (job_id + submitted_as come from the target, never the body), and the
// heads-only jobtracker/personnel reads incl. the NULL-poison regression.
// Real worker via cloudflare:test SELF.fetch + Miniflare D1.
// ─────────────────────────────────────────────────────────────────────────────

function post(cookie: string, body: unknown): Promise<Response> {
  return call("/api/fieldops/time-entry", { method: "POST", cookie, body: JSON.stringify(body) });
}
function amend(cookie: string, target: string, body: unknown): Promise<Response> {
  return call(`/api/fieldops/time-entry/${target}/amend`, { method: "POST", cookie, body: JSON.stringify(body) });
}
const seedJob = (jobId: string, status: string): Promise<void> => seedJobRow(jobId, { status });
async function seedTask(jobId: string): Promise<number> {
  await env.DB.prepare("INSERT INTO task_assignments (job_id, description, status, created_at) VALUES (?,?,?,?)")
    .bind(jobId, "Dig", "open", 1_700_000_000)
    .run();
  return (await env.DB.prepare("SELECT id FROM task_assignments WHERE job_id=? ORDER BY id DESC LIMIT 1").bind(jobId).first<{ id: number }>())!.id;
}
async function rowByUuid(uuid: string) {
  return (await env.DB.prepare("SELECT * FROM time_entries WHERE uuid=?").bind(uuid).first()) as any;
}
async function auditRows(action: string, uuidInDetail: string) {
  const all = (await env.DB.prepare("SELECT * FROM audit_log WHERE action=?").bind(action).all()).results as any[];
  return all.filter((r) => typeof r.detail === "string" && r.detail.includes(uuidInDetail));
}

beforeEach(async () => {
  await env.DB.batch([
    env.DB.prepare("DELETE FROM users"),
    env.DB.prepare("DELETE FROM audit_log"),
    env.DB.prepare("DELETE FROM time_entries"),
    env.DB.prepare("DELETE FROM task_assignments"),
    env.DB.prepare("DELETE FROM personnel"),
    env.DB.prepare("DELETE FROM jobs"),
  ]);
  await provision("admin.one", "password123", "admin");
  await provision("mo.manager", "password123", "manager");
  await provision("submitter.jim", "password123", "submitter");
  await provision("submitter.bob", "password123", "submitter");
  await seedJob("JOB-A", "active");
});

describe("amend — gate + target", () => {
  it("no session → 401", async () => {
    const res = await call("/api/fieldops/time-entry/x/amend", { method: "POST", body: JSON.stringify({ uuid: "a", hours: 1 }) });
    expect(res.status).toBe(401);
  });
  it("unknown target uuid → 404 not_found (nothing persisted)", async () => {
    const c = await login("submitter.jim", "password123");
    const res = await amend(c, "no-such", { uuid: "a1", hours: 2 });
    expect(res.status).toBe(404);
    expect(((await res.json()) as any).error).toBe("not_found");
    expect(await rowByUuid("a1")).toBeNull();
  });
});

describe("amend — WHO (the scoping matrix: recorder / other-sub / manager / admin)", () => {
  beforeEach(async () => {
    const jim = await login("submitter.jim", "password123");
    expect((await post(jim, { uuid: "orig", job_id: "JOB-A", hours: 8, notes: "first" })).status).toBe(201);
  });
  it("the ORIGINAL RECORDER may amend their own entry → 201", async () => {
    const jim = await login("submitter.jim", "password123");
    expect((await amend(jim, "orig", { uuid: "a1", hours: 7 })).status).toBe(201);
  });
  it("a DIFFERENT subcontractor may NOT → 403 forbidden_amend", async () => {
    const bob = await login("submitter.bob", "password123");
    const res = await amend(bob, "orig", { uuid: "a1", hours: 7 });
    expect(res.status).toBe(403);
    expect(((await res.json()) as any).error).toBe("forbidden_amend");
    expect(await rowByUuid("a1")).toBeNull();
  });
  it("a manager (cap.personnel.manage) may amend anyone's entry → 201", async () => {
    const mgr = await login("mo.manager", "password123");
    expect((await amend(mgr, "orig", { uuid: "a1", hours: 7 })).status).toBe(201);
    expect((await rowByUuid("a1")).actor_username).toBe("mo.manager"); // the corrector is stamped
  });
  it("an admin may amend anyone's entry → 201", async () => {
    const adm = await login("admin.one", "password123");
    expect((await amend(adm, "orig", { uuid: "a1", hours: 7 })).status).toBe(201);
  });
});

describe("amend — chain rules (head-only, non-destructive, atomic fold)", () => {
  it("only the HEAD can be amended: amending an already-amended entry → 409 not_head, no audit row", async () => {
    const jim = await login("submitter.jim", "password123");
    await post(jim, { uuid: "e1", job_id: "JOB-A", hours: 8 });
    expect((await amend(jim, "e1", { uuid: "a1", hours: 7 })).status).toBe(201);
    const res = await amend(jim, "e1", { uuid: "a2", hours: 6 }); // e1 is no longer the head
    expect(res.status).toBe(409);
    expect(((await res.json()) as any).error).toBe("not_head");
    expect(await rowByUuid("a2")).toBeNull(); // the folded INSERT matched nothing
    expect(await auditRows("time_entry_edit", "a2")).toHaveLength(0); // conditional audit skipped (changes()=0)
    // …but the NEW head can be amended (chain of 3).
    expect((await amend(jim, "a1", { uuid: "a2", hours: 6 })).status).toBe(201);
    expect((await rowByUuid("a2")).amends_uuid).toBe("a1");
  });

  it("a concurrent/out-of-band amender already on the target → 409 not_head (the atomic fold path)", async () => {
    const jim = await login("submitter.jim", "password123");
    await post(jim, { uuid: "e1", job_id: "JOB-A", hours: 8 });
    // Simulate the racing writer landing first (direct insert, as the loser's request sees it).
    await env.DB.prepare(
      "INSERT INTO time_entries (uuid, job_id, hours, actor_username, amends_uuid) VALUES ('race','JOB-A',5,'submitter.jim','e1')",
    ).run();
    const res = await amend(jim, "e1", { uuid: "a1", hours: 7 });
    expect(res.status).toBe(409);
    expect(((await res.json()) as any).error).toBe("not_head");
  });

  it("NON-DESTRUCTIVE: the amended row is byte-identical after the amend", async () => {
    const jim = await login("submitter.jim", "password123");
    await post(jim, { uuid: "e1", job_id: "JOB-A", hours: 8, notes: "first", work_started_at: 111, work_ended_at: 222 });
    const before = await rowByUuid("e1");
    expect((await amend(jim, "e1", { uuid: "a1", hours: 7, notes: "fixed" })).status).toBe(201);
    expect(await rowByUuid("e1")).toEqual(before); // NEVER mutated — every column identical
  });

  it("uuid replay against a DIFFERENT head → 409 uuid_conflict and the batch rolls back", async () => {
    const jim = await login("submitter.jim", "password123");
    await post(jim, { uuid: "e1", job_id: "JOB-A", hours: 8 });
    await post(jim, { uuid: "e2", job_id: "JOB-A", hours: 4 });
    expect((await amend(jim, "e1", { uuid: "dup", hours: 7 })).status).toBe(201);
    const res = await amend(jim, "e2", { uuid: "dup", hours: 3 });
    expect(res.status).toBe(409);
    expect(((await res.json()) as any).error).toBe("uuid_conflict");
    expect((await rowByUuid("dup")).amends_uuid).toBe("e1"); // the original amend, unchanged
    expect(
      await env.DB.prepare("SELECT COUNT(*) n FROM time_entries WHERE amends_uuid='e2'").first<{ n: number }>(),
    ).toMatchObject({ n: 0 }); // e2 still a head — the failed batch left nothing
  });
});

describe("amend — inheritance (job_id + submitted_as from the TARGET, never the body)", () => {
  it("job_id is inherited; a body job_id / submitted_as / amends_uuid is rejected loudly", async () => {
    const jim = await login("submitter.jim", "password123");
    await post(jim, { uuid: "e1", job_id: "JOB-A", hours: 8 });
    expect((await amend(jim, "e1", { uuid: "a0", hours: 7, job_id: "JOB-B" })).status).toBe(400);
    expect((await amend(jim, "e1", { uuid: "a0", hours: 7, submitted_as: "admin.one" })).status).toBe(400);
    expect((await amend(jim, "e1", { uuid: "a0", hours: 7, amends_uuid: "e1" })).status).toBe(400);
    expect((await amend(jim, "e1", { uuid: "a1", hours: 7 })).status).toBe(201);
    expect((await rowByUuid("a1")).job_id).toBe("JOB-A");
  });

  it("submitted_as inherited: a manager correcting an admin's submit-as row keeps the WORK's attribution", async () => {
    const adm = await login("admin.one", "password123");
    expect((await post(adm, { uuid: "e1", job_id: "JOB-A", hours: 8, submitted_as: "submitter.jim" })).status).toBe(201);
    const mgr = await login("mo.manager", "password123");
    expect((await amend(mgr, "e1", { uuid: "a1", hours: 7 })).status).toBe(201);
    const row = await rowByUuid("a1");
    expect(row.submitted_as).toBe("submitter.jim"); // inherited — the work is still Jim's
    expect(row.actor_username).toBe("mo.manager"); // the corrector is on the record
  });

  it("the entry on a CLOSED job can still be amended (the epic's whole point)", async () => {
    const jim = await login("submitter.jim", "password123");
    await post(jim, { uuid: "e1", job_id: "JOB-A", hours: 8 });
    await env.DB.prepare("UPDATE jobs SET active = 0, status = 'closed' WHERE job_id = 'JOB-A'").run();
    expect((await amend(jim, "e1", { uuid: "a1", hours: 7 })).status).toBe(201);
  });
});

describe("amend — void (hours 0 + REQUIRED reason) + hours bounds", () => {
  it("hours 0 WITHOUT a reason → 422 void_requires_reason; with a reason → 201 (audit detail marks the void)", async () => {
    const jim = await login("submitter.jim", "password123");
    await post(jim, { uuid: "e1", job_id: "JOB-A", hours: 8 });
    for (const notes of [undefined, "", "   "]) {
      const res = await amend(jim, "e1", { uuid: "v0", hours: 0, ...(notes !== undefined ? { notes } : {}) });
      expect(res.status, `notes=${JSON.stringify(notes)}`).toBe(422);
      expect(((await res.json()) as any).error).toBe("void_requires_reason");
    }
    expect((await amend(jim, "e1", { uuid: "v1", hours: 0, notes: "logged twice" })).status).toBe(201);
    const row = await rowByUuid("v1");
    expect(row.hours).toBe(0);
    expect(row.notes).toBe("logged twice");
    const audits = await auditRows("time_entry_edit", "v1");
    expect(audits).toHaveLength(1);
    expect(audits[0].detail).toContain('"void":true');
  });

  it("amend hours bounds are [0, 24]: negative / >24 / missing → 422 invalid_hours; 24 passes", async () => {
    const jim = await login("submitter.jim", "password123");
    await post(jim, { uuid: "e1", job_id: "JOB-A", hours: 8 });
    for (const body of [{ uuid: "b1" }, { uuid: "b2", hours: -1 }, { uuid: "b3", hours: 24.5 }, { uuid: "b4", hours: "8" }]) {
      const res = await amend(jim, "e1", body);
      expect(res.status, JSON.stringify(body)).toBe(422);
      expect(((await res.json()) as any).error).toBe("invalid_hours");
    }
    expect((await amend(jim, "e1", { uuid: "b5", hours: 24 })).status).toBe(201);
  });

  it("a VOID is a head like any other — amending it again is the recovery path", async () => {
    const jim = await login("submitter.jim", "password123");
    await post(jim, { uuid: "e1", job_id: "JOB-A", hours: 8 });
    expect((await amend(jim, "e1", { uuid: "v1", hours: 0, notes: "mistake" })).status).toBe(201);
    expect((await amend(jim, "v1", { uuid: "r1", hours: 8, notes: "un-voided" })).status).toBe(201);
    expect((await rowByUuid("r1")).amends_uuid).toBe("v1");
  });
});

describe("amend — corrected-subject referential guards (same rules as create)", () => {
  it("subcontractor scoping: a personnel the actor doesn't own → 403; own-created → 201; manager unrestricted", async () => {
    const jim = await login("submitter.jim", "password123");
    await post(jim, { uuid: "e1", job_id: "JOB-A", hours: 8 });
    const foreign = await seedPersonnel("Foreign Person", null, null, { created_by: "mo.manager" });
    const mine = await seedPersonnel("My Person", null, null, { created_by: "submitter.jim" });
    const res = await amend(jim, "e1", { uuid: "a1", hours: 7, personnel_id: foreign });
    expect(res.status).toBe(403);
    expect(((await res.json()) as any).error).toBe("forbidden_personnel");
    expect((await amend(jim, "e1", { uuid: "a2", hours: 7, personnel_id: mine })).status).toBe(201);
    // manager: any ACTIVE roster member (amend the new head a2 → the foreign person).
    const mgr = await login("mo.manager", "password123");
    expect((await amend(mgr, "a2", { uuid: "a3", hours: 7, personnel_id: foreign })).status).toBe(201);
  });

  it("a retired personnel → 422 unknown_personnel; a task from a different job → 422 unknown_task", async () => {
    const jim = await login("submitter.jim", "password123");
    await post(jim, { uuid: "e1", job_id: "JOB-A", hours: 8 });
    const retired = await seedPersonnel("Gone Person", null, null, { created_by: "submitter.jim", active: 0 });
    expect((await amend(jim, "e1", { uuid: "a1", hours: 7, personnel_id: retired })).status).toBe(422);
    await seedJob("JOB-B", "active");
    const otherTask = await seedTask("JOB-B");
    const res = await amend(jim, "e1", { uuid: "a2", hours: 7, task_id: otherTask });
    expect(res.status).toBe(422);
    expect(((await res.json()) as any).error).toBe("unknown_task");
    const okTask = await seedTask("JOB-A");
    expect((await amend(jim, "e1", { uuid: "a3", hours: 7, task_id: okTask })).status).toBe(201);
  });
});

// ── §4.5 — heads-only reads (jobtracker detail + personnel list/detail) ──────────────────────────
describe("heads-only reads", () => {
  it("jobtracker time leg lists ONLY chain heads, flags amended/voided, and NULL amends_uuid rows never poison the filter", async () => {
    const jim = await login("submitter.jim", "password123");
    // Three originals (amends_uuid NULL — the NOT-IN poison bait), one amended, one voided.
    await post(jim, { uuid: "e1", job_id: "JOB-A", hours: 8 });
    await post(jim, { uuid: "e2", job_id: "JOB-A", hours: 4 });
    await post(jim, { uuid: "e3", job_id: "JOB-A", hours: 2 });
    expect((await amend(jim, "e1", { uuid: "a1", hours: 7 })).status).toBe(201);
    expect((await amend(jim, "e2", { uuid: "v1", hours: 0, notes: "dup entry" })).status).toBe(201);

    const body = (await (await call("/api/fieldops/jobs/JOB-A", { cookie: jim })).json()) as { job: any };
    const uuids = body.job.time_entries.map((t: any) => t.uuid).sort();
    expect(uuids).toEqual(["a1", "e3", "v1"]); // e1/e2 superseded; the untouched original e3 survives
    const byUuid = new Map(body.job.time_entries.map((t: any) => [t.uuid, t]));
    expect((byUuid.get("a1") as any).amended).toBe(true);
    expect((byUuid.get("a1") as any).voided).toBe(false);
    expect((byUuid.get("v1") as any).voided).toBe(true);
    expect((byUuid.get("e3") as any).amended).toBe(false);
    // W9 posture: the raw chain/actor columns are STRIPPED from the wire.
    expect((byUuid.get("a1") as any).amends_uuid).toBeUndefined();
    expect((byUuid.get("a1") as any).actor_username).toBeUndefined();
  });

  it("can_amend mirrors the WHO rule: true for the recorder + cap.personnel.manage, false for another sub", async () => {
    const jim = await login("submitter.jim", "password123");
    await post(jim, { uuid: "e1", job_id: "JOB-A", hours: 8 });
    const mine = (await (await call("/api/fieldops/jobs/JOB-A", { cookie: jim })).json()) as { job: any };
    expect(mine.job.time_entries[0].can_amend).toBe(true); // recorder
    const bob = await login("submitter.bob", "password123");
    const theirs = (await (await call("/api/fieldops/jobs/JOB-A", { cookie: bob })).json()) as { job: any };
    expect(theirs.job.time_entries[0].can_amend).toBe(false); // neither recorder nor manager
    const mgr = await login("mo.manager", "password123");
    const mgrs = (await (await call("/api/fieldops/jobs/JOB-A", { cookie: mgr })).json()) as { job: any };
    expect(mgrs.job.time_entries[0].can_amend).toBe(true); // cap.personnel.manage
  });

  it("personnel list latest-entry + personnel detail history resolve to heads only", async () => {
    const pid = await seedPersonnel("Alice Chen", null, null, { created_by: "submitter.jim" });
    const jim = await login("submitter.jim", "password123");
    await post(jim, { uuid: "e1", job_id: "JOB-A", hours: 8, personnel_id: pid });
    expect((await amend(jim, "e1", { uuid: "a1", hours: 6, personnel_id: pid })).status).toBe(201);

    const adm = await login("admin.one", "password123");
    const list = (await (await call("/api/fieldops/personnel", { cookie: adm })).json()) as { latest_entries: any[] };
    const latest = list.latest_entries.find((e) => e.personnel_id === pid);
    expect(latest?.hours).toBe(6); // the head, not the superseded 8h original

    const detail = (await (await call(`/api/fieldops/personnel/${pid}`, { cookie: adm })).json()) as { personnel: any };
    expect(detail.personnel.time_entries.map((t: any) => t.uuid)).toEqual(["a1"]); // e1 hidden
  });
});
