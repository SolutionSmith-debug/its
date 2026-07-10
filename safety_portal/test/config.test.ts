import { env, SELF } from "cloudflare:test";
import { describe, it, expect, beforeEach } from "vitest";

// ─────────────────────────────────────────────────────────────────────────────
// Config-editor queue (§50) — worker/config.ts + migration 0045 (config_requests).
// Real workerd + Miniflare D1 (migrations applied by test/apply-migrations.ts).
//
// Coverage: the enqueue happy-path (201 queued) + the shape 400s (workstream / artifact /
// op / target_version / payload), the in-handler per-workstream capability gate (403), the
// C8 in-flight guard (409), the requireConfigToken bearer tier + cross-token isolation
// (missing / wrong / a constant-time-rejected PREFIX) across all four internal routes, the
// ATOMIC claim (two claims → one won, one lost — the mutual-exclusion proof), the stamp
// state-machine guard (legal vs. illegal transition), and the pending terminal-exclusion.
// ─────────────────────────────────────────────────────────────────────────────

const BASE = "https://portal.test";
const ADMIN_BEARER = "test-admin-token"; // provisioning token (helpers) — REJECTED on /api/internal/config/*
const CONFIG_BEARER = "test-config-token"; // == PORTAL_CONFIG_API_TOKEN (vitest.config.ts)
const INTERNAL_BEARER = "test-internal-token"; // portal_poll's token — must be REJECTED on /api/internal/config/*
const PO_BEARER = "test-po-token"; // PO daemon's token — must be REJECTED too

type Init = RequestInit & { cookie?: string; bearer?: string };
function call(path: string, init: Init = {}): Promise<Response> {
  const headers = new Headers(init.headers);
  if (init.cookie) headers.set("Cookie", init.cookie);
  if (init.bearer) headers.set("Authorization", `Bearer ${init.bearer}`);
  if (init.body && !headers.has("content-type")) headers.set("content-type", "application/json");
  return SELF.fetch(BASE + path, { ...init, headers });
}
async function provision(username: string, role: "submitter" | "manager" | "admin"): Promise<void> {
  const r = await call("/api/internal/admin/users", {
    method: "POST",
    bearer: ADMIN_BEARER,
    body: JSON.stringify({ username, password: "password123", role }),
  });
  expect(r.status, await r.clone().text()).toBe(201);
}
async function login(username: string): Promise<string> {
  const r = await call("/api/login", { method: "POST", body: JSON.stringify({ username, password: "password123" }) });
  expect(r.status, await r.clone().text()).toBe(200);
  return (r.headers.get("set-cookie") ?? "").split(";")[0];
}
const post = (cookie: string, path: string, body: unknown): Promise<Response> =>
  call(path, { method: "POST", cookie, body: JSON.stringify(body) });

/** A valid edit-op body: rewrite po_materials/purchaser. */
function editBody(over: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    workstream: "po_materials",
    artifact_key: "purchaser",
    op: "edit",
    payload: { entity: "Evergreen Renewables LLC", phone: "555-0100" },
    ...over,
  };
}

/** Seed a config_requests row directly (for the internal-route tests). */
async function seedCfg(
  artifact: string,
  status = "queued",
  payload = "{}",
  workstream = "po_materials",
): Promise<number> {
  const r = await env.DB
    .prepare(
      "INSERT INTO config_requests (requested_by, workstream, artifact_key, op, target_version, payload, status) VALUES (?,?,?,?,?,?,?)",
    )
    .bind("admin.one", workstream, artifact, "edit", null, payload, status)
    .run();
  return r.meta.last_row_id as number;
}

beforeEach(async () => {
  await env.DB.batch([
    env.DB.prepare("DELETE FROM users"),
    env.DB.prepare("DELETE FROM config_requests"),
    env.DB.prepare("DELETE FROM audit_log"),
  ]);
});

describe("POST /api/config/requests — enqueue", () => {
  it("enqueues a valid edit (201 queued) and writes a config_requests row", async () => {
    await provision("admin.one", "admin");
    const cookie = await login("admin.one");
    const res = await post(cookie, "/api/config/requests", editBody());
    expect(res.status, await res.clone().text()).toBe(201);
    expect(await res.json()).toMatchObject({ ok: true, status: "queued" });
    const row = await env.DB
      .prepare("SELECT workstream, artifact_key, op, target_version, status FROM config_requests")
      .first();
    expect(row).toMatchObject({
      workstream: "po_materials",
      artifact_key: "purchaser",
      op: "edit",
      target_version: null,
      status: "queued",
    });
  });

  it("enqueues an add_version with a valid target_version (201) + persists it", async () => {
    await provision("admin.one", "admin");
    const cookie = await login("admin.one");
    const res = await post(cookie, "/api/config/requests", {
      workstream: "po_materials",
      artifact_key: "terms",
      op: "add_version",
      target_version: "v2_2026",
      payload: { text: "New standard terms." },
    });
    expect(res.status, await res.clone().text()).toBe(201);
    const row = await env.DB.prepare("SELECT op, target_version FROM config_requests").first();
    expect(row).toMatchObject({ op: "add_version", target_version: "v2_2026" });
  });

  it("writes exactly one audit_log row atomically with the insert (W4)", async () => {
    await provision("admin.one", "admin");
    const cookie = await login("admin.one");
    await post(cookie, "/api/config/requests", editBody());
    const n = (await env.DB.prepare("SELECT COUNT(*) n FROM audit_log WHERE action='config_edit'").first<{ n: number }>())!.n;
    expect(n).toBe(1);
  });

  it("rejects an unknown workstream (400 invalid_workstream)", async () => {
    await provision("admin.one", "admin");
    const cookie = await login("admin.one");
    const res = await post(cookie, "/api/config/requests", editBody({ workstream: "nope" }));
    expect(res.status).toBe(400);
    expect(await res.json()).toMatchObject({ error: "invalid_workstream" });
  });

  it("rejects an unknown artifact for a real workstream (400 invalid_artifact)", async () => {
    await provision("admin.one", "admin");
    const cookie = await login("admin.one");
    const res = await post(cookie, "/api/config/requests", editBody({ artifact_key: "not_an_artifact" }));
    expect(res.status).toBe(400);
    expect(await res.json()).toMatchObject({ error: "invalid_artifact" });
  });

  it("rejects the placeholder workstream (subcontracts → 403: nobody holds cap.subcontracts.manage)", async () => {
    // The cap check runs FIRST (authorization-before-work); subcontracts' cap is unheld until a
    // real subcontract workflow registers it, so an admin with only cap.po.manage gets 403 here.
    await provision("admin.one", "admin");
    const cookie = await login("admin.one");
    const res = await post(cookie, "/api/config/requests", editBody({ workstream: "subcontracts", artifact_key: "anything" }));
    expect(res.status).toBe(403);
    expect(await res.json()).toMatchObject({ error: "forbidden" });
  });

  it("rejects an op that mismatches the artifact kind (add_version on a json artifact → 400)", async () => {
    // purchaser is kind:"json" (edit only); add_version is only for kind:"terms". The queue rejects
    // this structurally-nonsensical combo here rather than deferring it to the actuator.
    await provision("admin.one", "admin");
    const cookie = await login("admin.one");
    const res = await post(
      cookie,
      "/api/config/requests",
      editBody({ artifact_key: "purchaser", op: "add_version", target_version: "purchaser_v2" }),
    );
    expect(res.status).toBe(400);
    expect(await res.json()).toMatchObject({ error: "invalid_op" });
  });

  it("rejects `edit` on a terms (versioned) artifact → 400 invalid_op", async () => {
    await provision("admin.one", "admin");
    const cookie = await login("admin.one");
    const res = await post(cookie, "/api/config/requests", editBody({ artifact_key: "terms", op: "edit" }));
    expect(res.status).toBe(400);
    expect(await res.json()).toMatchObject({ error: "invalid_op" });
  });

  it("rejects an unknown op (400 invalid_op)", async () => {
    await provision("admin.one", "admin");
    const cookie = await login("admin.one");
    const res = await post(cookie, "/api/config/requests", editBody({ op: "delete" }));
    expect(res.status).toBe(400);
    expect(await res.json()).toMatchObject({ error: "invalid_op" });
  });

  it("rejects a malformed add_version target_version (400 invalid_target_version)", async () => {
    await provision("admin.one", "admin");
    const cookie = await login("admin.one");
    const res = await post(cookie, "/api/config/requests", {
      workstream: "po_materials",
      artifact_key: "terms",
      op: "add_version",
      target_version: "V2!", // uppercase + punctuation — fails /^[a-z0-9_]+$/
      payload: { text: "x" },
    });
    expect(res.status).toBe(400);
    expect(await res.json()).toMatchObject({ error: "invalid_target_version" });
  });

  it("rejects an empty payload (400 invalid_payload)", async () => {
    await provision("admin.one", "admin");
    const cookie = await login("admin.one");
    const res = await post(cookie, "/api/config/requests", editBody({ payload: {} }));
    expect(res.status).toBe(400);
    expect(await res.json()).toMatchObject({ error: "invalid_payload" });
  });

  it("a submitter (no cap.po.manage) is rejected by the in-handler cap gate (403)", async () => {
    await provision("pm.bob", "submitter");
    const cookie = await login("pm.bob");
    const res = await post(cookie, "/api/config/requests", editBody());
    expect(res.status).toBe(403);
    expect(await res.json()).toMatchObject({ error: "forbidden" });
    // Nothing enqueued.
    expect((await env.DB.prepare("SELECT COUNT(*) n FROM config_requests").first<{ n: number }>())!.n).toBe(0);
  });

  it("an unauthenticated request is 401 (requireSession)", async () => {
    const res = await call("/api/config/requests", { method: "POST", body: JSON.stringify(editBody()) });
    expect(res.status).toBe(401);
  });

  it("serializes per (workstream, artifact) — a 2nd in-flight edit is 409 config_edit_in_progress", async () => {
    await provision("admin.one", "admin");
    const cookie = await login("admin.one");
    expect((await post(cookie, "/api/config/requests", editBody())).status).toBe(201);
    const res2 = await post(cookie, "/api/config/requests", editBody());
    expect(res2.status).toBe(409);
    expect(await res2.json()).toMatchObject({ error: "config_edit_in_progress" });
    // A DIFFERENT artifact under the same workstream is NOT blocked.
    expect((await post(cookie, "/api/config/requests", editBody({ artifact_key: "tax", payload: { rates_bp: { IL: 900 } } }))).status).toBe(201);
  });
});

describe("GET /api/config/requests/status", () => {
  it("returns the enqueued requests newest-first for a capable session", async () => {
    await provision("admin.one", "admin");
    const cookie = await login("admin.one");
    await post(cookie, "/api/config/requests", editBody());
    const res = await call("/api/config/requests/status", { cookie });
    expect(res.status).toBe(200);
    const { requests } = (await res.json()) as { requests: { workstream: string; status: string }[] };
    expect(requests.length).toBe(1);
    expect(requests[0]).toMatchObject({ workstream: "po_materials", status: "queued" });
  });

  it("a submitter (no config cap) is rejected (403)", async () => {
    await provision("pm.bob", "submitter");
    const cookie = await login("pm.bob");
    expect((await call("/api/config/requests/status", { cookie })).status).toBe(403);
  });
});

describe("config daemon interface — bearer auth (requireConfigToken)", () => {
  const routes: [string, RequestInit][] = [
    ["/api/internal/config/pending", {}],
    ["/api/internal/config/claim", { method: "POST", body: "{}" }],
    ["/api/internal/config/stamp", { method: "POST", body: "{}" }],
    ["/api/internal/config/stuck", {}],
  ];
  it("401s on a MISSING token across all four endpoints", async () => {
    for (const [path, init] of routes) {
      expect((await call(path, init)).status, path).toBe(401);
    }
  });
  it("401s on a WRONG token across all four endpoints", async () => {
    for (const [path, init] of routes) {
      expect((await call(path, { ...init, bearer: "totally-wrong" })).status, path).toBe(401);
    }
  });
  it("401s on a token PREFIX (constant-time compare rejects a truncation, no length oracle)", async () => {
    const prefix = CONFIG_BEARER.slice(0, CONFIG_BEARER.length - 1); // "test-config-toke"
    for (const [path, init] of routes) {
      expect((await call(path, { ...init, bearer: prefix })).status, path).toBe(401);
    }
  });
  it("REJECTS a sibling tier's token (portal_poll + PO) — privilege separation", async () => {
    expect((await call("/api/internal/config/pending", { bearer: INTERNAL_BEARER })).status).toBe(401);
    expect((await call("/api/internal/config/pending", { bearer: PO_BEARER })).status).toBe(401);
    // And the config token cannot drain the submission queue.
    expect((await call("/api/internal/pending", { bearer: CONFIG_BEARER })).status).toBe(401);
  });
  it("ACCEPTS the config token on /pending (200)", async () => {
    expect((await call("/api/internal/config/pending", { bearer: CONFIG_BEARER })).status).toBe(200);
  });
});

describe("GET /api/internal/config/pending", () => {
  it("returns queued rows oldest-first, with payload; omits non-queued + leased", async () => {
    const id1 = await seedCfg("purchaser");
    const id2 = await seedCfg("tax");
    await seedCfg("terms", "live"); // non-terminal-but-not-queued → excluded
    const res = await call("/api/internal/config/pending", { bearer: CONFIG_BEARER });
    expect(res.status).toBe(200);
    const { pending } = (await res.json()) as { pending: { id: number; payload: string }[] };
    expect(pending.map((p) => p.id)).toEqual([id1, id2]);
    expect(pending[0].payload).toBe("{}");
  });
});

describe("POST /api/internal/config/claim — the ATOMIC lease (mutual exclusion)", () => {
  it("two claims of the same row → exactly one claimed:true, one claimed:false (no double-actuation)", async () => {
    const id = await seedCfg("purchaser", "queued", '{"entity":"X"}');
    const r1 = (await (await call("/api/internal/config/claim", {
      method: "POST",
      bearer: CONFIG_BEARER,
      body: JSON.stringify({ id, lease_owner: "mac1" }),
    })).json()) as { claimed: boolean; request?: { id: number; payload: string } };
    const r2 = (await (await call("/api/internal/config/claim", {
      method: "POST",
      bearer: CONFIG_BEARER,
      body: JSON.stringify({ id, lease_owner: "mac2" }),
    })).json()) as { claimed: boolean };
    // Exactly one won.
    expect([r1.claimed, r2.claimed].filter(Boolean).length).toBe(1);
    expect(r1.claimed).toBe(true);
    expect(r1.request).toMatchObject({ id, payload: '{"entity":"X"}' });
    expect(r2.claimed).toBe(false);
    // The lease landed on the winner.
    const row = await env.DB.prepare("SELECT lease_owner FROM config_requests WHERE id=?").bind(id).first<{ lease_owner: string }>();
    expect(row!.lease_owner).toBe("mac1");
  });
  it("400s on a missing id or lease_owner", async () => {
    expect((await call("/api/internal/config/claim", { method: "POST", bearer: CONFIG_BEARER, body: JSON.stringify({ id: 1 }) })).status).toBe(400);
  });
});

describe("POST /api/internal/config/stamp — state-machine guard", () => {
  it("advances a legal transition (queued -> validated)", async () => {
    const id = await seedCfg("purchaser");
    const res = await call("/api/internal/config/stamp", { method: "POST", bearer: CONFIG_BEARER, body: JSON.stringify({ id, status: "validated" }) });
    expect(((await res.json()) as { found: boolean }).found).toBe(true);
    const row = await env.DB.prepare("SELECT status FROM config_requests WHERE id=?").bind(id).first<{ status: string }>();
    expect(row!.status).toBe("validated");
  });
  it("rejects an ILLEGAL backward transition (live -> validated): found:false + reason, row unchanged", async () => {
    const id = await seedCfg("purchaser", "live");
    const body = (await (await call("/api/internal/config/stamp", { method: "POST", bearer: CONFIG_BEARER, body: JSON.stringify({ id, status: "validated" }) })).json()) as { found: boolean; reason?: string };
    expect(body.found).toBe(false);
    expect(body.reason).toMatch(/illegal transition/);
    const row = await env.DB.prepare("SELECT status FROM config_requests WHERE id=?").bind(id).first<{ status: string }>();
    expect(row!.status).toBe("live");
  });
  it("rejects stamping TO queued (never a stamp target) with 400", async () => {
    const id = await seedCfg("purchaser", "validated");
    expect((await call("/api/internal/config/stamp", { method: "POST", bearer: CONFIG_BEARER, body: JSON.stringify({ id, status: "queued" }) })).status).toBe(400);
  });
  it("records failed_stage + failure_reason on a failed stamp", async () => {
    const id = await seedCfg("purchaser");
    await call("/api/internal/config/stamp", {
      method: "POST",
      bearer: CONFIG_BEARER,
      body: JSON.stringify({ id, status: "failed", failed_stage: "deploy", failure_reason: "wrangler boom" }),
    });
    const row = await env.DB
      .prepare("SELECT status, failed_stage, failure_reason FROM config_requests WHERE id=?")
      .bind(id)
      .first<{ status: string; failed_stage: string; failure_reason: string }>();
    expect(row).toMatchObject({ status: "failed", failed_stage: "deploy", failure_reason: "wrangler boom" });
  });
});

describe("GET /api/internal/config/stuck", () => {
  it("returns a stalled non-terminal row past the cutoff; excludes terminal", async () => {
    // A validated row updated 2h ago (stalled) and an archived (terminal) row also 2h ago.
    await env.DB
      .prepare(
        "INSERT INTO config_requests (requested_by, workstream, artifact_key, op, payload, status, updated_at) " +
          "VALUES (?,?,?,?,?,?, unixepoch() - 7200)",
      )
      .bind("admin.one", "po_materials", "purchaser", "edit", "{}", "validated")
      .run();
    await env.DB
      .prepare(
        "INSERT INTO config_requests (requested_by, workstream, artifact_key, op, payload, status, updated_at) " +
          "VALUES (?,?,?,?,?,?, unixepoch() - 7200)",
      )
      .bind("admin.one", "po_materials", "tax", "edit", "{}", "archived")
      .run();
    const res = await call("/api/internal/config/stuck?older_than=3600", { bearer: CONFIG_BEARER });
    expect(res.status).toBe(200);
    const { stuck } = (await res.json()) as { stuck: { status: string }[] };
    expect(stuck.length).toBe(1);
    expect(stuck[0].status).toBe("validated");
  });
});
