import { env } from "cloudflare:test";
import { describe, it, expect, beforeEach } from "vitest";
import { call, provision, login, seedJob, seedPersonnel } from "./helpers";

// ─────────────────────────────────────────────────────────────────────────────
// SOP daily form (slice D2) — GET /api/fieldops/daily-form/status?job_id&date.
//   - For each parent family in [jha, visitor-sign-in, incident-report, daily-report], returns the
//     LATEST submission for (job_id, work_date = date) via the S4 family match: form_code = parent
//     OR a versioned variant (`parent || '-v%'`). Absent families are simply omitted from `filed`.
//   - `daily_filed` mirrors filed["daily-report"] (drives the "already filed today" banner).
//   - `filed_by_name` is DISPLAY-NAME-ONLY (personnel.name via submitted_as; the W9 posture) —
//     an account with no roster link yields NULL, never the raw username.
//   - Bound + validated: bad date shape → 400; unknown job → 404; oversize job_id → 400;
//     unauthenticated → 401; cap.tasks.own-gated → 403 when the role lacks the cap.
// Runs against the REAL worker with Miniflare D1 (migrations auto-apply); per-test storage isolation.
// ─────────────────────────────────────────────────────────────────────────────


// Submission with explicit created_at + submitted_as so latest-wins + attribution are provable.
async function seedSubmission(
  jobId: string,
  formCode: string,
  workDate: string,
  opts: { createdAt?: number; submittedAs?: string | null; uuid?: string } = {},
): Promise<void> {
  await env.DB.prepare(
    "INSERT INTO submissions (submission_uuid, job_id, form_code, work_date, payload_json, created_at, submitted_as) VALUES (?,?,?,?,?,?,?)",
  )
    .bind(
      opts.uuid ?? `sub-${formCode}-${Math.random()}`,
      jobId,
      formCode,
      workDate,
      "{}",
      opts.createdAt ?? 1_700_000_000,
      opts.submittedAs ?? null,
    )
    .run();
}

interface FiledEntry { filed_at: number; filed_by_name: string | null }
interface StatusResp { filed: Record<string, FiledEntry>; daily_filed: FiledEntry | null }
async function status(cookie: string, jobId: string, date: string): Promise<StatusResp> {
  const res = await call(`/api/fieldops/daily-form/status?job_id=${encodeURIComponent(jobId)}&date=${encodeURIComponent(date)}`, { cookie });
  expect(res.status, await res.clone().text()).toBe(200);
  return (await res.json()) as StatusResp;
}

const DATE = "2026-07-02";
let manager: string;

beforeEach(async () => {
  await env.DB.batch([
    env.DB.prepare("DELETE FROM submissions"),
    env.DB.prepare("DELETE FROM personnel"),
    env.DB.prepare("DELETE FROM users"),
    env.DB.prepare("DELETE FROM jobs"),
  ]);
  await provision("mgr.mo", "password123", "manager");
  manager = await login("mgr.mo", "password123");
  await seedJob("JOB-A");
  await seedJob("JOB-B");
  // Ownership scope (security review): a non-admin actor may only read their OWN placement — the
  // default querying manager is placed on JOB-A.
  await seedPersonnel("Mo Manager", "mgr.mo", "JOB-A");
});

describe("daily-form status — family match", () => {
  it("returns the filed entry for an exact PARENT form_code submission", async () => {
    await seedSubmission("JOB-A", "daily-report", DATE, { createdAt: 1_700_000_100 });
    const s = await status(manager, "JOB-A", DATE);
    expect(s.filed["daily-report"]).toEqual({ filed_at: 1_700_000_100, filed_by_name: null });
    expect(s.daily_filed).toEqual({ filed_at: 1_700_000_100, filed_by_name: null });
  });

  it("matches a VERSIONED VARIANT (daily-report-v2 → the daily-report family), incl. the link families", async () => {
    await seedSubmission("JOB-A", "daily-report-v2", DATE);
    await seedSubmission("JOB-A", "jha-v3", DATE);
    await seedSubmission("JOB-A", "incident-report-v3", DATE);
    const s = await status(manager, "JOB-A", DATE);
    expect(Object.keys(s.filed).sort()).toEqual(["daily-report", "incident-report", "jha"]);
    expect(s.filed["visitor-sign-in"]).toBeUndefined();
    expect(s.daily_filed).not.toBeNull();
  });

  it("material-incident joined the reported families (Material receipts M2) — versioned match + attribution", async () => {
    await seedSubmission("JOB-A", "material-incident-v1", DATE, {
      createdAt: 1_700_000_200,
      submittedAs: "mgr.mo",
    });
    const s = await status(manager, "JOB-A", DATE);
    expect(s.filed["material-incident"]).toEqual({ filed_at: 1_700_000_200, filed_by_name: "Mo Manager" });
    expect(s.daily_filed).toBeNull(); // the incident form is NOT the daily-report family
  });

  it("does NOT false-match a sibling prefix without the '-v' anchor, a wrong date, or a wrong job", async () => {
    await seedSubmission("JOB-A", "daily-report-extra", DATE); // sibling family, no -v anchor
    await seedSubmission("JOB-A", "jha-v3", "1999-01-01"); // wrong date
    await seedSubmission("JOB-B", "visitor-sign-in-v1", DATE); // wrong job
    const s = await status(manager, "JOB-A", DATE);
    expect(s.filed).toEqual({});
    expect(s.daily_filed).toBeNull();
  });

  it("empty day → empty map + null daily_filed (a valid, distinguishable nothing-filed state)", async () => {
    const s = await status(manager, "JOB-A", DATE);
    expect(s.filed).toEqual({});
    expect(s.daily_filed).toBeNull();
  });

  it("the LATEST submission wins within a family (created_at DESC)", async () => {
    await seedSubmission("JOB-A", "jha-v3", DATE, { createdAt: 1_700_000_100, submittedAs: "mgr.mo" });
    await seedSubmission("JOB-A", "jha-v3", DATE, { createdAt: 1_700_000_900, submittedAs: "sam.sub" });
    await seedPersonnel("Sam Submitter", "sam.sub", "JOB-A");
    const s = await status(manager, "JOB-A", DATE);
    expect(s.filed["jha"]).toEqual({ filed_at: 1_700_000_900, filed_by_name: "Sam Submitter" });
  });
});

describe("daily-form status — filed_by display-name-only (W9 posture)", () => {
  it("resolves submitted_as → the personnel display name", async () => {
    await seedSubmission("JOB-A", "daily-report-v2", DATE, { submittedAs: "mgr.mo" });
    const s = await status(manager, "JOB-A", DATE);
    expect(s.daily_filed?.filed_by_name).toBe("Mo Manager");
  });

  it("an account with NO roster link yields NULL — never the raw username", async () => {
    await seedSubmission("JOB-A", "daily-report-v2", DATE, { submittedAs: "orphan.account" });
    const s = await status(manager, "JOB-A", DATE);
    expect(s.daily_filed?.filed_by_name).toBeNull();
    expect(JSON.stringify(s)).not.toContain("orphan.account");
  });

  it("a NULL submitted_as (pre-attribution row) yields NULL", async () => {
    await seedSubmission("JOB-A", "daily-report-v2", DATE, { submittedAs: null });
    const s = await status(manager, "JOB-A", DATE);
    expect(s.daily_filed?.filed_by_name).toBeNull();
  });
});

describe("daily-form status — validation + gating", () => {
  it("rejects a bad date shape (400 invalid_date) before touching the job", async () => {
    for (const bad of ["2026-7-2", "02-07-2026", "tomorrow", "", "2026-07-02T00:00:00"]) {
      const res = await call(`/api/fieldops/daily-form/status?job_id=JOB-A&date=${encodeURIComponent(bad)}`, { cookie: manager });
      expect(res.status, `date=${bad}`).toBe(400);
      expect(((await res.json()) as { error: string }).error).toBe("invalid_date");
    }
  });

  it("404s an unknown job and 400s an oversize job_id (bound before query)", async () => {
    const unknown = await call(`/api/fieldops/daily-form/status?job_id=JOB-NOPE&date=${DATE}`, { cookie: manager });
    expect(unknown.status).toBe(404);
    const oversize = await call(`/api/fieldops/daily-form/status?job_id=${"x".repeat(65)}&date=${DATE}`, { cookie: manager });
    expect(oversize.status).toBe(400);
    const missing = await call(`/api/fieldops/daily-form/status?date=${DATE}`, { cookie: manager });
    expect(missing.status).toBe(400); // absent job_id = zero-length = invalid_job_id
  });

  it("401s an unauthenticated caller", async () => {
    const res = await call(`/api/fieldops/daily-form/status?job_id=JOB-A&date=${DATE}`);
    expect(res.status).toBe(401);
  });

  it("403s a role stripped of cap.tasks.own (fail-closed capability gate)", async () => {
    await provision("sam.sub", "password123", "submitter");
    const sub = await login("sam.sub", "password123");
    // Strip the cap from the submitter role — the gate must fail closed, not open.
    await env.DB.prepare("DELETE FROM role_capabilities WHERE role_key='submitter' AND capability_key='cap.tasks.own'").run();
    const res = await call(`/api/fieldops/daily-form/status?job_id=JOB-A&date=${DATE}`, { cookie: sub });
    expect(res.status).toBe(403);
  });
});

describe("daily-form status — per-job ownership scope (security review)", () => {
  it("a manager placed on JOB-A is 403 forbidden_job for JOB-B (no cross-job probing)", async () => {
    await seedSubmission("JOB-B", "incident-report-v1", DATE, {});
    const res = await call(`/api/fieldops/daily-form/status?job_id=JOB-B&date=${DATE}`, { cookie: manager });
    expect(res.status).toBe(403);
    expect(((await res.json()) as { error: string }).error).toBe("forbidden_job");
  });

  it("an UNPLACED actor (no linked personnel) is 403 even for a real job", async () => {
    await provision("sam.sub", "password123", "submitter");
    const sub = await login("sam.sub", "password123");
    const res = await call(`/api/fieldops/daily-form/status?job_id=JOB-A&date=${DATE}`, { cookie: sub });
    expect(res.status).toBe(403);
  });

  it("an admin (cap.jobtracker.manage) may query ANY job", async () => {
    await provision("adm.a", "password123", "admin");
    const admin = await login("adm.a", "password123");
    const res = await call(`/api/fieldops/daily-form/status?job_id=JOB-B&date=${DATE}`, { cookie: admin });
    expect(res.status).toBe(200);
  });
});
