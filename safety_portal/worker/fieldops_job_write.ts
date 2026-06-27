import type { FieldopsApp, FieldopsGates } from "./fieldops_gates";
import { auditStmt, isUniqueViolation } from "./audit";

// P2.3 Slice 2 — JOB WRITE (create / close / progress). cap.jobtracker.manage (admin-only).
//
// jobs is a PLAIN (in-place mutable) table — NOT integrity-bar — so updates UPDATE in place, but
// every mutation still writes an audit_log row in the SAME D1 batch (W4). Send-free (D1 only).
//
// 0017 ORIGIN FENCE: a portal-CREATED job is stamped origin='portal', sync_state='pending',
// canonical_job_id=NULL so the 60s `/api/internal/sync` full-replace (scoped to origin='smartsheet')
// can never deactivate it. The P2.4 Mac mirror daemon later promotes it into ITS_Active_Jobs,
// writes the assigned JOB-#### into canonical_job_id, and flips sync_state 'pending'→'synced'. That
// flip is NOT done here — see docs/runbooks/fieldops_job_write.md (§43).

const JOB_ID_RE = /^[A-Z0-9][A-Z0-9-]{0,63}$/;
const MAX_JOB_ID = 64;
const MAX_NAME = 256;
const MAX_PHONE = 40;
const MAX_EMAIL = 320;

function clampPct(n: number): number {
  return Math.max(0, Math.min(100, Math.round(n)));
}

export function registerJobWriteRoutes(app: FieldopsApp, gates: FieldopsGates): void {
  // POST /api/fieldops/job — create a portal-origin job (+ optional inline client).
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
      if (projectName.length < 1 || projectName.length > MAX_NAME) return c.json({ error: "invalid_project_name" }, 400);
      const progress = typeof body.progress === "number" && Number.isFinite(body.progress) ? clampPct(body.progress) : 0;

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
      // client). A rare TOCTOU race (another writer creates the job between here and the batch) is
      // caught by the UNIQUE → 409 below; the only residue is a harmless unreferenced clients row.
      const dup = await c.env.DB.prepare("SELECT job_id FROM jobs WHERE job_id = ?1").bind(jobId).first();
      if (dup) return c.json({ error: "job_exists" }, 409);

      let clientId: number | null = clientIdRaw;
      if (newClient) {
        const name = typeof newClient.name === "string" ? newClient.name.trim() : "";
        if (name.length < 1 || name.length > MAX_NAME) return c.json({ error: "invalid_client_name" }, 400);
        const contact = typeof newClient.contact === "string" ? newClient.contact : null;
        const phone = typeof newClient.phone === "string" ? newClient.phone : null;
        const email = typeof newClient.email === "string" ? newClient.email : null;
        // Every body string reaching D1 is length-bounded (the columns are plain TEXT, no CHECK).
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

      // Mutation + audit in ONE batch. 0017 fence + server-authoritative created_at (jobs' ALTER
      // default is 0, so unixepoch() is written explicitly — a body-supplied created_at is ignored).
      try {
        await c.env.DB.batch([
          c.env.DB
            .prepare(
              `INSERT INTO jobs (job_id, project_name, active, status, progress, client_id, created_at, origin, sync_state, canonical_job_id)
               VALUES (?1, ?2, 1, 'active', ?3, ?4, unixepoch(), 'portal', 'pending', NULL)`,
            )
            .bind(jobId, projectName, progress, clientId),
          auditStmt(c, actor, "job_create", jobId, { job_id: jobId, client_id: clientId, origin: "portal" }),
        ]);
      } catch (e) {
        if (isUniqueViolation(e)) return c.json({ error: "job_exists" }, 409);
        throw e;
      }
      return c.json({ ok: true, job_id: jobId }, 201);
    },
  );

  // POST /api/fieldops/job/:job_id/close — TOCTOU-safe close of an ACTIVE job.
  app.post(
    "/api/fieldops/job/:job_id/close",
    gates.requireSession,
    gates.requireCapability("cap.jobtracker.manage"),
    async (c) => {
      const jobId = c.req.param("job_id");
      if (jobId.length > MAX_JOB_ID) return c.json({ error: "invalid_job_id" }, 400);
      const actor = c.get("session").username;
      // The UPDATE's own WHERE status='active' is the atomic guard (no separate read-then-write); the
      // conditional audit (SELECT … WHERE changes()=1) logs only when a row actually closed.
      const res = await c.env.DB.batch([
        c.env.DB.prepare("UPDATE jobs SET status='closed', active=0 WHERE job_id=?1 AND status='active'").bind(jobId),
        c.env.DB
          .prepare(
            "INSERT INTO audit_log (actor_username, action, target_username, detail) SELECT ?1,?2,?3,?4 WHERE changes()=1",
          )
          .bind(actor, "job_close", jobId, JSON.stringify({ job_id: jobId })),
      ]);
      if ((res[0].meta.changes ?? 0) === 0) {
        const row = await c.env.DB.prepare("SELECT status FROM jobs WHERE job_id=?1").bind(jobId).first<{ status: string }>();
        return row ? c.json({ error: "not_active" }, 409) : c.json({ error: "not_found" }, 404);
      }
      return c.json({ ok: true, job_id: jobId }, 200);
    },
  );

  // POST /api/fieldops/job/:job_id/progress — update the progress bar (0–100).
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
