import { env, SELF } from "cloudflare:test";
import { describe, it, expect, beforeEach } from "vitest";

// ─────────────────────────────────────────────────────────────────────────────
// PR-4 Part A — request-driven canonical PDF download. Runs against the REAL
// worker in workerd with a Miniflare D1 (migrations applied by apply-migrations.ts,
// incl. 0011's pdf_requested/box_file_id/pdf_ready_at + the filed_pdfs chunk table).
// Same harness as submit-as.test.ts: SELF.fetch is stateless, so cookies are
// forwarded by hand to mirror a browser's same-origin session.
//
// What this locks:
//   - the ownership 404 matrix: owner / attributee / foreign / admin / unknown-uuid;
//   - POST request-pdf is idempotent (flips once) and audits exactly once;
//   - GET status returns the {requested, ready, expires_at} shape;
//   - GET internal/pdf-requests returns ONLY requested + unready + filed rows;
//   - POST internal/filed-pdf chunk upload is idempotent + completion sets ready;
//   - GET /pdf reassembles ≥2 chunks (bytes round-trip) + Content-Disposition
//     attachment + 404 when not ready.
// ─────────────────────────────────────────────────────────────────────────────

const BASE = "https://portal.test";
const ADMIN_BEARER = "test-admin-token"; // == PORTAL_ADMIN_API_TOKEN
const INTERNAL_BEARER = "test-internal-token"; // == PORTAL_INTERNAL_API_TOKEN
const JOB = "JOB-PDF";

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

/** Insert a submission row directly (bypassing /api/submit) so a test can set the exact
 *  attribution + box_verified + box_file_id it needs. */
async function seedSubmission(opts: {
  uuid: string;
  actor: string;
  submittedAs?: string;
  boxVerified?: number; // default 1 (filed)
  formCode?: string;
  workDate?: string;
  boxFileId?: string | null; // pass null EXPLICITLY for an unfiled-to-Box-id row
}): Promise<void> {
  // Honor an explicit `null` boxFileId (??-coalescing would wrongly default it).
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
      JOB,
      opts.formCode ?? "jha",
      opts.workDate ?? "2026-06-08",
      "{}",
      "deadbeef",
      opts.boxVerified ?? 1,
      opts.actor,
      opts.submittedAs ?? opts.actor,
      boxFileId,
    )
    .run();
}

beforeEach(async () => {
  await env.DB.batch([
    env.DB.prepare("DELETE FROM users"),
    env.DB.prepare("DELETE FROM audit_log"),
    env.DB.prepare("DELETE FROM submissions"),
    env.DB.prepare("DELETE FROM filed_pdfs"),
    env.DB.prepare("INSERT OR REPLACE INTO jobs (job_id, project_name, active) VALUES (?,?,1)").bind(JOB, "PDF Test"),
  ]);
});

// ── Ownership 404 matrix ──────────────────────────────────────────────────────
describe("PDF ownership 404 matrix", () => {
  const UUID = "aaaaaaaa-0000-0000-0000-000000000001";
  beforeEach(async () => {
    await provision("pm.owner", "password123", "submitter");
    await provision("pm.attributee", "password123", "submitter");
    await provision("pm.foreign", "password123", "submitter");
    await provision("admin.one", "password123", "admin");
    // actor = pm.owner, attributed = pm.attributee (an admin submit-as scenario).
    await seedSubmission({ uuid: UUID, actor: "pm.owner", submittedAs: "pm.attributee" });
  });

  it("the true actor (owner) sees the row (200 on status)", async () => {
    const c = await login("pm.owner", "password123");
    const res = await call(`/api/submissions/${UUID}/status`, { cookie: c });
    expect(res.status).toBe(200);
  });

  it("the attributee sees the row (200 on status)", async () => {
    const c = await login("pm.attributee", "password123");
    const res = await call(`/api/submissions/${UUID}/status`, { cookie: c });
    expect(res.status).toBe(200);
  });

  it("a foreign account → 404 (not 403, no enumeration)", async () => {
    const c = await login("pm.foreign", "password123");
    for (const path of [`/api/submissions/${UUID}/status`, `/api/submissions/${UUID}/pdf`]) {
      const res = await call(path, { cookie: c });
      expect(res.status).toBe(404);
      expect(await res.json()).toMatchObject({ error: "not_found" });
    }
    const post = await call(`/api/submissions/${UUID}/request-pdf`, { method: "POST", cookie: c });
    expect(post.status).toBe(404);
  });

  it("an admin sees ANY row (200 on status)", async () => {
    const c = await login("admin.one", "password123");
    const res = await call(`/api/submissions/${UUID}/status`, { cookie: c });
    expect(res.status).toBe(200);
  });

  it("an unknown uuid → 404 even for the would-be owner", async () => {
    const c = await login("pm.owner", "password123");
    const res = await call(`/api/submissions/unknown-uuid-0000/status`, { cookie: c });
    expect(res.status).toBe(404);
    expect(await res.json()).toMatchObject({ error: "not_found" });
  });

  it("no session → 401 (requireSession), not 404", async () => {
    const res = await call(`/api/submissions/${UUID}/status`);
    expect(res.status).toBe(401);
  });
});

// ── request-pdf idempotency + audit ───────────────────────────────────────────
describe("POST request-pdf — idempotent + audits once", () => {
  const UUID = "bbbbbbbb-0000-0000-0000-000000000001";
  beforeEach(async () => {
    await provision("pm.owner", "password123", "submitter");
    await seedSubmission({ uuid: UUID, actor: "pm.owner" });
  });

  it("flips pdf_requested 0→1 once; a second request is a no-op; audits exactly once", async () => {
    const c = await login("pm.owner", "password123");
    const r1 = await call(`/api/submissions/${UUID}/request-pdf`, { method: "POST", cookie: c });
    expect(r1.status).toBe(200);
    expect(await r1.json()).toMatchObject({ ok: true, ready: false });

    const flag1 = await env.DB.prepare("SELECT pdf_requested FROM submissions WHERE submission_uuid=?").bind(UUID).first<{ pdf_requested: number }>();
    expect(flag1?.pdf_requested).toBe(1);

    const r2 = await call(`/api/submissions/${UUID}/request-pdf`, { method: "POST", cookie: c });
    expect(r2.status).toBe(200);

    const audits = await env.DB.prepare("SELECT COUNT(*) AS n FROM audit_log WHERE action='request_pdf'").first<{ n: number }>();
    expect(audits?.n).toBe(1); // only the real 0→1 flip is audited
  });

  it("request-pdf on a rejected (box_verified=-1) row → 404", async () => {
    await seedSubmission({ uuid: "rej-uuid", actor: "pm.owner", boxVerified: -1 });
    const c = await login("pm.owner", "password123");
    const res = await call(`/api/submissions/rej-uuid/request-pdf`, { method: "POST", cookie: c });
    expect(res.status).toBe(404);
  });
});

// ── status shape ──────────────────────────────────────────────────────────────
describe("GET status — shape", () => {
  const UUID = "cccccccc-0000-0000-0000-000000000001";
  beforeEach(async () => {
    await provision("pm.owner", "password123", "submitter");
    await seedSubmission({ uuid: UUID, actor: "pm.owner" });
  });

  it("returns {requested, ready, expires_at} — not requested, not ready", async () => {
    const c = await login("pm.owner", "password123");
    const res = await call(`/api/submissions/${UUID}/status`, { cookie: c });
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual({ requested: false, ready: false, expires_at: null });
  });

  it("after request-pdf: requested true, still not ready", async () => {
    const c = await login("pm.owner", "password123");
    await call(`/api/submissions/${UUID}/request-pdf`, { method: "POST", cookie: c });
    const res = await call(`/api/submissions/${UUID}/status`, { cookie: c });
    expect(await res.json()).toMatchObject({ requested: true, ready: false, expires_at: null });
  });

  it("a rejected (box_verified=-1) row → 404 on status AND pdf (consistent with request-pdf)", async () => {
    await seedSubmission({ uuid: "rej-status", actor: "pm.owner", boxVerified: -1 });
    const c = await login("pm.owner", "password123");
    for (const path of [`/api/submissions/rej-status/status`, `/api/submissions/rej-status/pdf`]) {
      const res = await call(path, { cookie: c });
      expect(res.status).toBe(404);
    }
  });
});

// ── internal/pdf-requests filter ───────────────────────────────────────────────
describe("GET internal/pdf-requests — serviceable filter", () => {
  it("returns ONLY requested + unready + filed (box_file_id) rows", async () => {
    // requested + filed + unready → INCLUDED
    await seedSubmission({ uuid: "svc-1", actor: "pm.owner", boxFileId: "box-1" });
    await env.DB.prepare("UPDATE submissions SET pdf_requested=1 WHERE submission_uuid='svc-1'").run();
    // requested but NO box_file_id → excluded
    await seedSubmission({ uuid: "svc-2", actor: "pm.owner", boxFileId: null });
    await env.DB.prepare("UPDATE submissions SET pdf_requested=1 WHERE submission_uuid='svc-2'").run();
    // requested + filed but ALREADY cached (pdf_ready_at set) → excluded
    await seedSubmission({ uuid: "svc-3", actor: "pm.owner", boxFileId: "box-3" });
    await env.DB.prepare("UPDATE submissions SET pdf_requested=1, pdf_ready_at=unixepoch() WHERE submission_uuid='svc-3'").run();
    // NOT requested → excluded
    await seedSubmission({ uuid: "svc-4", actor: "pm.owner", boxFileId: "box-4" });

    const res = await call("/api/internal/pdf-requests", { bearer: INTERNAL_BEARER });
    expect(res.status).toBe(200);
    const { pdf_requests } = (await res.json()) as { pdf_requests: { submission_uuid: string; box_file_id: string }[] };
    expect(pdf_requests.map((r) => r.submission_uuid)).toEqual(["svc-1"]);
    expect(pdf_requests[0]).toMatchObject({ submission_uuid: "svc-1", box_file_id: "box-1", form_code: "jha", work_date: "2026-06-08" });
  });

  it("requires the internal bearer (401 without)", async () => {
    const res = await call("/api/internal/pdf-requests");
    expect(res.status).toBe(401);
  });
});

// ── filed-pdf chunk upload ──────────────────────────────────────────────────────
describe("POST internal/filed-pdf — chunk upload", () => {
  const UUID = "dddddddd-0000-0000-0000-000000000001";
  beforeEach(async () => {
    await seedSubmission({ uuid: UUID, actor: "pm.owner", boxFileId: "box-d" });
    await env.DB.prepare("UPDATE submissions SET pdf_requested=1 WHERE submission_uuid=?").bind(UUID).run();
  });

  function chunkBody(index: number, total: number, b64: string) {
    return JSON.stringify({ submission_uuid: UUID, chunk_index: index, chunk_total: total, chunk_b64: b64 });
  }

  it("a single chunk completes the cache and sets pdf_ready_at", async () => {
    const res = await call("/api/internal/filed-pdf", {
      method: "POST",
      bearer: INTERNAL_BEARER,
      body: chunkBody(0, 1, btoa("hello")),
    });
    expect(res.status).toBe(200);
    expect(await res.json()).toMatchObject({ ok: true, ready: true, stored: true, received: 1 });
    const row = await env.DB.prepare("SELECT pdf_ready_at FROM submissions WHERE submission_uuid=?").bind(UUID).first<{ pdf_ready_at: number | null }>();
    expect(row?.pdf_ready_at).not.toBeNull();
  });

  it("re-POSTing the same chunk is idempotent (INSERT OR REPLACE, no extra row)", async () => {
    await call("/api/internal/filed-pdf", { method: "POST", bearer: INTERNAL_BEARER, body: chunkBody(0, 2, btoa("aa")) });
    await call("/api/internal/filed-pdf", { method: "POST", bearer: INTERNAL_BEARER, body: chunkBody(0, 2, btoa("aa")) });
    const n = await env.DB.prepare("SELECT COUNT(*) AS n FROM filed_pdfs WHERE submission_uuid=?").bind(UUID).first<{ n: number }>();
    expect(n?.n).toBe(1); // still one chunk row at index 0
  });

  it("two chunks complete the cache (count reaches chunk_total → ready)", async () => {
    const r0 = await call("/api/internal/filed-pdf", { method: "POST", bearer: INTERNAL_BEARER, body: chunkBody(0, 2, btoa("aa")) });
    expect(await r0.json()).toMatchObject({ ready: false, received: 1 });
    const r1 = await call("/api/internal/filed-pdf", { method: "POST", bearer: INTERNAL_BEARER, body: chunkBody(1, 2, btoa("bb")) });
    expect(await r1.json()).toMatchObject({ ready: true, received: 2 });
  });

  it("once cached, a further chunk upload is a no-op (stored:false)", async () => {
    await call("/api/internal/filed-pdf", { method: "POST", bearer: INTERNAL_BEARER, body: chunkBody(0, 1, btoa("x")) });
    const res = await call("/api/internal/filed-pdf", { method: "POST", bearer: INTERNAL_BEARER, body: chunkBody(0, 1, btoa("x")) });
    expect(await res.json()).toMatchObject({ ok: true, ready: true, stored: false });
  });

  it("an unfiled / unknown row → 404", async () => {
    const res = await call("/api/internal/filed-pdf", {
      method: "POST",
      bearer: INTERNAL_BEARER,
      body: JSON.stringify({ submission_uuid: "no-such-row", chunk_index: 0, chunk_total: 1, chunk_b64: btoa("x") }),
    });
    expect(res.status).toBe(404);
  });

  it("a bad chunk (out-of-range index) → 400 invalid_chunk", async () => {
    const res = await call("/api/internal/filed-pdf", { method: "POST", bearer: INTERNAL_BEARER, body: chunkBody(2, 2, btoa("x")) });
    expect(res.status).toBe(400);
    expect(await res.json()).toMatchObject({ error: "invalid_chunk" });
  });

  it("an INCONSISTENT chunk_total set never completes (truncation guard)", async () => {
    // count can reach the completing chunk_total with the WRONG total declared on earlier
    // chunks: chunks 0,1 declare total 5; the completing chunk 2 declares total 3 → count=3,
    // but the chunk_totals disagree, so the cache must NOT be marked ready (a bare
    // count===chunk_total would serve a 3-of-5 truncated PDF as canonical).
    await call("/api/internal/filed-pdf", { method: "POST", bearer: INTERNAL_BEARER, body: chunkBody(0, 5, btoa("aa")) });
    await call("/api/internal/filed-pdf", { method: "POST", bearer: INTERNAL_BEARER, body: chunkBody(1, 5, btoa("bb")) });
    const r = await call("/api/internal/filed-pdf", { method: "POST", bearer: INTERNAL_BEARER, body: chunkBody(2, 3, btoa("cc")) });
    expect(await r.json()).toMatchObject({ ready: false, received: 3 });
    const row = await env.DB.prepare("SELECT pdf_ready_at FROM submissions WHERE submission_uuid=?").bind(UUID).first<{ pdf_ready_at: number | null }>();
    expect(row?.pdf_ready_at).toBeNull(); // never stamped ready on an inconsistent set
  });

  it("a chunk_b64 over the max string length → 400 (rejected before the regex scan)", async () => {
    const tooLong = "A".repeat(2_000_000); // > MAX_CHUNK_B64_LEN (~1.33M)
    const res = await call("/api/internal/filed-pdf", { method: "POST", bearer: INTERNAL_BEARER, body: chunkBody(0, 1, tooLong) });
    expect(res.status).toBe(400);
    expect(await res.json()).toMatchObject({ error: "invalid_chunk", detail: "chunk_b64" });
  });

  it("requires the internal bearer (401 without)", async () => {
    const res = await call("/api/internal/filed-pdf", { method: "POST", body: chunkBody(0, 1, btoa("x")) });
    expect(res.status).toBe(401);
  });
});

// ── GET /pdf reassembly ─────────────────────────────────────────────────────────
describe("GET /pdf — reassembly + headers", () => {
  const UUID = "eeeeeeee-0000-0000-0000-000000000001";
  beforeEach(async () => {
    await provision("pm.owner", "password123", "submitter");
    await seedSubmission({ uuid: UUID, actor: "pm.owner", boxFileId: "box-e", formCode: "jha", workDate: "2026-06-08" });
    await env.DB.prepare("UPDATE submissions SET pdf_requested=1 WHERE submission_uuid=?").bind(UUID).run();
  });

  it("404 when not yet ready", async () => {
    const c = await login("pm.owner", "password123");
    const res = await call(`/api/submissions/${UUID}/pdf`, { cookie: c });
    expect(res.status).toBe(404);
    expect(await res.json()).toMatchObject({ error: "not_ready" });
  });

  it("reassembles ≥2 chunks byte-for-byte + Content-Disposition attachment", async () => {
    // Build a 2-chunk PDF that round-trips exactly. Use raw byte values (incl. non-ASCII)
    // so the base64 decode path is exercised, then split into two chunks.
    const original = new Uint8Array([0x25, 0x50, 0x44, 0x46, 0x2d, 0x31, 0x2e, 0x34, 0x00, 0xff, 0xfe, 0x0a]); // "%PDF-1.4" + bytes
    const split = 7;
    const part0 = original.slice(0, split);
    const part1 = original.slice(split);
    const b64 = (u: Uint8Array) => btoa(String.fromCharCode(...u));
    await call("/api/internal/filed-pdf", { method: "POST", bearer: INTERNAL_BEARER, body: JSON.stringify({ submission_uuid: UUID, chunk_index: 0, chunk_total: 2, chunk_b64: b64(part0) }) });
    await call("/api/internal/filed-pdf", { method: "POST", bearer: INTERNAL_BEARER, body: JSON.stringify({ submission_uuid: UUID, chunk_index: 1, chunk_total: 2, chunk_b64: b64(part1) }) });

    const c = await login("pm.owner", "password123");
    const res = await call(`/api/submissions/${UUID}/pdf`, { cookie: c });
    expect(res.status).toBe(200);
    expect(res.headers.get("content-type")).toBe("application/pdf");
    expect(res.headers.get("content-disposition")).toBe('attachment; filename="jha-2026-06-08.pdf"');

    const got = new Uint8Array(await res.arrayBuffer());
    expect([...got]).toEqual([...original]);
  });
});
