import type { Context } from "hono";
import type { Env, Vars } from "./types";
import type { FieldopsApp, FieldopsGates } from "./fieldops_gates";
import { auditStmt } from "./audit";

// P2.3 Slice 3 — TASK WRITE (add / status). task_assignments is a PLAIN table (in-place mutable,
// not integrity-bar): add INSERTs, status UPDATEs in place, each with its audit_log row in ONE
// D1 batch (W4). Send-free (D1 only).
//
// MIXED CAP: adding a task (which also assigns crew via personnel_id) or reassigning one is task
// authority → cap.jobtracker.manage (admin) OR cap.tasks.assign (manager, migration 0025 — the
// Assigned-Tasks S1 re-gate). Changing a task's own status is a field action → cap.tasks.own
// (submitter + manager + admin). A submitter can move a task's status but not create/assign one.
//
// SUBCONTRACTOR-TARGET GUARD: a cap.tasks.assign-only actor (a manager, WITHOUT cap.jobtracker.manage)
// may only TARGET a personnel whose linked account role is 'submitter' (the current role key for the
// to-be-renamed 'subcontractor') — an unlinked / admin / manager target → 403. An admin (holds
// cap.jobtracker.manage) is unrestricted. Enforced in-handler on the target personnel_id.

const MAX_DESC = 256;
const STATUSES = new Set(["open", "in_progress", "done"]);

const CAP_MANAGE = "cap.jobtracker.manage";
const CAP_ASSIGN = "cap.tasks.assign";
const TASK_WRITE_CAPS = [CAP_MANAGE, CAP_ASSIGN] as const;

// True when the actor holds cap.tasks.assign but NOT cap.jobtracker.manage — i.e. a manager whose
// task authority is constrained by the subcontractor-target guard. An admin (has cap.jobtracker.manage)
// returns false here and is unrestricted.
function isAssignOnly(c: Context<{ Bindings: Env; Variables: Vars }>): boolean {
  const caps = c.get("capabilities");
  return !caps.has(CAP_MANAGE) && caps.has(CAP_ASSIGN);
}

// Resolve + validate a target personnel_id: must be an ACTIVE roster member (else 422), and for a
// cap.tasks.assign-only actor its LINKED account role must be 'submitter' (else 403). Returns null on
// success, or the JSON error Response to return. Single query (LEFT JOIN users) — no extra round-trip.
//
// TOCTOU (accepted, low-severity — applies to both this guard and checkTaskCurrentOwner): the account
// ROLE is read here and re-checked separately from the mutating UPDATE, unlike the file's `active=1`
// checks (race-free because `active` only flips 1→0). `role` is bidirectional, so an admin promoting/
// demoting an account in the millisecond window between this SELECT and the UPDATE could shift the
// boundary. This is NOT a self-service escalation — the manager cannot trigger the concurrent role
// change; it requires an independent admin action landing in a window the actor doesn't control.
// Accepted for now; fast-follow tracked in docs/tech_debt.md to fold the role predicate into the
// UPDATE's WHERE (conditional for an assign-only actor) if this is ever treated as a hard boundary.
async function checkTaskTarget(
  c: Context<{ Bindings: Env; Variables: Vars }>,
  personnelId: number,
): Promise<Response | null> {
  const p = await c.env.DB.prepare(
    "SELECT u.role AS account_role FROM personnel p LEFT JOIN users u ON u.username = p.username WHERE p.id = ?1 AND p.active = 1",
  )
    .bind(personnelId)
    .first<{ account_role: string | null }>();
  if (!p) return c.json({ error: "unknown_personnel" }, 422);
  if (isAssignOnly(c) && p.account_role !== "submitter") {
    return c.json({ error: "forbidden_target" }, 403);
  }
  return null;
}

// (W1) For a cap.tasks.assign-only actor (a manager), the task being (re)assigned OR unassigned must
// CURRENTLY be unassigned or held by a submitter-linked personnel — a manager may not touch a task
// owned by an admin/manager account or an unlinked roster person (symmetric with checkTaskTarget on the
// DESTINATION; without this, a manager could unassign any task or reassign an admin's task away). Admins
// (unrestricted) skip it. Task-not-found → null so the mutation's changes()=0 returns 404 (no existence leak).
async function checkTaskCurrentOwner(
  c: Context<{ Bindings: Env; Variables: Vars }>,
  taskId: number,
): Promise<Response | null> {
  if (!isAssignOnly(c)) return null;
  const row = await c.env.DB.prepare(
    "SELECT ta.personnel_id, u.role AS owner_role FROM task_assignments ta LEFT JOIN personnel p ON p.id = ta.personnel_id LEFT JOIN users u ON u.username = p.username WHERE ta.id = ?1",
  )
    .bind(taskId)
    .first<{ personnel_id: number | null; owner_role: string | null }>();
  if (!row) return null;
  if (row.personnel_id !== null && row.owner_role !== "submitter") {
    return c.json({ error: "forbidden_task" }, 403);
  }
  return null;
}

export function registerTaskWriteRoutes(app: FieldopsApp, gates: FieldopsGates): void {
  // POST /api/fieldops/job/:job_id/task — add a task (optionally assigned to a person) to a live job.
  app.post(
    "/api/fieldops/job/:job_id/task",
    gates.requireSession,
    gates.requireAnyCapability(TASK_WRITE_CAPS),
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
        const guardErr = await checkTaskTarget(c, personnelId);
        if (guardErr) return guardErr;
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
  // task is task authority → cap.jobtracker.manage (admin) OR cap.tasks.assign (manager, 0025). Body
  // { personnel_id }: null unassigns; an integer places (roster-verified + subcontractor-target-guarded
  // for a manager). Mutation + audit in ONE D1 batch (W4). Send-free.
  app.post(
    "/api/fieldops/task/:id/assign",
    gates.requireSession,
    gates.requireAnyCapability(TASK_WRITE_CAPS),
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

      // (W1) A manager may only touch a task currently unassigned or held by a submitter — covers both
      // reassign and unassign (the null branch below). Admins skip this.
      const ownerErr = await checkTaskCurrentOwner(c, id);
      if (ownerErr) return ownerErr;

      // A given personnel_id must be an ACTIVE roster member (mirrors the add-task check above;
      // retired personnel aren't assignable). Check-then-act is race-free — personnel are soft-deleted
      // (active=0), never hard-DELETEd — so this matches the atomic guarantee of fieldops_crew_assign.
      if (personnelId !== null) {
        const guardErr = await checkTaskTarget(c, personnelId);
        if (guardErr) return guardErr;
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
