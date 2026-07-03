import { env } from "cloudflare:test";
import { describe, it, expect, beforeEach, afterAll } from "vitest";
import { call, provision, login } from "./helpers";

// ─────────────────────────────────────────────────────────────────────────────
// BRIEF A — Personnel tab (cap.personnel.read, admin-only)
// Runs against the REAL worker with Miniflare D1; SELF.fetch cookie-forwarding.
// ─────────────────────────────────────────────────────────────────────────────


// Seed personnel + time_entries
async function seedPersonnel(): Promise<{ aliceId: number; bobId: number }> {
  // personnel columns: id (auto), name, username, trade, active, created_at (default)
  await env.DB.prepare("INSERT INTO personnel (name, username, trade, active) VALUES (?,?,?,1)")
    .bind("Alice Chen", "alice.chen", "operator")
    .run();
  const aliceId = (await env.DB.prepare("SELECT id FROM personnel WHERE name='Alice Chen'").first<{id:number}>())!.id;
  await env.DB.prepare("INSERT INTO personnel (name, username, trade, active) VALUES (?,?,?,1)")
    .bind("Bob Martinez", "bob.martinez", "foreman")
    .run();
  const bobId = (await env.DB.prepare("SELECT id FROM personnel WHERE name='Bob Martinez'").first<{id:number}>())!.id;
  return { aliceId, bobId };
}

async function seedTimeEntries(personnelId: number, count: number): Promise<void> {
  const now = Math.floor(Date.now() / 1000);
  for (let i = 0; i < count; i++) {
    // Set created_at explicitly + monotonically (Entry 0 newest) so the route's primary
    // sort key (created_at DESC, uuid DESC) is deterministic — rapid inserts would otherwise
    // share one unixepoch() second and collapse the order onto the uuid tiebreak.
    await env.DB.prepare(
      "INSERT INTO time_entries (uuid, job_id, personnel_id, work_started_at, work_ended_at, hours, notes, created_at, actor_username) VALUES (?,?,?,?,?,?,?,?,?)",
    )
      .bind(`tt-${personnelId}-${i}`, "JOB-A", personnelId, now - 3600 * (i + 1), now - 3600 * i, 8, `Entry ${i}`, now - i * 60, "admin.one")
      .run();
  }
}

beforeEach(async () => {
  await env.DB.batch([
    env.DB.prepare("DELETE FROM users"),
    // Delete in reverse dependency order: time_entries depends on personnel
    env.DB.prepare("DELETE FROM time_entries"),
    env.DB.prepare("DELETE FROM personnel"),
  ]);
});

// ── GET /api/fieldops/personnel (list) ────────────────────────────────────────
describe("GET /api/fieldops/personnel", () => {
  beforeEach(async () => {
    await provision("admin.one", "password123", "admin");
    await provision("submitter.jim", "password123", "submitter");
  });

  it("no session → 401 (requireSession)", async () => {
    const res = await call("/api/fieldops/personnel");
    expect(res.status).toBe(401);
  });

  it("session without cap.personnel.read → 403", async () => {
    const c = await login("submitter.jim", "password123");
    const res = await call("/api/fieldops/personnel", { cookie: c });
    expect(res.status).toBe(403);
  });

  it("admin — empty list when no personnel", async () => {
    const c = await login("admin.one", "password123");
    const res = await call("/api/fieldops/personnel", { cookie: c });
    expect(res.status).toBe(200);
    const body = (await res.json()) as { personnel: any[]; latest_entries: any[]; next_cursor: string | null };
    expect(body.personnel).toEqual([]);
    expect(body.latest_entries).toEqual([]);
    expect(body.next_cursor).toBeNull();
  });

  it("admin — returns roster + latest entry per person", async () => {
    const { aliceId } = await seedPersonnel();
    await seedTimeEntries(aliceId, 1); // Alice has one time entry
    const c = await login("admin.one", "password123");
    const res = await call("/api/fieldops/personnel", { cookie: c });
    expect(res.status).toBe(200);
    const body = (await res.json()) as { personnel: any[]; latest_entries: any[]; next_cursor: string | null };
    expect(body.personnel).toHaveLength(2);
    expect(body.personnel.map((p) => p.name)).toEqual(["Alice Chen", "Bob Martinez"]);
    expect(body.latest_entries).toHaveLength(1);
    expect(body.latest_entries[0].personnel_id).toBe(aliceId);
    expect(body.next_cursor).toBeNull(); // less than limit (2 < 50)
  });

  it("honors limit + next_cursor walks page 2 with no overlap", async () => {
    // Seed > limit personnel
    for (let i = 0; i < 75; i++) {
      await env.DB.prepare("INSERT INTO personnel (name, username, trade, active) VALUES (?,?,?,1)")
        .bind(`User ${i}`, `user.${i}`, "laborer")
        .run();
    }
    const c = await login("admin.one", "password123");

    // Page 1
    let res = await call("/api/fieldops/personnel?limit=50", { cookie: c });
    expect(res.status).toBe(200);
    let body = (await res.json()) as { personnel: any[]; next_cursor: string | null };
    expect(body.personnel).toHaveLength(50);
    expect(body.next_cursor).not.toBeNull();

    const page1Names = body.personnel.map((p) => p.name);

    // Page 2
    const cursor = body.next_cursor!;
    res = await call(`/api/fieldops/personnel?limit=50&cursor=${cursor}`, { cookie: c });
    expect(res.status).toBe(200);
    body = (await res.json()) as { personnel: any[]; next_cursor: string | null };
    expect(body.personnel).toHaveLength(25); // 75 total, 50 on page 1
    const page2Names = body.personnel.map((p) => p.name);

    // No overlap
    for (const n of page2Names) {
      expect(page1Names.includes(n)).toBe(false);
    }
    expect(body.next_cursor).toBeNull(); // last page
  });

  it("hostile non-primitive cursor → first page (200), never 500", async () => {
    await seedPersonnel();
    const c = await login("admin.one", "password123");
    // A cursor whose field values are an object/array must fall back to the first page, not reach
    // .bind() as a non-primitive (which mis-coerces or throws a 500). Hand-encoded — encodeCursor
    // only accepts primitives.
    const hostile = btoa(JSON.stringify({ n: {}, i: [] }))
      .replace(/\+/g, "-")
      .replace(/\//g, "_")
      .replace(/=+$/, "");
    const res = await call(`/api/fieldops/personnel?cursor=${hostile}`, { cookie: c });
    expect(res.status, await res.clone().text()).toBe(200);
    const body = (await res.json()) as { personnel: any[] };
    expect(body.personnel.length).toBeGreaterThan(0); // served the first page
  });
});

// ── GET /api/fieldops/personnel/:id (detail) ───────────────────────────────────
describe("GET /api/fieldops/personnel/:id", () => {
  beforeEach(async () => {
    await provision("admin.one", "password123", "admin");
    await provision("submitter.jim", "password123", "submitter");
    const { aliceId } = await seedPersonnel();
    // Store aliceId in a way it can be accessed by tests
    (globalThis as any).__TEST_ALICE_ID__ = aliceId;
  });

  afterAll(() => {
    delete (globalThis as any).__TEST_ALICE_ID__;
  });

  it("no session → 401", async () => {
    const res = await call("/api/fieldops/personnel/1");
    expect(res.status).toBe(401);
  });

  it("session without cap.personnel.read → 403", async () => {
    const c = await login("submitter.jim", "password123");
    const res = await call("/api/fieldops/personnel/1", { cookie: c });
    expect(res.status).toBe(403);
  });

  it("non-integer id → 400", async () => {
    const c = await login("admin.one", "password123");
    const res = await call("/api/fieldops/personnel/notanumber", { cookie: c });
    expect(res.status).toBe(400);
  });

  it("unknown id → 404", async () => {
    const c = await login("admin.one", "password123");
    const res = await call("/api/fieldops/personnel/999999", { cookie: c });
    expect(res.status).toBe(404);
  });

  it("valid id returns header + empty time_entries when no entries", async () => {
    const aliceId = (globalThis as any).__TEST_ALICE_ID__;
    const c = await login("admin.one", "password123");
    const res = await call(`/api/fieldops/personnel/${aliceId}`, { cookie: c });
    expect(res.status).toBe(200);
    const body = (await res.json()) as { personnel: any; next_cursor: string | null };
    expect(body.personnel.id).toBe(aliceId);
    expect(body.personnel.name).toBe("Alice Chen");
    expect(body.personnel.username).toBe("alice.chen");
    expect(body.personnel.trade).toBe("operator");
    expect(body.personnel.time_entries).toEqual([]);
    expect(body.next_cursor).toBeNull();
  });

  it("returns header + time entries (keyset paginated)", async () => {
    const aliceId = (globalThis as any).__TEST_ALICE_ID__;
    await seedTimeEntries(aliceId, 3);
    const c = await login("admin.one", "password123");
    const res = await call(`/api/fieldops/personnel/${aliceId}`, { cookie: c });
    expect(res.status).toBe(200);
    const body = (await res.json()) as { personnel: any; next_cursor: string | null };
    expect(body.personnel.time_entries).toHaveLength(3);
    // Entries ordered by created_at DESC, uuid DESC → first is newest
    expect(body.personnel.time_entries[0].notes).toBe("Entry 0");
  });

  it("honors limit + cursor walks page 2 with no overlap", async () => {
    const aliceId = (globalThis as any).__TEST_ALICE_ID__;
    await seedTimeEntries(aliceId, 75);
    const c = await login("admin.one", "password123");

    // Page 1
    let res = await call(`/api/fieldops/personnel/${aliceId}?limit=50`, { cookie: c });
    expect(res.status).toBe(200);
    let body = (await res.json()) as { personnel: any; next_cursor: string | null };
    expect(body.personnel.time_entries).toHaveLength(50);
    expect(body.next_cursor).not.toBeNull();

    const page1Ids = body.personnel.time_entries.map((e: any) => e.uuid);

    // Page 2
    const cursor = body.next_cursor!;
    res = await call(`/api/fieldops/personnel/${aliceId}?limit=50&cursor=${cursor}`, { cookie: c });
    expect(res.status).toBe(200);
    body = (await res.json()) as { personnel: any; next_cursor: string | null };
    expect(body.personnel.time_entries).toHaveLength(25);
    const page2Ids = body.personnel.time_entries.map((e: any) => e.uuid);

    // No overlap
    for (const id of page2Ids) {
      expect(page1Ids.includes(id)).toBe(false);
    }
  });
});

// ── current_job_name resolution (LEFT JOIN jobs) ───────────────────────────────
describe("personnel current_job_name resolution", () => {
  beforeEach(async () => {
    await env.DB.prepare("DELETE FROM jobs").run();
    await provision("admin.one", "password123", "admin");
    await env.DB.prepare("INSERT INTO jobs (job_id, project_name, active, status, created_at) VALUES (?,?,?,?,?)")
      .bind("JOB-77", "Solar Rooftop 77", 1, "active", 1_700_000_000)
      .run();
  });

  it("list resolves current_job_name for a placed person; null for an unplaced one", async () => {
    const { aliceId, bobId } = await seedPersonnel();
    await env.DB.prepare("UPDATE personnel SET current_job = ? WHERE id = ?").bind("JOB-77", aliceId).run();
    const c = await login("admin.one", "password123");

    const res = await call("/api/fieldops/personnel", { cookie: c });
    expect(res.status, await res.clone().text()).toBe(200);
    const body = (await res.json()) as { personnel: any[] };
    const alice = body.personnel.find((p) => p.id === aliceId);
    const bob = body.personnel.find((p) => p.id === bobId);
    expect(alice.current_job).toBe("JOB-77");
    expect(alice.current_job_name).toBe("Solar Rooftop 77");
    expect(bob.current_job).toBeNull();
    expect(bob.current_job_name).toBeNull();
  });

  it("detail resolves current_job_name for a placed person; null for an unplaced one", async () => {
    const { aliceId, bobId } = await seedPersonnel();
    await env.DB.prepare("UPDATE personnel SET current_job = ? WHERE id = ?").bind("JOB-77", aliceId).run();
    const c = await login("admin.one", "password123");

    const aliceRes = await call(`/api/fieldops/personnel/${aliceId}`, { cookie: c });
    expect(aliceRes.status).toBe(200);
    const aliceBody = (await aliceRes.json()) as { personnel: any };
    expect(aliceBody.personnel.current_job).toBe("JOB-77");
    expect(aliceBody.personnel.current_job_name).toBe("Solar Rooftop 77");

    const bobRes = await call(`/api/fieldops/personnel/${bobId}`, { cookie: c });
    const bobBody = (await bobRes.json()) as { personnel: any };
    expect(bobBody.personnel.current_job).toBeNull();
    expect(bobBody.personnel.current_job_name).toBeNull();
  });
});
