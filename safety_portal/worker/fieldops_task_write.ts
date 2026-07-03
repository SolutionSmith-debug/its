import type { Context } from "hono";
import type { Env, Vars } from "./types";
import type { FieldopsApp, FieldopsGates } from "./fieldops_gates";
import { auditStmtIfChanged } from "./audit";

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
// (G2.6) Optional task deadline — a Pacific calendar date, same shape + validation as the
// checklist assign route's due_date (fieldops_checklist.ts DUE_DATE_RE). Stored in
// task_assignments.due_date (migration 0035, nullable TEXT). Deliberately NOT range-checked:
// past dates are legal (backfilling an already-late task) and future dates unbounded.
const DUE_DATE_RE = /^\d{4}-\d{2}-\d{2}$/;

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

// DIAGNOSTIC read for a target personnel_id (CS4 TOCTOU fold — see the route bodies): the target
// predicate itself now lives INSIDE the mutating statement's WHERE (target must be an ACTIVE roster
// member; for a cap.tasks.assign-only actor its LINKED account role must be 'submitter'), so
// check + write are atomic — the tracked role-TOCTOU (an admin promoting/demoting an account in the
// window between a pre-check SELECT and the UPDATE) can no longer shift the boundary of a write.
// This helper runs ONLY AFTER a refused mutation (changes()=0) to pick the SAME response code the
// old pre-check produced: 422 unknown_personnel (missing/retired) or 403 forbidden_target
// (assign-only actor, non-submitter target). A race that RESOLVES between the refused write and
// this read yields a best-guess retryable code — never a wrong write (the crew-assign
// disambiguation pattern). Single query (LEFT JOIN users) — no extra round-trip on success paths.
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
//
// CS4 TOCTOU fold: like checkTaskTarget, this is now a post-refusal DIAGNOSTIC — the current-owner
// predicate lives in the assign UPDATE's WHERE; this read only picks the response code (403
// forbidden_task) after changes()=0.
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

// (R1 SECURITY) OWNERSHIP GUARD for the status route: an actor whose ONLY task authority is
// cap.tasks.own (holds NEITHER cap.jobtracker.manage NOR cap.tasks.assign — i.e. a subcontractor)
// may change the status of a task ONLY when it is currently assigned to THEIR OWN linked ACTIVE
// personnel row. Before this guard, ANY cap.tasks.own holder could flip ANY task's status (the A3
// blocker). Managers/admins are unrestricted here — their task-authority caps already gate them.
// Task-not-found → null so the mutation's changes()=0 returns 404 (same shape as
// checkTaskCurrentOwner); an unassigned task or one whose owner link is retired (p.active=0) is NOT
// the actor's → 403 forbidden_task.
//
// CS4 TOCTOU fold: post-refusal DIAGNOSTIC only — the ownership predicate lives in the status
// UPDATE's WHERE (a task reassigned away in the pre-check window can no longer be flipped by its
// former owner); this read picks 403-vs-404 after changes()=0.
async function checkTaskStatusOwnership(
  c: Context<{ Bindings: Env; Variables: Vars }>,
  taskId: number,
): Promise<Response | null> {
  const caps = c.get("capabilities");
  if (caps.has(CAP_MANAGE) || caps.has(CAP_ASSIGN)) return null; // manager/admin: unrestricted
  const username = c.get("session").username;
  const row = await c.env.DB.prepare(
    "SELECT ta.personnel_id, p.username AS owner_username FROM task_assignments ta LEFT JOIN personnel p ON p.id = ta.personnel_id AND p.active = 1 WHERE ta.id = ?1",
  )
    .bind(taskId)
    .first<{ personnel_id: number | null; owner_username: string | null }>();
  if (!row) return null; // unknown task → the UPDATE's changes()=0 → 404 (no existence leak)
  if (row.personnel_id === null || row.owner_username !== username) {
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
      // (G2.6) Optional due_date — absent / null / '' all mean "no deadline" (NULL column), the
      // checklist assign route's tri-state convention; anything else must be 'YYYY-MM-DD'.
      let dueDate: string | null = null;
      if (body.due_date !== undefined && body.due_date !== null && body.due_date !== "") {
        if (typeof body.due_date !== "string" || !DUE_DATE_RE.test(body.due_date)) {
          return c.json({ error: "invalid_due_date" }, 400);
        }
        dueDate = body.due_date;
      }

      // Job must exist + be active (active=1; closed jobs reject). Disambiguate 404 vs 409.
      const job = await c.env.DB.prepare("SELECT active FROM jobs WHERE job_id = ?1").bind(jobId).first<{ active: number }>();
      if (!job) return c.json({ error: "not_found" }, 404);
      if (job.active === 0) return c.json({ error: "not_active" }, 409);

      const actor = c.get("session").username;
      // CS4 TOCTOU fold: the TARGET predicate (given personnel_id must be an ACTIVE roster member;
      // for a cap.tasks.assign-only actor its linked account role must be 'submitter') lives IN the
      // INSERT's WHERE (INSERT … SELECT … WHERE), so check + write are one atomic statement — a
      // concurrent role change or (future) hard delete can no longer land between check and write.
      // INSERT omits created_at → schema DEFAULT (unixepoch()). RETURNING the new id for the
      // response; the audit rides changes()=1 in the SAME batch (a refused create audits nothing,
      // exactly as the old pre-check-then-return did).
      const res = await c.env.DB.batch([
        c.env.DB
          .prepare(
            `INSERT INTO task_assignments (job_id, personnel_id, description, status, assigned_by, due_date)
             SELECT ?1, ?2, ?3, 'open', ?4, ?6
             WHERE ?2 IS NULL OR EXISTS (
               SELECT 1 FROM personnel p LEFT JOIN users u ON u.username = p.username
               WHERE p.id = ?2 AND p.active = 1 AND (?5 = 0 OR u.role = 'submitter'))
             RETURNING id`,
          )
          .bind(jobId, personnelId, description, actor, isAssignOnly(c) ? 1 : 0, dueDate),
        auditStmtIfChanged(c, actor, "task_create", jobId, { job_id: jobId, description, personnel_id: personnelId, due_date: dueDate }),
      ]);
      const newId = (res[0].results?.[0] as { id: number } | undefined)?.id ?? null;
      if (newId === null) {
        // Refused by the in-WHERE target predicate. Diagnose with the SAME read the old pre-check
        // used so the response codes are identical: 422 unknown_personnel / 403 forbidden_target.
        // A race that resolved since the refusal falls back to 422 (retryable best-guess — only
        // the target predicate can zero this INSERT); no row was written either way.
        const guardErr = personnelId !== null ? await checkTaskTarget(c, personnelId) : null;
        return guardErr ?? c.json({ error: "unknown_personnel" }, 422);
      }
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

      // (R1 SECURITY / CS4 TOCTOU fold) own-only actors may only touch tasks assigned to their
      // linked ACTIVE personnel row — and the predicate lives IN the UPDATE's WHERE, so a task
      // reassigned away between check and write can no longer be flipped by its former owner.
      // Managers/admins (?3 = 1) are unrestricted, exactly as the old pre-check short-circuited.
      const caps = c.get("capabilities");
      const privileged = caps.has(CAP_MANAGE) || caps.has(CAP_ASSIGN) ? 1 : 0;
      const actor = c.get("session").username;
      const res = await c.env.DB.batch([
        c.env.DB
          .prepare(
            `UPDATE task_assignments SET status = ?2
             WHERE id = ?1 AND (?3 = 1 OR EXISTS (
               SELECT 1 FROM personnel p
               WHERE p.id = task_assignments.personnel_id AND p.active = 1 AND p.username = ?4))`,
          )
          .bind(id, status, privileged, actor),
        auditStmtIfChanged(c, actor, "task_status", String(id), { task_id: id, status }),
      ]);
      if ((res[0].meta.changes ?? 0) === 0) {
        // Refused: diagnose with the SAME read the old pre-check used — 403 forbidden_task when
        // the task exists but isn't the actor's; 404 not_found otherwise (unknown task — no
        // existence leak; a resolved race best-guesses 404, and no row was written either way).
        const ownErr = await checkTaskStatusOwnership(c, id);
        return ownErr ?? c.json({ error: "not_found" }, 404);
      }
      return c.json({ ok: true, id, status }, 200);
    },
  );

  // POST /api/fieldops/task/:id/assign — (re)assign or clear a task's assignee. Assigning who does a
  // task is task authority → cap.jobtracker.manage (admin) OR cap.tasks.assign (manager, 0025). Body
  // { personnel_id }: null unassigns; an integer places (roster-verified + subcontractor-target-guarded
  // for a manager). Mutation + audit in ONE D1 batch (W4). Send-free.
  // (G2.6) due_date is deliberately NOT in this UPDATE's SET — a reassign/unassign PRESERVES the
  // deadline: the due date belongs to the WORK, not to who currently holds it.
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

      const actor = c.get("session").username;
      const assignOnly = isAssignOnly(c) ? 1 : 0;
      // CS4 TOCTOU fold — BOTH role predicates live IN the UPDATE's WHERE, so check + write are one
      // atomic statement (the tracked fast-follow; fieldops_crew_assign's atomic-guard pattern):
      //   • (W1) current owner: an assign-only actor (?4 = 1) may only touch a task currently
      //     unassigned or held by a submitter-linked personnel (the subquery reads the row's OLD
      //     personnel_id — UPDATE's WHERE evaluates pre-assignment). Covers reassign AND unassign.
      //   • target: a given personnel_id (?2) must be an ACTIVE roster member, and for an
      //     assign-only actor its linked account role must be 'submitter' (mirrors the add-task
      //     fold above). An admin-window role flip can no longer shift either boundary mid-write.
      // (R1) Re-stamp assigned_by on every (re/un)assign — the column means "who last placed this
      // task" (the /tasks/mine context field), not just the original creator. Additive: historical
      // rows keep whatever the create route stamped (or NULL, pre-0014-stamping).
      const res = await c.env.DB.batch([
        c.env.DB
          .prepare(
            `UPDATE task_assignments SET personnel_id = ?2, assigned_by = ?3
             WHERE id = ?1
               AND (?4 = 0 OR task_assignments.personnel_id IS NULL OR EXISTS (
                 SELECT 1 FROM personnel p JOIN users u ON u.username = p.username
                 WHERE p.id = task_assignments.personnel_id AND u.role = 'submitter'))
               AND (?2 IS NULL OR EXISTS (
                 SELECT 1 FROM personnel p LEFT JOIN users u ON u.username = p.username
                 WHERE p.id = ?2 AND p.active = 1 AND (?4 = 0 OR u.role = 'submitter')))`,
          )
          .bind(id, personnelId, actor, assignOnly),
        auditStmtIfChanged(c, actor, "task_assign", String(id), { task_id: id, personnel_id: personnelId }),
      ]);
      if ((res[0].meta.changes ?? 0) === 0) {
        // Refused: diagnose in the OLD pre-check ORDER so the response codes are identical —
        // current-owner first (403 forbidden_task), then target (422 unknown_personnel / 403
        // forbidden_target), then 404 not_found (unknown task; also the resolved-race best-guess).
        // No row was written on any of these paths.
        const ownerErr = await checkTaskCurrentOwner(c, id);
        if (ownerErr) return ownerErr;
        if (personnelId !== null) {
          const targetErr = await checkTaskTarget(c, personnelId);
          if (targetErr) return targetErr;
        }
        return c.json({ error: "not_found" }, 404);
      }
      return c.json({ ok: true, id, personnel_id: personnelId }, 200);
    },
  );
}
