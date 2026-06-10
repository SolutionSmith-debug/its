import { env } from "cloudflare:test";
import { describe, it, expect, beforeEach } from "vitest";
import { pruneOldData } from "../worker/prune";

// A3 — the daily D1 prune. Verifies the retention windows AND the load-bearing guard:
// an UNFILED submission (box_verified=0) is NEVER evicted, even when old.

const NOW = 1_780_000_000; // a fixed "now" (~2026) so the test never drifts with wall clock
const DAY = 86_400;

beforeEach(async () => {
  await env.DB.batch([
    env.DB.prepare("DELETE FROM submissions"),
    env.DB.prepare("DELETE FROM audit_log"),
  ]);
});

async function seedSub(uuid: string, boxVerified: number, filedAt: number | null): Promise<void> {
  await env.DB
    .prepare(
      "INSERT INTO submissions (submission_uuid, job_id, form_code, work_date, payload_json, created_at, box_verified, filed_at) VALUES (?,?,?,?,?,?,?,?)",
    )
    .bind(uuid, "JOB-1", "jha-v1", "2026-01-01", "{}", NOW, boxVerified, filedAt)
    .run();
}

async function remaining(): Promise<string[]> {
  const r = await env.DB
    .prepare("SELECT submission_uuid FROM submissions ORDER BY submission_uuid")
    .all<{ submission_uuid: string }>();
  return r.results.map((x) => x.submission_uuid);
}

describe("pruneOldData (A3 D1 housekeeping)", () => {
  it("deletes FILED submissions older than 90d, keeps recent ones", async () => {
    await seedSub("filed-old", 1, NOW - 100 * DAY);
    await seedSub("filed-recent", 1, NOW - 10 * DAY);

    const res = await pruneOldData(env.DB, NOW);

    expect(res.submissions).toBe(1);
    expect(await remaining()).toEqual(["filed-recent"]);
  });

  it("NEVER evicts an unfiled row (box_verified=0), even with an old filed_at", async () => {
    // box_verified=0 means Box does not hold it yet — the D1 row is the only copy.
    await seedSub("unfiled-old", 0, NOW - 200 * DAY);
    await seedSub("filed-old", 1, NOW - 200 * DAY);

    const res = await pruneOldData(env.DB, NOW);

    expect(res.submissions).toBe(1); // only the FILED old one
    expect(await remaining()).toEqual(["unfiled-old"]); // the unfiled one survives
  });

  it("keeps audit_log ~1 year, prunes older", async () => {
    await env.DB.prepare("INSERT INTO audit_log (created_at, actor_username, action) VALUES (?,?,?)").bind(NOW - 400 * DAY, "admin.one", "old").run();
    await env.DB.prepare("INSERT INTO audit_log (created_at, actor_username, action) VALUES (?,?,?)").bind(NOW - 10 * DAY, "admin.one", "recent").run();

    const res = await pruneOldData(env.DB, NOW);

    expect(res.audit).toBe(1);
    expect((await env.DB.prepare("SELECT COUNT(*) n FROM audit_log").first<{ n: number }>())!.n).toBe(1);
  });

  it("prunes REJECTED (box_verified=-1) rows after 30d, keeps recent + never the unfiled (M4/PR-4)", async () => {
    await seedSub("rej-old", -1, NOW - 40 * DAY);
    await seedSub("rej-recent", -1, NOW - 5 * DAY);
    await seedSub("unfiled-old", 0, NOW - 200 * DAY); // box_verified=0 still NEVER evicted
    const res = await pruneOldData(env.DB, NOW);
    expect(res.rejected).toBe(1);
    expect(await remaining()).toEqual(["rej-recent", "unfiled-old"]);
  });
});
