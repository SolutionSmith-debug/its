import type { FieldopsApp, FieldopsGates } from "./fieldops_gates";
import { auditStmt } from "./audit";

// P2.3 Slice 3 — TASK WRITE (add / status). task_assignments is a PLAIN table (in-place mutable,
// not integrity-bar): add INSERTs, status UPDATEs in place, each with its audit_log row in ONE
// D1 batch (W4). Send-free (D1 only).
//
// MIXED CAP: adding a task (which also assigns crew via personnel_id) is management →
// cap.jobtracker.manage (admin-only); changing a task's own status is a field action →
// cap.tasks.own (submitter + admin). A submitter can move a task's status but not create one.

const MAX_DESC = 256;
const STATUSES = new Set(["open", "in_progress", "done"]);

export function registerTaskWriteRoutes(app: FieldopsApp, gates: FieldopsGates): void {
  // POST /api/fieldops/job/:job_id/task — add a task (optionally assigned to a person) to a live job.
  app.post(
    "/api/fieldops/job/:job_id/task",
    gates.requireSession,
    gates.requireCapability("cap.jobtracker.manage"),
    async (c) => {
      const jobId = c.req.param("job_id");
      if (jobId.length > 64) return c.json({ error: "invalid_job_id" }, 400);

      let body: Record<string, unknown>;
      try {
        body = (await c.req.json()) as Record<string, unknown>;
      } catch {
        return c.json({ error: "bad_request" }, 400);
      }
      if (typeof body !== "object" || body === null || Array.isArray(body)) {
        return c.json({ error: "bad_request" }, 400);
      }
      const description = typeof body.description === "string" ? body.description.trim() : "";
      if (description.length < 1 || description.length > MAX_DESC) return c.json({ error: "invalid_description" }, 400);
      const personnelId =
        typeof body.personnel_id === "number" && Number.isInteger(body.personnel_id) ? body.personnel_id : null;
      if (body.personnel_id !== undefined && personnelId === null) return c.json({ error: "invalid_personnel_id" }, 400);

      // Job must exist + be active (active=1; closed jobs reject). Disambiguate 404 vs 409.
      const job = await c.env.DB.prepare("SELECT active FROM jobs WHERE job_id = ?1").bind(jobId).first<{ active: number }>();
      if (!job) return c.json({ error: "not_found" }, 404);
      if (job.active === 0) return c.json({ error: "not_active" }, 409);
      // A given personnel_id must be an ACTIVE roster member (a retired person — soft-deleted via
      // active=0 — can't be assigned new work). Check-then-act is race-free: personnel are only ever
      // soft-deleted, never hard-DELETEd (see fieldops_personnel_write retire), so an id that passes
      // here can't vanish before the batch. (If a hard DELETE is ever added, fold this into the WHERE.)
      if (personnelId !== null) {
        const p = await c.env.DB.prepare("SELECT id FROM personnel WHERE id = ?1 AND active = 1").bind(personnelId).first();
        if (!p) return c.json({ error: "unknown_personnel" }, 422);
      }

      const actor = c.get("session").username;
      // INSERT omits created_at → schema DEFAULT (unixepoch()). RETURNING the new id for the response;
      // the audit (same batch) keys on job_id + description (the auto-id isn't available to it).
      const res = await c.env.DB.batch([
        c.env.DB
          .prepare("INSERT INTO task_assignments (job_id, personnel_id, description, status, assigned_by) VALUES (?1,?2,?3,'open',?4) RETURNING id")
          .bind(jobId, personnelId, description, actor),
        auditStmt(c, actor, "task_create", jobId, { job_id: jobId, description, personnel_id: personnelId }),
      ]);
      const newId = (res[0].results?.[0] as { id: number } | undefined)?.id ?? null;
      return c.json({ ok: true, id: newId }, 201);
    },
  );

  // POST /api/fieldops/task/:id/status — change a task's status (field action; submitter + admin).
  app.post(
    "/api/fieldops/task/:id/status",
    gates.requireSession,
    gates.requireCapability("cap.tasks.own"),
    async (c) => {
      const id = parseInt(c.req.param("id"), 10);
      if (isNaN(id)) return c.json({ error: "invalid_id" }, 400);

      let body: Record<string, unknown>;
      try {
        body = (await c.req.json()) as Record<string, unknown>;
      } catch {
        return c.json({ error: "bad_request" }, 400);
      }
      // A literal `null`/array body parses without throwing — reject before any property access.
      if (typeof body !== "object" || body === null || Array.isArray(body)) {
        return c.json({ error: "bad_request" }, 400);
      }
      const status = typeof body.status === "string" ? body.status : "";
      if (!STATUSES.has(status)) return c.json({ error: "invalid_status" }, 400);

      const actor = c.get("session").username;
      const res = await c.env.DB.batch([
        c.env.DB.prepare("UPDATE task_assignments SET status = ?2 WHERE id = ?1").bind(id, status),
        c.env.DB
          .prepare("INSERT INTO audit_log (actor_username, action, target_username, detail) SELECT ?1,?2,?3,?4 WHERE changes()=1")
          .bind(actor, "task_status", String(id), JSON.stringify({ task_id: id, status })),
      ]);
      if ((res[0].meta.changes ?? 0) === 0) return c.json({ error: "not_found" }, 404);
      return c.json({ ok: true, id, status }, 200);
    },
  );

  // POST /api/fieldops/task/:id/assign — (re)assign or clear a task's assignee. Assigning who does a
  // task is MANAGEMENT (same cap as add-task) → cap.jobtracker.manage (admin). Body { personnel_id }:
  // null unassigns; an integer places (roster-verified). Mutation + audit in ONE D1 batch (W4). Send-free.
  app.post(
    "/api/fieldops/task/:id/assign",
    gates.requireSession,
    gates.requireCapability("cap.jobtracker.manage"),
    async (c) => {
      const id = parseInt(c.req.param("id"), 10);
      if (isNaN(id)) return c.json({ error: "invalid_id" }, 400);

      let body: Record<string, unknown>;
      try {
        body = (await c.req.json()) as Record<string, unknown>;
      } catch {
        return c.json({ error: "bad_request" }, 400);
      }
      // A literal `null`/array body parses without throwing — reject before any property access.
      if (typeof body !== "object" || body === null || Array.isArray(body)) {
        return c.json({ error: "bad_request" }, 400);
      }

      // personnel_id must be present and be null OR an integer (mirrors crew-assign's job_id guard:
      // the key must be present — `undefined`, a string, or a float is ambiguous → 400).
      const raw = body.personnel_id;
      if (raw !== null && !(typeof raw === "number" && Number.isInteger(raw))) {
        return c.json({ error: "invalid_personnel_id" }, 400);
      }
      const personnelId = raw as number | null;

      // A given personnel_id must be an ACTIVE roster member (mirrors the add-task check above;
      // retired personnel aren't assignable). Check-then-act is race-free — personnel are soft-deleted
      // (active=0), never hard-DELETEd — so this matches the atomic guarantee of fieldops_crew_assign.
      if (personnelId !== null) {
        const p = await c.env.DB.prepare("SELECT id FROM personnel WHERE id = ?1 AND active = 1").bind(personnelId).first();
        if (!p) return c.json({ error: "unknown_personnel" }, 422);
      }

      const actor = c.get("session").username;
      const res = await c.env.DB.batch([
        c.env.DB.prepare("UPDATE task_assignments SET personnel_id = ?2 WHERE id = ?1").bind(id, personnelId),
        c.env.DB
          .prepare("INSERT INTO audit_log (actor_username, action, target_username, detail) SELECT ?1,?2,?3,?4 WHERE changes()=1")
          .bind(actor, "task_assign", String(id), JSON.stringify({ task_id: id, personnel_id: personnelId })),
      ]);
      if ((res[0].meta.changes ?? 0) === 0) return c.json({ error: "not_found" }, 404);
      return c.json({ ok: true, id, personnel_id: personnelId }, 200);
    },
  );
}
