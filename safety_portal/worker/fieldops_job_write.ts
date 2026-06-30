import type { Context } from "hono";
import type { FieldopsApp, FieldopsGates } from "./fieldops_gates";
import type { Env, Vars } from "./types";
import { auditStmt, isUniqueViolation } from "./audit";

// P2.3 Slice 2 + P2.5 Slice 1 — JOB WRITE (create / lifecycle / contacts / progress).
// cap.jobtracker.manage (admin-only). Send-free (D1 only).
//
// jobs is a PLAIN (in-place mutable) table — NOT integrity-bar — so updates UPDATE in place, but
// every mutation still writes an audit_log row in the SAME D1 batch (W4).
//
// P2.5 — PORTAL IS THE AUTHORITATIVE WRITER. The create form now owns the full job source-of-truth
// (address, stakeholder, Safety + Progress routing contacts + CC arrays, lifecycle). The Mac-side
// mirror daemon (field_ops/fieldops_sync.py, Slice 5) reads dirty portal rows over
// GET /api/internal/fieldops/pending-jobs and find-or-creates a row in BOTH ITS-owned Active-Jobs
// sheets keyed by the typed job_id (each sheet's "Portal Job Key" column).
//
// 0017 ORIGIN FENCE: a portal-CREATED job is stamped origin='portal' FOREVER, so the 60s
// `/api/internal/sync` full-replace (scoped to origin='smartsheet') can never deactivate it.
//
// VERSION VECTOR (migration 0021): every SoR/lifecycle mutation bumps mirror_version + sets
// sync_state='pending' (the dirty flag). The daemon advances each sheet's watermark independently
// and flips sync_state→'synced' only when BOTH catch up (see /api/internal/fieldops/jobs-mark-mirrored
// in index.ts). progress% is NOT a mirrored SoR field (the Active-Jobs sheets have no progress
// column), so it deliberately does NOT bump the version — see the /progress route.

// A portal job_id is a USER-TYPED key. Reject anything shaped like a Smartsheet AUTO_NUMBER
// (JOB-####) so a typed key can never collide with / shadow a canonical sheet id (the
// Portal Job Key bridge + canonical_job_id duplicate pre-pass both rely on this disjointness).
const JOB_ID_RE = /^[A-Z0-9][A-Z0-9-]{0,63}$/;
const CANONICAL_JOB_ID_RE = /^JOB-\d+$/i;
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

      const jobId = (typeof body.job_id === "string" ? body.job_id : "").trim().toUpperCase();
      const projectName = typeof body.project_name === "string" ? body.project_name.trim() : "";
      if (!JOB_ID_RE.test(jobId)) return c.json({ error: "invalid_job_id" }, 400);
      // Reject an AUTO_NUMBER-shaped key so a typed portal id can never shadow a canonical JOB-####.
      if (CANONICAL_JOB_ID_RE.test(jobId)) return c.json({ error: "reserved_job_id" }, 400);
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

      // Pre-check job uniqueness BEFORE any client insert (keeps the common dup case from orphaning a
      // client). A rare TOCTOU race is caught by the UNIQUE → 409 below; residue is a harmless
      // unreferenced clients row.
      const dup = await c.env.DB.prepare("SELECT job_id FROM jobs WHERE job_id = ?1").bind(jobId).first();
      if (dup) return c.json({ error: "job_exists" }, 409);

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

      // Mutation + audit in ONE batch. 0017 fence (origin='portal', sync_state='pending') + 0021
      // SoR fields + version vector (lifecycle='active', mirror_version=1; watermarks default 0 so the
      // brand-new row is immediately dirty for the mirror daemon). Server-authoritative created_at.
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
                       'portal', 'pending', NULL,
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
  // Derives the legacy `active` flag, bumps the mirror version, re-dirties the row. Replaces the
  // bare /close in the UI; /close is kept below as a thin 'inactive' alias.
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

  // POST /api/fieldops/job/:job_id/close — thin alias → lifecycle='inactive' (back-compat).
  app.post(
    "/api/fieldops/job/:job_id/close",
    gates.requireSession,
    gates.requireCapability("cap.jobtracker.manage"),
    async (c) => {
      const jobId = c.req.param("job_id");
      if (jobId.length > MAX_JOB_ID) return c.json({ error: "invalid_job_id" }, 400);
      return setLifecycle(c, jobId, "inactive");
    },
  );

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
        c.env.DB
          .prepare(
            "INSERT INTO audit_log (actor_username, action, target_username, detail) SELECT ?1,?2,?3,?4 WHERE changes()=1",
          )
          .bind(actor, "job_contacts", jobId, JSON.stringify({ job_id: jobId })),
      ]);
      if ((res[0].meta.changes ?? 0) === 0) return c.json({ error: "not_found" }, 404);
      return c.json({ ok: true, job_id: jobId }, 200);
    },
  );

  // POST /api/fieldops/job/:job_id/progress — update the progress bar (0–100). NOT a mirrored SoR
  // field (the Active-Jobs sheets carry no progress column), so it does NOT bump mirror_version.
  app.post(
    "/api/fieldops/job/:job_id/progress",
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
      if (typeof body !== "object" || body === null || Array.isArray(body) || typeof body.progress !== "number" || !Number.isFinite(body.progress)) {
        return c.json({ error: "invalid_progress" }, 400);
      }
      const progress = clampPct(body.progress);
      const actor = c.get("session").username;
      const res = await c.env.DB.batch([
        c.env.DB.prepare("UPDATE jobs SET progress=?2 WHERE job_id=?1").bind(jobId, progress),
        c.env.DB
          .prepare(
            "INSERT INTO audit_log (actor_username, action, target_username, detail) SELECT ?1,?2,?3,?4 WHERE changes()=1",
          )
          .bind(actor, "job_progress", jobId, JSON.stringify({ job_id: jobId, progress })),
      ]);
      if ((res[0].meta.changes ?? 0) === 0) return c.json({ error: "not_found" }, 404);
      return c.json({ ok: true, job_id: jobId, progress }, 200);
    },
  );
}

// ---- shared lifecycle setter (used by /lifecycle + /close) -----------------

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
    c.env.DB
      .prepare(
        "INSERT INTO audit_log (actor_username, action, target_username, detail) SELECT ?1,?2,?3,?4 WHERE changes()=1",
      )
      .bind(actor, "job_lifecycle", jobId, JSON.stringify({ job_id: jobId, lifecycle })),
  ]);
  if ((res[0].meta.changes ?? 0) === 0) return c.json({ error: "not_found" }, 404);
  return c.json({ ok: true, job_id: jobId, lifecycle }, 200);
}
