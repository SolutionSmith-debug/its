import { env } from "cloudflare:test";
import { describe, it, expect, beforeEach } from "vitest";
import { call, json, provision, login, seedJob as seedJobRow, seedPersonnel } from "./helpers";

// ─────────────────────────────────────────────────────────────────────────────
// G2.3 — scoped crew EDIT (/crew/:id/update) + RETIRE (/crew/:id/retire), both
// cap.crew.create-gated and created_by-scoped (SPEC.md §2.1–2.2 / §4.1–4.2), plus
// /crew/mine's created_by_me flag (§4.6). The scoping matrix: creator / other-sub /
// manager (holds cap.personnel.manage but NOT cap.crew.create — uses the fuller
// personnel routes) / admin. Retire guards: foreign time entries + other-job
// placement → 409 with actionable codes; ownership misses collapse to 404 (no
// roster oracle). Real worker via cloudflare:test SELF.fetch + Miniflare D1.
// ─────────────────────────────────────────────────────────────────────────────

function upd(cookie: string, id: number | string, body: unknown): Promise<Response> {
  return call(`/api/fieldops/crew/${id}/update`, { method: "POST", cookie, body: JSON.stringify(body) });
}
function ret(cookie: string, id: number | string): Promise<Response> {
  return call(`/api/fieldops/crew/${id}/retire`, { method: "POST", cookie, body: "{}" });
}
async function person(id: number) {
  return (await env.DB.prepare("SELECT * FROM personnel WHERE id=?").bind(id).first()) as any;
}
async function seedTime(personnelId: number, actor: string): Promise<void> {
  await env.DB.prepare(
    "INSERT INTO time_entries (uuid, job_id, personnel_id, hours, actor_username) VALUES (?,?,?,?,?)",
  )
    .bind(`t-${personnelId}-${actor}-${Math.random().toString(36).slice(2)}`, "JOB-A", personnelId, 4, actor)
    .run();
}

beforeEach(async () => {
  await env.DB.batch([
    env.DB.prepare("DELETE FROM users"),
    env.DB.prepare("DELETE FROM audit_log"),
    env.DB.prepare("DELETE FROM time_entries"),
    env.DB.prepare("DELETE FROM personnel"),
    env.DB.prepare("DELETE FROM jobs"),
  ]);
  await provision("admin.one", "password123", "admin");
  await provision("mo.manager", "password123", "manager");
  await provision("submitter.jim", "password123", "submitter");
  await provision("submitter.bob", "password123", "submitter");
  await seedJobRow("JOB-A", { status: "active" });
  await seedJobRow("JOB-B", { status: "active" });
  // Jim is a placed subcontractor (his linked roster row is on JOB-A).
  await seedPersonnel("Jim Sub", "submitter.jim", "JOB-A");
});

describe("POST /api/fieldops/crew/:id/update — scoped edit", () => {
  it("no session → 401; a manager (no cap.crew.create) → 403 (uses the fuller personnel route instead)", async () => {
    const id = await seedPersonnel("Typo Guy", null, "JOB-A", { created_by: "submitter.jim" });
    expect((await call(`/api/fieldops/crew/${id}/update`, { method: "POST", body: "{}" })).status).toBe(401);
    const mgr = await login("mo.manager", "password123");
    expect((await upd(mgr, id, { name: "X" })).status).toBe(403);
  });

  it("the creator fixes name/trade on their own crew → 200 + audit crew_update", async () => {
    const id = await seedPersonnel("Tpyo Guy", null, "JOB-A", { created_by: "submitter.jim", trade: "labor" });
    const jim = await login("submitter.jim", "password123");
    const res = await upd(jim, id, { name: "Typo Guy", trade: "laborer" });
    expect(res.status, await res.clone().text()).toBe(200);
    const row = await person(id);
    expect(row.name).toBe("Typo Guy");
    expect(row.trade).toBe("laborer");
    const audits = (await env.DB.prepare("SELECT * FROM audit_log WHERE action='crew_update'").all()).results as any[];
    expect(audits).toHaveLength(1);
    expect(audits[0].actor_username).toBe("submitter.jim");
  });

  it("another subcontractor / a non-creator admin / a retired row / an unknown id → 404 (one answer, no oracle)", async () => {
    const id = await seedPersonnel("Typo Guy", null, "JOB-A", { created_by: "submitter.jim" });
    const bob = await login("submitter.bob", "password123");
    expect((await upd(bob, id, { name: "Hijack" })).status).toBe(404);
    // admin HOLDS cap.crew.create (0027) but didn't create this row → the scoped route still 404s
    // (admins edit via the cap.personnel.manage route — this route never widens).
    const adm = await login("admin.one", "password123");
    expect((await upd(adm, id, { name: "Hijack" })).status).toBe(404);
    const jim = await login("submitter.jim", "password123");
    await env.DB.prepare("UPDATE personnel SET active=0 WHERE id=?").bind(id).run();
    expect((await upd(jim, id, { name: "Zombie" })).status).toBe(404);
    expect((await upd(jim, 99999, { name: "Ghost" })).status).toBe(404);
    expect((await person(id)).name).toBe("Typo Guy"); // nothing changed anywhere above
  });

  it("bounds + the non-login wall: bad name/trade → 400; any account/login/role key → 400 login_not_allowed", async () => {
    const id = await seedPersonnel("Typo Guy", null, "JOB-A", { created_by: "submitter.jim" });
    const jim = await login("submitter.jim", "password123");
    expect((await upd(jim, id, { name: "" })).status).toBe(400);
    expect((await upd(jim, id, { name: "x".repeat(129) })).status).toBe(400);
    expect((await upd(jim, id, { name: "Ok", trade: "x".repeat(65) })).status).toBe(400);
    expect((await upd(jim, "abc", { name: "Ok" })).status).toBe(400);
    for (const k of ["account", "username", "password", "role"]) {
      const res = await upd(jim, id, { name: "Ok", [k]: "x" });
      expect(res.status, k).toBe(400);
      expect(((await res.json()) as any).error).toBe("login_not_allowed");
    }
  });
});

describe("POST /api/fieldops/crew/:id/retire — scoped soft-retire + guards", () => {
  it("the creator retires a typo'd duplicate (no foreign time, same job) → 200; idempotent replay → 200 already_retired", async () => {
    const id = await seedPersonnel("Dup Guy", null, "JOB-A", { created_by: "submitter.jim" });
    const jim = await login("submitter.jim", "password123");
    const res = await ret(jim, id);
    expect(res.status, await res.clone().text()).toBe(200);
    expect((await person(id)).active).toBe(0); // SOFT — the row (and any history target) survives
    const again = await ret(jim, id);
    expect(again.status).toBe(200);
    expect((await json<any>(again)).already_retired).toBe(true);
    const audits = (await env.DB.prepare("SELECT * FROM audit_log WHERE action='crew_retire'").all()).results as any[];
    expect(audits).toHaveLength(1); // the idempotent replay writes NO second audit row
  });

  it("the actor's OWN time entries don't block the retire", async () => {
    const id = await seedPersonnel("Dup Guy", null, "JOB-A", { created_by: "submitter.jim" });
    await seedTime(id, "submitter.jim");
    const jim = await login("submitter.jim", "password123");
    expect((await ret(jim, id)).status).toBe(200);
  });

  it("time logged by ANYONE ELSE → 409 crew_has_foreign_time (a real worker escalates to the office)", async () => {
    const id = await seedPersonnel("Real Worker", null, "JOB-A", { created_by: "submitter.jim" });
    await seedTime(id, "mo.manager");
    const jim = await login("submitter.jim", "password123");
    const res = await ret(jim, id);
    expect(res.status).toBe(409);
    expect(((await res.json()) as any).error).toBe("crew_has_foreign_time");
    expect((await person(id)).active).toBe(1); // untouched
  });

  it("placed on a DIFFERENT job than the actor → 409 crew_on_other_job; unplaced (NULL) retires fine", async () => {
    const moved = await seedPersonnel("Moved Guy", null, "JOB-B", { created_by: "submitter.jim" });
    const unplaced = await seedPersonnel("Unplaced Guy", null, null, { created_by: "submitter.jim" });
    const jim = await login("submitter.jim", "password123");
    const res = await ret(jim, moved);
    expect(res.status).toBe(409);
    expect(((await res.json()) as any).error).toBe("crew_on_other_job");
    expect((await person(moved)).active).toBe(1);
    expect((await ret(jim, unplaced)).status).toBe(200);
  });

  it("not the creator (other sub / admin) or unknown id → 404; manager (no cap) → 403; bad id → 400", async () => {
    const id = await seedPersonnel("Typo Guy", null, "JOB-A", { created_by: "submitter.jim" });
    const bob = await login("submitter.bob", "password123");
    expect((await ret(bob, id)).status).toBe(404);
    const adm = await login("admin.one", "password123");
    expect((await ret(adm, id)).status).toBe(404);
    const mgr = await login("mo.manager", "password123");
    expect((await ret(mgr, id)).status).toBe(403);
    const jim = await login("submitter.jim", "password123");
    expect((await ret(jim, 99999)).status).toBe(404);
    expect((await ret(jim, "abc")).status).toBe(400);
    expect((await person(id)).active).toBe(1);
  });
});

describe("GET /api/fieldops/crew/mine — created_by_me gates the controls", () => {
  it("created rows carry created_by_me=1; the actor's own linked row carries 0", async () => {
    await seedPersonnel("Their Guy", null, "JOB-A", { created_by: "submitter.jim" });
    const jim = await login("submitter.jim", "password123");
    const body = await json<{ personnel: { name: string; created_by_me: number }[] }>(
      await call("/api/fieldops/crew/mine", { cookie: jim }),
    );
    const byName = new Map(body.personnel.map((m) => [m.name, m.created_by_me]));
    expect(byName.get("Their Guy")).toBe(1);
    expect(byName.get("Jim Sub")).toBe(0); // own linked row — no Edit/Retire controls
  });
});
