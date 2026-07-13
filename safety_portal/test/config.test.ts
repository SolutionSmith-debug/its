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

  it("enqueues a delivery_contacts edit (201 queued) + persists the row — Feature C registry round-trip", async () => {
    await provision("admin.one", "admin");
    const cookie = await login("admin.one");
    const payload = { contacts: [{ name: "Riley Receiver", phone: "555-0142", email: "riley@site.example" }] };
    const res = await post(cookie, "/api/config/requests", {
      workstream: "po_materials",
      artifact_key: "delivery_contacts",
      op: "edit",
      payload,
    });
    expect(res.status, await res.clone().text()).toBe(201);
    const row = await env.DB
      .prepare("SELECT workstream, artifact_key, op, target_version, status, payload FROM config_requests")
      .first<{ payload: string }>();
    expect(row).toMatchObject({
      workstream: "po_materials",
      artifact_key: "delivery_contacts",
      op: "edit",
      target_version: null,
      status: "queued",
    });
    expect(JSON.parse(row!.payload)).toEqual(payload); // the queued payload round-trips intact
  });

  it("rejects a versioned op on delivery_contacts (json artifact → 400 invalid_op)", async () => {
    await provision("admin.one", "admin");
    const cookie = await login("admin.one");
    const res = await post(cookie, "/api/config/requests", {
      workstream: "po_materials",
      artifact_key: "delivery_contacts",
      op: "add_version",
      target_version: "v2",
      payload: { contacts: [] },
    });
    expect(res.status).toBe(400);
    expect(await res.json()).toMatchObject({ error: "invalid_op" });
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

  it("enqueues a terms set_current with a valid target_version (201) + persists it", async () => {
    await provision("admin.one", "admin");
    const cookie = await login("admin.one");
    const res = await post(cookie, "/api/config/requests", {
      workstream: "po_materials",
      artifact_key: "terms",
      op: "set_current",
      target_version: "standard_17_v2",
      payload: { profile_id: "standard_17" },
    });
    expect(res.status, await res.clone().text()).toBe(201);
    const row = await env.DB.prepare("SELECT op, target_version FROM config_requests").first();
    expect(row).toMatchObject({ op: "set_current", target_version: "standard_17_v2" });
  });

  it("rejects a terms set_current with no target_version (400 invalid_target_version)", async () => {
    await provision("admin.one", "admin");
    const cookie = await login("admin.one");
    const res = await post(cookie, "/api/config/requests", {
      workstream: "po_materials",
      artifact_key: "terms",
      op: "set_current",
      payload: { profile_id: "standard_17" },
    });
    expect(res.status).toBe(400);
    expect(await res.json()).toMatchObject({ error: "invalid_target_version" });
  });

  it("rejects set_current on a json artifact (kind mismatch → 400 invalid_op)", async () => {
    await provision("admin.one", "admin");
    const cookie = await login("admin.one");
    const res = await post(
      cookie,
      "/api/config/requests",
      editBody({ op: "set_current", target_version: "purchaser_v2" }),
    );
    expect(res.status).toBe(400);
    expect(await res.json()).toMatchObject({ error: "invalid_op" });
  });

  // ── exhibit (subcontracts, PR-B2 — the versioned per-trade Article II templates) ──
  it("enqueues an exhibit add_version with target_version + {template_key, text} (201)", async () => {
    await provision("admin.one", "admin");
    const cookie = await login("admin.one");
    const res = await post(cookie, "/api/config/requests", {
      workstream: "subcontracts",
      artifact_key: "exhibit",
      op: "add_version",
      target_version: "v2",
      payload: { template_key: "civil", text: "Civil v2 scope." },
    });
    expect(res.status, await res.clone().text()).toBe(201);
    const row = await env.DB
      .prepare("SELECT workstream, artifact_key, op, target_version FROM config_requests")
      .first();
    expect(row).toMatchObject({
      workstream: "subcontracts",
      artifact_key: "exhibit",
      op: "add_version",
      target_version: "v2",
    });
  });

  it("enqueues an exhibit set_current (201)", async () => {
    await provision("admin.one", "admin");
    const cookie = await login("admin.one");
    const res = await post(cookie, "/api/config/requests", {
      workstream: "subcontracts",
      artifact_key: "exhibit",
      op: "set_current",
      target_version: "v2",
      payload: { template_key: "civil" },
    });
    expect(res.status, await res.clone().text()).toBe(201);
  });

  it("rejects an exhibit 'edit' op (versioned artifact → 400 invalid_op)", async () => {
    await provision("admin.one", "admin");
    const cookie = await login("admin.one");
    const res = await post(cookie, "/api/config/requests", {
      workstream: "subcontracts",
      artifact_key: "exhibit",
      op: "edit",
      payload: { template_key: "civil" },
    });
    expect(res.status).toBe(400);
    expect(await res.json()).toMatchObject({ error: "invalid_op" });
  });

  it("rejects an exhibit add_version with a missing template_key (400 invalid_template_key)", async () => {
    await provision("admin.one", "admin");
    const cookie = await login("admin.one");
    const res = await post(cookie, "/api/config/requests", {
      workstream: "subcontracts",
      artifact_key: "exhibit",
      op: "add_version",
      target_version: "v2",
      payload: { text: "scope with no key" },
    });
    expect(res.status).toBe(400);
    expect(await res.json()).toMatchObject({ error: "invalid_template_key" });
  });

  it("enqueues an exhibit create_profile (new trade + template) 201 + records template_key/trade in audit", async () => {
    await provision("admin.one", "admin");
    const cookie = await login("admin.one");
    const res = await post(cookie, "/api/config/requests", {
      workstream: "subcontracts",
      artifact_key: "exhibit",
      op: "create_profile",
      payload: { template_key: "battery_storage", trade: "Battery Storage", text: "Battery Storage scope." },
    });
    expect(res.status, await res.clone().text()).toBe(201);
    const row = await env.DB
      .prepare("SELECT op, target_version FROM config_requests")
      .first<{ op: string; target_version: string | null }>();
    expect(row!.op).toBe("create_profile");
    expect(row!.target_version).toBeNull(); // create carries version v1 IN payload, not the column
    const detail = (await env.DB.prepare("SELECT detail FROM audit_log WHERE action='config_edit'").first<{ detail: string }>())!.detail;
    expect(JSON.parse(detail)).toMatchObject({ op: "create_profile", template_key: "battery_storage", trade: "Battery Storage" });
  });

  it("normalizes a padded trade in the enqueued payload + audit (parity with the manifest write)", async () => {
    await provision("admin.one", "admin");
    const cookie = await login("admin.one");
    const res = await post(cookie, "/api/config/requests", {
      workstream: "subcontracts", artifact_key: "exhibit", op: "create_profile",
      payload: { template_key: "battery_storage", trade: "  Battery Storage  ", text: "scope." },
    });
    expect(res.status, await res.clone().text()).toBe(201);
    const row = await env.DB.prepare("SELECT payload FROM config_requests").first<{ payload: string }>();
    expect(JSON.parse(row!.payload).trade).toBe("Battery Storage"); // trimmed in the queued record
    const detail = (await env.DB.prepare("SELECT detail FROM audit_log WHERE action='config_edit'").first<{ detail: string }>())!.detail;
    expect(JSON.parse(detail).trade).toBe("Battery Storage"); // and in the audit row
  });

  it("rejects an exhibit create_profile for an EXISTING template key (409 template_exists → add_version, not create)", async () => {
    await provision("admin.one", "admin");
    const cookie = await login("admin.one");
    const res = await post(cookie, "/api/config/requests", {
      workstream: "subcontracts", artifact_key: "exhibit", op: "create_profile",
      payload: { template_key: "civil", trade: "New Civil", text: "x" },
    });
    expect(res.status).toBe(409);
    expect(await res.json()).toMatchObject({ error: "template_exists" });
  });

  it("rejects an exhibit create_profile for an EXISTING trade (409 trade_exists → a re-map, not create)", async () => {
    await provision("admin.one", "admin");
    const cookie = await login("admin.one");
    const res = await post(cookie, "/api/config/requests", {
      workstream: "subcontracts", artifact_key: "exhibit", op: "create_profile",
      payload: { template_key: "civil_two", trade: "Civil", text: "x" },
    });
    expect(res.status).toBe(409);
    expect(await res.json()).toMatchObject({ error: "trade_exists" });
  });

  it("rejects an exhibit create_profile with a missing trade (400 invalid_trade)", async () => {
    await provision("admin.one", "admin");
    const cookie = await login("admin.one");
    const res = await post(cookie, "/api/config/requests", {
      workstream: "subcontracts", artifact_key: "exhibit", op: "create_profile",
      payload: { template_key: "battery_storage", text: "no trade" },
    });
    expect(res.status).toBe(400);
    expect(await res.json()).toMatchObject({ error: "invalid_trade" });
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

  it("enqueues a valid subcontracts config edit (contractor → 201 queued) — SC-S2 filled the tier", async () => {
    // SC-S2 filled the subcontracts artifacts map (contractor/payment_terms/terms), so it is no longer
    // a placeholder: an admin holding cap.subcontracts.manage (granted by 0051) can queue a real edit.
    await provision("admin.one", "admin");
    const cookie = await login("admin.one");
    const res = await post(cookie, "/api/config/requests", {
      workstream: "subcontracts", artifact_key: "contractor", op: "edit",
      payload: { entity: "Evergreen Renewables LLC" },
    });
    expect(res.status, await res.clone().text()).toBe(201);
    const row = await env.DB.prepare("SELECT workstream, artifact_key FROM config_requests").first();
    expect(row).toMatchObject({ workstream: "subcontracts", artifact_key: "contractor" });
  });

  it("rejects an unknown subcontracts artifact (400 invalid_artifact)", async () => {
    // The cap is held (admin), so this falls through to the artifact lookup — 'anything' is not one of
    // {contractor, payment_terms, terms}, so it fails closed at 400 invalid_artifact.
    await provision("admin.one", "admin");
    const cookie = await login("admin.one");
    const res = await post(cookie, "/api/config/requests", editBody({ workstream: "subcontracts", artifact_key: "anything" }));
    expect(res.status).toBe(400);
    expect(await res.json()).toMatchObject({ error: "invalid_artifact" });
  });

  it("a submitter is rejected on the subcontracts workstream by the cap gate (403)", async () => {
    // The cap gate stays load-bearing: a non-admin without cap.subcontracts.manage is refused at the
    // cap check (authorization-before-work), never reaching the artifact lookup.
    await provision("pm.bob", "submitter");
    const cookie = await login("pm.bob");
    const res = await post(cookie, "/api/config/requests", editBody({ workstream: "subcontracts", artifact_key: "contractor" }));
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

describe("POST /api/config/requests — create_profile (mint a new terms profile)", () => {
  function createLibBody(over: { payload?: Record<string, unknown>; [k: string]: unknown } = {}): Record<string, unknown> {
    const { payload: payloadOver, ...rest } = over;
    return {
      workstream: "po_materials",
      artifact_key: "terms",
      op: "create_profile",
      ...rest,
      payload: {
        profile_id: "vendor_acme",
        kind: "library",
        label: "ACME vendor terms",
        version_id: "v1",
        text: "1. ACME clause.",
        ...payloadOver,
      },
    };
  }

  it("enqueues a valid library create_profile (201 queued) + persists op create_profile, target_version NULL", async () => {
    await provision("admin.one", "admin");
    const cookie = await login("admin.one");
    const res = await post(cookie, "/api/config/requests", createLibBody());
    expect(res.status, await res.clone().text()).toBe(201);
    const row = await env.DB.prepare("SELECT op, target_version, payload FROM config_requests").first<{ op: string; target_version: string | null; payload: string }>();
    expect(row!.op).toBe("create_profile");
    expect(row!.target_version).toBeNull(); // create_profile carries the version IN payload
    expect(JSON.parse(row!.payload)).toMatchObject({ profile_id: "vendor_acme", kind: "library" });
  });

  it("records profile_id + kind in the create_profile audit row (forensic parity with target_version)", async () => {
    await provision("admin.one", "admin");
    const cookie = await login("admin.one");
    await post(cookie, "/api/config/requests", createLibBody());
    const detail = (await env.DB.prepare("SELECT detail FROM audit_log WHERE action='config_edit'").first<{ detail: string }>())!.detail;
    expect(JSON.parse(detail)).toMatchObject({ op: "create_profile", profile_id: "vendor_acme", kind: "library" });
  });

  it("enqueues a valid attach create_profile (201) with a render_line", async () => {
    await provision("admin.one", "admin");
    const cookie = await login("admin.one");
    const res = await post(cookie, "/api/config/requests", {
      workstream: "po_materials",
      artifact_key: "terms",
      op: "create_profile",
      payload: { profile_id: "vendor_gtc", kind: "attach", label: "Vendor GTC", render_line: "SUBJECT TO THE GTC." },
    });
    expect(res.status, await res.clone().text()).toBe(201);
  });

  it("rejects a duplicate profile id (409 profile_exists — that is an add_version, not a create)", async () => {
    await provision("admin.one", "admin");
    const cookie = await login("admin.one");
    // standard_17 is a real profile in the bundled manifest.
    const res = await post(cookie, "/api/config/requests", createLibBody({ payload: { profile_id: "standard_17" } }));
    expect(res.status).toBe(409);
    expect(await res.json()).toMatchObject({ error: "profile_exists" });
  });

  it("rejects a malformed profile id (400 invalid_profile_id)", async () => {
    await provision("admin.one", "admin");
    const cookie = await login("admin.one");
    const res = await post(cookie, "/api/config/requests", createLibBody({ payload: { profile_id: "Bad-Id!" } }));
    expect(res.status).toBe(400);
    expect(await res.json()).toMatchObject({ error: "invalid_profile_id" });
  });

  it("rejects a bad kind (400 invalid_profile_kind)", async () => {
    await provision("admin.one", "admin");
    const cookie = await login("admin.one");
    const res = await post(cookie, "/api/config/requests", createLibBody({ payload: { profile_id: "ok_id", kind: "weird" } }));
    expect(res.status).toBe(400);
    expect(await res.json()).toMatchObject({ error: "invalid_profile_kind" });
  });

  it("rejects a missing label (400 invalid_label)", async () => {
    await provision("admin.one", "admin");
    const cookie = await login("admin.one");
    const res = await post(cookie, "/api/config/requests", createLibBody({ payload: { profile_id: "ok_id", label: "   " } }));
    expect(res.status).toBe(400);
    expect(await res.json()).toMatchObject({ error: "invalid_label" });
  });

  it("rejects a library create missing version_id (400 invalid_target_version)", async () => {
    await provision("admin.one", "admin");
    const cookie = await login("admin.one");
    const res = await post(cookie, "/api/config/requests", {
      workstream: "po_materials", artifact_key: "terms", op: "create_profile",
      payload: { profile_id: "ok_id", kind: "library", label: "L", text: "x" }, // no version_id
    });
    expect(res.status).toBe(400);
    expect(await res.json()).toMatchObject({ error: "invalid_target_version" });
  });

  it("rejects a library create with empty text (400 invalid_payload)", async () => {
    await provision("admin.one", "admin");
    const cookie = await login("admin.one");
    const res = await post(cookie, "/api/config/requests", createLibBody({ payload: { profile_id: "ok_id", text: "   " } }));
    expect(res.status).toBe(400);
    expect(await res.json()).toMatchObject({ error: "invalid_payload" });
  });

  it("rejects an attach create with no render_line (400 invalid_payload)", async () => {
    await provision("admin.one", "admin");
    const cookie = await login("admin.one");
    const res = await post(cookie, "/api/config/requests", {
      workstream: "po_materials", artifact_key: "terms", op: "create_profile",
      payload: { profile_id: "ok_id", kind: "attach", label: "L" }, // no render_line
    });
    expect(res.status).toBe(400);
    expect(await res.json()).toMatchObject({ error: "invalid_payload" });
  });

  it("a submitter (no cap.po.manage) is rejected before any create_profile work (403)", async () => {
    await provision("pm.bob", "submitter");
    const cookie = await login("pm.bob");
    const res = await post(cookie, "/api/config/requests", createLibBody());
    expect(res.status).toBe(403);
    expect((await env.DB.prepare("SELECT COUNT(*) n FROM config_requests").first<{ n: number }>())!.n).toBe(0);
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

  it("hides a cleared row by default; ?include_cleared=1 shows it", async () => {
    await provision("admin.one", "admin");
    const cookie = await login("admin.one");
    const id = await seedCfg("purchaser", "archived");
    // Clear it, then confirm the default view omits it but include_cleared surfaces it.
    expect((await post(cookie, `/api/config/requests/${id}/clear`, {})).status).toBe(200);
    const def = (await (await call("/api/config/requests/status", { cookie })).json()) as { requests: unknown[] };
    expect(def.requests.length).toBe(0);
    const inc = (await (await call("/api/config/requests/status?include_cleared=1", { cookie })).json()) as {
      requests: { id: number; cleared_at: number | null }[];
    };
    expect(inc.requests.map((r) => r.id)).toEqual([id]);
    expect(inc.requests[0].cleared_at).not.toBeNull();
  });
});

describe("POST /api/config/requests/:id/clear — forensic-safe soft-dismiss", () => {
  it("clears a terminal row: gone from default view, still SELECT-able (forensic), reappears with include_cleared", async () => {
    await provision("admin.one", "admin");
    const cookie = await login("admin.one");
    const id = await seedCfg("purchaser", "failed");
    const res = await post(cookie, `/api/config/requests/${id}/clear`, {});
    expect(res.status, await res.clone().text()).toBe(200);
    expect(await res.json()).toMatchObject({ ok: true, cleared: true });
    // The row is NOT deleted — cleared_at set, everything else intact (the §50 forensic record).
    const row = await env.DB
      .prepare("SELECT status, cleared_at FROM config_requests WHERE id=?")
      .bind(id)
      .first<{ status: string; cleared_at: number | null }>();
    expect(row).not.toBeNull();
    expect(row!.status).toBe("failed");
    expect(row!.cleared_at).not.toBeNull();
    // Gone from the default monitor, present under include_cleared.
    const def = (await (await call("/api/config/requests/status", { cookie })).json()) as { requests: unknown[] };
    expect(def.requests.length).toBe(0);
    const inc = (await (await call("/api/config/requests/status?include_cleared=1", { cookie })).json()) as {
      requests: { id: number }[];
    };
    expect(inc.requests.map((r) => r.id)).toEqual([id]);
  });

  it("clears a `live` row (deploy succeeded, archive pending — the operator's done view)", async () => {
    await provision("admin.one", "admin");
    const cookie = await login("admin.one");
    const id = await seedCfg("tax", "live");
    expect((await post(cookie, `/api/config/requests/${id}/clear`, {})).status).toBe(200);
    const row = await env.DB.prepare("SELECT cleared_at FROM config_requests WHERE id=?").bind(id).first<{ cleared_at: number | null }>();
    expect(row!.cleared_at).not.toBeNull();
  });

  it("REFUSES to clear an in-flight (non-terminal) row — 409 config_not_terminal, row untouched", async () => {
    await provision("admin.one", "admin");
    const cookie = await login("admin.one");
    for (const status of ["queued", "validated", "tested", "merged"]) {
      const id = await seedCfg("purchaser", status);
      const res = await post(cookie, `/api/config/requests/${id}/clear`, {});
      expect(res.status, status).toBe(409);
      expect(await res.json()).toMatchObject({ error: "config_not_terminal" });
      const row = await env.DB.prepare("SELECT cleared_at FROM config_requests WHERE id=?").bind(id).first<{ cleared_at: number | null }>();
      expect(row!.cleared_at, status).toBeNull();
    }
  });

  it("is idempotent — a second clear is a no-op ok (cleared:false), timestamp unchanged", async () => {
    await provision("admin.one", "admin");
    const cookie = await login("admin.one");
    const id = await seedCfg("purchaser", "archived");
    const first = (await (await post(cookie, `/api/config/requests/${id}/clear`, {})).json()) as { cleared: boolean };
    expect(first.cleared).toBe(true);
    const ts1 = (await env.DB.prepare("SELECT cleared_at FROM config_requests WHERE id=?").bind(id).first<{ cleared_at: number }>())!.cleared_at;
    const second = await post(cookie, `/api/config/requests/${id}/clear`, {});
    expect(second.status).toBe(200);
    expect(await second.json()).toMatchObject({ ok: true, cleared: false });
    const ts2 = (await env.DB.prepare("SELECT cleared_at FROM config_requests WHERE id=?").bind(id).first<{ cleared_at: number }>())!.cleared_at;
    expect(ts2).toBe(ts1); // no re-stamp on the no-op
    // And NO second "config_clear" audit row — the no-op re-clear is forensically silent
    // (auditStmtIfChanged: the audit lands only for the clear that actually flips cleared_at).
    const n = (await env.DB.prepare("SELECT COUNT(*) n FROM audit_log WHERE action='config_clear'").first<{ n: number }>())!.n;
    expect(n).toBe(1);
  });

  it("writes exactly one audit_log row for the clear (W4 atomic)", async () => {
    await provision("admin.one", "admin");
    const cookie = await login("admin.one");
    const id = await seedCfg("purchaser", "archived");
    await post(cookie, `/api/config/requests/${id}/clear`, {});
    const n = (await env.DB.prepare("SELECT COUNT(*) n FROM audit_log WHERE action='config_clear'").first<{ n: number }>())!.n;
    expect(n).toBe(1);
  });

  it("404s clearing a row that does not exist", async () => {
    await provision("admin.one", "admin");
    const cookie = await login("admin.one");
    expect((await post(cookie, "/api/config/requests/999999/clear", {})).status).toBe(404);
  });

  it("400s on a non-numeric :id", async () => {
    await provision("admin.one", "admin");
    const cookie = await login("admin.one");
    expect((await post(cookie, "/api/config/requests/abc/clear", {})).status).toBe(400);
  });

  it("a submitter (no config cap for the row's workstream) is refused (403), row untouched", async () => {
    await provision("admin.one", "admin");
    await provision("pm.bob", "submitter");
    const id = await seedCfg("purchaser", "archived");
    const cookie = await login("pm.bob");
    const res = await post(cookie, `/api/config/requests/${id}/clear`, {});
    expect(res.status).toBe(403);
    const row = await env.DB.prepare("SELECT cleared_at FROM config_requests WHERE id=?").bind(id).first<{ cleared_at: number | null }>();
    expect(row!.cleared_at).toBeNull();
  });

  it("an unauthenticated clear is 401 (requireSession)", async () => {
    const id = await seedCfg("purchaser", "archived");
    expect((await call(`/api/config/requests/${id}/clear`, { method: "POST", body: "{}" })).status).toBe(401);
  });

  it("clearing does NOT free the C8 in-flight lock or affect the internal stuck sweep", async () => {
    // A `live` row is non-terminal for C8 / the /stuck sweep. Clearing it must not change that: the
    // internal routes filter on status, not cleared_at.
    await provision("admin.one", "admin");
    const cookie = await login("admin.one");
    // Seed a live row updated 2h ago so /stuck would surface it.
    await env.DB
      .prepare(
        "INSERT INTO config_requests (requested_by, workstream, artifact_key, op, payload, status, updated_at) " +
          "VALUES (?,?,?,?,?,?, unixepoch() - 7200)",
      )
      .bind("admin.one", "po_materials", "purchaser", "edit", "{}", "live")
      .run();
    const id = (await env.DB.prepare("SELECT id FROM config_requests ORDER BY id DESC LIMIT 1").first<{ id: number }>())!.id;
    await post(cookie, `/api/config/requests/${id}/clear`, {});
    // Still visible to the daemon's stuck sweep (status-based), unaffected by the clear.
    const { stuck } = (await (await call("/api/internal/config/stuck?older_than=3600", { bearer: CONFIG_BEARER })).json()) as {
      stuck: { id: number }[];
    };
    expect(stuck.map((s) => s.id)).toContain(id);
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
