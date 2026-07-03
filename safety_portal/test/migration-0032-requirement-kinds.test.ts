import { env } from "cloudflare:test";
import { describe, it, expect } from "vitest";

// ─────────────────────────────────────────────────────────────────────────────
// Migration 0032 (slice D5) — the job_daily_requirements REBUILD (SQLite can't widen a CHECK
// in place): new 7-kind CHECK + the `options` column, INSERT-SELECT the 0030 rows (options
// NULL), drop old, rename, recreate the (job_id, active, seq) index.
//
// The suite's setup (test/apply-migrations.ts) has already applied ALL migrations — including
// 0032 — so this file REPLAYS the shipped 0032 SQL against a reconstructed 0030-shape table to
// prove the live upgrade path: an operator applying 0032 over a D1 that has 0030 data loses
// nothing. (On the REAL live D1, 0030 has not been applied yet either — 0030→0031→0032 apply
// in one `wrangler d1 migrations apply` run, so the copy runs over an empty table; this test
// covers the stronger with-data case.) isolatedStorage undoes the surgery after each test.
// Re-apply safety is d1_migrations tracking (the 0020 rebuild precedent) — see the 0032 header.
// ─────────────────────────────────────────────────────────────────────────────

// The 0030 table shape, verbatim from 0032's predecessor (4-kind CHECK, no options column).
const TABLE_0030 = `CREATE TABLE job_daily_requirements (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  job_id     TEXT    NOT NULL,
  seq        INTEGER NOT NULL DEFAULT 0,
  kind       TEXT    NOT NULL CHECK (kind IN ('note', 'confirm', 'text', 'form_link')),
  label      TEXT    NOT NULL,
  form_code  TEXT,
  active     INTEGER NOT NULL DEFAULT 1,
  created_at INTEGER NOT NULL DEFAULT (unixepoch())
)`;

/** Rewind job_daily_requirements to its 0030 shape (drop the post-0032 table, recreate). */
async function rewindTo0030(): Promise<void> {
  await env.DB.prepare("DROP INDEX IF EXISTS idx_job_daily_requirements_job").run();
  await env.DB.prepare("DROP TABLE job_daily_requirements").run();
  await env.DB.prepare(TABLE_0030).run();
  await env.DB.prepare(
    "CREATE INDEX idx_job_daily_requirements_job ON job_daily_requirements (job_id, active, seq)",
  ).run();
}

/** Replay the SHIPPED 0032 statements (from the same readD1Migrations array the suite setup
 *  applies) — the test exercises the real migration SQL, not a copy that could drift. */
async function replay0032(): Promise<void> {
  const m = env.TEST_MIGRATIONS.find((x) => x.name.startsWith("0032"));
  expect(m, "0032 present in migrations/").toBeDefined();
  for (const q of m!.queries) await env.DB.prepare(q).run();
}

describe("migration 0032 applies over 0030 data", () => {
  it("preserves every 0030 row byte-for-byte (ids/seq/kind/label/form_code/active/created_at) with options NULL", async () => {
    await rewindTo0030();
    // Seed 0030-era rows across all four legacy kinds, incl. a soft-deleted one.
    await env.DB.batch([
      env.DB.prepare(
        "INSERT INTO job_daily_requirements (id, job_id, seq, kind, label, form_code, active, created_at) VALUES " +
          "(1,'JOB-A',10,'note','Client requires FR clothing',NULL,1,1000)," +
          "(2,'JOB-A',20,'confirm','Badge in at the client gate',NULL,1,2000)," +
          "(3,'JOB-A',30,'text','Client rep spoken to today',NULL,1,3000)," +
          "(4,'JOB-A',40,'form_link','File the client JHA','jha',1,4000)," +
          "(5,'JOB-B',10,'note','B-only rule',NULL,0,5000)",
      ),
    ]);

    await replay0032();

    const rows = await env.DB.prepare(
      "SELECT id, job_id, seq, kind, label, form_code, options, active, created_at FROM job_daily_requirements ORDER BY id",
    ).all<Record<string, unknown>>();
    expect(rows.results).toEqual([
      { id: 1, job_id: "JOB-A", seq: 10, kind: "note", label: "Client requires FR clothing", form_code: null, options: null, active: 1, created_at: 1000 },
      { id: 2, job_id: "JOB-A", seq: 20, kind: "confirm", label: "Badge in at the client gate", form_code: null, options: null, active: 1, created_at: 2000 },
      { id: 3, job_id: "JOB-A", seq: 30, kind: "text", label: "Client rep spoken to today", form_code: null, options: null, active: 1, created_at: 3000 },
      { id: 4, job_id: "JOB-A", seq: 40, kind: "form_link", label: "File the client JHA", form_code: "jha", options: null, active: 1, created_at: 4000 },
      { id: 5, job_id: "JOB-B", seq: 10, kind: "note", label: "B-only rule", form_code: null, options: null, active: 0, created_at: 5000 },
    ]);
  });

  it("widens the kind CHECK (new kinds insertable; a bogus kind still refused) and keeps AUTOINCREMENT past the copied ids", async () => {
    await rewindTo0030();
    await env.DB.prepare(
      "INSERT INTO job_daily_requirements (id, job_id, seq, kind, label) VALUES (7,'JOB-A',10,'note','seed')",
    ).run();
    await replay0032();

    // The three D5 kinds insert cleanly — select with its JSON options text.
    await env.DB.prepare(
      "INSERT INTO job_daily_requirements (job_id, seq, kind, label, options) VALUES " +
        "('JOB-A',20,'number','Crew headcount',NULL)," +
        "('JOB-A',30,'date','Walkthrough date',NULL)," +
        "('JOB-A',40,'select','Shift worked','[\"Day\",\"Night\"]')",
    ).run();
    const ids = await env.DB.prepare("SELECT id, kind FROM job_daily_requirements ORDER BY id").all<{ id: number; kind: string }>();
    expect(ids.results!.map((r) => r.kind)).toEqual(["note", "number", "date", "select"]);
    expect(Math.min(...ids.results!.slice(1).map((r) => r.id))).toBeGreaterThan(7); // ids continue past the copy

    // The rebuilt CHECK still refuses garbage.
    await expect(
      env.DB.prepare("INSERT INTO job_daily_requirements (job_id, kind, label) VALUES ('JOB-A','photo','x')").run(),
    ).rejects.toThrow(/CHECK/i);
  });

  it("recreates the (job_id, active, seq) read index on the rebuilt table", async () => {
    await rewindTo0030();
    await replay0032();
    const idx = await env.DB.prepare(
      "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='job_daily_requirements' AND name='idx_job_daily_requirements_job'",
    ).first<{ name: string }>();
    expect(idx?.name).toBe("idx_job_daily_requirements_job");
  });
});
