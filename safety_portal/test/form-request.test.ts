import { env, SELF } from "cloudflare:test";
import { describe, it, expect, beforeEach } from "vitest";

// ─────────────────────────────────────────────────────────────────────────────
// PR-5 — Form Request (browse + batch request) and the REQUESTER-BOUND download.
// Runs against the REAL worker in workerd with a Miniflare D1. Same SELF.fetch
// cookie-forwarding harness as pdf.test.ts.
//
// What this locks (the access matrix that pdf.test.ts does NOT cover):
//   GET /api/filed     — active-job scope, filed-only, per-account request/ready state;
//   POST /api/request-pdfs — body/cap/audit/idempotency, valid-only filtering;
//   /pdf requester-binding — a DIFFERENT account (even the actor) who never requested
//     gets 404; the request expires after 24h (→ 404) and a re-request restores access;
//     an admin downloads without a pdf_requests row.
// ─────────────────────────────────────────────────────────────────────────────

const BASE = "https://portal.test";
const ADMIN_BEARER = "test-admin-token"; // == PORTAL_ADMIN_API_TOKEN
const ACTIVE = "JOB-ACTIVE";
const INACTIVE = "JOB-INACTIVE";

type Init = RequestInit & { cookie?: string; bearer?: string };

function call(path: string, init: Init = {}): Promise<Response> {
  const headers = new Headers(init.headers);
  if (init.cookie) headers.set("Cookie", init.cookie);
  if (init.bearer) headers.set("Authorization", `Bearer ${init.bearer}`);
  if (init.body && !headers.has("content-type")) headers.set("content-type", "application/json");
  return SELF.fetch(BASE + path, { ...init, headers });
}

function cookieFrom(res: Response): string {
  return (res.headers.get("set-cookie") ?? "").split(";")[0];
}

async function provision(username: string, password: string, role: "submitter" | "admin" = "submitter") {
  const res = await call("/api/internal/admin/users", {
    method: "POST",
    bearer: ADMIN_BEARER,
    body: JSON.stringify({ username, password, role }),
  });
  expect(res.status, await res.clone().text()).toBe(201);
}

async function login(username: string, password: string): Promise<string> {
  const res = await call("/api/login", { method: "POST", body: JSON.stringify({ username, password }) });
  expect(res.status, await res.clone().text()).toBe(200);
  return cookieFrom(res);
}

/** Insert a filed submission row directly. Defaults: box_verified=1 (filed), on the ACTIVE job. */
async function seedSubmission(opts: {
  uuid: string;
  actor: string;
  jobId?: string;
  boxVerified?: number;
  formCode?: string;
  workDate?: string;
  boxFileId?: string | null;
}): Promise<void> {
  const boxFileId = "boxFileId" in opts ? opts.boxFileId : "boxfile-123";
  await env.DB
    .prepare(
      "INSERT OR REPLACE INTO submissions " +
        "(submission_uuid, job_id, form_code, work_date, payload_json, hmac, box_verified, filed_at, " +
        "actor_username, submitted_as, box_file_id) " +
        "VALUES (?,?,?,?,?,?,?,unixepoch(),?,?,?)",
    )
    .bind(
      opts.uuid,
      opts.jobId ?? ACTIVE,
      opts.formCode ?? "jha",
      opts.workDate ?? "2026-06-08",
      "{}",
      "deadbeef",
      opts.boxVerified ?? 1,
      opts.actor,
      opts.actor,
      boxFileId,
    )
    .run();
}

/** Stamp the cache ready: one chunk row + pdf_ready_at set, so /pdf can serve a 200. */
async function seedCache(uuid: string): Promise<void> {
  await env.DB
    .prepare("INSERT OR REPLACE INTO filed_pdfs (submission_uuid, chunk_index, chunk_total, chunk_b64) VALUES (?,0,1,?)")
    .bind(uuid, btoa("%PDF-1.4"))
    .run();
  await env.DB.prepare("UPDATE submissions SET pdf_ready_at=unixepoch() WHERE submission_uuid=?").bind(uuid).run();
}

/** Insert a live (ageSec=0) or aged pdf_requests row. ageSec>86400 → expired (outside 24h). */
async function requestAs(uuid: string, account: string, ageSec = 0): Promise<void> {
  await env.DB
    .prepare("INSERT OR REPLACE INTO pdf_requests (submission_uuid, account, requested_at) VALUES (?,?,unixepoch()-?)")
    .bind(uuid, account, ageSec)
    .run();
}

beforeEach(async () => {
  await env.DB.batch([
    env.DB.prepare("DELETE FROM users"),
    env.DB.prepare("DELETE FROM audit_log"),
    env.DB.prepare("DELETE FROM submissions"),
    env.DB.prepare("DELETE FROM filed_pdfs"),
    env.DB.prepare("DELETE FROM pdf_requests"),
    env.DB.prepare("DELETE FROM jobs"),
    env.DB.prepare("INSERT OR REPLACE INTO jobs (job_id, project_name, active) VALUES (?,?,1)").bind(ACTIVE, "Active Job"),
    env.DB.prepare("INSERT OR REPLACE INTO jobs (job_id, project_name, active) VALUES (?,?,0)").bind(INACTIVE, "Inactive Job"),
  ]);
});

// ── GET /api/filed — browse an active job's filed forms ─────────────────────────
describe("GET /api/filed — active-job browse", () => {
  beforeEach(async () => {
    await provision("pm.alice", "password123", "submitter");
    await provision("pm.bob", "password123", "submitter");
  });

  it("no session → 401 (requireSession)", async () => {
    const res = await call(`/api/filed?job_id=${ACTIVE}`);
    expect(res.status).toBe(401);
  });

  it("missing or empty job_id → 404", async () => {
    const c = await login("pm.alice", "password123");
    for (const path of ["/api/filed", "/api/filed?job_id="]) {
      const res = await call(path, { cookie: c });
      expect(res.status).toBe(404);
    }
  });

  it("an INACTIVE job → 404 (browse is scoped to active jobs, no enumeration)", async () => {
    await seedSubmission({ uuid: "inact-1", actor: "pm.alice", jobId: INACTIVE });
    const c = await login("pm.alice", "password123");
    const res = await call(`/api/filed?job_id=${INACTIVE}`, { cookie: c });
    expect(res.status).toBe(404);
    expect(await res.json()).toMatchObject({ error: "not_found" });
  });

  it("returns ONLY filed (box_verified=1) rows — never unfiled or rejected", async () => {
    await seedSubmission({ uuid: "filed-1", actor: "pm.alice" });
    await seedSubmission({ uuid: "unfiled-1", actor: "pm.alice", boxVerified: 0 });
    await seedSubmission({ uuid: "rejected-1", actor: "pm.alice", boxVerified: -1 });
    const c = await login("pm.alice", "password123");
    const res = await call(`/api/filed?job_id=${ACTIVE}`, { cookie: c });
    expect(res.status).toBe(200);
    const { filed } = (await res.json()) as { filed: { submission_uuid: string }[] };
    expect(filed.map((f) => f.submission_uuid)).toEqual(["filed-1"]);
  });

  it("returns metadata only (no payload) with per-row requested/ready flags", async () => {
    await seedSubmission({ uuid: "m-1", actor: "pm.alice", formCode: "toolbox", workDate: "2026-06-09" });
    const c = await login("pm.alice", "password123");
    const res = await call(`/api/filed?job_id=${ACTIVE}`, { cookie: c });
    const { filed } = (await res.json()) as {
      filed: { submission_uuid: string; form_code: string; work_date: string; filed_at: number; requested: boolean; ready: boolean }[];
    };
    expect(filed).toHaveLength(1);
    expect(filed[0]).toMatchObject({ submission_uuid: "m-1", form_code: "toolbox", work_date: "2026-06-09", requested: false, ready: false });
    expect(filed[0].filed_at).toBeGreaterThan(0);
    expect(filed[0]).not.toHaveProperty("payload_json");
  });

  it("request/ready state is PER-ACCOUNT (requester-bound): alice's request is invisible to bob", async () => {
    await seedSubmission({ uuid: "p-1", actor: "pm.alice" });
    await seedCache("p-1"); // cache is populated…
    await requestAs("p-1", "pm.alice"); // …but ONLY alice requested it
    const ca = await login("pm.alice", "password123");
    const cb = await login("pm.bob", "password123");

    const ra = (await (await call(`/api/filed?job_id=${ACTIVE}`, { cookie: ca })).json()) as { filed: { requested: boolean; ready: boolean }[] };
    const rb = (await (await call(`/api/filed?job_id=${ACTIVE}`, { cookie: cb })).json()) as { filed: { requested: boolean; ready: boolean }[] };

    expect(ra.filed[0]).toMatchObject({ requested: true, ready: true }); // alice: requested + cache ready
    expect(rb.filed[0]).toMatchObject({ requested: false, ready: false }); // bob: never requested → not ready to HIM
  });

  it("an EXPIRED request (>24h) no longer counts as requested", async () => {
    await seedSubmission({ uuid: "e-1", actor: "pm.alice" });
    await seedCache("e-1");
    await requestAs("e-1", "pm.alice", 90_000); // outside the 24h window
    const c = await login("pm.alice", "password123");
    const { filed } = (await (await call(`/api/filed?job_id=${ACTIVE}`, { cookie: c })).json()) as { filed: { requested: boolean; ready: boolean }[] };
    expect(filed[0]).toMatchObject({ requested: false, ready: false });
  });
});

// ── POST /api/request-pdfs — batch request ──────────────────────────────────────
describe("POST /api/request-pdfs — batch", () => {
  beforeEach(async () => {
    await provision("pm.alice", "password123", "submitter");
  });

  it("no session → 401", async () => {
    const res = await call("/api/request-pdfs", { method: "POST", body: JSON.stringify({ uuids: ["x"] }) });
    expect(res.status).toBe(401);
  });

  it("a non-object / missing-uuids / empty-uuids body → 400", async () => {
    const c = await login("pm.alice", "password123");
    for (const body of ["[]", "null", "42", JSON.stringify({}), JSON.stringify({ uuids: [] })]) {
      const res = await call("/api/request-pdfs", { method: "POST", cookie: c, body });
      expect(res.status, body).toBe(400);
    }
  });

  it("over 20 uuids → 400 too_many", async () => {
    const c = await login("pm.alice", "password123");
    const uuids = Array.from({ length: 21 }, (_, i) => `u-${i}`);
    const res = await call("/api/request-pdfs", { method: "POST", cookie: c, body: JSON.stringify({ uuids }) });
    expect(res.status).toBe(400);
    expect(await res.json()).toMatchObject({ error: "too_many" });
  });

  it("filed uuids on the active job → upserts a pdf_requests row per uuid for THIS account + ONE audit row", async () => {
    await seedSubmission({ uuid: "b-1", actor: "pm.alice" });
    await seedSubmission({ uuid: "b-2", actor: "pm.alice" });
    const c = await login("pm.alice", "password123");
    const res = await call("/api/request-pdfs", { method: "POST", cookie: c, body: JSON.stringify({ uuids: ["b-1", "b-2"] }) });
    expect(res.status).toBe(200);
    expect(await res.json()).toMatchObject({ requested: 2 });

    const rows = await env.DB.prepare("SELECT submission_uuid, account FROM pdf_requests ORDER BY submission_uuid").all<{ submission_uuid: string; account: string }>();
    expect(rows.results).toEqual([
      { submission_uuid: "b-1", account: "pm.alice" },
      { submission_uuid: "b-2", account: "pm.alice" },
    ]);
    const audits = await env.DB.prepare("SELECT COUNT(*) AS n FROM audit_log WHERE action='request_pdfs'").first<{ n: number }>();
    expect(audits?.n).toBe(1); // ONE audit per batch, not per uuid
  });

  it("filters to valid only: unfiled, inactive-job, and unknown uuids are silently dropped", async () => {
    await seedSubmission({ uuid: "ok-1", actor: "pm.alice" }); // valid
    await seedSubmission({ uuid: "unfiled", actor: "pm.alice", boxVerified: 0 }); // not filed
    await seedSubmission({ uuid: "inact", actor: "pm.alice", jobId: INACTIVE }); // inactive job
    const c = await login("pm.alice", "password123");
    const res = await call("/api/request-pdfs", { method: "POST", cookie: c, body: JSON.stringify({ uuids: ["ok-1", "unfiled", "inact", "ghost"] }) });
    expect(await res.json()).toMatchObject({ requested: 1 });
    const rows = await env.DB.prepare("SELECT submission_uuid FROM pdf_requests").all<{ submission_uuid: string }>();
    expect(rows.results.map((r) => r.submission_uuid)).toEqual(["ok-1"]);
  });

  it("a repeat request is idempotent (no duplicate row) and refreshes the window", async () => {
    await seedSubmission({ uuid: "r-1", actor: "pm.alice" });
    await requestAs("r-1", "pm.alice", 50_000); // an existing, aging request
    const c = await login("pm.alice", "password123");
    await call("/api/request-pdfs", { method: "POST", cookie: c, body: JSON.stringify({ uuids: ["r-1"] }) });
    const rows = await env.DB.prepare("SELECT requested_at FROM pdf_requests WHERE submission_uuid='r-1' AND account='pm.alice'").all<{ requested_at: number }>();
    expect(rows.results).toHaveLength(1); // still one row (upsert, not insert)
    // the window was refreshed → now well within 24h
    const live = await env.DB.prepare("SELECT 1 FROM pdf_requests WHERE submission_uuid='r-1' AND account='pm.alice' AND requested_at > unixepoch()-86400").first();
    expect(live).not.toBeNull();
  });

  it("dedupes uuids within a single batch", async () => {
    await seedSubmission({ uuid: "d-1", actor: "pm.alice" });
    const c = await login("pm.alice", "password123");
    const res = await call("/api/request-pdfs", { method: "POST", cookie: c, body: JSON.stringify({ uuids: ["d-1", "d-1", "d-1"] }) });
    expect(await res.json()).toMatchObject({ requested: 1 });
  });
});

// ── /pdf — REQUESTER-BOUND download matrix (the PR-5 heart) ──────────────────────
describe("GET /pdf — requester-bound download matrix", () => {
  const UUID = "ffffffff-0000-0000-0000-000000000001";
  beforeEach(async () => {
    await provision("pm.requester", "password123", "submitter");
    await provision("pm.actor", "password123", "submitter");
    await provision("admin.one", "password123", "admin");
    // The ACTOR submitted it; cache is ready. Downloads are bound to whoever REQUESTS.
    await seedSubmission({ uuid: UUID, actor: "pm.actor", formCode: "jha", workDate: "2026-06-08" });
    await seedCache(UUID);
  });

  it("the requester (live request within 24h) downloads it (200, attachment)", async () => {
    await requestAs(UUID, "pm.requester");
    const c = await login("pm.requester", "password123");
    const res = await call(`/api/submissions/${UUID}/pdf`, { cookie: c });
    expect(res.status).toBe(200);
    expect(res.headers.get("content-type")).toBe("application/pdf");
    expect(res.headers.get("content-disposition")).toBe('attachment; filename="Active Job_2026-06-08_jha.pdf"');
  });

  it("a DIFFERENT account — even the original actor — who never requested → 404 (the PDF is private to its requester)", async () => {
    await requestAs(UUID, "pm.requester"); // only the requester has a live request
    const c = await login("pm.actor", "password123"); // the actor never requested THIS staged copy
    const res = await call(`/api/submissions/${UUID}/pdf`, { cookie: c });
    expect(res.status).toBe(404);
    expect(await res.json()).toMatchObject({ error: "not_found" });
  });

  it("after the 24h window expires → 404; a fresh request restores access", async () => {
    await requestAs(UUID, "pm.requester", 90_000); // expired
    const c = await login("pm.requester", "password123");
    const expired = await call(`/api/submissions/${UUID}/pdf`, { cookie: c });
    expect(expired.status).toBe(404);

    // Re-request through the real endpoint → access restored.
    const rr = await call("/api/request-pdfs", { method: "POST", cookie: c, body: JSON.stringify({ uuids: [UUID] }) });
    expect(await rr.json()).toMatchObject({ requested: 1 });
    const restored = await call(`/api/submissions/${UUID}/pdf`, { cookie: c });
    expect(restored.status).toBe(200);
  });

  it("an admin downloads ANY filed PDF without holding a pdf_requests row", async () => {
    const c = await login("admin.one", "password123");
    const res = await call(`/api/submissions/${UUID}/pdf`, { cookie: c });
    expect(res.status).toBe(200);
  });

  it("the requester sees the cache READY on /status; a non-requester sees not-ready (404 if also not owner)", async () => {
    await requestAs(UUID, "pm.requester");
    const cr = await login("pm.requester", "password123");
    const sr = (await (await call(`/api/submissions/${UUID}/status`, { cookie: cr })).json()) as { requested: boolean; ready: boolean; expires_at: number | null };
    expect(sr).toMatchObject({ requested: true, ready: true });
    expect(sr.expires_at).toBeGreaterThan(0);

    // pm.actor is the owner (actor_username) so /status is reachable (200) but NOT ready to him.
    const ca = await login("pm.actor", "password123");
    const sa = (await (await call(`/api/submissions/${UUID}/status`, { cookie: ca })).json()) as { requested: boolean; ready: boolean };
    expect(sa).toMatchObject({ requested: false, ready: false });
  });
});

// ── GET /api/filed/months — PR-6 cascade source (month buckets + form codes) ─────
describe("GET /api/filed/months — PR-6 cascade", () => {
  beforeEach(async () => {
    await provision("pm.alice", "password123", "submitter");
  });

  it("no session → 401 (requireSession)", async () => {
    const res = await call(`/api/filed/months?job_id=${ACTIVE}`);
    expect(res.status).toBe(401);
  });

  it("missing/empty job_id → 404", async () => {
    const c = await login("pm.alice", "password123");
    for (const path of ["/api/filed/months", "/api/filed/months?job_id="]) {
      expect((await call(path, { cookie: c })).status).toBe(404);
    }
  });

  it("an INACTIVE/unknown job → 404 not_found (same guard as /api/filed, no enumeration)", async () => {
    const c = await login("pm.alice", "password123");
    const inact = await call(`/api/filed/months?job_id=${INACTIVE}`, { cookie: c });
    expect(inact.status).toBe(404);
    expect(await inact.json()).toMatchObject({ error: "not_found" });
    expect((await call(`/api/filed/months?job_id=GHOST`, { cookie: c })).status).toBe(404);
  });

  it("returns months newest-first with counts + distinct form codes, FILED only", async () => {
    await seedSubmission({ uuid: "jun-1", actor: "pm.alice", formCode: "jha", workDate: "2026-06-08" });
    await seedSubmission({ uuid: "jun-2", actor: "pm.alice", formCode: "toolbox", workDate: "2026-06-20" });
    await seedSubmission({ uuid: "may-1", actor: "pm.alice", formCode: "jha", workDate: "2026-05-15" });
    await seedSubmission({ uuid: "may-2", actor: "pm.alice", formCode: "jha", workDate: "2026-05-31" });
    // An UNFILED row (box_verified=0) must be excluded from both aggregates.
    await seedSubmission({ uuid: "unf-1", actor: "pm.alice", formCode: "incident", workDate: "2026-06-01", boxVerified: 0 });
    const c = await login("pm.alice", "password123");
    const res = await call(`/api/filed/months?job_id=${ACTIVE}`, { cookie: c });
    expect(res.status).toBe(200);
    const body = (await res.json()) as { months: { month: string; count: number }[]; form_codes: string[] };
    expect(body.months).toEqual([
      { month: "2026-06", count: 2 },
      { month: "2026-05", count: 2 },
    ]);
    expect(body.form_codes).toEqual(["jha", "toolbox"]); // distinct, sorted, no "incident" (unfiled)
  });

  it("a job with no filed forms → empty arrays (200, not 404)", async () => {
    const c = await login("pm.alice", "password123");
    const res = await call(`/api/filed/months?job_id=${ACTIVE}`, { cookie: c });
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual({ months: [], form_codes: [] });
  });
});

// ── GET /api/filed — PR-6 month + form_code filters ──────────────────────────────
describe("GET /api/filed — PR-6 month/form filters", () => {
  beforeEach(async () => {
    await provision("pm.alice", "password123", "submitter");
    await provision("pm.bob", "password123", "submitter");
    // June: jha (j-jha), toolbox (j-tb). May: jha (m-jha). A straddle pair around the boundary.
    await seedSubmission({ uuid: "j-jha", actor: "pm.alice", formCode: "jha", workDate: "2026-06-08" });
    await seedSubmission({ uuid: "j-tb", actor: "pm.alice", formCode: "toolbox", workDate: "2026-06-20" });
    await seedSubmission({ uuid: "m-jha", actor: "pm.alice", formCode: "jha", workDate: "2026-05-15" });
    await seedSubmission({ uuid: "edge-may", actor: "pm.alice", formCode: "jha", workDate: "2026-05-31" });
    await seedSubmission({ uuid: "edge-jun", actor: "pm.alice", formCode: "jha", workDate: "2026-06-01" });
  });

  async function filedUuids(cookie: string, query: string): Promise<string[]> {
    const res = await call(`/api/filed?${query}`, { cookie });
    expect(res.status, await res.clone().text()).toBe(200);
    const { filed } = (await res.json()) as { filed: { submission_uuid: string }[] };
    return filed.map((f) => f.submission_uuid).sort();
  }

  it("month= narrows to that WORK-month (not filed_at)", async () => {
    const c = await login("pm.alice", "password123");
    expect(await filedUuids(c, `job_id=${ACTIVE}&month=2026-06`)).toEqual(["edge-jun", "j-jha", "j-tb"]);
    expect(await filedUuids(c, `job_id=${ACTIVE}&month=2026-05`)).toEqual(["edge-may", "m-jha"]);
  });

  it("a work_date straddling the month boundary lands in the right bucket", async () => {
    const c = await login("pm.alice", "password123");
    expect(await filedUuids(c, `job_id=${ACTIVE}&month=2026-05`)).toContain("edge-may"); // 2026-05-31 → May
    expect(await filedUuids(c, `job_id=${ACTIVE}&month=2026-05`)).not.toContain("edge-jun");
    expect(await filedUuids(c, `job_id=${ACTIVE}&month=2026-06`)).toContain("edge-jun"); // 2026-06-01 → June
  });

  it("form_code= narrows to that form; combined with month= it intersects", async () => {
    const c = await login("pm.alice", "password123");
    expect(await filedUuids(c, `job_id=${ACTIVE}&form_code=toolbox`)).toEqual(["j-tb"]);
    expect(await filedUuids(c, `job_id=${ACTIVE}&month=2026-06&form_code=jha`)).toEqual(["edge-jun", "j-jha"]);
  });

  it("a malformed month → 400 bad_request; an empty month param is ignored (no filter)", async () => {
    const c = await login("pm.alice", "password123");
    for (const bad of ["2026-6", "2026-13-01", "garbage", "26-06"]) {
      const res = await call(`/api/filed?job_id=${ACTIVE}&month=${encodeURIComponent(bad)}`, { cookie: c });
      expect(res.status, bad).toBe(400);
      expect(await res.json()).toMatchObject({ error: "bad_request" });
    }
    // "?month=" (empty) is treated as ABSENT → all filed (5 rows), not a 400.
    expect((await filedUuids(c, `job_id=${ACTIVE}&month=`)).length).toBe(5);
  });

  it("no params → unchanged PR-5 all-filed behavior (backward compatible)", async () => {
    const c = await login("pm.alice", "password123");
    expect((await filedUuids(c, `job_id=${ACTIVE}`)).length).toBe(5);
  });

  it("per-account requested/ready stays correct UNDER a month filter", async () => {
    await seedCache("j-jha");
    await requestAs("j-jha", "pm.alice"); // only alice requested
    const ca = await login("pm.alice", "password123");
    const cb = await login("pm.bob", "password123");
    const ra = (await (await call(`/api/filed?job_id=${ACTIVE}&month=2026-06&form_code=jha`, { cookie: ca })).json()) as { filed: { submission_uuid: string; requested: boolean; ready: boolean }[] };
    const rb = (await (await call(`/api/filed?job_id=${ACTIVE}&month=2026-06&form_code=jha`, { cookie: cb })).json()) as { filed: { submission_uuid: string; requested: boolean; ready: boolean }[] };
    expect(ra.filed.find((f) => f.submission_uuid === "j-jha")).toMatchObject({ requested: true, ready: true });
    expect(rb.filed.find((f) => f.submission_uuid === "j-jha")).toMatchObject({ requested: false, ready: false });
  });
});
