/// <reference types="vite/client" />
import { env, SELF } from "cloudflare:test";
import { describe, it, expect, beforeEach } from "vitest";
import { validateDefinition } from "../worker/publishValidation";
import catalogJson from "../catalog.json";

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
// per-form validateDefinition loop below, which every EDITOR-REACHABLE shipped form must
// pass.
const formModules = import.meta.glob("../forms/*.json", { eager: true, import: "default" });
const FORMS: Record<string, Record<string, unknown>> = {};
for (const [path, def] of Object.entries(formModules)) {
  if (path.endsWith("meta-schema.json")) continue;
  FORMS[(def as { form_code: string }).form_code] = def as Record<string, unknown>;
}

// The editor-reachable set: each ACTIVE identity's current_form_code — the builder's Edit /
// Add-version open ONLY these (FormsPage startEdit/startAddVersion take viewDef.form_code
// from the formCatalog() active set). Historical/retired files stay shipped (append-only,
// renderable for filed submissions) but are NOT validated here: required-content floors may
// legitimately postdate them (Slice 1, R3-F3 — daily-report v1-v4 predate the
// job_requirements/expected_materials mounts the floor now requires), and they re-enter
// service only via rollback, which is deliberately floor-exempt.
const CURRENT_CODES = new Set<string>();
for (const p of (catalogJson as { parents: { forms: { status: string; current_form_code: string }[] }[] }).parents) {
  for (const f of p.forms) if (f.status === "active") CURRENT_CODES.add(f.current_form_code);
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

describe("validateDefinition — every editor-reachable (current) form passes", () => {
  it("loaded at least the shipped forms (count is dynamic — the pipeline adds them)", () => {
    expect(Object.keys(FORMS).length).toBeGreaterThan(0);
    expect(CURRENT_CODES.size).toBeGreaterThan(0);
  });
  it("every current_form_code has a shipped definition file", () => {
    for (const code of CURRENT_CODES) expect(FORMS[code], code).toBeDefined();
  });
  for (const [code, def] of Object.entries(FORMS)) {
    if (!CURRENT_CODES.has(code)) continue; // historical/retired — see CURRENT_CODES note
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
  // Base: the CURRENT daily-report (v5) — it carries the D1 guidance + form_link sections
  // (inherited verbatim from v2) AND the mounts the Slice-1 floor now requires, so a clean
  // clone validates ok and each rejection case isolates ONE mutation. (The block originally
  // based on daily-report-v2; v2 is now floor-rejected as a pre-mount historical version —
  // see the amputation-guard block below.)
  const dr2 = () => structuredClone(FORMS["daily-report-v5"]);
  const dr2Ctx = () => ctxFor(FORMS["daily-report-v5"]);

  it("the current daily-report (guidance + form_link + fields interleaved) validates ok", () => {
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

// ── job_requirements section (SOP daily form, slice D4) ───────────────────────────
describe("validateDefinition — job_requirements (per-job requirements D4)", () => {
  // Base: the CURRENT daily-report (v5) — carries the D4 placeholder section (inherited
  // verbatim from v4) and passes the Slice-1 section-type floor, so each rejection case
  // isolates ONE mutation. (Originally based on daily-report-v4; v4 is now floor-rejected —
  // it predates the expected_materials mount — see the amputation-guard block below.)
  const dr4 = () => structuredClone(FORMS["daily-report-v5"]);
  const dr4Ctx = () => ctxFor(FORMS["daily-report-v5"]);

  it("the current daily-report (with the job_requirements placeholder) validates ok", () => {
    expect(validateDefinition(dr4(), dr4Ctx())).toEqual({ ok: true });
  });
  it("rejects a job_requirements section with a missing / malformed key", () => {
    const d = dr4();
    const jr = sectionOfType(d, "job_requirements");
    delete jr.key;
    expect(validateDefinition(d, dr4Ctx()).ok).toBe(false);
    const d2 = dr4();
    sectionOfType(d2, "job_requirements").key = "Not Snake";
    expect(validateDefinition(d2, dr4Ctx()).ok).toBe(false);
  });
  it("the key IS a top-level value key: colliding with another section's key is rejected", () => {
    const d = dr4();
    sectionOfType(d, "job_requirements").key = "comments"; // the freeform's key
    const r = validateDefinition(d, dr4Ctx());
    expect(r.ok).toBe(false);
    expect((r as { reason: string }).reason).toMatch(/duplicate value key/);
  });
  it("a reserved envelope key (work_date) is rejected as the section key", () => {
    const d = dr4();
    sectionOfType(d, "job_requirements").key = "work_date";
    expect(validateDefinition(d, dr4Ctx()).ok).toBe(false);
  });
  it("AT MOST ONE job_requirements section — a second mount (even under a different key) is rejected", () => {
    const d = dr4();
    (d.sections as Record<string, unknown>[]).push({
      type: "job_requirements", key: "job_requirements_two", title: "Second mount",
    });
    const r = validateDefinition(d, dr4Ctx());
    expect(r.ok).toBe(false);
    expect((r as { reason: string }).reason).toMatch(/multiple job_requirements/);
  });
  it("rejects a non-string title", () => {
    const d = dr4();
    sectionOfType(d, "job_requirements").title = 7;
    const r = validateDefinition(d, dr4Ctx());
    expect(r.ok).toBe(false);
    expect((r as { reason: string }).reason).toMatch(/job_requirements invalid title/);
  });
});

// ── expected_materials section (Material receipts M2) ─────────────────────────────
describe("validateDefinition — expected_materials (Material receipts M2)", () => {
  // Base: the shipped daily-report-v5 (carries the receipt mount in the D.13 region).
  const dr5 = () => structuredClone(FORMS["daily-report-v5"]);
  const dr5Ctx = () => ctxFor(FORMS["daily-report-v5"]);

  it("daily-report-v5 (with the expected_materials mount) validates ok", () => {
    expect(validateDefinition(dr5(), dr5Ctx())).toEqual({ ok: true });
  });
  it("material-incident-v1 validates ok AND its required-content floor bites (details dropped → reject)", () => {
    const ctx = ctxFor(FORMS["material-incident-v1"]);
    expect(validateDefinition(structuredClone(FORMS["material-incident-v1"]), ctx)).toEqual({ ok: true });
    // The floor (required-content.json parents['material-incident']): material_description +
    // issue + details. Dropping the details freeform is a required-content rejection.
    const d = structuredClone(FORMS["material-incident-v1"]);
    d.sections = (d.sections as Record<string, unknown>[]).filter((s) => s.key !== "details");
    const r = validateDefinition(d, ctx);
    expect(r.ok).toBe(false);
    expect((r as { reason: string }).reason).toMatch(/required content missing: core field 'details'/);
  });
  it("rejects an expected_materials section with a missing / malformed key", () => {
    const d = dr5();
    const em = sectionOfType(d, "expected_materials");
    delete em.key;
    expect(validateDefinition(d, dr5Ctx()).ok).toBe(false);
    const d2 = dr5();
    sectionOfType(d2, "expected_materials").key = "Not Snake";
    expect(validateDefinition(d2, dr5Ctx()).ok).toBe(false);
  });
  it("the key is RESERVED in the value namespace: colliding with another section's key is rejected", () => {
    const d = dr5();
    sectionOfType(d, "expected_materials").key = "deliveries_received"; // the table's key
    const r = validateDefinition(d, dr5Ctx());
    expect(r.ok).toBe(false);
    expect((r as { reason: string }).reason).toMatch(/duplicate value key/);
  });
  it("a reserved envelope key (job) is rejected as the section key", () => {
    const d = dr5();
    sectionOfType(d, "expected_materials").key = "job";
    expect(validateDefinition(d, dr5Ctx()).ok).toBe(false);
  });
  it("AT MOST ONE expected_materials section — a second mount (even under a different key) is rejected", () => {
    const d = dr5();
    (d.sections as Record<string, unknown>[]).push({
      type: "expected_materials", key: "expected_materials_two", title: "Second mount",
    });
    const r = validateDefinition(d, dr5Ctx());
    expect(r.ok).toBe(false);
    expect((r as { reason: string }).reason).toMatch(/multiple expected_materials/);
  });
  it("rejects a non-string title", () => {
    const d = dr5();
    sectionOfType(d, "expected_materials").title = 7;
    const r = validateDefinition(d, dr5Ctx());
    expect(r.ok).toBe(false);
    expect((r as { reason: string }).reason).toMatch(/expected_materials invalid title/);
  });
});

// ── required_section_types floor — the mount amputation guard (Slice 1, R3-F3) ─────
// required-content.json parents['daily-report'] now floors the D4/M2 mounts, so a
// form-builder edit can never silently amputate the per-job requirements / expected-
// materials sections. This is the Worker C3 half; the Mac half is
// tests/test_form_definitions.py::test_amputated_daily_report_rejected_by_the_mac_c3_layer.
describe("validateDefinition — daily-report mount amputation is floor-rejected (Slice 1)", () => {
  const cur = () => structuredClone(FORMS["daily-report-v5"]);
  const curCtx = () => ctxFor(FORMS["daily-report-v5"]);

  for (const mount of ["job_requirements", "expected_materials"] as const) {
    it(`rejects a daily-report edit that drops the ${mount} mount`, () => {
      const d = cur();
      d.sections = (d.sections as Record<string, unknown>[]).filter((s) => s.type !== mount);
      const r = validateDefinition(d, curCtx());
      expect(r.ok).toBe(false);
      expect((r as { reason: string }).reason).toMatch(
        new RegExp(`must contain a '${mount}' section`),
      );
    });
  }

  it("historical pre-mount versions (v2 / v4) are floor-rejected — not editor-reachable", () => {
    // v2 predates BOTH mounts; v4 predates expected_materials. Neither is openable in the
    // builder (Edit/Add-version take only current_form_code), so rejecting them costs
    // nothing and proves the floor bites on any pre-mount shape.
    const v2 = validateDefinition(
      structuredClone(FORMS["daily-report-v2"]), ctxFor(FORMS["daily-report-v2"]),
    );
    expect(v2.ok).toBe(false);
    expect((v2 as { reason: string }).reason).toMatch(/must contain a 'job_requirements' section/);
    const v4 = validateDefinition(
      structuredClone(FORMS["daily-report-v4"]), ctxFor(FORMS["daily-report-v4"]),
    );
    expect(v4.ok).toBe(false);
    expect((v4 as { reason: string }).reason).toMatch(/must contain a 'expected_materials' section/);
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
