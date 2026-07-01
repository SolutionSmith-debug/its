import { env, SELF } from "cloudflare:test";
import { describe, it, expect, beforeEach } from "vitest";

// ─────────────────────────────────────────────────────────────────────────────
// Assigned-Tasks tab (P4 field-ops feature) S2 — the checklist ENGINE + per-job template editor.
//   - cap.checklist.manage gates every route (admin holds it; submitter + manager do NOT).
//   - Default-template CRUD (the global daily_default seeded by migration 0026).
//   - Per-job overrides + THE MERGE: a job's effective daily checklist =
//        [ default items NOT suppressed by the job ] ∪ [ the job's own added items ], seq-ordered.
// Runs against the REAL worker with Miniflare D1 (migrations incl. 0026 auto-apply).
// ─────────────────────────────────────────────────────────────────────────────

const BASE = "https://portal.test";
const ADMIN_BEARER = "test-admin-token";
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
async function provision(username: string, password: string, role: "submitter" | "manager" | "admin"): Promise<void> {
  const res = await call("/api/internal/admin/users", { method: "POST", bearer: ADMIN_BEARER, body: JSON.stringify({ username, password, role }) });
  expect(res.status, await res.clone().text()).toBe(201);
}
async function login(username: string, password: string): Promise<string> {
  const res = await call("/api/login", { method: "POST", body: JSON.stringify({ username, password }) });
  expect(res.status, await res.clone().text()).toBe(200);
  return cookieFrom(res);
}
const get = (cookie: string, path: string) => call(path, { cookie });
const post = (cookie: string, path: string, body?: unknown) =>
  call(path, { method: "POST", cookie, body: body === undefined ? undefined : JSON.stringify(body) });

async function seedJob(jobId: string): Promise<void> {
  await env.DB.prepare("INSERT INTO jobs (job_id, project_name, active, status, created_at) VALUES (?,?,1,'active',?)")
    .bind(jobId, `Project ${jobId}`, 1_700_000_000).run();
}
async function defaultTemplateId(): Promise<number> {
  return (await env.DB.prepare("SELECT id FROM checklist_templates WHERE kind='daily_default'").first<{ id: number }>())!.id;
}
async function defaultItemLabels(): Promise<string[]> {
  const r = await env.DB.prepare(
    "SELECT label FROM checklist_items WHERE template_id=(SELECT id FROM checklist_templates WHERE kind='daily_default') AND suppresses_default_item_id IS NULL ORDER BY seq",
  ).all<{ label: string }>();
  return (r.results ?? []).map((x) => x.label);
}

interface EffectiveItem { source_item_id: number; label: string; item_type: string; origin: string; seq: number; }
async function effective(cookie: string, jobId: string): Promise<{ items: EffectiveItem[]; suppressed: { source_item_id: number; label: string }[] }> {
  const res = await get(cookie, `/api/fieldops/checklist/job/${jobId}`);
  expect(res.status, await res.clone().text()).toBe(200);
  return (await res.json()) as { items: EffectiveItem[]; suppressed: { source_item_id: number; label: string }[] };
}

let admin: string, manager: string, submitter: string;

beforeEach(async () => {
  await env.DB.batch([
    env.DB.prepare("DELETE FROM checklist_item_states"),
    env.DB.prepare("DELETE FROM checklist_instances"),
    // wipe every override template's items, then the override templates; KEEP the seeded daily_default.
    env.DB.prepare("DELETE FROM checklist_items WHERE template_id IN (SELECT id FROM checklist_templates WHERE kind='job_override')"),
    env.DB.prepare("DELETE FROM checklist_templates WHERE kind='job_override'"),
    env.DB.prepare("DELETE FROM users"),
    env.DB.prepare("DELETE FROM jobs"),
    env.DB.prepare("DELETE FROM audit_log"),
  ]);
  await provision("admin.one", "password123", "admin");
  await provision("manager.mo", "password123", "manager");
  await provision("sub.sam", "password123", "submitter");
  admin = await login("admin.one", "password123");
  manager = await login("manager.mo", "password123");
  submitter = await login("sub.sam", "password123");
  await seedJob("JOB-A");
});

describe("checklist S2 — capability gating", () => {
  it("admin (cap.checklist.manage) can read the default; submitter + manager are 403", async () => {
    expect((await get(admin, "/api/fieldops/checklist/default")).status).toBe(200);
    expect((await get(submitter, "/api/fieldops/checklist/default")).status).toBe(403);
    expect((await get(manager, "/api/fieldops/checklist/default")).status).toBe(403);
    // A write is gated too.
    expect((await post(submitter, "/api/fieldops/checklist/default/item", { item_type: "manual_attest", label: "x" })).status).toBe(403);
    expect((await post(manager, "/api/fieldops/checklist/job/JOB-A/item", { item_type: "manual_attest", label: "x" })).status).toBe(403);
  });
});

describe("checklist S2 — default template CRUD + seed", () => {
  it("GET /default returns the migration-0026 seed (form_linked + manual_attest items)", async () => {
    const res = await get(admin, "/api/fieldops/checklist/default");
    const body = (await res.json()) as { template: { source_form_code: string } | null; items: { item_type: string; label: string }[] };
    expect(body.template?.source_form_code).toBe("daily-report");
    expect(body.items.length).toBeGreaterThanOrEqual(2);
    expect(body.items.some((i) => i.item_type === "form_linked")).toBe(true);
    expect(body.items.some((i) => i.item_type === "manual_attest")).toBe(true);
  });

  it("add / edit / delete a default item", async () => {
    const add = await post(admin, "/api/fieldops/checklist/default/item", { item_type: "manual_attest", label: "Walk the site", seq: 5 });
    expect(add.status).toBe(201);
    const id = ((await add.json()) as { id: number }).id;
    expect(await defaultItemLabels()).toContain("Walk the site");

    const edit = await post(admin, `/api/fieldops/checklist/default/item/${id}/edit`, { item_type: "count", label: "Count deliveries", target_count: 3, seq: 5 });
    expect(edit.status, await edit.clone().text()).toBe(200);
    const row = await env.DB.prepare("SELECT item_type, target_count FROM checklist_items WHERE id=?").bind(id).first<{ item_type: string; target_count: number }>();
    expect(row).toMatchObject({ item_type: "count", target_count: 3 });

    const del = await post(admin, `/api/fieldops/checklist/default/item/${id}/delete`);
    expect(del.status).toBe(200);
    expect(await defaultItemLabels()).not.toContain("Count deliveries");
    // (W4) the delete IS audited — the conditional audit gates on the item DELETE (last mutation),
    // not the orphan-marker cleanup. Deleting an item with no suppressions still writes one audit row.
    expect((await env.DB.prepare("SELECT COUNT(*) n FROM audit_log WHERE action='checklist_default_item_delete'").first<{ n: number }>())!.n).toBe(1);
    // Deleting a nonexistent default item → 404 (and writes NO audit row).
    expect((await post(admin, `/api/fieldops/checklist/default/item/${id}/delete`)).status).toBe(404);
    expect((await env.DB.prepare("SELECT COUNT(*) n FROM audit_log WHERE action='checklist_default_item_delete'").first<{ n: number }>())!.n).toBe(1);
  });

  it("validation: bad item_type, missing form_code, missing target_count", async () => {
    expect((await post(admin, "/api/fieldops/checklist/default/item", { item_type: "nope", label: "x" })).status).toBe(400);
    expect((await post(admin, "/api/fieldops/checklist/default/item", { item_type: "form_linked", label: "x" })).status).toBe(400);
    expect((await post(admin, "/api/fieldops/checklist/default/item", { item_type: "count", label: "x" })).status).toBe(400);
    expect((await post(admin, "/api/fieldops/checklist/default/item", { item_type: "manual_attest", label: "" })).status).toBe(400);
  });
});

describe("checklist S2 — per-job effective checklist (THE MERGE)", () => {
  it("a job with NO override → exactly the default items, all origin='default'", async () => {
    const { items, suppressed } = await effective(admin, "JOB-A");
    const defLabels = await defaultItemLabels();
    expect(items.map((i) => i.label)).toEqual(defLabels);
    expect(items.every((i) => i.origin === "default")).toBe(true);
    expect(suppressed).toEqual([]);
  });

  it("adding a job item → it appears with origin='override'; deleting it removes it", async () => {
    const add = await post(admin, "/api/fieldops/checklist/job/JOB-A/item", { item_type: "manual_attest", label: "Job-specific step", seq: 1000 });
    expect(add.status, await add.clone().text()).toBe(201);
    const overrideId = ((await add.json()) as { id: number }).id;

    let eff = await effective(admin, "JOB-A");
    const added = eff.items.find((i) => i.label === "Job-specific step");
    expect(added?.origin).toBe("override");
    // Default items still present alongside the addition.
    expect(eff.items.some((i) => i.origin === "default")).toBe(true);

    const del = await post(admin, `/api/fieldops/checklist/job/JOB-A/item/${overrideId}/delete`);
    expect(del.status).toBe(200);
    eff = await effective(admin, "JOB-A");
    expect(eff.items.some((i) => i.label === "Job-specific step")).toBe(false);
  });

  it("suppressing a default item hides it for the job (and lists it under `suppressed`); unsuppress restores it", async () => {
    const before = await effective(admin, "JOB-A");
    const victim = before.items[0];

    const sup = await post(admin, `/api/fieldops/checklist/job/JOB-A/item/${victim.source_item_id}/suppress`);
    expect(sup.status, await sup.clone().text()).toBe(201);

    let eff = await effective(admin, "JOB-A");
    expect(eff.items.some((i) => i.source_item_id === victim.source_item_id && i.origin === "default")).toBe(false);
    expect(eff.suppressed.some((s) => s.source_item_id === victim.source_item_id)).toBe(true);
    // Suppress is idempotent (no duplicate marker).
    expect((await post(admin, `/api/fieldops/checklist/job/JOB-A/item/${victim.source_item_id}/suppress`)).status).toBe(200);

    const uns = await post(admin, `/api/fieldops/checklist/job/JOB-A/item/${victim.source_item_id}/unsuppress`);
    expect(uns.status).toBe(200);
    eff = await effective(admin, "JOB-A");
    expect(eff.items.some((i) => i.source_item_id === victim.source_item_id)).toBe(true);
    expect(eff.suppressed).toEqual([]);
  });

  it("full merge: default-minus-suppressed ∪ addition, ordered by seq", async () => {
    // Clean-slate the default to a known 3-item set (seq 10/20/30) so the assertion is exact.
    await env.DB.prepare("DELETE FROM checklist_items WHERE template_id=(SELECT id FROM checklist_templates WHERE kind='daily_default')").run();
    const dt = await defaultTemplateId();
    for (const [seq, label] of [[10, "D-ten"], [20, "D-twenty"], [30, "D-thirty"]] as [number, string][]) {
      await env.DB.prepare("INSERT INTO checklist_items (template_id, seq, item_type, label) VALUES (?,?,?,?)").bind(dt, seq, "manual_attest", label).run();
    }
    const twentyId = (await env.DB.prepare("SELECT id FROM checklist_items WHERE template_id=? AND seq=20").bind(dt).first<{ id: number }>())!.id;

    // Suppress D-twenty and add a job item at seq 25 (between D-ten and D-thirty).
    expect((await post(admin, `/api/fieldops/checklist/job/JOB-A/item/${twentyId}/suppress`)).status).toBe(201);
    expect((await post(admin, "/api/fieldops/checklist/job/JOB-A/item", { item_type: "manual_attest", label: "J-twentyfive", seq: 25 })).status).toBe(201);

    const eff = await effective(admin, "JOB-A");
    expect(eff.items.map((i) => i.label)).toEqual(["D-ten", "J-twentyfive", "D-thirty"]);
    expect(eff.items.map((i) => i.origin)).toEqual(["default", "override", "default"]);

    // Another job is UNAFFECTED (edit-the-default propagates, per-job overrides don't).
    await seedJob("JOB-B");
    const effB = await effective(admin, "JOB-B");
    expect(effB.items.map((i) => i.label)).toEqual(["D-ten", "D-twenty", "D-thirty"]);
  });

  it("editing the default propagates to an un-overridden job", async () => {
    await post(admin, "/api/fieldops/checklist/default/item", { item_type: "manual_attest", label: "New shared step", seq: 99 });
    const eff = await effective(admin, "JOB-A");
    expect(eff.items.some((i) => i.label === "New shared step" && i.origin === "default")).toBe(true);
  });

  it("unknown job → 404; suppress of a non-default item → 404", async () => {
    expect((await get(admin, "/api/fieldops/checklist/job/NOPE-999")).status).toBe(404);
    expect((await post(admin, "/api/fieldops/checklist/job/NOPE-999/item", { item_type: "manual_attest", label: "x" })).status).toBe(404);
    expect((await post(admin, "/api/fieldops/checklist/job/JOB-A/item/999999/suppress")).status).toBe(404);
  });
});
