/// <reference types="vite/client" />
import { env, SELF } from "cloudflare:test";
import { describe, it, expect, beforeEach } from "vitest";
import { validateDefinition } from "../worker/publishValidation";

// ─────────────────────────────────────────────────────────────────────────────
// Slice 3a — the publish enqueue gate + the C3 server-side validator.
//   - validateDefinition (pure): every SHIPPED form passes (the editor clones them,
//     so a false-reject would break add-version), plus the rejection rules.
//   - POST /api/admin/publish + GET /api/admin/publish-status (real workerd + D1,
//     migration 0010 applied by test/apply-migrations.ts).
// ─────────────────────────────────────────────────────────────────────────────

// Load every shipped definition the SAME way registry.ts does (Vite eager glob). The
// COUNT is intentionally not asserted — the publish pipeline adds forms, so a hardcoded
// total is self-defeating (it red-CIs every new-form publish). The real gate is the
// per-form validateDefinition loop below, which every shipped form must pass.
const formModules = import.meta.glob("../forms/*.json", { eager: true, import: "default" });
const FORMS: Record<string, Record<string, unknown>> = {};
for (const [path, def] of Object.entries(formModules)) {
  if (path.endsWith("meta-schema.json")) continue;
  FORMS[(def as { form_code: string }).form_code] = def as Record<string, unknown>;
}

function ctxFor(def: Record<string, unknown>) {
  return {
    identity: (def.form_code as string).replace(/-v\d+$/, ""),
    parentFormCode: def.parent_form_code as string,
  };
}
const jha = () => structuredClone(FORMS["jha-v1"]);
const jhaCtx = () => ctxFor(FORMS["jha-v1"]);
function sectionOfType(def: Record<string, unknown>, t: string): Record<string, unknown> {
  return (def.sections as Record<string, unknown>[]).find((s) => s.type === t)!;
}

describe("validateDefinition — every shipped form passes (editor clones them)", () => {
  it("loaded at least the shipped forms (count is dynamic — the pipeline adds them)", () => {
    expect(Object.keys(FORMS).length).toBeGreaterThan(0);
  });
  for (const [code, def] of Object.entries(FORMS)) {
    it(`${code} validates ok`, () => {
      expect(validateDefinition(def, ctxFor(def))).toEqual({ ok: true });
    });
  }
});

describe("validateDefinition — rejections (the C3 gate)", () => {
  it("rejects a non-object", () => {
    expect(validateDefinition(null, jhaCtx()).ok).toBe(false);
    expect(validateDefinition([], jhaCtx()).ok).toBe(false);
  });
  it("rejects form_code != identity-v<version>", () => {
    const d = jha();
    d.form_code = "jha-v2"; // version still 1
    expect(validateDefinition(d, jhaCtx()).ok).toBe(false);
  });
  it("rejects a parent mismatch vs the request envelope", () => {
    expect(validateDefinition(jha(), { identity: "jha", parentFormCode: "other" }).ok).toBe(false);
  });
  it("rejects an unknown archetype", () => {
    const d = jha();
    d.archetype = "nope";
    expect(validateDefinition(d, jhaCtx()).ok).toBe(false);
  });
  it("rejects a reserved key (work_date) used as a section key", () => {
    const d = jha();
    sectionOfType(d, "repeating_table").key = "work_date";
    expect(validateDefinition(d, jhaCtx()).ok).toBe(false);
  });
  it("allows job/work_date as HEADER field keys (the existing envelope convention)", () => {
    // jha's header already carries them; this is the positive control for the rule.
    expect(validateDefinition(jha(), jhaCtx())).toEqual({ ok: true });
  });
  it("rejects a duplicate value key across sections", () => {
    const d = jha();
    sectionOfType(d, "signature_table").key = sectionOfType(d, "repeating_table").key as string;
    expect(validateDefinition(d, jhaCtx()).ok).toBe(false);
  });
  it("rejects an invalid field input", () => {
    const d = jha();
    (sectionOfType(d, "header").fields as Record<string, unknown>[])[0].input = "rainbow";
    expect(validateDefinition(d, jhaCtx()).ok).toBe(false);
  });
  it("rejects a signature_table without exactly one signature column", () => {
    const d = jha();
    const sig = sectionOfType(d, "signature_table");
    for (const col of sig.columns as Record<string, unknown>[]) col.input = "text";
    expect(validateDefinition(d, jhaCtx()).ok).toBe(false);
  });
  it("rejects too many sections (hard bound)", () => {
    const d = jha();
    d.sections = Array.from({ length: 41 }, () => ({ type: "static_text", text: "x" }));
    expect(validateDefinition(d, jhaCtx()).ok).toBe(false);
  });

  // ── Required-content legal floor (Brief 1 PR-1) — mirrors check_required_content ──
  it("rejects a jha edit that drops the required signature_table (legal floor)", () => {
    const d = jha();
    d.sections = (d.sections as Record<string, unknown>[]).filter((s) => s.type !== "signature_table");
    const r = validateDefinition(d, jhaCtx());
    expect(r.ok).toBe(false);
    expect((r as { reason: string }).reason).toMatch(/required content missing/);
  });
  it("rejects a jha edit that drops the mandatory legal/footer line", () => {
    const d = jha();
    d.sections = (d.sections as Record<string, unknown>[]).filter(
      (s) => !(s.type === "static_text" && String((s as { text?: unknown }).text ?? "").includes("REVIEW AND REVISE THE PLAN")),
    );
    expect(validateDefinition(d, jhaCtx()).ok).toBe(false);
  });
  it("rejects a brand-new form type with no signature input (defaults_for_new_identities)", () => {
    const d = jha();
    d.form_code = "newkind-v1";
    d.parent_form_code = "newkind";
    d.version = 1;
    d.variant_label = null;
    d.sections = [{ type: "static_text", text: "hello" }];
    const r = validateDefinition(d, { identity: "newkind", parentFormCode: "newkind" });
    expect(r.ok).toBe(false);
    expect((r as { reason: string }).reason).toMatch(/signature input/);
  });
});

// ── guidance + form_link sections (SOP daily form, slice D1) ──────────────────────
describe("validateDefinition — guidance / form_link (SOP daily form D1)", () => {
  // Base: the shipped daily-report-v2 (carries both new section types) — mutate a
  // deep clone per rejection case, exactly like the jha() cases above.
  const dr2 = () => structuredClone(FORMS["daily-report-v2"]);
  const dr2Ctx = () => ctxFor(FORMS["daily-report-v2"]);

  it("daily-report-v2 (guidance + form_link + fields interleaved) validates ok", () => {
    expect(validateDefinition(dr2(), dr2Ctx())).toEqual({ ok: true });
  });
  it("rejects a guidance section with a missing heading", () => {
    const d = dr2();
    const g = sectionOfType(d, "guidance");
    delete g.heading;
    const r = validateDefinition(d, dr2Ctx());
    expect(r.ok).toBe(false);
    expect((r as { reason: string }).reason).toMatch(/guidance missing heading/);
  });
  it("rejects an unknown guidance block type (no free HTML vocabulary)", () => {
    const d = dr2();
    const g = sectionOfType(d, "guidance");
    (g.blocks as Record<string, unknown>[]).push({ type: "html", text: "<b>x</b>" });
    const r = validateDefinition(d, dr2Ctx());
    expect(r.ok).toBe(false);
    expect((r as { reason: string }).reason).toMatch(/unknown guidance block type/);
  });
  it("rejects a callout with an invalid style (closed enum)", () => {
    const d = dr2();
    const g = sectionOfType(d, "guidance");
    (g.blocks as Record<string, unknown>[]).push({ type: "callout", style: "loud", text: "x" });
    const r = validateDefinition(d, dr2Ctx());
    expect(r.ok).toBe(false);
    expect((r as { reason: string }).reason).toMatch(/callout invalid style/);
  });
  it("rejects a bullets block with a non-string / empty item", () => {
    const d = dr2();
    const g = sectionOfType(d, "guidance");
    (g.blocks as Record<string, unknown>[]).push({ type: "bullets", items: ["ok", ""] });
    expect(validateDefinition(d, dr2Ctx()).ok).toBe(false);
  });
  it("rejects an empty guidance blocks array", () => {
    const d = dr2();
    const g = sectionOfType(d, "guidance");
    g.blocks = [];
    expect(validateDefinition(d, dr2Ctx()).ok).toBe(false);
  });
  it("rejects a form_link whose parent_form_code is not in the catalog", () => {
    const d = dr2();
    const fl = sectionOfType(d, "form_link");
    fl.parent_form_code = "no-such-form-type";
    const r = validateDefinition(d, dr2Ctx());
    expect(r.ok).toBe(false);
    expect((r as { reason: string }).reason).toMatch(/not a known form type/);
  });
  it("rejects a form_link with a malformed parent_form_code slug", () => {
    const d = dr2();
    const fl = sectionOfType(d, "form_link");
    fl.parent_form_code = "Not A Slug!";
    expect(validateDefinition(d, dr2Ctx()).ok).toBe(false);
  });
  it("rejects a form_link with no label", () => {
    const d = dr2();
    const fl = sectionOfType(d, "form_link");
    delete fl.label;
    expect(validateDefinition(d, dr2Ctx()).ok).toBe(false);
  });
  it("accepts a form_link to a known catalog parent (jha)", () => {
    const d = dr2();
    const fl = sectionOfType(d, "form_link");
    fl.parent_form_code = "jha";
    expect(validateDefinition(d, dr2Ctx())).toEqual({ ok: true });
  });
  it("guidance/form_link contribute NO top-level value keys (no duplicate-key clash)", () => {
    // Two guidance sections with identical headings + two form_links to the same parent
    // must NOT trip the cross-section-unique-value-key rule (they are keyless).
    const d = dr2();
    const g = structuredClone(sectionOfType(d, "guidance"));
    const fl = structuredClone(sectionOfType(d, "form_link"));
    (d.sections as Record<string, unknown>[]).push(g, fl);
    expect(validateDefinition(d, dr2Ctx())).toEqual({ ok: true });
  });
});

// ── endpoint harness (mirrors test/session-epoch.test.ts) ───────────────────────
const BASE = "https://portal.test";
const ADMIN_BEARER = "test-admin-token";
type Init = RequestInit & { cookie?: string };
function callApi(path: string, init: Init = {}): Promise<Response> {
  const headers = new Headers(init.headers);
  if (init.cookie) headers.set("Cookie", init.cookie);
  if (init.body && !headers.has("content-type")) headers.set("content-type", "application/json");
  return SELF.fetch(BASE + path, { ...init, headers });
}
async function provision(username: string, role: "submitter" | "admin"): Promise<void> {
  const r = await callApi("/api/internal/admin/users", {
    method: "POST",
    headers: { Authorization: `Bearer ${ADMIN_BEARER}` },
    body: JSON.stringify({ username, password: "password123", role }),
  });
  expect(r.status, await r.clone().text()).toBe(201);
}
async function login(username: string): Promise<string> {
  const r = await callApi("/api/login", { method: "POST", body: JSON.stringify({ username, password: "password123" }) });
  expect(r.status, await r.clone().text()).toBe(200);
  return (r.headers.get("set-cookie") ?? "").split(";")[0];
}
/** A valid edit-op payload: bump jha to v2 (same identity). */
function editToV2() {
  const def = jha();
  def.version = 2;
  def.form_code = "jha-v2";
  return { op: "edit", identity: "jha", parent_form_code: "jha", definition: def };
}

beforeEach(async () => {
  await env.DB.batch([
    env.DB.prepare("DELETE FROM users"),
    env.DB.prepare("DELETE FROM publish_requests"),
    env.DB.prepare("DELETE FROM audit_log"),
  ]);
});

describe("POST /api/admin/publish", () => {
  it("a submitter is rejected (403)", async () => {
    await provision("pm.bob", "submitter");
    const cookie = await login("pm.bob");
    const res = await callApi("/api/admin/publish", { method: "POST", cookie, body: JSON.stringify(editToV2()) });
    expect(res.status).toBe(403);
  });

  it("enqueues a valid edit (201 queued) and writes a publish_requests row", async () => {
    await provision("admin.one", "admin");
    const cookie = await login("admin.one");
    const res = await callApi("/api/admin/publish", { method: "POST", cookie, body: JSON.stringify(editToV2()) });
    expect(res.status, await res.clone().text()).toBe(201);
    expect(await res.json()).toMatchObject({ ok: true, status: "queued" });
    const row = await env.DB.prepare("SELECT op, identity, target_form_code, status FROM publish_requests").first();
    expect(row).toMatchObject({ op: "edit", identity: "jha", target_form_code: "jha-v2", status: "queued" });
  });

  it("rejects an invalid definition with 400 + a reason", async () => {
    await provision("admin.one", "admin");
    const cookie = await login("admin.one");
    const payload = editToV2();
    (payload.definition as Record<string, unknown>).archetype = "nope";
    const res = await callApi("/api/admin/publish", { method: "POST", cookie, body: JSON.stringify(payload) });
    expect(res.status).toBe(400);
    expect(await res.json()).toMatchObject({ error: "invalid_definition" });
    expect((await env.DB.prepare("SELECT COUNT(*) n FROM publish_requests").first<{ n: number }>())!.n).toBe(0);
  });

  it("serializes per parent — a 2nd in-flight publish is 409", async () => {
    await provision("admin.one", "admin");
    const cookie = await login("admin.one");
    expect((await callApi("/api/admin/publish", { method: "POST", cookie, body: JSON.stringify(editToV2()) })).status).toBe(201);
    const res2 = await callApi("/api/admin/publish", { method: "POST", cookie, body: JSON.stringify(editToV2()) });
    expect(res2.status).toBe(409);
    expect(await res2.json()).toMatchObject({ error: "publish_in_progress" });
  });

  it("rejects an unknown op (400)", async () => {
    await provision("admin.one", "admin");
    const cookie = await login("admin.one");
    const res = await callApi("/api/admin/publish", {
      method: "POST", cookie, body: JSON.stringify({ op: "nuke", identity: "jha", parent_form_code: "jha" }),
    });
    expect(res.status).toBe(400);
    expect(await res.json()).toMatchObject({ error: "invalid_op" });
  });
});

describe("GET /api/admin/publish-status", () => {
  it("returns the enqueued requests, newest first", async () => {
    await provision("admin.one", "admin");
    const cookie = await login("admin.one");
    await callApi("/api/admin/publish", { method: "POST", cookie, body: JSON.stringify(editToV2()) });
    const res = await callApi("/api/admin/publish-status", { cookie });
    expect(res.status).toBe(200);
    const { requests } = (await res.json()) as { requests: { identity: string; status: string }[] };
    expect(requests.length).toBe(1);
    expect(requests[0]).toMatchObject({ identity: "jha", status: "queued" });
  });

  it("a submitter is rejected (403)", async () => {
    await provision("pm.bob", "submitter");
    const cookie = await login("pm.bob");
    expect((await callApi("/api/admin/publish-status", { cookie })).status).toBe(403);
  });
});

describe("parent-grouping guard at enqueue (mirrors apply_publish)", () => {
  function createUnder(identity: string, parent: string, variant: string | null) {
    const def = jha();
    def.form_code = `${identity}-v1`;
    def.parent_form_code = parent;
    def.variant_label = variant;
    def.version = 1;
    return { op: "create", identity, parent_form_code: parent, definition: def };
  }

  it("rejects a create under an existing standalone parent (jha) with a clear reason", async () => {
    await provision("admin.one", "admin");
    const cookie = await login("admin.one");
    const res = await callApi("/api/admin/publish", {
      method: "POST", cookie, body: JSON.stringify(createUnder("jha-extra", "jha", "Extra")),
    });
    expect(res.status).toBe(400);
    const body = (await res.json()) as { error: string; reason?: string };
    expect(body.error).toBe("invalid_definition");
    expect(body.reason).toMatch(/standalone form/i);
    expect((await env.DB.prepare("SELECT COUNT(*) n FROM publish_requests").first<{ n: number }>())!.n).toBe(0);
  });

  it("allows a create under a brand-new form type (201)", async () => {
    await provision("admin.one", "admin");
    const cookie = await login("admin.one");
    // validateParentGrouping reads the BUNDLED catalog.json (worker/index.ts), so the
    // "brand-new type" must be a name that no real form will ever publish — else a future
    // publish of that parent makes this 201 become a 400 and self-defeats the gate (the same
    // live-catalog coupling that req-8's "incident-report" exposed in test_publish_manifest).
    const res = await callApi("/api/admin/publish", {
      method: "POST", cookie, body: JSON.stringify(createUnder("zztest-brand-new-type", "zztest-brand-new-type", null)),
    });
    expect(res.status, await res.clone().text()).toBe(201);
  });
});

describe("workflow category (form-builder workflow selector)", () => {
  function createNewType(category?: string) {
    const def = jha();
    def.form_code = "zzcat-brand-new-v1";
    def.parent_form_code = "zzcat-brand-new";
    def.variant_label = null;
    def.version = 1;
    const base: Record<string, unknown> = {
      op: "create", identity: "zzcat-brand-new", parent_form_code: "zzcat-brand-new", definition: def,
    };
    if (category !== undefined) base.category = category;
    return base;
  }

  it("recategorize with a valid workflow → 201 + queues op=recategorize with the category", async () => {
    await provision("admin.one", "admin");
    const cookie = await login("admin.one");
    const res = await callApi("/api/admin/publish", {
      method: "POST", cookie,
      body: JSON.stringify({ op: "recategorize", identity: "jha", parent_form_code: "jha", category: "progress" }),
    });
    expect(res.status, await res.clone().text()).toBe(201);
    const row = await env.DB.prepare("SELECT op, parent_form_code, category FROM publish_requests").first();
    expect(row).toMatchObject({ op: "recategorize", parent_form_code: "jha", category: "progress" });
  });

  it("recategorize with an unknown workflow → 400 invalid_category, STATIC reason (no input reflected)", async () => {
    await provision("admin.one", "admin");
    const cookie = await login("admin.one");
    const res = await callApi("/api/admin/publish", {
      method: "POST", cookie,
      body: JSON.stringify({ op: "recategorize", identity: "jha", parent_form_code: "jha", category: "bogus" }),
    });
    expect(res.status).toBe(400);
    const j = await res.json() as { error: string; reason?: string };
    expect(j).toMatchObject({ error: "invalid_category" });
    // W8: the failure reason is a STATIC string — caller input is never echoed back.
    expect(j.reason).toBe("unknown workflow category");
    expect(j.reason).not.toContain("bogus");
  });

  it("recategorize with an oversized category → 400, no multi-KB body reflected (W8)", async () => {
    await provision("admin.one", "admin");
    const cookie = await login("admin.one");
    const huge = "x".repeat(5000);
    const res = await callApi("/api/admin/publish", {
      method: "POST", cookie,
      body: JSON.stringify({ op: "recategorize", identity: "jha", parent_form_code: "jha", category: huge }),
    });
    expect(res.status).toBe(400);
    const text = await res.text();
    expect(JSON.parse(text)).toMatchObject({ error: "invalid_category", reason: "unknown workflow category" });
    expect(text).not.toContain(huge);        // the 5 KB input is NOT reflected
    expect(text.length).toBeLessThan(200);
  });

  it("recategorize without a category → 400 invalid_category", async () => {
    await provision("admin.one", "admin");
    const cookie = await login("admin.one");
    const res = await callApi("/api/admin/publish", {
      method: "POST", cookie,
      body: JSON.stringify({ op: "recategorize", identity: "jha", parent_form_code: "jha" }),
    });
    expect(res.status).toBe(400);
    expect(await res.json()).toMatchObject({ error: "invalid_category" });
  });

  it("create with a valid category persists it on the queued row", async () => {
    await provision("admin.one", "admin");
    const cookie = await login("admin.one");
    const res = await callApi("/api/admin/publish", {
      method: "POST", cookie, body: JSON.stringify(createNewType("progress")),
    });
    expect(res.status, await res.clone().text()).toBe(201);
    const row = await env.DB.prepare("SELECT op, category FROM publish_requests").first();
    expect(row).toMatchObject({ op: "create", category: "progress" });
  });

  it("create with an unknown category → 400 invalid_category", async () => {
    await provision("admin.one", "admin");
    const cookie = await login("admin.one");
    const res = await callApi("/api/admin/publish", {
      method: "POST", cookie, body: JSON.stringify(createNewType("bogus")),
    });
    expect(res.status).toBe(400);
    expect(await res.json()).toMatchObject({ error: "invalid_category" });
  });

  it("create WITHOUT a category still succeeds (defaults safety) — backward-compatible", async () => {
    await provision("admin.one", "admin");
    const cookie = await login("admin.one");
    const res = await callApi("/api/admin/publish", {
      method: "POST", cookie, body: JSON.stringify(createNewType()),
    });
    expect(res.status, await res.clone().text()).toBe(201);
  });
});

describe("POST /api/admin/publish-dismiss", () => {
  async function seedReq(status: string): Promise<void> {
    await env.DB
      .prepare("INSERT INTO publish_requests (requested_by, op, parent_form_code, identity, target_form_code, status) VALUES (?,?,?,?,?,?)")
      .bind("admin.one", "create", "jha", "x", "x-v1", status)
      .run();
  }

  it("clears terminal (archived/failed) rows but leaves in-flight ones", async () => {
    await provision("admin.one", "admin");
    const cookie = await login("admin.one");
    await seedReq("failed");
    await seedReq("archived");
    await seedReq("queued");
    const res = await callApi("/api/admin/publish-dismiss", { method: "POST", cookie });
    expect(res.status).toBe(200);
    expect(await res.json()).toMatchObject({ cleared: 2 });
    const { results } = await env.DB.prepare("SELECT status FROM publish_requests").all<{ status: string }>();
    expect(results.map((r) => r.status)).toEqual(["queued"]);
  });

  it("a submitter is rejected (403)", async () => {
    await provision("pm.bob", "submitter");
    const cookie = await login("pm.bob");
    expect((await callApi("/api/admin/publish-dismiss", { method: "POST", cookie })).status).toBe(403);
  });
});

describe("GET /api/admin/publish-request (re-open a failed publish)", () => {
  async function seedWithDef(definitionJson: string | null): Promise<number> {
    const r = await env.DB
      .prepare(
        "INSERT INTO publish_requests (requested_by, op, parent_form_code, identity, target_form_code, status, definition_json) VALUES (?,?,?,?,?,?,?)",
      )
      .bind("admin.one", "create", "incident", "incident", "incident-v1", "failed", definitionJson)
      .run();
    return r.meta.last_row_id as number;
  }

  it("returns the saved definition_json for one request", async () => {
    await provision("admin.one", "admin");
    const cookie = await login("admin.one");
    const id = await seedWithDef('{"form_code":"incident-v1"}');
    const res = await callApi(`/api/admin/publish-request?id=${id}`, { cookie });
    expect(res.status).toBe(200);
    const body = (await res.json()) as { request: { id: number; op: string; definition_json: string } };
    expect(body.request).toMatchObject({ id, op: "create", definition_json: '{"form_code":"incident-v1"}' });
  });

  it("404s an unknown id", async () => {
    await provision("admin.one", "admin");
    const cookie = await login("admin.one");
    expect((await callApi("/api/admin/publish-request?id=999999", { cookie })).status).toBe(404);
  });

  it("a submitter is rejected (403)", async () => {
    await provision("pm.bob", "submitter");
    const cookie = await login("pm.bob");
    expect((await callApi("/api/admin/publish-request?id=1", { cookie })).status).toBe(403);
  });
});
