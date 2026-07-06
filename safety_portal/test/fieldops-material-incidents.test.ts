import { env } from "cloudflare:test";
import { describe, it, expect, beforeEach } from "vitest";
import { call, seedJob, seedPersonnel } from "./helpers";

// P7 Material Incidents up-sync (M3 Slice 2) — the field-ops Material Incidents ledger route
// (GET /api/internal/fieldops/material-incidents). An APPEND-ONLY EVENT LEDGER (not a snapshot): one
// row per FILED (box_verified=1) `material-incident%` submission on an ACTIVE job. Immutable field
// events — the daemon never retires a row. Same field-ops token privilege separation as the
// job/hours/equipment/material-list mirror queues. Structured fields live in payload_json; line_uuid
// is a submission VALUE (M3 Slice 1). DISPLAY-NAME-ONLY reported_by.

const FIELDOPS_BEARER = "test-fieldops-token"; // == PORTAL_FIELDOPS_API_TOKEN (vitest.config.ts)
const INTERNAL_BEARER = "test-internal-token"; // portal_poll's token — must be REJECTED here
const ADMIN_BEARER = "test-admin-token"; // operator token — must be REJECTED here

const PATH = "/api/internal/fieldops/material-incidents";

interface IncidentRow {
  submission_uuid: string;
  job_id: string;
  project_name: string;
  work_date: string;
  created_at: number;
  box_link: string | null;
  material_description: string | null;
  delivery_ref: string | null;
  qty_expected: number | null;
  qty_received: number | null;
  issue: string | null;
  details: string | null;
  action_taken: string | null;
  line_uuid: string | null;
  reported_by_display: string | null;
  line_status: string | null;
}

interface SeedIncidentOpts {
  formCode?: string;
  workDate?: string;
  createdAt?: number;
  boxVerified?: -1 | 0 | 1;
  actorUsername?: string | null;
  boxLink?: string | null;
  values?: Record<string, unknown>;
}

/** Seed one submission row DIRECTLY (bypassing /api/submit) so the read endpoint can be exercised at
 *  every box_verified state. Defaults: a FILED (box_verified=1) material-incident-v1 submission. */
async function seedIncident(jobId: string, opts: SeedIncidentOpts = {}): Promise<string> {
  const uuid = crypto.randomUUID();
  const values = {
    material_description: "Rebar bundles",
    issue: "Damaged",
    details: "crushed corner, 3 of 20 unusable",
    ...opts.values,
  };
  await env.DB.prepare(
    `INSERT INTO submissions
       (submission_uuid, job_id, form_code, work_date, payload_json, box_verified, actor_username,
        box_link, created_at)
     VALUES (?,?,?,?,?,?,?,?,?)`,
  )
    .bind(
      uuid,
      jobId,
      opts.formCode ?? "material-incident-v1",
      opts.workDate ?? "2026-07-06",
      JSON.stringify(values),
      opts.boxVerified ?? 1,
      opts.actorUsername ?? null,
      opts.boxLink ?? null,
      opts.createdAt ?? 1_751_000_000,
    )
    .run();
  return uuid;
}

/** Seed one job_expected_materials line (for the line_status LEFT JOIN). */
async function seedLine(jobId: string, lineUuid: string, status = "incident", active: 0 | 1 = 1): Promise<void> {
  await env.DB
    .prepare(
      "INSERT INTO job_expected_materials (job_id, description, line_uuid, status, active) VALUES (?,?,?,?,?)",
    )
    .bind(jobId, "Rebar bundles", lineUuid, status, active)
    .run();
}

beforeEach(async () => {
  await env.DB.batch([
    env.DB.prepare("DELETE FROM submissions"),
    env.DB.prepare("DELETE FROM job_expected_materials"),
    env.DB.prepare("DELETE FROM personnel"),
    env.DB.prepare("DELETE FROM jobs"),
  ]);
});

describe("GET /api/internal/fieldops/material-incidents", () => {
  it("rejects the portal_poll + admin tokens and no token (privilege separation)", async () => {
    expect((await call(PATH)).status).toBe(401);
    expect((await call(PATH, { bearer: INTERNAL_BEARER })).status).toBe(401);
    expect((await call(PATH, { bearer: ADMIN_BEARER })).status).toBe(401);
    expect((await call(PATH, { bearer: FIELDOPS_BEARER })).status).toBe(200);
  });

  it("projects the full field set for a filed incident on an active job", async () => {
    await seedJob("JOB-A", { projectName: "Job Alpha", status: "active" });
    await seedPersonnel("Mo Manager", "mgr.mo", "JOB-A");
    const uuid = await seedIncident("JOB-A", {
      actorUsername: "mgr.mo",
      boxLink: "https://app.box.com/file/999",
      values: {
        material_description: "Q.PEAK panels",
        delivery_ref: "PO-4471",
        qty_expected: 120,
        qty_received: 118,
        issue: "Short",
        details: "2 pallets short on the delivery",
        action_taken: "Flagged on the receipt; CM notified",
      },
    });

    const res = await call(PATH, { bearer: FIELDOPS_BEARER });
    expect(res.status, await res.clone().text()).toBe(200);
    const { incidents } = (await res.json()) as { incidents: IncidentRow[] };
    expect(incidents).toHaveLength(1);
    expect(incidents[0]).toMatchObject({
      submission_uuid: uuid,
      job_id: "JOB-A",
      project_name: "Job Alpha",
      work_date: "2026-07-06",
      box_link: "https://app.box.com/file/999",
      material_description: "Q.PEAK panels",
      delivery_ref: "PO-4471",
      qty_expected: 120,
      qty_received: 118,
      issue: "Short",
      details: "2 pallets short on the delivery",
      action_taken: "Flagged on the receipt; CM notified",
      reported_by_display: "Mo Manager",
      line_uuid: null, // unlinked (no line_uuid value)
      line_status: null,
    });
  });

  it("resolves reported_by DISPLAY-NAME-ONLY (never the raw username; unmatched → NULL)", async () => {
    await seedJob("JOB-A", { projectName: "Job Alpha", status: "active" });
    await seedPersonnel("Mo Manager", "mgr.mo", "JOB-A");
    await seedIncident("JOB-A", { actorUsername: "mgr.mo", createdAt: 1_751_000_010 }); // known
    await seedIncident("JOB-A", { actorUsername: "ghost.acct", createdAt: 1_751_000_020 }); // unknown

    const res = await call(PATH, { bearer: FIELDOPS_BEARER });
    const { incidents } = (await res.json()) as { incidents: IncidentRow[] };
    const byTime = Object.fromEntries(incidents.map((r) => [r.created_at, r]));
    expect(byTime[1_751_000_010].reported_by_display).toBe("Mo Manager");
    expect(byTime[1_751_000_020].reported_by_display).toBeNull(); // unmatched → NULL, never the username
    // The raw username never appears anywhere in the response body.
    expect(JSON.stringify(incidents)).not.toContain("mgr.mo");
    expect(JSON.stringify(incidents)).not.toContain("ghost.acct");
  });

  it("resolves a LINKED incident's line_status from the referenced expected-materials line", async () => {
    await seedJob("JOB-A", { projectName: "Job Alpha", status: "active" });
    const lineUuid = "line-uuid-aaaa-1111";
    await seedLine("JOB-A", lineUuid, "received"); // the line was later received in full
    await seedIncident("JOB-A", { values: { line_uuid: lineUuid } });

    const res = await call(PATH, { bearer: FIELDOPS_BEARER });
    const { incidents } = (await res.json()) as { incidents: IncidentRow[] };
    expect(incidents).toHaveLength(1);
    expect(incidents[0].line_uuid).toBe(lineUuid);
    expect(incidents[0].line_status).toBe("received"); // live resolution signal
  });

  it("a linked incident whose line does not exist → line_status NULL (never row-multiplies)", async () => {
    await seedJob("JOB-A", { projectName: "Job Alpha", status: "active" });
    await seedIncident("JOB-A", { values: { line_uuid: "line-that-was-deleted" } });

    const res = await call(PATH, { bearer: FIELDOPS_BEARER });
    const { incidents } = (await res.json()) as { incidents: IncidentRow[] };
    expect(incidents).toHaveLength(1); // exactly one — the LEFT JOIN never duplicates
    expect(incidents[0].line_uuid).toBe("line-that-was-deleted");
    expect(incidents[0].line_status).toBeNull();
  });

  it("excludes unfiled (box_verified=0) and rejected (box_verified=-1) submissions", async () => {
    await seedJob("JOB-A", { projectName: "Job Alpha", status: "active" });
    const filed = await seedIncident("JOB-A", { boxVerified: 1, values: { details: "filed" } });
    await seedIncident("JOB-A", { boxVerified: 0, values: { details: "still in pipeline" } });
    await seedIncident("JOB-A", { boxVerified: -1, values: { details: "rejected — malicious photo" } });

    const res = await call(PATH, { bearer: FIELDOPS_BEARER });
    const { incidents } = (await res.json()) as { incidents: IncidentRow[] };
    expect(incidents.map((r) => r.submission_uuid)).toEqual([filed]); // only the FILED one
  });

  it("excludes incidents whose job is non-active (closed/on_hold) — active-job filter", async () => {
    await seedJob("JOB-ACTIVE", { projectName: "Active Job", status: "active" });
    await seedJob("JOB-CLOSED", { projectName: "Closed Job", status: "closed" });
    await seedIncident("JOB-ACTIVE", { values: { details: "on active" } });
    await seedIncident("JOB-CLOSED", { values: { details: "on closed" } });

    const res = await call(PATH, { bearer: FIELDOPS_BEARER });
    const { incidents } = (await res.json()) as { incidents: IncidentRow[] };
    expect(incidents.map((r) => r.details)).toEqual(["on active"]);
  });

  it("is SCOPED to the material-incident family (form_code LIKE) — other forms excluded", async () => {
    await seedJob("JOB-A", { projectName: "Job Alpha", status: "active" });
    await seedIncident("JOB-A", { formCode: "material-incident-v1", values: { details: "v1 incident" } });
    await seedIncident("JOB-A", { formCode: "material-incident-v2", values: { details: "future variant" } });
    await seedIncident("JOB-A", { formCode: "jha", values: { details: "a JHA, not an incident" } });
    await seedIncident("JOB-A", { formCode: "daily-report-v1", values: { details: "a daily report" } });

    const res = await call(PATH, { bearer: FIELDOPS_BEARER });
    const { incidents } = (await res.json()) as { incidents: IncidentRow[] };
    expect(incidents.map((r) => r.details).sort()).toEqual(["future variant", "v1 incident"]);
  });

  it("orders incidents by project_name then created_at", async () => {
    await seedJob("JOB-B", { projectName: "Bravo", status: "active" });
    await seedJob("JOB-A", { projectName: "Alpha", status: "active" });
    await seedIncident("JOB-B", { createdAt: 1_751_000_005, values: { details: "b1" } });
    await seedIncident("JOB-A", { createdAt: 1_751_000_020, values: { details: "a2" } });
    await seedIncident("JOB-A", { createdAt: 1_751_000_010, values: { details: "a1" } });

    const res = await call(PATH, { bearer: FIELDOPS_BEARER });
    const { incidents } = (await res.json()) as { incidents: IncidentRow[] };
    expect(incidents.map((r) => `${r.project_name}/${r.details}`)).toEqual(["Alpha/a1", "Alpha/a2", "Bravo/b1"]);
  });
});
