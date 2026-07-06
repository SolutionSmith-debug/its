import { env } from "cloudflare:test";
import { describe, it, expect, beforeEach } from "vitest";
import { call, provision, login, seedJob } from "./helpers";

// ─────────────────────────────────────────────────────────────────────────────
// M3 Slice 1 — a material-incident submission may OPTIONALLY reference an M2
// expected-materials line via values.line_uuid (a submission VALUE, not a form
// field). The Worker /api/submit is the TRUST BOUNDARY (Invariant 2): a present
// line_uuid MUST name an ACTIVE job_expected_materials line of THIS job, else
// fail-closed 422 unknown_material_line BEFORE any submission INSERT. Absent /
// empty line_uuid → a valid UNLINKED incident.
// Runs against the REAL worker with Miniflare D1 (migrations auto-apply).
// ─────────────────────────────────────────────────────────────────────────────

const FORM = "material-incident-v1";
const JOB_A = "JOB-INCA";
const JOB_B = "JOB-INCB";
const LINE_A = "line-uuid-aaaa-1111";
const LINE_A_INACTIVE = "line-uuid-aaaa-dead";
const LINE_B = "line-uuid-bbbb-2222";

/** Seed one job_expected_materials line (defaults: status 'expected', seq 0). */
async function seedLine(jobId: string, lineUuid: string, active = 1): Promise<void> {
  await env.DB
    .prepare("INSERT INTO job_expected_materials (job_id, description, line_uuid, active) VALUES (?,?,?,?)")
    .bind(jobId, "Rebar bundles", lineUuid, active)
    .run();
}

function submitBody(extra: Record<string, unknown> = {}, values: Record<string, unknown> = {}) {
  return JSON.stringify({
    job_id: JOB_A,
    form_code: FORM,
    work_date: "2026-07-06",
    submission_uuid: crypto.randomUUID(),
    values: { material_description: "Rebar bundles", issue: "damaged", details: "crushed corner", ...values },
    ...extra,
  });
}

async function submissionCount(): Promise<number> {
  return (await env.DB.prepare("SELECT COUNT(*) AS n FROM submissions").first<{ n: number }>())!.n;
}

let admin: string;

beforeEach(async () => {
  await env.DB.batch([
    env.DB.prepare("DELETE FROM submissions"),
    env.DB.prepare("DELETE FROM job_expected_materials"),
    env.DB.prepare("DELETE FROM users"),
    env.DB.prepare("DELETE FROM jobs"),
    env.DB.prepare("DELETE FROM audit_log"),
  ]);
  await provision("admin.one", "password123", "admin");
  admin = await login("admin.one", "password123");
  await seedJob(JOB_A);
  await seedJob(JOB_B);
  await seedLine(JOB_A, LINE_A);
  await seedLine(JOB_A, LINE_A_INACTIVE, 0); // a DEACTIVATED line on JOB_A
  await seedLine(JOB_B, LINE_B); // an ACTIVE line, but on a DIFFERENT job
});

describe("M3 Slice 1 — /api/submit material-incident line_uuid validation", () => {
  it("(a) a VALID active line_uuid for the job → 200, and the submission stores line_uuid in its values", async () => {
    const res = await call("/api/submit", {
      method: "POST",
      cookie: admin,
      body: submitBody({}, { line_uuid: LINE_A }),
    });
    expect(res.status, await res.clone().text()).toBe(200);
    const row = await env.DB
      .prepare("SELECT payload_json FROM submissions LIMIT 1")
      .first<{ payload_json: string }>();
    expect(JSON.parse(row!.payload_json).line_uuid).toBe(LINE_A);
  });

  it("(b) a line_uuid from a DIFFERENT job → 422 unknown_material_line, NOTHING filed", async () => {
    const res = await call("/api/submit", {
      method: "POST",
      cookie: admin,
      body: submitBody({}, { line_uuid: LINE_B }), // active, but belongs to JOB_B
    });
    expect(res.status).toBe(422);
    expect(await res.json()).toMatchObject({ error: "unknown_material_line" });
    expect(await submissionCount()).toBe(0);
  });

  it("(b') a DEACTIVATED line of THIS job → 422 unknown_material_line, NOTHING filed", async () => {
    const res = await call("/api/submit", {
      method: "POST",
      cookie: admin,
      body: submitBody({}, { line_uuid: LINE_A_INACTIVE }),
    });
    expect(res.status).toBe(422);
    expect(await res.json()).toMatchObject({ error: "unknown_material_line" });
    expect(await submissionCount()).toBe(0);
  });

  it("(b'') a NON-EXISTENT line_uuid → 422 unknown_material_line, NOTHING filed", async () => {
    const res = await call("/api/submit", {
      method: "POST",
      cookie: admin,
      body: submitBody({}, { line_uuid: "line-uuid-does-not-exist" }),
    });
    expect(res.status).toBe(422);
    expect(await res.json()).toMatchObject({ error: "unknown_material_line" });
    expect(await submissionCount()).toBe(0);
  });

  it("(b''') a malformed (non-string) line_uuid → 422 unknown_material_line, fail-closed", async () => {
    const res = await call("/api/submit", {
      method: "POST",
      cookie: admin,
      body: submitBody({}, { line_uuid: 12345 }),
    });
    expect(res.status).toBe(422);
    expect(await res.json()).toMatchObject({ error: "unknown_material_line" });
    expect(await submissionCount()).toBe(0);
  });

  it("(c) NO line_uuid → 200 (a valid UNLINKED incident)", async () => {
    const res = await call("/api/submit", { method: "POST", cookie: admin, body: submitBody() });
    expect(res.status, await res.clone().text()).toBe(200);
    expect(await submissionCount()).toBe(1);
  });

  it("(c') an EMPTY-string line_uuid → 200 (treated as absent → unlinked)", async () => {
    const res = await call("/api/submit", {
      method: "POST",
      cookie: admin,
      body: submitBody({}, { line_uuid: "" }),
    });
    expect(res.status, await res.clone().text()).toBe(200);
    expect(await submissionCount()).toBe(1);
  });

  it("the gate is SCOPED to the material-incident family: a NON-incident form with a bogus line_uuid is unaffected", async () => {
    // A jha submission carrying a line_uuid value is NOT gated (the gate keys on form_code) — it files.
    const res = await call("/api/submit", {
      method: "POST",
      cookie: admin,
      body: JSON.stringify({
        job_id: JOB_A,
        form_code: "jha",
        work_date: "2026-07-06",
        submission_uuid: crypto.randomUUID(),
        values: { hazards: "none", line_uuid: "line-uuid-does-not-exist" },
      }),
    });
    expect(res.status, await res.clone().text()).toBe(200);
    expect(await submissionCount()).toBe(1);
  });
});
