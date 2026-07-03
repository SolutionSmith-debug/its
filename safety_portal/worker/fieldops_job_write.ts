import type { Context } from "hono";
import type { FieldopsApp, FieldopsGates } from "./fieldops_gates";
import type { Env, Vars } from "./types";
import { auditStmt, auditStmtIfChanged, isUniqueViolation } from "./audit";

// P2.3 Slice 2 + P2.5 Slice 1 — JOB WRITE (create / lifecycle / contacts).
// cap.jobtracker.manage (admin-only). Send-free (D1 only).
//
// jobs is a PLAIN (in-place mutable) table — NOT integrity-bar — so updates UPDATE in place, but
// every mutation still writes an audit_log row in the SAME D1 batch (W4).
//
// P2.5 — PORTAL IS THE AUTHORITATIVE WRITER. The create form owns the full job source-of-truth
// (address, stakeholder, Safety + Progress routing contacts + CC arrays, lifecycle). The Mac-side
// mirror daemon (field_ops/fieldops_sync.py, Slice 5) reads dirty portal rows over
// GET /api/internal/fieldops/pending-jobs and find-or-creates a row in BOTH ITS-owned Active-Jobs
// sheets keyed by job_id (each sheet's "Portal Job Key" column).
//
// P2.5 Slice 6 — THE PORTAL ASSIGNS THE CANONICAL NUMBER. The office employee no longer types a
// Job ID; the create route allocates the next JOB-###### from the `job_counter` table (migration
// 0022) and uses it as BOTH job_id (D1 PK) and canonical_job_id from birth. That single identity
// shows everywhere — portal, both Active-Jobs sheets (Job ID column, retyped off AUTO_NUMBER),
// every report, Box. There is no Smartsheet-generates-then-read-back handshake: see
// allocateJobNumber() below and shared/active_jobs_writer.py (writes Job ID = job_id on upsert).
//
// 0017 ORIGIN FENCE: a portal-CREATED job is stamped origin='portal' FOREVER, so the 60s
// `/api/internal/sync` full-replace (scoped to origin='smartsheet') can never deactivate it.
//
// VERSION VECTOR (migration 0021): every SoR/lifecycle mutation bumps mirror_version + sets
// sync_state='pending' (the dirty flag). The daemon advances each sheet's watermark independently
// and flips sync_state→'synced' only when BOTH catch up (see /api/internal/fieldops/jobs-mark-mirrored
// in index.ts). progress% is NOT a mirrored SoR field (the Active-Jobs sheets have no progress
// column) — it survives only as an optional create-body field (default 0); the standalone
// POST /:job_id/progress route was deleted (see the tombstone below).

const MAX_JOB_ID = 64;
const MAX_NAME = 256;
const MAX_PHONE = 40;
const MAX_EMAIL = 320;
const MAX_ADDRESS = 512;
const MAX_CC = 5; // mirrors each Active-Jobs sheet's CC 1..5 columns
// Loose email shape: no whitespace, one @, a dotted domain (matches shared/active_jobs _EMAIL_RE).
const EMAIL_RE = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;

const LIFECYCLE_VALUES = new Set(["active", "inactive", "archived"]);

function clampPct(n: number): number {
  return Math.max(0, Math.min(100, Math.round(n)));
}

/** lifecycle → the legacy `active` int (the dropdown/down-sync flag): only 'active' is live. */
function lifecycleToActive(lifecycle: string): number {
  return lifecycle === "active" ? 1 : 0;
}

/** Allocate the next portal-assigned canonical job number, atomically.
 *
 *  `UPDATE … SET last_value = last_value + 1 … RETURNING last_value` is a SINGLE atomic statement;
 *  D1 serializes writes, so two concurrent creates receive distinct numbers (no read-then-write
 *  race). Returns the formatted `JOB-######` (6-digit zero-pad, matching the legacy AUTO_NUMBER),
 *  or null when the counter is UNAVAILABLE — either the seed row is missing OR the `job_counter`
 *  table itself is absent (migration 0022 not applied before the Worker deployed; D1 THROWS
 *  "no such table" rather than returning null, so we catch it). Both collapse to null so the caller
 *  returns ONE clean fail-closed 500 `counter_unavailable` for the real deploy-order case — never a
 *  malformed id, and never an opaque `internal_error` the runbook can't grep for. */
async function allocateJobNumber(db: D1Database): Promise<string | null> {
  let row: { last_value: number } | null;
  try {
    row = await db
      .prepare("UPDATE job_counter SET last_value = last_value + 1 WHERE id = 1 RETURNING last_value")
      .first<{ last_value: number }>();
  } catch {
    return null; // table absent (0022 not applied) → fail closed, identical to a missing row
  }
  if (!row) return null;
  return `JOB-${String(row.last_value).padStart(6, "0")}`;
}

// ---- routing-block validation (shared by create + contacts edit) ----------

interface Routing {
  address: string;
  stakeholder_name: string;
  stakeholder_email: string;
  stakeholder_phone: string;
  safety_contact_name: string;
  safety_contact_email: string;
  safety_cc: string[];
  progress_contact_name: string;
  progress_contact_email: string;
  progress_cc: string[];
}

function str(v: unknown): string {
  return typeof v === "string" ? v.trim() : "";
}

/** Validate + normalize a CC array field: ≤MAX_CC entries, each a non-empty length-bounded
 *  email-shaped string. Returns the cleaned array, or null on any malformed entry / over-cap. */
function parseCc(v: unknown): string[] | null {
  if (v === undefined || v === null) return [];
  if (!Array.isArray(v)) return null;
  if (v.length > MAX_CC) return null;
  const out: string[] = [];
  for (const e of v) {
    const s = typeof e === "string" ? e.trim() : "";
    if (!s) continue; // skip blanks (an empty CC slot)
    if (s.length > MAX_EMAIL || !EMAIL_RE.test(s)) return null;
    out.push(s);
  }
  return out;
}

type RoutingResult = { ok: true; routing: Routing } | { ok: false; error: string };

/** Parse the SoR/routing block from a request body. All fields optional (default ''); a present
 *  contact email must be email-shaped; CC arrays bounded. Used by create (full) + contacts (edit). */
function parseRouting(body: Record<string, unknown>): RoutingResult {
  const address = str(body.address);
  const stakeholder_name = str(body.stakeholder_name);
  const stakeholder_email = str(body.stakeholder_email);
  const stakeholder_phone = str(body.stakeholder_phone);
  const safety_contact_name = str(body.safety_contact_name);
  const safety_contact_email = str(body.safety_contact_email);
  const progress_contact_name = str(body.progress_contact_name);
  const progress_contact_email = str(body.progress_contact_email);

  if (address.length > MAX_ADDRESS) return { ok: false, error: "invalid_address" };
  for (const n of [stakeholder_name, safety_contact_name, progress_contact_name]) {
    if (n.length > MAX_NAME) return { ok: false, error: "invalid_contact_name" };
  }
  if (stakeholder_phone.length > MAX_PHONE) return { ok: false, error: "invalid_phone" };
  for (const e of [stakeholder_email, safety_contact_email, progress_contact_email]) {
    if (e.length > MAX_EMAIL) return { ok: false, error: "invalid_email" };
    if (e && !EMAIL_RE.test(e)) return { ok: false, error: "invalid_email" };
  }
  const safety_cc = parseCc(body.safety_cc);
  const progress_cc = parseCc(body.progress_cc);
  if (safety_cc === null || progress_cc === null) return { ok: false, error: "invalid_cc" };

  return {
    ok: true,
    routing: {
      address, stakeholder_name, stakeholder_email, stakeholder_phone,
      safety_contact_name, safety_contact_email, safety_cc,
      progress_contact_name, progress_contact_email, progress_cc,
    },
  };
}

export function registerJobWriteRoutes(app: FieldopsApp, gates: FieldopsGates): void {
  // POST /api/fieldops/job — create a portal-origin job with full routing SoR (+ optional client).
  app.post(
    "/api/fieldops/job",
    gates.requireSession,
    gates.requireCapability("cap.jobtracker.manage"),
    async (c) => {
      let body: Record<string, unknown>;
      try {
        body = (await c.req.json()) as Record<string, unknown>;
      } catch {
        return c.json({ error: "bad_request" }, 400);
      }
      if (typeof body !== "object" || body === null || Array.isArray(body)) {
        return c.json({ error: "bad_request" }, 400);
      }

      // Slice 6: the portal ASSIGNS the Job ID (allocated below); the office employee types only
      // the Project Name. body.job_id is ignored.
      const projectName = typeof body.project_name === "string" ? body.project_name.trim() : "";
      if (projectName.length < 1 || projectName.length > MAX_NAME) return c.json({ error: "invalid_project_name" }, 400);
      const progress = typeof body.progress === "number" && Number.isFinite(body.progress) ? clampPct(body.progress) : 0;

      const routed = parseRouting(body);
      if (!routed.ok) return c.json({ error: routed.error }, 400);
      const r = routed.routing;

      // Client linkage: an existing client_id (verified) OR an inline new_client. Validate shapes.
      const clientIdRaw = typeof body.client_id === "number" && Number.isInteger(body.client_id) ? body.client_id : null;
      const newClient =
        body.new_client !== null && typeof body.new_client === "object" && !Array.isArray(body.new_client)
          ? (body.new_client as Record<string, unknown>)
          : null;
      if (body.client_id !== undefined && clientIdRaw === null) return c.json({ error: "invalid_client_id" }, 400);
      if (newClient && clientIdRaw !== null) return c.json({ error: "client_id_and_new_client" }, 400);

      const actor = c.get("session").username;

      let clientId: number | null = clientIdRaw;
      if (newClient) {
        const name = typeof newClient.name === "string" ? newClient.name.trim() : "";
        if (name.length < 1 || name.length > MAX_NAME) return c.json({ error: "invalid_client_name" }, 400);
        const contact = typeof newClient.contact === "string" ? newClient.contact : null;
        const phone = typeof newClient.phone === "string" ? newClient.phone : null;
        const email = typeof newClient.email === "string" ? newClient.email : null;
        if ((contact !== null && contact.length > MAX_NAME) || (phone !== null && phone.length > MAX_PHONE) || (email !== null && email.length > MAX_EMAIL)) {
          return c.json({ error: "invalid_client_field" }, 400);
        }
        const inserted = await c.env.DB
          .prepare("INSERT INTO clients (name, contact, phone, email) VALUES (?1,?2,?3,?4) RETURNING id")
          .bind(name, contact, phone, email)
          .first<{ id: number }>();
        clientId = inserted!.id;
      } else if (clientId !== null) {
        const client = await c.env.DB.prepare("SELECT id FROM clients WHERE id = ?1").bind(clientId).first();
        if (!client) return c.json({ error: "unknown_client" }, 422);
      }

      // Allocate the canonical number LAST — after all validation + the optional client insert — so
      // a 400/422 never burns a number. null ⇒ migration 0022 not applied → fail closed (500), never
      // a malformed id (deploy-order discipline: apply 0022 --remote BEFORE the Worker deploys).
      const jobId = await allocateJobNumber(c.env.DB);
      if (jobId === null) return c.json({ error: "counter_unavailable" }, 500);

      // Mutation + audit in ONE batch. 0017 fence (origin='portal', sync_state='pending') + 0021
      // SoR fields + version vector (lifecycle='active', mirror_version=1; watermarks default 0 so the
      // brand-new row is immediately dirty for the mirror daemon). canonical_job_id = job_id from
      // birth (?1) — the portal owns the number, so there is no Smartsheet read-back to wait for.
      // Server-authoritative created_at.
      try {
        await c.env.DB.batch([
          c.env.DB
            .prepare(
              `INSERT INTO jobs (
                 job_id, project_name, active, status, progress, client_id, created_at,
                 origin, sync_state, canonical_job_id,
                 address, stakeholder_name, stakeholder_email, stakeholder_phone,
                 safety_contact_name, safety_contact_email, safety_cc,
                 progress_contact_name, progress_contact_email, progress_cc,
                 lifecycle, mirror_version)
               VALUES (?1, ?2, 1, 'active', ?3, ?4, unixepoch(),
                       'portal', 'pending', ?1,
                       ?5, ?6, ?7, ?8,
                       ?9, ?10, ?11,
                       ?12, ?13, ?14,
                       'active', 1)`,
            )
            .bind(
              jobId, projectName, progress, clientId,
              r.address, r.stakeholder_name, r.stakeholder_email, r.stakeholder_phone,
              r.safety_contact_name, r.safety_contact_email, JSON.stringify(r.safety_cc),
              r.progress_contact_name, r.progress_contact_email, JSON.stringify(r.progress_cc),
            ),
          auditStmt(c, actor, "job_create", jobId, { job_id: jobId, client_id: clientId, origin: "portal" }),
        ]);
      } catch (e) {
        if (isUniqueViolation(e)) return c.json({ error: "job_exists" }, 409);
        throw e;
      }
      return c.json({ ok: true, job_id: jobId }, 201);
    },
  );

  // POST /api/fieldops/job/:job_id/lifecycle — set the job lifecycle (active|inactive|archived).
  // Derives the legacy `active` flag, bumps the mirror version, re-dirties the row. THE close path:
  // the UI closes a job via { lifecycle: 'inactive' } (the old bare /close alias was deleted —
  // tombstone below).
  app.post(
    "/api/fieldops/job/:job_id/lifecycle",
    gates.requireSession,
    gates.requireCapability("cap.jobtracker.manage"),
    async (c) => {
      const jobId = c.req.param("job_id");
      if (jobId.length > MAX_JOB_ID) return c.json({ error: "invalid_job_id" }, 400);
      let body: Record<string, unknown>;
      try {
        body = (await c.req.json()) as Record<string, unknown>;
      } catch {
        return c.json({ error: "bad_request" }, 400);
      }
      // JSON `null`/arrays parse fine but aren't objects; dereferencing body.lifecycle on them
      // would throw → bare 500 (the "audit #1" class). Require a plain object first.
      if (typeof body !== "object" || body === null || Array.isArray(body)) {
        return c.json({ error: "bad_request" }, 400);
      }
      const lifecycle = typeof body.lifecycle === "string" ? body.lifecycle.trim().toLowerCase() : "";
      if (!LIFECYCLE_VALUES.has(lifecycle)) return c.json({ error: "invalid_lifecycle" }, 400);
      const res = await setLifecycle(c, jobId, lifecycle);
      return res;
    },
  );

  // TOMBSTONE (operator-approved deletion, 2026-07-03): POST /api/fieldops/job/:job_id/close — the
  // thin back-compat alias → lifecycle='inactive' — was DELETED (zero SPA/Python callers since
  // setLifecycle superseded it in the UI, P2.5). The /lifecycle route above is the live close path.
  // Git history has the handler (this file, pre-deletion).

  // POST /api/fieldops/job/:job_id/contacts — partial edit of the routing SoR block; bumps the
  // mirror version + re-dirties. Only routing fields are touched (job_id/lifecycle/status untouched).
  app.post(
    "/api/fieldops/job/:job_id/contacts",
    gates.requireSession,
    gates.requireCapability("cap.jobtracker.manage"),
    async (c) => {
      const jobId = c.req.param("job_id");
      if (jobId.length > MAX_JOB_ID) return c.json({ error: "invalid_job_id" }, 400);
      let body: Record<string, unknown>;
      try {
        body = (await c.req.json()) as Record<string, unknown>;
      } catch {
        return c.json({ error: "bad_request" }, 400);
      }
      if (typeof body !== "object" || body === null || Array.isArray(body)) {
        return c.json({ error: "bad_request" }, 400);
      }
      const routed = parseRouting(body);
      if (!routed.ok) return c.json({ error: routed.error }, 400);
      const r = routed.routing;
      const actor = c.get("session").username;
      // Bump the version + re-dirty in the same batch; conditional audit logs only on a real row.
      const res = await c.env.DB.batch([
        c.env.DB
          .prepare(
            // SCOPED TO origin='portal' (security, W5): never edit a smartsheet-origin job's SoR —
            // the down-sync can't reconcile these fields, so a stray write would corrupt it forever.
            `UPDATE jobs SET
               address=?2, stakeholder_name=?3, stakeholder_email=?4, stakeholder_phone=?5,
               safety_contact_name=?6, safety_contact_email=?7, safety_cc=?8,
               progress_contact_name=?9, progress_contact_email=?10, progress_cc=?11,
               mirror_version=mirror_version+1, sync_state='pending'
             WHERE job_id=?1 AND origin='portal'`,
          )
          .bind(
            jobId,
            r.address, r.stakeholder_name, r.stakeholder_email, r.stakeholder_phone,
            r.safety_contact_name, r.safety_contact_email, JSON.stringify(r.safety_cc),
            r.progress_contact_name, r.progress_contact_email, JSON.stringify(r.progress_cc),
          ),
        auditStmtIfChanged(c, actor, "job_contacts", jobId, { job_id: jobId }),
      ]);
      if ((res[0].meta.changes ?? 0) === 0) return c.json({ error: "not_found" }, 404);
      return c.json({ ok: true, job_id: jobId }, 200);
    },
  );

  // TOMBSTONE (operator-approved deletion, 2026-07-03): POST /api/fieldops/job/:job_id/progress —
  // the manual progress-% write — was DELETED. Nothing displayed the value (the UI slider/bar was
  // removed in #403, the P6 rollup deliberately excludes progress %), and no Python read it. The
  // `jobs.progress` COLUMN and the optional create-body `progress` field remain (see the
  // "Remove the progress-% estimate system-wide" tech-debt entry for the full multi-surface
  // removal). Git history has the handler (this file, pre-deletion).
}

// ---- lifecycle setter (used by /lifecycle; formerly also the deleted /close alias) -------------

/** Set lifecycle + the derived `active` flag (+ legacy `status` for back-compat: inactive/archived
 *  → 'closed', active → 'active'), bump mirror_version, re-dirty (sync_state='pending'), audit.
 *  Idempotent-friendly: a no-op same-value set still bumps the version (cheap; the daemon no-ops).
 *
 *  SCOPED TO origin='portal' (security): the portal is the SOLE writer of portal-created jobs;
 *  these edit routes must NEVER touch an origin='smartsheet' row (the down-sync only reconciles
 *  project_name/active, so a stray lifecycle/SoR write to a smartsheet job would corrupt it
 *  permanently, with no self-heal). A non-portal (or unknown) job_id → 0 changes → 404. */
async function setLifecycle(
  c: Context<{ Bindings: Env; Variables: Vars }>,
  jobId: string,
  lifecycle: string,
): Promise<Response> {
  const active = lifecycleToActive(lifecycle);
  const status = lifecycle === "active" ? "active" : "closed";
  const actor = c.get("session").username;
  const res = await c.env.DB.batch([
    c.env.DB
      .prepare(
        "UPDATE jobs SET lifecycle=?2, active=?3, status=?4, mirror_version=mirror_version+1, sync_state='pending' " +
          "WHERE job_id=?1 AND origin='portal'",
      )
      .bind(jobId, lifecycle, active, status),
    auditStmtIfChanged(c, actor, "job_lifecycle", jobId, { job_id: jobId, lifecycle }),
  ]);
  if ((res[0].meta.changes ?? 0) === 0) return c.json({ error: "not_found" }, 404);
  return c.json({ ok: true, job_id: jobId, lifecycle }, 200);
}
