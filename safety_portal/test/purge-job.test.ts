import { env, SELF } from "cloudflare:test";
import { describe, it, expect, beforeEach } from "vitest";

// /api/internal/admin/purge-job — operator hard-delete of a job + ALL its D1 rows
// (submissions, the filed_pdfs cache, pdf_requests). Bearer-gated (requireAdminToken).

const BASE = "https://portal.test";
const ADMIN_BEARER = "test-admin-token"; // == PORTAL_ADMIN_API_TOKEN in vitest.config.ts
const TS = 1_780_000_000;

type Init = RequestInit & { bearer?: string };
function call(path: string, init: Init = {}): Promise<Response> {
  const headers = new Headers(init.headers);
  if (init.bearer) headers.set("Authorization", `Bearer ${init.bearer}`);
  if (init.body && !headers.has("content-type")) headers.set("content-type", "application/json");
  return SELF.fetch(BASE + path, { ...init, headers });
}

beforeEach(async () => {
  await env.DB.batch([
    env.DB.prepare("DELETE FROM submissions"),
    env.DB.prepare("DELETE FROM filed_pdfs"),
    env.DB.prepare("DELETE FROM pdf_requests"),
    env.DB.prepare("DELETE FROM job_daily_requirements"),
    env.DB.prepare("DELETE FROM job_expected_materials"),
    env.DB.prepare("DELETE FROM time_entries"),
    env.DB.prepare("DELETE FROM task_assignments"),
    env.DB.prepare("DELETE FROM inspections"),
    env.DB.prepare("DELETE FROM checklist_instances"),
    env.DB.prepare("DELETE FROM equipment_location"),
    env.DB.prepare("DELETE FROM jobs"),
    env.DB.prepare("DELETE FROM audit_log"),
  ]);
});

async function seedJobWithData(job: string, uuid: string): Promise<void> {
  // equipment_location.equipment_id is a REAL foreign key (FKs are enforced in this
  // harness), so the referenced equipment row must exist first.
  await env.DB
    .prepare("INSERT OR IGNORE INTO equipment (id, name) VALUES (1, 'Crane 1')")
    .run();
  await env.DB.batch([
    env.DB.prepare("INSERT INTO jobs (job_id, project_name, active) VALUES (?,?,1)").bind(job, "P"),
    env.DB
      .prepare(
        "INSERT INTO submissions (submission_uuid, job_id, form_code, work_date, payload_json, created_at, box_verified, filed_at) VALUES (?,?,?,?,?,?,1,?)",
      )
      .bind(uuid, job, "jha-v1", "2026-01-01", "{}", TS, TS),
    env.DB
      .prepare("INSERT INTO filed_pdfs (submission_uuid, chunk_index, chunk_total, chunk_b64) VALUES (?,?,?,?)")
      .bind(uuid, 0, 1, "QUJD"),
    env.DB
      .prepare("INSERT INTO pdf_requests (submission_uuid, account, requested_at) VALUES (?,?,?)")
      .bind(uuid, "pm", TS),
    // Slice 1 (R3-F4): the two per-job content tables join the cascade — 2 requirements +
    // 1 expected material give the response counts distinct values.
    env.DB
      .prepare("INSERT INTO job_daily_requirements (job_id, seq, kind, label) VALUES (?,10,'confirm','Client daily brief')")
      .bind(job),
    env.DB
      .prepare("INSERT INTO job_daily_requirements (job_id, seq, kind, label) VALUES (?,20,'text','Crane hours')")
      .bind(job),
    env.DB
      .prepare("INSERT INTO job_expected_materials (job_id, description, seq) VALUES (?, 'Panels pallet', 10)")
      .bind(job),
    // The five job-context tables prune.ts guards a job on. purge-job's own comment
    // claimed it was "the explicit operator cleanup path (cascades both)" — it did
    // not touch any of them, so an operator purge returned ok:true while orphaning
    // payroll/billing-grade rows behind a now-absent job.
    env.DB
      .prepare("INSERT INTO time_entries (uuid, job_id, actor_username, hours) VALUES (?,?, 'pm', 8)")
      .bind(`te-${job}`, job),
    env.DB
      .prepare("INSERT INTO task_assignments (job_id, description) VALUES (?, 'Set panels')")
      .bind(job),
    env.DB
      .prepare(
        "INSERT INTO inspections (uuid, job_id, form_code, version, payload_json, actor_username) VALUES (?,?, 'insp-v1', 1, '{}', 'pm')",
      )
      .bind(`insp-${job}`, job),
    env.DB
      .prepare("INSERT INTO checklist_instances (kind, job_id, instance_date) VALUES ('daily', ?, '2026-01-01')")
      .bind(job),
    env.DB.prepare("INSERT INTO equipment_location (equipment_id, job_id) VALUES (1, ?)").bind(job),
  ]);
}

async function counts(job: string, uuid: string) {
  const q = async (sql: string, p: string) =>
    (await env.DB.prepare(sql).bind(p).first<{ n: number }>())!.n;
  return {
    jobs: await q("SELECT COUNT(*) n FROM jobs WHERE job_id=?", job),
    subs: await q("SELECT COUNT(*) n FROM submissions WHERE job_id=?", job),
    pdfs: await q("SELECT COUNT(*) n FROM filed_pdfs WHERE submission_uuid=?", uuid),
    reqs: await q("SELECT COUNT(*) n FROM pdf_requests WHERE submission_uuid=?", uuid),
    dailyReqs: await q("SELECT COUNT(*) n FROM job_daily_requirements WHERE job_id=?", job),
    materials: await q("SELECT COUNT(*) n FROM job_expected_materials WHERE job_id=?", job),
    timeEntries: await q("SELECT COUNT(*) n FROM time_entries WHERE job_id=?", job),
    tasks: await q("SELECT COUNT(*) n FROM task_assignments WHERE job_id=?", job),
    inspections: await q("SELECT COUNT(*) n FROM inspections WHERE job_id=?", job),
    checklists: await q("SELECT COUNT(*) n FROM checklist_instances WHERE job_id=?", job),
    equipLoc: await q("SELECT COUNT(*) n FROM equipment_location WHERE job_id=?", job),
  };
}

describe("POST /api/internal/admin/purge-job", () => {
  it("hard-deletes the job + cascades submissions/filed_pdfs/pdf_requests/requirements/materials, audits, leaves OTHER jobs", async () => {
    await seedJobWithData("JOB-PURGE", "u-purge");
    await seedJobWithData("JOB-KEEP", "u-keep");

    const res = await call("/api/internal/admin/purge-job", {
      method: "POST",
      bearer: ADMIN_BEARER,
      body: JSON.stringify({ job_id: "JOB-PURGE" }),
    });
    expect(res.status, await res.clone().text()).toBe(200);
    expect(await res.json()).toMatchObject({
      ok: true, found: true, job_id: "JOB-PURGE", job_deleted: 1, submissions: 1, pdfChunks: 1, pdfRequests: 1,
      requirements: 2, expectedMaterials: 1, // Slice 1 (R3-F4): per-job content cascades too
    });

    expect(await counts("JOB-PURGE", "u-purge")).toEqual({
      jobs: 0, subs: 0, pdfs: 0, reqs: 0, dailyReqs: 0, materials: 0,
      timeEntries: 0, tasks: 0, inspections: 0, checklists: 0, equipLoc: 0,
    });
    // The OTHER job keeps every one of them — the cascade is job-scoped, not a sweep.
    expect(await counts("JOB-KEEP", "u-keep")).toEqual({
      jobs: 1, subs: 1, pdfs: 1, reqs: 1, dailyReqs: 2, materials: 1,
      timeEntries: 1, tasks: 1, inspections: 1, checklists: 1, equipLoc: 1,
    });
    const audit = await env.DB
      .prepare("SELECT action, target_username FROM audit_log WHERE action='purge-job'")
      .first<{ action: string; target_username: string }>();
    expect(audit).toMatchObject({ action: "purge-job", target_username: "JOB-PURGE" });
  });

  it("unknown job → ok:true, found:false, all counts 0 (idempotent)", async () => {
    const res = await call("/api/internal/admin/purge-job", {
      method: "POST", bearer: ADMIN_BEARER, body: JSON.stringify({ job_id: "NOPE" }),
    });
    expect(res.status).toBe(200);
    expect(await res.json()).toMatchObject({
      ok: true, found: false, job_deleted: 0, submissions: 0, pdfChunks: 0, pdfRequests: 0,
      requirements: 0, expectedMaterials: 0,
    });
  });

  it("blank job_id → 400 invalid_job_id; non-object body → 400 bad_request", async () => {
    expect(
      (await call("/api/internal/admin/purge-job", { method: "POST", bearer: ADMIN_BEARER, body: JSON.stringify({}) })).status,
    ).toBe(400);
    expect(
      (await call("/api/internal/admin/purge-job", { method: "POST", bearer: ADMIN_BEARER, body: "null" })).status,
    ).toBe(400);
  });

  it("requires the admin bearer (401 without)", async () => {
    expect(
      (await call("/api/internal/admin/purge-job", { method: "POST", body: JSON.stringify({ job_id: "X" }) })).status,
    ).toBe(401);
  });
});
