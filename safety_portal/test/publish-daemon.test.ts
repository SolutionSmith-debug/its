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

describe("lease TTL reclaim (PR-2)", () => {
  async function seedLeased(identity: string, leaseAgeS: number): Promise<number> {
    const r = await env.DB
      .prepare(
        "INSERT INTO publish_requests (requested_by, op, parent_form_code, identity, target_form_code, definition_json, status, lease_owner, lease_at) " +
          "VALUES (?,?,?,?,?,?,?,?, unixepoch() - ?)",
      )
      .bind("admin.one", "create", "jha", identity, `${identity}-v1`, "{}", "queued", "deadmac", leaseAgeS)
      .run();
    return r.meta.last_row_id as number;
  }
  it("pending returns a queued row whose lease is older than the TTL (the daemon died)", async () => {
    const fresh = await seedLeased("jha-fresh", 60); // leased 1 min ago — still held
    const stale = await seedLeased("jha-stale", 4000); // leased >30 min ago — reclaimable
    const { pending } = (await (await call("/api/internal/publish/pending", { bearer: TOKEN })).json()) as { pending: { id: number }[] };
    const ids = pending.map((p) => p.id);
    expect(ids).toContain(stale);
    expect(ids).not.toContain(fresh);
  });
  it("claim takes over a stale-leased row; refuses a freshly-leased one", async () => {
    const stale = await seedLeased("jha-takeover", 4000);
    const fresh = await seedLeased("jha-held", 60);
    const r1 = (await (await call("/api/internal/publish/claim", { method: "POST", bearer: TOKEN, body: JSON.stringify({ id: stale, lease_owner: "newmac" }) })).json()) as { claimed: boolean };
    expect(r1.claimed).toBe(true);
    const row = await env.DB.prepare("SELECT lease_owner FROM publish_requests WHERE id=?").bind(stale).first<{ lease_owner: string }>();
    expect(row!.lease_owner).toBe("newmac");
    const r2 = (await (await call("/api/internal/publish/claim", { method: "POST", bearer: TOKEN, body: JSON.stringify({ id: fresh, lease_owner: "intruder" }) })).json()) as { claimed: boolean };
    expect(r2.claimed).toBe(false);
  });
});

describe("stamp state-machine guard (PR-2 — forged/out-of-order transitions)", () => {
  it("rejects a backward transition (live -> validated): found:false + reason, row unchanged", async () => {
    const id = await seed("jha-back", "live");
    const body = (await (await call("/api/internal/publish/stamp", { method: "POST", bearer: TOKEN, body: JSON.stringify({ id, status: "validated" }) })).json()) as { found: boolean; reason?: string };
    expect(body.found).toBe(false);
    expect(body.reason).toMatch(/illegal transition/);
    const row = await env.DB.prepare("SELECT status FROM publish_requests WHERE id=?").bind(id).first<{ status: string }>();
    expect(row!.status).toBe("live");
  });
  it("rejects a skip-ahead transition (queued -> archived), row unchanged", async () => {
    const id = await seed("jha-skip", "queued");
    const body = (await (await call("/api/internal/publish/stamp", { method: "POST", bearer: TOKEN, body: JSON.stringify({ id, status: "archived" }) })).json()) as { found: boolean };
    expect(body.found).toBe(false);
    const row = await env.DB.prepare("SELECT status FROM publish_requests WHERE id=?").bind(id).first<{ status: string }>();
    expect(row!.status).toBe("queued");
  });
  it("rejects stamping a terminal row (archived -> failed)", async () => {
    const id = await seed("jha-term", "archived");
    const body = (await (await call("/api/internal/publish/stamp", { method: "POST", bearer: TOKEN, body: JSON.stringify({ id, status: "failed" }) })).json()) as { found: boolean };
    expect(body.found).toBe(false);
  });
  it("rejects stamping TO queued (never a stamp target) with 400", async () => {
    const id = await seed("jha-toq", "validated");
    expect((await call("/api/internal/publish/stamp", { method: "POST", bearer: TOKEN, body: JSON.stringify({ id, status: "queued" }) })).status).toBe(400);
  });
  it("allows the legal happy-path chain queued->validated->tested->live->archived", async () => {
    const id = await seed("jha-happy", "queued");
    for (const s of ["validated", "tested", "live", "archived"]) {
      const body = (await (await call("/api/internal/publish/stamp", { method: "POST", bearer: TOKEN, body: JSON.stringify({ id, status: s }) })).json()) as { found: boolean };
      expect(body.found, `stamp ${s}`).toBe(true);
    }
  });
});
