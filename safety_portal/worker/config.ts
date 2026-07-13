import type { MiddlewareHandler } from "hono";
import type { FieldopsApp } from "./fieldops_gates";
import { auditStmt, auditStmtIfChanged } from "./audit";
import type { Env, Vars } from "./types";
// Build-time bundle of the terms manifest (SAME import worker/po.ts uses) — for the create_profile
// duplicate-id shape check. Bundled at build time, so it re-bundles on every actuator deploy.
import termsManifest from "../../po_materials/terms/manifest.json";
// Build-time bundle of the exhibit manifest (SAME import worker/subcontract.ts uses) — for the exhibit
// create_profile duplicate-key / duplicate-trade shape checks. Re-bundles on every actuator deploy.
import exhibitManifest from "../../subcontracts/exhibit/manifest.json";

// ─────────────────────────────────────────────────────────────────────────────
// Config-editor queue (§50 privileged code-actuation) — worker/config.ts
//
// The Worker half of the GENERIC versioned-config editor: a browser route (session +
// per-workstream capability) that VALIDATES + ENQUEUES a config edit send-free in D1, and
// four internal routes under the NEW requireConfigToken bearer tier
// (PORTAL_CONFIG_API_TOKEN / Keychain ITS_PORTAL_CONFIG_TOKEN) that the Mac-side config
// daemon (built LATER — NOT in this change) will consume to pull → validate → git-commit →
// auto-deploy the edit. This module clones the form-editor publish_requests pipeline
// (index.ts /api/admin/publish + /api/internal/publish/*) one-for-one, generalized so the
// serialization key is (workstream, artifact_key) instead of parent_form_code.
//
// Invariants:
//   - Invariant 1 (External Send Gate): SEND-FREE — this module performs zero git/deploy/
//     transmit. It validates + ENQUEUES in D1; the Mac config daemon is the sole privileged
//     actuator (§50 mirrors the publish daemon: the cloud can only queue).
//   - Invariant 2 (Adversarial Input): config edits arrive from authenticated office admins
//     but are still client-supplied data — every body is shape-guarded + bounded, the
//     capability is re-checked server-side per-workstream (never trusts the SPA), all SQL is
//     ?-bound, and the insert batches atomically with its audit row (W4).
//
// MULTI-SURFACE FAN-OUT: CONFIG_STATUSES / CONFIG_OPS / LEGAL_PREDECESSORS below are kept in
// LOCKSTEP with the migration 0045 CHECK constraints — a new op/status must land in BOTH.
// ─────────────────────────────────────────────────────────────────────────────

export type ConfigGates = {
  requireSession: MiddlewareHandler<{ Bindings: Env; Variables: Vars }>;
  requireCapability: (cap: string) => MiddlewareHandler<{ Bindings: Env; Variables: Vars }>;
  /** Bearer gate for /api/internal/config/* — the Mac-side config daemon's OWN token tier
   *  (PORTAL_CONFIG_API_TOKEN), privilege-separated from the portal_poll / admin / fieldops /
   *  PO tokens. Built in index.ts next to its siblings (same fail-closed constant-time shape). */
  requireConfigToken: MiddlewareHandler<{ Bindings: Env; Variables: Vars }>;
};

// ── The generic workstream → artifact registry ───────────────────────────────────
// A future workflow adds its artifacts here with ZERO route changes. `subcontracts` is LIVE (SC-S2 +
// PR-B2): `contractor`/`payment_terms` (json), `terms` (the versioned body library), and `exhibit` (the
// versioned per-trade Article II templates) — this same enqueue/claim/stamp machinery serves them all.
interface ArtifactSpec {
  kind: "json" | "terms" | "exhibit";
}
interface WorkstreamSpec {
  cap: string;
  placeholder?: boolean;
  artifacts: Record<string, ArtifactSpec>;
}
const CONFIG_REGISTRY: Record<string, WorkstreamSpec> = {
  po_materials: {
    cap: "cap.po.manage",
    artifacts: {
      purchaser: { kind: "json" },
      tax: { kind: "json" },
      terms: { kind: "terms" },
    },
  },
  subcontracts: {
    cap: "cap.subcontracts.manage",
    artifacts: {
      contractor: { kind: "json" }, // the Evergreen prime identity (SC-S2)
      payment_terms: { kind: "json" }, // the §2.5 retention defaults
      terms: { kind: "terms" }, // the 27-article subcontract body library (subcontracts/terms manifest)
      exhibit: { kind: "exhibit" }, // the versioned per-trade Exhibit A Article II templates (PR-B2)
    },
  },
};

const CONFIG_OPS = new Set(["edit", "add_version", "set_current", "create_profile"]);
// The ops a versioned `terms` artifact accepts (a json artifact takes only `edit`). Module-level so
// it is allocated once, not per request.
const TERMS_OPS = new Set(["add_version", "set_current", "create_profile"]);
// The ops a versioned `exhibit` artifact accepts. Beyond add_version/set_current on an existing template,
// `create_profile` mints a BRAND-NEW template KEY together with a new TRADE that maps to it (the "New trade
// + article template" op) — reuses the create_profile op value already in migration 0048's CHECK, so NO new
// migration. The exhibit create payload is {template_key, trade, text}, NOT the terms {profile_id, kind, ...}.
const EXHIBIT_OPS = new Set(["add_version", "set_current", "create_profile"]);
const TARGET_VERSION_RE = /^[a-z0-9_]+$/;
const MAX_TARGET_VERSION = 64;
const MAX_PAYLOAD_BYTES = 100_000; // 100 KB — generous ceiling on a config value / terms version

// create_profile shape bounds (the Worker is the SHAPE gate; config_apply is the authoritative
// live-HEAD gate — the duplicate-id / manifest-write checks re-run there, C3).
const PROFILE_ID_RE = /^[a-z0-9_]+$/;
const MAX_PROFILE_ID = 64;
const MAX_LABEL = 200;
const MAX_DESCRIPTION = 1000;
const MAX_RENDER_LINE = 2000;
const PROFILE_KINDS = new Set(["library", "attach"]);
// The profile ids already present in the BUILD-time-bundled manifest (worker/po.ts imports the same
// JSON). A create_profile for an existing id is really an add_version — reject it early here (belt);
// config_apply.py re-checks against LIVE HEAD (which may have moved since this bundle) as the boundary.
const BUNDLED_PROFILE_IDS = new Set(Object.keys(termsManifest.profiles as Record<string, unknown>));
// The exhibit template KEYS + TRADE names already in the build-time-bundled exhibit manifest — for the
// exhibit create_profile shape check. A duplicate key is really an add_version; a duplicate trade a re-map.
// config_apply.py re-checks against LIVE HEAD (which may have moved since this bundle) as the boundary.
const BUNDLED_EXHIBIT_KEYS = new Set(Object.keys(exhibitManifest.trade_templates as Record<string, unknown>));
const BUNDLED_TRADES = new Set(Object.keys(exhibitManifest.trade_map as Record<string, unknown>));


// ── Internal (daemon) surface constants — LOCKSTEP with migration 0045's CHECK sets ──────────
const CONFIG_STATUSES = new Set(["queued", "validated", "tested", "merged", "live", "archived", "failed"]);
// A config edit still in flight, for per-(workstream,artifact) serialization (C8). archived |
// failed are terminal; 'live' still blocks (the archive stage is pending). A crashed daemon no
// longer wedges an artifact forever: LEASE_TTL_S makes a stale lease re-claimable and the Mac
// daemon's stale-row sweep stamps a stalled non-terminal row failed('stale_reclaimed').
const CONFIG_NON_TERMINAL_STATUSES = "('queued','validated','tested','merged','live')";
// Lease TTL: a claimed-but-stalled row (daemon died after claim, before any stamp) becomes
// re-claimable once its lease is older than this. Must exceed the daemon's CI+deploy slack. 30 min.
const LEASE_TTL_S = 30 * 60;
// Legal predecessors per stamp target: the stamp endpoint only advances a row whose CURRENT
// status is a legal predecessor of the requested status. Blocks a forged / out-of-order stamp on
// the config token (an archived→queued revert, a queued→archived skip) and a re-stamp of a
// terminal row. 'queued' is absent (the initial state is never a stamp target). IDENTICAL to the
// publish pipeline's map.
const LEGAL_PREDECESSORS: Record<string, string[]> = {
  validated: ["queued"],
  tested: ["validated"],
  merged: ["tested"],
  live: ["tested", "merged"],
  archived: ["live"],
  failed: ["queued", "validated", "tested", "merged", "live"],
};

const STATUS_LIST_CAP = 50;

// The resting states a config request may be SOFT-DISMISSED (cleared) from the status monitor. NOT
// the C8 non-terminal set: 'live' is clearable (the deploy succeeded — the operator's "done" view)
// even though C8 still treats it as in-flight until 'archived'. Clearing is a DISPLAY-ONLY dismissal
// (migration 0047 cleared_at) — it NEVER frees the C8 in-flight lock, advances the state machine, or
// touches the internal pending/claim/stamp/stuck routes (they filter on `status`, not cleared_at). An
// in-flight request (queued|validated|tested|merged) is REFUSED (409) — you cannot clear a request out
// from under the actuator. Kept in lockstep as a JS Set (the in-handler guard) + an SQL IN-list (the
// atomic in-WHERE re-guard).
const CONFIG_CLEARABLE_STATUSES = new Set(["live", "archived", "failed"]);
const CONFIG_CLEARABLE_STATUSES_SQL = "('live','archived','failed')";

function isPlainObject(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

/** Shape-validate a create_profile payload. Returns an error CODE (400, except `profile_exists`→409)
 *  or null when the shape is acceptable. The AUTHORITATIVE checks (duplicate id vs LIVE HEAD, the
 *  manifest write) re-run in config_apply.py — this is only the send-free enqueue's shape gate.
 *
 *  library: { profile_id, kind:'library', label, description?, version_id, text }
 *  attach:  { profile_id, kind:'attach',  label, description?, render_line }
 *  The new profile's initial version rides IN the payload (not the target_version column); it lands
 *  legal_review:'pending' at actuation, so a library profile cannot render on a PO until a subsequent
 *  set_current clears it (the existing Layer-A fence enforces this — create_profile never bypasses it). */
function validateCreateProfile(payload: unknown): string | null {
  if (!isPlainObject(payload)) return "invalid_payload";
  const profileId = typeof payload.profile_id === "string" ? payload.profile_id : "";
  if (!PROFILE_ID_RE.test(profileId) || profileId.length > MAX_PROFILE_ID) return "invalid_profile_id";
  if (BUNDLED_PROFILE_IDS.has(profileId)) return "profile_exists"; // a duplicate id is add_version
  const kind = typeof payload.kind === "string" ? payload.kind : "";
  if (!PROFILE_KINDS.has(kind)) return "invalid_profile_kind";
  const label = typeof payload.label === "string" ? payload.label.trim() : "";
  if (!label || label.length > MAX_LABEL) return "invalid_label";
  if (payload.description !== undefined) {
    if (typeof payload.description !== "string" || payload.description.length > MAX_DESCRIPTION) {
      return "invalid_payload";
    }
  }
  if (kind === "library") {
    const versionId = typeof payload.version_id === "string" ? payload.version_id : "";
    if (!TARGET_VERSION_RE.test(versionId) || versionId.length > MAX_TARGET_VERSION) {
      return "invalid_target_version";
    }
    const text = typeof payload.text === "string" ? payload.text.trim() : "";
    if (!text) return "invalid_payload"; // overall UTF-8 byte cap is enforced by MAX_PAYLOAD_BYTES
  } else {
    // attach: a render_line pointer to an externally-negotiated GTC (no versioned text / legal gate).
    const renderLine = typeof payload.render_line === "string" ? payload.render_line.trim() : "";
    if (!renderLine || renderLine.length > MAX_RENDER_LINE) return "invalid_payload";
  }
  return null;
}

/** Shape-gate an `exhibit` config edit. add_version needs {template_key, text}; set_current needs
 *  {template_key}; create_profile (the "New trade + template" op) needs {template_key (a FRESH key), trade
 *  (a FRESH trade name), text}. The Mac actuator (config_apply._apply_exhibit_*) is the live-HEAD boundary
 *  (unknown key, duplicate version/key/trade, embedded {{tokens}}); this is only the send-free shape gate. */
function validateExhibit(op: string, payload: unknown): string | null {
  if (!isPlainObject(payload)) return "invalid_payload";
  const templateKey = typeof payload.template_key === "string" ? payload.template_key : "";
  if (!templateKey || templateKey.length > MAX_PROFILE_ID) return "invalid_template_key";
  if (op === "add_version") {
    const text = typeof payload.text === "string" ? payload.text : "";
    if (!text.trim()) return "invalid_payload";
  }
  if (op === "create_profile") {
    // A brand-new template KEY (must match the id charset AND be genuinely new) + a new TRADE that maps to
    // it + the Article II v1 text. config_apply.py re-checks duplicate key/trade vs LIVE HEAD (the boundary).
    if (!PROFILE_ID_RE.test(templateKey)) return "invalid_template_key";
    if (BUNDLED_EXHIBIT_KEYS.has(templateKey)) return "template_exists"; // a duplicate key is add_version
    const trade = typeof payload.trade === "string" ? payload.trade.trim() : "";
    if (!trade || trade.length > MAX_LABEL) return "invalid_trade";
    if (BUNDLED_TRADES.has(trade)) return "trade_exists"; // a duplicate trade is a re-map, not a create
    const text = typeof payload.text === "string" ? payload.text.trim() : "";
    if (!text) return "invalid_payload";
  }
  return null;
}

/** True if `v` is a non-empty JSON value: a non-null/undefined value that, when it is an
 *  object/array/string, carries at least one key/element/char. Numbers/booleans are accepted
 *  as-is (a valid config scalar). Blocks an empty {} / [] / "" that carries no real edit. */
function isNonEmptyJson(v: unknown): boolean {
  if (v === undefined || v === null) return false;
  if (typeof v === "string") return v.length > 0;
  if (Array.isArray(v)) return v.length > 0;
  if (typeof v === "object") return Object.keys(v as Record<string, unknown>).length > 0;
  return true; // number / boolean
}

// ── Route registration ──────────────────────────────────────────────────────────
export function registerConfigRoutes(app: FieldopsApp, gates: ConfigGates): void {
  // ══ Browser surface (session + per-workstream capability) ══════════════════════════

  // POST /api/config/requests — validate + ENQUEUE a config edit (send-free). The capability
  // is re-checked IN-HANDLER against the resolved workstream's registry cap (the workstream is
  // only known after body parse), so the per-workstream authorization can never be bypassed by
  // the SPA (Invariant 2). C8 in-flight guard serializes per (workstream, artifact_key).
  app.post("/api/config/requests", gates.requireSession, async (c) => {
    let body: Record<string, unknown>;
    try {
      body = (await c.req.json()) as Record<string, unknown>;
    } catch {
      return c.json({ error: "bad_request" }, 400);
    }
    if (!isPlainObject(body)) return c.json({ error: "bad_request" }, 400);

    const workstream = typeof body.workstream === "string" ? body.workstream : "";
    const spec = CONFIG_REGISTRY[workstream];
    if (!spec) return c.json({ error: "invalid_workstream" }, 400);

    // Capability check FIRST — immediately after the workstream resolves, BEFORE artifact/op/
    // payload validation (Invariant 2, authorization-before-work). requireSession already put the
    // fresh D1 capability SET on the request; the SPA hiding a tab is a hint, THIS is the boundary.
    // Ordering it first denies an unauthorized session the ability to probe the registry shape via
    // differing 400 codes, and avoids parsing/stringifying an attacker payload before the 403.
    if (!c.get("capabilities").has(spec.cap)) return c.json({ error: "forbidden" }, 403);

    const artifactKey = typeof body.artifact_key === "string" ? body.artifact_key : "";
    // A placeholder workstream has no artifacts, so the artifact lookup fails closed for it too.
    const artifact = spec.placeholder ? undefined : spec.artifacts[artifactKey];
    if (!artifact) return c.json({ error: "invalid_artifact" }, 400);

    const op = typeof body.op === "string" ? body.op : "";
    if (!CONFIG_OPS.has(op)) return c.json({ error: "invalid_op" }, 400);
    // The op must match the artifact KIND: a versioned terms artifact takes `add_version` (mint a new
    // sha-pinned version), `set_current` (make a version live + clear its legal review), or
    // `create_profile` (mint a brand-new profile); a json artifact only takes `edit` (replace the
    // value). A mismatch is a structurally-nonsensical request the queue rejects here, not later.
    const opAllowed =
      artifact.kind === "terms"
        ? TERMS_OPS.has(op)
        : artifact.kind === "exhibit"
          ? EXHIBIT_OPS.has(op)
          : op === "edit";
    if (!opAllowed) {
      return c.json({ error: "invalid_op" }, 400);
    }

    let targetVersion: string | null = null;
    if (op === "add_version" || op === "set_current") {
      const tv = typeof body.target_version === "string" ? body.target_version : "";
      if (!TARGET_VERSION_RE.test(tv) || tv.length > MAX_TARGET_VERSION) {
        return c.json({ error: "invalid_target_version" }, 400);
      }
      targetVersion = tv;
    }

    // create_profile carries its new-profile fields (id, kind, label, and per-kind version+text /
    // render_line) INSIDE `payload` — target_version stays NULL. Validate the shape here (the SHAPE
    // gate); config_apply.py re-checks against live HEAD (duplicate id, manifest write) as the boundary.
    // Audit metadata beyond {workstream, artifact_key, op}: create_profile surfaces the new profile
    // id + kind so an auditor reading audit_log alone (without joining the request row) sees WHAT was
    // created — parity with add_version/set_current's target_version.
    let opMeta: Record<string, unknown> = {};
    if (artifact.kind === "exhibit") {
      // exhibit owns ALL its ops (add_version / set_current / create_profile) — validated by validateExhibit,
      // NOT the terms validateCreateProfile (the exhibit create payload is {template_key, trade, text}, never
      // {profile_id, kind}). Mutually exclusive with the terms create_profile branch below.
      const err = validateExhibit(op, body.payload);
      if (err) {
        const status = err === "template_exists" || err === "trade_exists" ? 409 : 400;
        return c.json({ error: err }, status);
      }
      const p = body.payload as Record<string, unknown>;
      // Normalize the trade IN the enqueued payload so the queued row + audit row record EXACTLY what
      // config_apply writes to trade_map (it does its own trade.strip() before the manifest write) — no
      // forensic drift between " Battery Storage " enqueued and "Battery Storage" actuated.
      if (op === "create_profile" && typeof p.trade === "string") p.trade = p.trade.trim();
      opMeta = op === "create_profile" ? { template_key: p.template_key, trade: p.trade } : { template_key: p.template_key };
    } else if (op === "create_profile") {
      const err = validateCreateProfile(body.payload);
      if (err) return c.json({ error: err }, err === "profile_exists" ? 409 : 400);
      const p = body.payload as Record<string, unknown>;
      opMeta = { profile_id: p.profile_id, kind: p.kind };
    }

    if (!isNonEmptyJson(body.payload)) return c.json({ error: "invalid_payload" }, 400);
    const payloadJson = JSON.stringify(body.payload);
    // Bound by UTF-8 byte length (what D1 stores), not JS string .length (UTF-16 code units).
    if (new TextEncoder().encode(payloadJson).length > MAX_PAYLOAD_BYTES) {
      return c.json({ error: "payload_too_large" }, 400);
    }

    // Per-(workstream, artifact) serialization (C8): reject a 2nd edit while one is in flight.
    const inflight = await c.env.DB
      .prepare(
        `SELECT id FROM config_requests WHERE workstream=? AND artifact_key=? AND status IN ${CONFIG_NON_TERMINAL_STATUSES} LIMIT 1`,
      )
      .bind(workstream, artifactKey)
      .first();
    if (inflight) return c.json({ error: "config_edit_in_progress" }, 409);

    const actor = c.get("session").username;
    const res = await c.env.DB.batch([
      c.env.DB
        .prepare(
          "INSERT INTO config_requests (requested_by, workstream, artifact_key, op, target_version, payload) VALUES (?,?,?,?,?,?)",
        )
        .bind(actor, workstream, artifactKey, op, targetVersion, payloadJson),
      auditStmt(c, actor, "config_edit", `${workstream}/${artifactKey}`, {
        workstream,
        artifact_key: artifactKey,
        op,
        ...(targetVersion !== null ? { target_version: targetVersion } : {}),
        ...opMeta,
      }),
    ]);
    return c.json({ ok: true, id: res[0]?.meta?.last_row_id ?? null, status: "queued" }, 201);
  });

  // GET /api/config/requests/status — the SPA status monitor's read view (most-recent first).
  // Cross-workstream, so it gates on requireSession + the config-cap FLOOR (the session must
  // hold at least ONE non-placeholder workstream cap). Send-free read.
  app.get("/api/config/requests/status", gates.requireSession, async (c) => {
    const caps = c.get("capabilities");
    // Least-privilege row scoping: return ONLY the rows of workstreams whose cap the caller holds.
    // The registry is designed to grow with "zero route changes", so a user with only cap.po.manage
    // must never see a future workstream's rows (workstream/artifact_key/op/failure_reason — the last
    // can carry daemon-internal detail) through this same code. Scope now; nobody edits this later.
    const held = Object.entries(CONFIG_REGISTRY)
      .filter(([, s]) => !s.placeholder && caps.has(s.cap))
      .map(([ws]) => ws);
    if (held.length === 0) return c.json({ error: "forbidden" }, 403);
    const placeholders = held.map(() => "?").join(",");
    // Cleared (soft-dismissed, migration 0047) rows are hidden by default; ?include_cleared=1 shows
    // them (they stay fully SELECT-able — the row is the forensic record, never deleted).
    const clearedFilter = c.req.query("include_cleared") === "1" ? "" : " AND cleared_at IS NULL";
    const { results } = await c.env.DB
      .prepare(
        "SELECT id, workstream, artifact_key, op, status, failed_stage, failure_reason, created_at, updated_at, cleared_at " +
          `FROM config_requests WHERE workstream IN (${placeholders})${clearedFilter} ORDER BY id DESC LIMIT ?`,
      )
      .bind(...held, STATUS_LIST_CAP)
      .all();
    return c.json({ requests: results });
  });

  // POST /api/config/requests/:id/clear — forensic-SAFE soft-dismiss of a TERMINAL config request from
  // the status monitor. A PORTAL-SIDE dismissal (session + the ROW's own workstream capability), NOT an
  // actuation: it takes NO config token and NEVER hard-deletes the row (the config_requests row is the
  // §50 forensic record). It only stamps cleared_at so the default monitor hides it; the row stays
  // SELECT-able and reappears with ?include_cleared=1. Terminal-only — an in-flight request
  // (queued|validated|tested|merged) is refused (409 config_not_terminal); you cannot clear a request
  // out from under the actuator. Idempotent: a re-clear of an already-cleared row is a no-op ok.
  app.post("/api/config/requests/:id/clear", gates.requireSession, async (c) => {
    const id = Number(c.req.param("id"));
    if (!Number.isInteger(id) || id <= 0) return c.json({ error: "bad_request" }, 400);
    // Look the row up FIRST — the clear is authorized against the ROW's workstream cap (least-privilege,
    // mirroring the monitor's per-workstream row scoping: you may only clear a workstream you manage).
    const row = await c.env.DB
      .prepare("SELECT workstream, artifact_key, op, status, cleared_at FROM config_requests WHERE id=?")
      .bind(id)
      .first<{ workstream: string; artifact_key: string; op: string; status: string; cleared_at: number | null }>();
    if (!row) return c.json({ error: "not_found" }, 404);
    const spec = CONFIG_REGISTRY[row.workstream];
    // A placeholder workstream (subcontracts) holds no rows today; exclude it explicitly for parity
    // with the monitor's read-side scoping (`!s.placeholder` above) — belt for a future backfill/bug.
    if (!spec || spec.placeholder || !c.get("capabilities").has(spec.cap)) return c.json({ error: "forbidden" }, 403);
    // Already cleared → idempotent no-op (never re-audit, never re-stamp the timestamp).
    if (row.cleared_at !== null) return c.json({ ok: true, cleared: false });
    // Terminal-only: refuse to dismiss an in-flight request.
    if (!CONFIG_CLEARABLE_STATUSES.has(row.status)) return c.json({ error: "config_not_terminal" }, 409);
    const actor = c.get("session").username;
    // W4: the soft-dismiss + its audit row batch atomically. The UPDATE re-guards cleared_at IS NULL AND
    // status IN (terminal) in the WHERE, so a concurrent clear / an actuator advance cannot double-apply;
    // the audit uses auditStmtIfChanged (INSERT … WHERE changes()=1, placed directly after the UPDATE) so
    // a lost race writes NO lying "config_clear" row — the forensic-safe property this feature exists for.
    const res = await c.env.DB.batch([
      c.env.DB
        .prepare(
          `UPDATE config_requests SET cleared_at=unixepoch() WHERE id=? AND cleared_at IS NULL AND status IN ${CONFIG_CLEARABLE_STATUSES_SQL}`,
        )
        .bind(id),
      auditStmtIfChanged(c, actor, "config_clear", `${row.workstream}/${row.artifact_key}`, {
        id,
        op: row.op,
        prev_status: row.status,
      }),
    ]);
    return c.json({ ok: true, cleared: (res[0]?.meta?.changes ?? 0) === 1 });
  });

  // ══ Internal surface (requireConfigToken — the Mac-side config daemon) ═════════════

  // GET /api/internal/config/pending — claimable rows (queued + unleased OR stale-leased),
  // oldest-first. Only status='queued' rows appear (mid-flight + terminal are structurally
  // excluded); a queued row whose lease is fresh stays hidden until its lease goes stale.
  app.get("/api/internal/config/pending", gates.requireConfigToken, async (c) => {
    const limit = Math.min(Number(c.req.query("limit")) || 20, 100);
    const { results } = await c.env.DB
      .prepare(
        "SELECT id, created_at, requested_by, workstream, artifact_key, op, target_version, payload " +
          "FROM config_requests WHERE status='queued' AND (lease_owner IS NULL OR lease_at < unixepoch() - ?) " +
          "ORDER BY id ASC LIMIT ?",
      )
      .bind(LEASE_TTL_S, limit)
      .all();
    return c.json({ pending: results });
  });

  // POST /api/internal/config/claim — ATOMICALLY lease a queued row for one daemon run.
  // { id, lease_owner } leases ONLY if still queued AND (unleased OR its lease is stale past
  // LEASE_TTL_S — takeover of a dead daemon's lease). The single-statement WHERE is the mutual
  // exclusion: only the run whose UPDATE reports changes===1 won the lease; a concurrent claimer
  // sees changes===0 and backs off. Returns the full row (incl. payload) when claimed.
  app.post("/api/internal/config/claim", gates.requireConfigToken, async (c) => {
    let body: Record<string, unknown>;
    try {
      body = await c.req.json();
    } catch {
      return c.json({ error: "bad_request" }, 400);
    }
    if (!isPlainObject(body)) return c.json({ error: "bad_request" }, 400);
    const id = typeof body.id === "number" && Number.isInteger(body.id) ? body.id : 0;
    const lease_owner = typeof body.lease_owner === "string" ? body.lease_owner.slice(0, 128) : "";
    if (!id || !lease_owner) return c.json({ error: "invalid" }, 400);
    const res = await c.env.DB
      .prepare(
        "UPDATE config_requests SET lease_owner=?, lease_at=unixepoch() WHERE id=? AND status='queued' AND (lease_owner IS NULL OR lease_at < unixepoch() - ?)",
      )
      .bind(lease_owner, id, LEASE_TTL_S)
      .run();
    if ((res.meta?.changes ?? 0) === 0) return c.json({ ok: true, claimed: false });
    const request = await c.env.DB
      .prepare(
        "SELECT id, workstream, artifact_key, op, target_version, payload, status FROM config_requests WHERE id=?",
      )
      .bind(id)
      .first();
    return c.json({ ok: true, claimed: true, request });
  });

  // POST /api/internal/config/stamp — advance the state machine. { id, status, failed_stage?,
  // failure_reason? }. failed_stage/reason are kept ONLY for a failed stamp. Guarded by the
  // legal-predecessor allowlist (blocks a forged / out-of-order stamp on the config token).
  app.post("/api/internal/config/stamp", gates.requireConfigToken, async (c) => {
    let body: Record<string, unknown>;
    try {
      body = await c.req.json();
    } catch {
      return c.json({ error: "bad_request" }, 400);
    }
    if (!isPlainObject(body)) return c.json({ error: "bad_request" }, 400);
    const id = typeof body.id === "number" && Number.isInteger(body.id) ? body.id : 0;
    const status = typeof body.status === "string" ? body.status : "";
    if (!id || !CONFIG_STATUSES.has(status)) return c.json({ error: "invalid" }, 400);
    const failed = status === "failed";
    const failed_stage = failed && typeof body.failed_stage === "string" ? body.failed_stage.slice(0, 64) : null;
    const failure_reason = failed && typeof body.failure_reason === "string" ? body.failure_reason.slice(0, 2000) : null;
    const preds = LEGAL_PREDECESSORS[status];
    if (!preds) return c.json({ error: "invalid" }, 400); // 'queued' is never a stamp target
    const placeholders = preds.map(() => "?").join(",");
    const res = await c.env.DB
      .prepare(
        "UPDATE config_requests SET status=?, failed_stage=?, failure_reason=?, updated_at=unixepoch() " +
          `WHERE id=? AND status IN (${placeholders})`,
      )
      .bind(status, failed_stage, failure_reason, id, ...preds)
      .run();
    if ((res.meta?.changes ?? 0) === 0) {
      // changes==0 is overloaded: the row is gone, OR its current status isn't a legal
      // predecessor of `status` (a forged / out-of-order stamp). Re-read for an honest reason;
      // the row was NOT advanced either way.
      const row = await c.env.DB.prepare("SELECT status FROM config_requests WHERE id=?").bind(id).first<{ status: string }>();
      if (!row) return c.json({ ok: true, found: false });
      return c.json({ ok: true, found: false, reason: `illegal transition ${row.status} -> ${status}` });
    }
    return c.json({ ok: true, found: true });
  });

  // GET /api/internal/config/stuck?older_than=<sec> — non-terminal rows whose updated_at is
  // older than the cutoff (a config edit that crashed mid-actuation, or a stalled stage). The
  // Mac daemon's stale-row sweep reclaims these by stamping failed('stale_reclaimed') so they
  // stop wedging the artifact's C8 in-flight check. Bearer-gated.
  app.get("/api/internal/config/stuck", gates.requireConfigToken, async (c) => {
    const olderThan = Math.min(Math.max(Number(c.req.query("older_than")) || 0, 0), 86400);
    const { results } = await c.env.DB
      .prepare(
        "SELECT id, status, lease_owner, lease_at, updated_at, workstream, artifact_key, op " +
          `FROM config_requests WHERE status IN ${CONFIG_NON_TERMINAL_STATUSES} AND updated_at < unixepoch() - ? ` +
          "ORDER BY id ASC LIMIT 50",
      )
      .bind(olderThan)
      .all();
    return c.json({ stuck: results });
  });
}
