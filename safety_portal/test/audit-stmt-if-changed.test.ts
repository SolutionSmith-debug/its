import { env } from "cloudflare:test";
import { describe, it, expect, beforeEach } from "vitest";
import { auditStmt, auditStmtIfChanged } from "../worker/audit";

// ─────────────────────────────────────────────────────────────────────────────
// worker/audit.ts auditStmtIfChanged (optimization slice 3, finding #4) — the changes()=1
// CONDITIONAL twin of auditStmt that replaced the 29 hand-rolled
//   "INSERT INTO audit_log … SELECT ?1,?2,?3,?4 WHERE changes()=1"
// literals across the 9 field-ops write modules. Contract under test, against the REAL Miniflare
// D1 (the helper only touches c.env.DB, so a {env} stub stands in for the Hono context):
//   • batched directly after a mutation that changed EXACTLY ONE row → the audit row lands,
//     bindings (actor / action / target / JSON-encoded detail) identical to auditStmt's;
//   • batched after a NO-OP mutation (guard missed, lost race, repeat) → NO audit row — the W4
//     "never a lying audit record" guarantee;
//   • detail=null binds SQL NULL (parity with auditStmt), detail objects JSON.stringify identically.
// ─────────────────────────────────────────────────────────────────────────────

// The helpers only read c.env.DB — a bindings-only stub is the whole surface they touch.
type AuditCtx = Parameters<typeof auditStmtIfChanged>[0];
const ctx = { env } as unknown as AuditCtx;

interface AuditRow {
  actor_username: string;
  action: string;
  target_username: string | null;
  detail: string | null;
}
async function auditRows(action: string): Promise<AuditRow[]> {
  return (
    await env.DB.prepare(
      "SELECT actor_username, action, target_username, detail FROM audit_log WHERE action=? ORDER BY id ASC",
    )
      .bind(action)
      .all<AuditRow>()
  ).results;
}

beforeEach(async () => {
  await env.DB.batch([
    env.DB.prepare("DELETE FROM audit_log"),
    env.DB.prepare("DELETE FROM jobs"),
  ]);
  await env.DB.prepare("INSERT INTO jobs (job_id, project_name, active, status, created_at) VALUES (?,?,1,'active',?)")
    .bind("JOB-A", "Project A", 1_700_000_000)
    .run();
});

describe("auditStmtIfChanged — conditional landing", () => {
  it("lands the audit row when the preceding batched mutation changed exactly one row", async () => {
    await env.DB.batch([
      env.DB.prepare("UPDATE jobs SET project_name='Renamed' WHERE job_id=?1").bind("JOB-A"),
      auditStmtIfChanged(ctx, "actor.a", "test_rename", "JOB-A", { job_id: "JOB-A", to: "Renamed" }),
    ]);
    const rows = await auditRows("test_rename");
    expect(rows).toEqual([
      {
        actor_username: "actor.a",
        action: "test_rename",
        target_username: "JOB-A",
        detail: JSON.stringify({ job_id: "JOB-A", to: "Renamed" }),
      },
    ]);
  });

  it("writes NO audit row when the preceding batched mutation was a no-op (W4: never a lying record)", async () => {
    await env.DB.batch([
      env.DB.prepare("UPDATE jobs SET project_name='Renamed' WHERE job_id=?1").bind("JOB-NOPE"),
      auditStmtIfChanged(ctx, "actor.a", "test_noop", "JOB-NOPE", { job_id: "JOB-NOPE" }),
    ]);
    expect(await auditRows("test_noop")).toEqual([]);
    // …and the same batch shape on a real row still lands (the guard is changes(), not the helper).
    await env.DB.batch([
      env.DB.prepare("UPDATE jobs SET project_name='Renamed' WHERE job_id=?1").bind("JOB-A"),
      auditStmtIfChanged(ctx, "actor.a", "test_noop", "JOB-A", { job_id: "JOB-A" }),
    ]);
    expect((await auditRows("test_noop")).length).toBe(1);
  });

  it("a repeat of a guarded transition audits exactly once (the receive/flag-incident shape)", async () => {
    // Guard IN-WHERE (status='active'): first flip changes 1 row + audits; the repeat matches 0
    // rows and the conditional INSERT is skipped — one stamp, one audit row, ever.
    const flip = () =>
      env.DB.batch([
        env.DB.prepare("UPDATE jobs SET status='closed' WHERE job_id=?1 AND status='active'").bind("JOB-A"),
        auditStmtIfChanged(ctx, "actor.a", "test_flip", "JOB-A", { job_id: "JOB-A" }),
      ]);
    await flip();
    await flip();
    expect((await auditRows("test_flip")).length).toBe(1);
  });

  it("binds detail=null as SQL NULL and a null target as NULL (parity with auditStmt)", async () => {
    await env.DB.batch([
      env.DB.prepare("UPDATE jobs SET project_name='X' WHERE job_id=?1").bind("JOB-A"),
      auditStmtIfChanged(ctx, "actor.a", "test_nulls", null, null),
    ]);
    expect(await auditRows("test_nulls")).toEqual([
      { actor_username: "actor.a", action: "test_nulls", target_username: null, detail: null },
    ]);
  });

  it("encodes detail identically to auditStmt (the unconditional twin)", async () => {
    const detail = { id: 7, nested: { a: [1, 2, 3] }, note: "x" };
    await env.DB.batch([
      env.DB.prepare("UPDATE jobs SET project_name='Y' WHERE job_id=?1").bind("JOB-A"),
      auditStmtIfChanged(ctx, "actor.a", "test_twin_cond", "t", detail),
      auditStmt(ctx, "actor.a", "test_twin_uncond", "t", detail),
    ]);
    const [cond] = await auditRows("test_twin_cond");
    const [uncond] = await auditRows("test_twin_uncond");
    expect(cond.detail).toBe(uncond.detail);
    expect(cond.detail).toBe(JSON.stringify(detail));
  });
});
