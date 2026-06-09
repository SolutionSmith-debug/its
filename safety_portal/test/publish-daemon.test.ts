import { env, SELF } from "cloudflare:test";
import { describe, it, expect, beforeEach } from "vitest";

// Slice 3b — the Mac publish daemon's bearer-gated queue interface (pull/claim/stamp).
// Real workerd + D1 (migration 0010's publish_requests applied by apply-migrations.ts).

const BASE = "https://portal.test";
const TOKEN = "test-internal-token"; // == PORTAL_INTERNAL_API_TOKEN in vitest.config.ts

function call(path: string, init: RequestInit & { bearer?: string } = {}): Promise<Response> {
  const headers = new Headers(init.headers);
  if (init.bearer) headers.set("Authorization", `Bearer ${init.bearer}`);
  if (init.body && !headers.has("content-type")) headers.set("content-type", "application/json");
  return SELF.fetch(BASE + path, { ...init, headers });
}

async function seed(identity: string, status = "queued", def: string | null = "{}"): Promise<number> {
  const r = await env.DB
    .prepare(
      "INSERT INTO publish_requests (requested_by, op, parent_form_code, identity, target_form_code, definition_json, status) VALUES (?,?,?,?,?,?,?)",
    )
    .bind("admin.one", "create", "jha", identity, `${identity}-v1`, def, status)
    .run();
  return r.meta.last_row_id as number;
}

beforeEach(async () => {
  await env.DB.prepare("DELETE FROM publish_requests").run();
});

describe("publish daemon interface — bearer auth", () => {
  it("401s on missing/wrong token across all three endpoints", async () => {
    expect((await call("/api/internal/publish/pending")).status).toBe(401);
    expect((await call("/api/internal/publish/claim", { method: "POST", body: "{}" })).status).toBe(401);
    expect((await call("/api/internal/publish/stamp", { method: "POST", bearer: "wrong", body: "{}" })).status).toBe(401);
  });
});

describe("GET /api/internal/publish/pending", () => {
  it("returns queued + unleased rows oldest-first, with definition_json", async () => {
    const id1 = await seed("jha-a");
    const id2 = await seed("jha-b");
    const res = await call("/api/internal/publish/pending", { bearer: TOKEN });
    expect(res.status).toBe(200);
    const { pending } = (await res.json()) as { pending: { id: number; definition_json: string }[] };
    expect(pending.map((p) => p.id)).toEqual([id1, id2]);
    expect(pending[0].definition_json).toBe("{}");
  });
  it("omits non-queued and leased rows", async () => {
    await seed("jha-c", "live");
    const leased = await seed("jha-d");
    await call("/api/internal/publish/claim", { method: "POST", bearer: TOKEN, body: JSON.stringify({ id: leased, lease_owner: "d1" }) });
    const { pending } = (await (await call("/api/internal/publish/pending", { bearer: TOKEN })).json()) as { pending: unknown[] };
    expect(pending.length).toBe(0);
  });
});

describe("POST /api/internal/publish/claim", () => {
  it("atomically leases a queued row + returns it; a 2nd claim fails (no double-actuation)", async () => {
    const id = await seed("jha", "queued", '{"form_code":"jha-v2"}');
    const r1 = (await (await call("/api/internal/publish/claim", { method: "POST", bearer: TOKEN, body: JSON.stringify({ id, lease_owner: "mac1" }) })).json()) as {
      claimed: boolean; request: { id: number; definition_json: string };
    };
    expect(r1.claimed).toBe(true);
    expect(r1.request).toMatchObject({ id, definition_json: '{"form_code":"jha-v2"}' });
    const r2 = (await (await call("/api/internal/publish/claim", { method: "POST", bearer: TOKEN, body: JSON.stringify({ id, lease_owner: "mac2" }) })).json()) as { claimed: boolean };
    expect(r2.claimed).toBe(false);
  });
  it("400s on a missing id or lease_owner", async () => {
    expect((await call("/api/internal/publish/claim", { method: "POST", bearer: TOKEN, body: JSON.stringify({ id: 1 }) })).status).toBe(400);
  });
});

describe("POST /api/internal/publish/stamp", () => {
  it("advances the status", async () => {
    const id = await seed("jha-e");
    const res = await call("/api/internal/publish/stamp", { method: "POST", bearer: TOKEN, body: JSON.stringify({ id, status: "validated" }) });
    expect(((await res.json()) as { found: boolean }).found).toBe(true);
    const row = await env.DB.prepare("SELECT status FROM publish_requests WHERE id=?").bind(id).first<{ status: string }>();
    expect(row!.status).toBe("validated");
  });
  it("records failed_stage + failure_reason on a failed stamp", async () => {
    const id = await seed("jha-f");
    await call("/api/internal/publish/stamp", {
      method: "POST", bearer: TOKEN,
      body: JSON.stringify({ id, status: "failed", failed_stage: "deploy", failure_reason: "wrangler boom" }),
    });
    const row = await env.DB
      .prepare("SELECT status, failed_stage, failure_reason FROM publish_requests WHERE id=?")
      .bind(id).first<{ status: string; failed_stage: string; failure_reason: string }>();
    expect(row).toMatchObject({ status: "failed", failed_stage: "deploy", failure_reason: "wrangler boom" });
  });
  it("rejects an invalid status (400) and reports found=false for an unknown id", async () => {
    const id = await seed("jha-g");
    expect((await call("/api/internal/publish/stamp", { method: "POST", bearer: TOKEN, body: JSON.stringify({ id, status: "bogus" }) })).status).toBe(400);
    const res = await call("/api/internal/publish/stamp", { method: "POST", bearer: TOKEN, body: JSON.stringify({ id: 99999, status: "live" }) });
    expect(((await res.json()) as { found: boolean }).found).toBe(false);
  });
});
