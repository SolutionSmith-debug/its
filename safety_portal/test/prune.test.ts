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
    env.DB.prepare("DELETE FROM filed_pdfs"),
    env.DB.prepare("DELETE FROM pdf_requests"),
    env.DB.prepare("DELETE FROM jobs"),
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
  it("STRIPS payload from FILED submissions older than 90d but KEEPS the metadata row (PR-5 Stage 1)", async () => {
    await seedSub("filed-old", 1, NOW - 100 * DAY);
    await seedSub("filed-recent", 1, NOW - 10 * DAY);

    const res = await pruneOldData(env.DB, NOW);

    expect(res.stripped).toBe(1);
    expect(res.submissions).toBe(0); // nothing DELETED (no inactive job)
    expect(await remaining()).toEqual(["filed-old", "filed-recent"]); // both rows kept
    const old = await env.DB.prepare("SELECT payload_json FROM submissions WHERE submission_uuid='filed-old'").first<{ payload_json: string }>();
    const recent = await env.DB.prepare("SELECT payload_json FROM submissions WHERE submission_uuid='filed-recent'").first<{ payload_json: string }>();
    expect(old?.payload_json).toBe(""); // stripped
    expect(recent?.payload_json).toBe("{}"); // intact (within 90d)
  });

  it("deletes an INACTIVE-job filed row (PR-5 Stage 2) but NEVER an unfiled row (box_verified=0)", async () => {
    // JOB-1 is INACTIVE → its filed rows are deletable 30d after filing.
    await env.DB.prepare("INSERT OR REPLACE INTO jobs (job_id, project_name, active) VALUES ('JOB-1','J',0)").run();
    await seedSub("unfiled-old", 0, NOW - 200 * DAY); // box_verified=0 — Box has no copy → NEVER touched
    await seedSub("filed-old", 1, NOW - 200 * DAY);   // inactive job + old → Stage-2 delete

    const res = await pruneOldData(env.DB, NOW);

    expect(res.submissions).toBe(1); // the inactive-job filed row deleted
    expect(await remaining()).toEqual(["unfiled-old"]); // the unfiled one survives
    const unf = await env.DB.prepare("SELECT payload_json FROM submissions WHERE submission_uuid='unfiled-old'").first<{ payload_json: string }>();
    expect(unf?.payload_json).toBe("{}"); // unfiled payload NOT stripped either
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

// ── PR-4 Part A — the filed_pdfs cache prune branch + D1 size telemetry. ──────────
async function seedChunk(uuid: string, index: number, total: number): Promise<void> {
  await env.DB
    .prepare("INSERT OR REPLACE INTO filed_pdfs (submission_uuid, chunk_index, chunk_total, chunk_b64) VALUES (?,?,?,?)")
    .bind(uuid, index, total, "QUJD") // "ABC"
    .run();
}
async function chunkUuids(): Promise<string[]> {
  const r = await env.DB
    .prepare("SELECT DISTINCT submission_uuid FROM filed_pdfs ORDER BY submission_uuid")
    .all<{ submission_uuid: string }>();
  return r.results.map((x) => x.submission_uuid);
}

describe("pruneOldData — PDF cache (filed_pdfs)", () => {
  it("drops chunks for a submission with NO live request, keeps those with a live request (PR-5)", async () => {
    // cache-norequest: cached, but its only request EXPIRED (>24h) → chunks dropped + reset.
    await seedSub("cache-norequest", 1, NOW - 10 * DAY);
    await env.DB.prepare("UPDATE submissions SET pdf_ready_at=? WHERE submission_uuid='cache-norequest'").bind(NOW - 100).run();
    await env.DB.prepare("INSERT INTO pdf_requests (submission_uuid, account, requested_at) VALUES ('cache-norequest','pm',?)").bind(NOW - 2 * DAY).run();
    await seedChunk("cache-norequest", 0, 1);
    // cache-live: cached WITH a live (within 24h) request → chunks survive.
    await seedSub("cache-live", 1, NOW - 10 * DAY);
    await env.DB.prepare("UPDATE submissions SET pdf_ready_at=? WHERE submission_uuid='cache-live'").bind(NOW - 100).run();
    await env.DB.prepare("INSERT INTO pdf_requests (submission_uuid, account, requested_at) VALUES ('cache-live','pm',?)").bind(NOW - 100).run();
    await seedChunk("cache-live", 0, 1);

    const res = await pruneOldData(env.DB, NOW);

    expect(res.pdfChunks).toBe(1); // the no-live-request chunk
    expect(await chunkUuids()).toEqual(["cache-live"]); // live-request chunk survives
    // The no-live-request submission's pdf_ready_at was reset so a fresh request re-services.
    const reset = await env.DB.prepare("SELECT pdf_ready_at FROM submissions WHERE submission_uuid='cache-norequest'").first<{ pdf_ready_at: number | null }>();
    expect(reset?.pdf_ready_at).toBeNull();
  });

  it("deletes ORPHAN chunks whose parent submission is gone", async () => {
    // No submission row for this uuid — a pure orphan (parent already pruned).
    await seedChunk("orphan-uuid", 0, 1);
    await seedChunk("orphan-uuid", 1, 2);
    const res = await pruneOldData(env.DB, NOW);
    expect(res.pdfChunks).toBe(2);
    expect(await chunkUuids()).toEqual([]);
  });

  it("surfaces dbSizeBytes telemetry (present, non-negative)", async () => {
    await seedSub("any", 1, NOW - 10 * DAY);
    const res = await pruneOldData(env.DB, NOW);
    expect(typeof res.dbSizeBytes).toBe("number");
    expect(res.dbSizeBytes).toBeGreaterThanOrEqual(0);
  });
});
