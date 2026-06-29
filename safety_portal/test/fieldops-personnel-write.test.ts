import { env, SELF } from "cloudflare:test";
import { describe, it, expect, beforeEach } from "vitest";

// ─────────────────────────────────────────────────────────────────────────────
// Task #22 — PERSONNEL CRUD (create [roster-only | with-account] / update / link / unlink / retire).
// cap.personnel.manage (admin-only). The with-account branch ADDITIONALLY requires actor role=admin
// (defense-in-depth). Linking validates the target account exists (422 unknown_account). Retire is a
// soft-delete (active=0), idempotent. Runs against the REAL worker with Miniflare D1.
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
async function provision(username: string, password: string, role: "submitter" | "admin"): Promise<void> {
  const res = await call("/api/internal/admin/users", { method: "POST", bearer: ADMIN_BEARER, body: JSON.stringify({ username, password, role }) });
  expect(res.status, await res.clone().text()).toBe(201);
}
async function login(username: string, password: string): Promise<string> {
  const res = await call("/api/login", { method: "POST", body: JSON.stringify({ username, password }) });
  expect(res.status, await res.clone().text()).toBe(200);
  return cookieFrom(res);
}
const p = (cookie: string, path: string, body?: unknown) =>
  call(path, { method: "POST", cookie, body: body === undefined ? undefined : JSON.stringify(body) });
const createId = async (cookie: string, body: unknown): Promise<number> => {
  const res = await p(cookie, "/api/fieldops/personnel", body);
  expect(res.status, await res.clone().text()).toBe(201);
  return ((await res.json()) as { id: number }).id;
};
async function personRow(id: number) {
  return await env.DB.prepare("SELECT * FROM personnel WHERE id=?").bind(id).first<any>();
}
async function userRow(username: string) {
  return await env.DB.prepare("SELECT * FROM users WHERE username=?").bind(username).first<any>();
}
async function audits(action: string) {
  return ((await env.DB.prepare("SELECT * FROM audit_log WHERE action=?").bind(action).all()).results as any[]);
}
async function personCountByName(name: string): Promise<number> {
  return (await env.DB.prepare("SELECT COUNT(*) n FROM personnel WHERE name=?").bind(name).first<{ n: number }>())!.n;
}

let admin: string, submitter: string;
beforeEach(async () => {
  await env.DB.batch([
    // Remove the (submitter, cap.personnel.manage) grant the defense-in-depth test adds, so it
    // never leaks between tests. No-op when absent (the migration never seeds it).
    env.DB.prepare("DELETE FROM role_capabilities WHERE role_key='submitter' AND capability_key='cap.personnel.manage'"),
    env.DB.prepare("DELETE FROM time_entries"),
    env.DB.prepare("DELETE FROM personnel"),
    env.DB.prepare("DELETE FROM users"),
    env.DB.prepare("DELETE FROM audit_log"),
  ]);
  await provision("admin.one", "password123", "admin");
  await provision("submitter.jim", "password123", "submitter");
  admin = await login("admin.one", "password123");
  submitter = await login("submitter.jim", "password123");
});

describe("POST /api/fieldops/personnel (create)", () => {
  it("gate + roster-only: anon → 401, submitter (no manage cap) → 403, admin → 201 (id, username NULL, audit)", async () => {
    expect((await call("/api/fieldops/personnel", { method: "POST", body: JSON.stringify({ name: "X" }) })).status).toBe(401);
    expect((await p(submitter, "/api/fieldops/personnel", { name: "X" })).status).toBe(403);

    const id = await createId(admin, { name: "Jane Doe", trade: "electrician" });
    const row = await personRow(id);
    expect(row.name).toBe("Jane Doe");
    expect(row.trade).toBe("electrician");
    expect(row.username).toBeNull(); // roster-only
    expect(row.active).toBe(1);
    expect(await audits("personnel_create")).toHaveLength(1);
    expect(await audits("user_create")).toHaveLength(0); // no account minted
  });

  it("bounds → 400 (empty name, over-long name, over-long trade)", async () => {
    expect((await p(admin, "/api/fieldops/personnel", { name: "" })).status).toBe(400);
    expect((await p(admin, "/api/fieldops/personnel", { name: "x".repeat(129) })).status).toBe(400);
    expect((await p(admin, "/api/fieldops/personnel", { name: "OK", trade: "x".repeat(65) })).status).toBe(400);
  });

  it("with-account: admin → 201 creates BOTH a users row (role default submitter) + a linked personnel row", async () => {
    const id = await createId(admin, { name: "Acct Person", account: { username: "acct.person", password: "password123" } });
    const u = await userRow("acct.person");
    expect(u).toBeTruthy();
    expect(u.role).toBe("submitter"); // default when role omitted
    expect((await personRow(id)).username).toBe("acct.person");
    expect(await audits("user_create")).toHaveLength(1);
    expect(await audits("personnel_create")).toHaveLength(1);
  });

  it("with-account: explicit admin role is honored", async () => {
    await createId(admin, { name: "Boss Person", account: { username: "boss.person", password: "password123", role: "admin" } });
    expect((await userRow("boss.person")).role).toBe("admin");
  });

  it("with-account: duplicate username → 409 AND no orphan personnel row (atomic rollback)", async () => {
    await createId(admin, { name: "Dup A", account: { username: "dup.user", password: "password123" } });
    const res = await p(admin, "/api/fieldops/personnel", { name: "Dup B", account: { username: "dup.user", password: "password123" } });
    expect(res.status).toBe(409);
    expect(((await res.json()) as { error: string }).error).toBe("exists");
    expect(await personCountByName("Dup B")).toBe(0); // batch rolled back — no orphan roster row
  });

  it("with-account: invalid inputs → 400 (bad username / short password / bad role)", async () => {
    expect((await p(admin, "/api/fieldops/personnel", { name: "A", account: { username: "NoDot", password: "password123" } })).status).toBe(400);
    expect((await p(admin, "/api/fieldops/personnel", { name: "A", account: { username: "a.b", password: "short" } })).status).toBe(400);
    expect((await p(admin, "/api/fieldops/personnel", { name: "A", account: { username: "a.b", password: "password123", role: "superuser" } })).status).toBe(400);
  });

  it("defense-in-depth: a submitter WITH cap.personnel.manage may create a roster person but NOT an account", async () => {
    // Grant the manage cap to the submitter ROLE. resolveCapabilities reads role_capabilities fresh
    // per request, so the existing submitter cookie now carries cap.personnel.manage.
    await env.DB.prepare("INSERT INTO role_capabilities (role_key, capability_key) VALUES ('submitter','cap.personnel.manage')").run();

    // roster-only now allowed (cap present, no role check)
    expect((await p(submitter, "/api/fieldops/personnel", { name: "Roster By Sub" })).status).toBe(201);

    // account branch refused — role !== admin (mints a credential + assigns a role)
    const res = await p(submitter, "/api/fieldops/personnel", { name: "Acct By Sub", account: { username: "acct.bysub", password: "password123" } });
    expect(res.status).toBe(403);
    expect(await userRow("acct.bysub")).toBeFalsy(); // nothing created
    expect(await personCountByName("Acct By Sub")).toBe(0);
  });
});

describe("POST /api/fieldops/personnel/:id/update", () => {
  it("admin edits name/trade (200, audit); submitter → 403; unknown → 404", async () => {
    const id = await createId(admin, { name: "Old Name", trade: "laborer" });
    expect((await p(submitter, `/api/fieldops/personnel/${id}/update`, { name: "New" })).status).toBe(403);
    expect((await p(admin, `/api/fieldops/personnel/${id}/update`, { name: "New Name", trade: "foreman" })).status).toBe(200);
    const row = await personRow(id);
    expect(row.name).toBe("New Name");
    expect(row.trade).toBe("foreman");
    expect(await audits("personnel_update")).toHaveLength(1);
    expect((await p(admin, "/api/fieldops/personnel/999999/update", { name: "X" })).status).toBe(404);
  });
});

describe("POST /api/fieldops/personnel/:id/link + /unlink", () => {
  it("links to an EXISTING account (200, audit); 422 unknown_account; 400 malformed; 403 submitter; 404 unknown id", async () => {
    const id = await createId(admin, { name: "Linkable" });

    expect((await p(submitter, `/api/fieldops/personnel/${id}/link`, { username: "admin.one" })).status).toBe(403);

    // unknown (well-formed) account → 422
    const r422 = await p(admin, `/api/fieldops/personnel/${id}/link`, { username: "nobody.here" });
    expect(r422.status).toBe(422);
    expect(((await r422.json()) as { error: string }).error).toBe("unknown_account");

    // malformed username → 400 (before the existence check)
    expect((await p(admin, `/api/fieldops/personnel/${id}/link`, { username: "NoDot" })).status).toBe(400);

    // existing account → 200, link persisted
    expect((await p(admin, `/api/fieldops/personnel/${id}/link`, { username: "submitter.jim" })).status).toBe(200);
    expect((await personRow(id)).username).toBe("submitter.jim");
    expect(await audits("personnel_link")).toHaveLength(1);

    // unknown personnel id (account valid) → 404
    expect((await p(admin, "/api/fieldops/personnel/999999/link", { username: "admin.one" })).status).toBe(404);
  });

  it("unlinks (username → NULL, 200, audit); 403 submitter; 404 unknown id", async () => {
    const id = await createId(admin, { name: "Linked Acct", account: { username: "linked.acct", password: "password123" } });
    expect((await personRow(id)).username).toBe("linked.acct");

    expect((await p(submitter, `/api/fieldops/personnel/${id}/unlink`)).status).toBe(403);
    expect((await p(admin, `/api/fieldops/personnel/${id}/unlink`)).status).toBe(200);
    expect((await personRow(id)).username).toBeNull();
    expect(await audits("personnel_unlink")).toHaveLength(1);
    expect((await p(admin, "/api/fieldops/personnel/999999/unlink")).status).toBe(404);
  });
});

describe("POST /api/fieldops/personnel/:id/retire (soft-retire)", () => {
  it("soft-retires (active=0), preserves time history, is idempotent, 403 submitter, 404 unknown", async () => {
    const id = await createId(admin, { name: "Doomed" });
    // a time entry proves retire preserves history (FK target kept)
    await env.DB.prepare(
      "INSERT INTO time_entries (uuid, job_id, personnel_id, hours, created_at, actor_username) VALUES (?,?,?,?,?,?)",
    ).bind("te-1", "JOB-A", id, 8, Math.floor(Date.now() / 1000), "admin.one").run();

    expect((await p(submitter, `/api/fieldops/personnel/${id}/retire`)).status).toBe(403);

    const r1 = await p(admin, `/api/fieldops/personnel/${id}/retire`);
    expect(r1.status).toBe(200);
    expect((await personRow(id)).active).toBe(0);
    expect((await env.DB.prepare("SELECT COUNT(*) n FROM time_entries WHERE personnel_id=?").bind(id).first<{ n: number }>())!.n).toBe(1);
    expect(await audits("personnel_retire")).toHaveLength(1);

    // idempotent: second retire → 200 already_retired, no second audit
    const r2 = await p(admin, `/api/fieldops/personnel/${id}/retire`);
    expect(r2.status).toBe(200);
    expect(((await r2.json()) as { already_retired?: boolean }).already_retired).toBe(true);
    expect(await audits("personnel_retire")).toHaveLength(1);

    expect((await p(admin, "/api/fieldops/personnel/999999/retire")).status).toBe(404);
  });
});
