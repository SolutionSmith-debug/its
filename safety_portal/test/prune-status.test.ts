import { env } from "cloudflare:test";
import { describe, it, expect, beforeEach } from "vitest";
import { call, json } from "./helpers";
import { writePruneMeta, type PruneResult } from "../worker/prune";

// GS2 — GET /api/internal/prune-status: the prune-observability read the Mac watchdog
// (Check V) consumes. Bearer-gated on the INTERNAL token tier (the same
// PORTAL_INTERNAL_API_TOKEN that guards /api/internal/pending — see index.ts), returns
// the one-row prune_meta record or { prune: null } when the prune has never recorded a run.

const INTERNAL_BEARER = "test-internal-token"; // vitest.config.ts PORTAL_INTERNAL_API_TOKEN
const NOW = 1_780_000_000;

function result(overrides: Partial<PruneResult> = {}): PruneResult {
  return {
    submissions: 0,
    stripped: 0,
    rejected: 0,
    audit: 0,
    pdfRequests: 0,
    pdfChunks: 0,
    publishRequests: 0,
    itemPhotos: 0,
    dailyPhotos: 0,
    jobs: 0,
    dbSizeBytes: 1234,
    sizeWarn: false,
    failedStages: [],
    ...overrides,
  };
}

interface PruneStatusBody {
  prune: {
    last_run_at: number;
    db_size_bytes: number;
    size_warn: boolean;
    counters: Record<string, number>;
    failed_stages: string[];
  } | null;
}

beforeEach(async () => {
  await env.DB.prepare("DELETE FROM prune_meta").run();
});

describe("GET /api/internal/prune-status (GS2)", () => {
  it("401s without a bearer", async () => {
    const res = await call("/api/internal/prune-status");
    expect(res.status).toBe(401);
  });

  it("401s with a WRONG bearer (and with the admin/fieldops tokens — internal tier only)", async () => {
    for (const bearer of ["nope", "test-admin-token", "test-fieldops-token"]) {
      const res = await call("/api/internal/prune-status", { bearer });
      expect(res.status, `bearer=${bearer}`).toBe(401);
    }
  });

  it("returns { prune: null } when the prune has never recorded a run", async () => {
    const res = await call("/api/internal/prune-status", { bearer: INTERNAL_BEARER });
    expect(res.status).toBe(200);
    expect((await json<PruneStatusBody>(res)).prune).toBeNull();
  });

  it("returns the meta row after a prune run recorded it", async () => {
    await writePruneMeta(
      env.DB,
      NOW,
      result({ failedStages: ["audit"], sizeWarn: true, dbSizeBytes: 7_000_000_000, jobs: 2 }),
    );

    const res = await call("/api/internal/prune-status", { bearer: INTERNAL_BEARER });
    expect(res.status).toBe(200);
    const body = await json<PruneStatusBody>(res);
    expect(body.prune).not.toBeNull();
    expect(body.prune!.last_run_at).toBe(NOW);
    expect(body.prune!.db_size_bytes).toBe(7_000_000_000);
    expect(body.prune!.size_warn).toBe(true);
    expect(body.prune!.counters.jobs).toBe(2);
    expect(body.prune!.failed_stages).toEqual(["audit"]);
  });

  it("surfaces an unparseable failed_stages_json as ['<unparseable>'] — never a clean read", async () => {
    await env.DB
      .prepare(
        "INSERT INTO prune_meta (id, last_run_at, db_size_bytes, size_warn, counters_json, failed_stages_json) " +
          "VALUES (1, ?, 0, 0, '{}', 'not-json')",
      )
      .bind(NOW)
      .run();

    const res = await call("/api/internal/prune-status", { bearer: INTERNAL_BEARER });
    expect(res.status).toBe(200);
    const body = await json<PruneStatusBody>(res);
    expect(body.prune!.failed_stages).toEqual(["<unparseable>"]); // fail-LOUD downstream (Check V CRITICALs)
  });
});
