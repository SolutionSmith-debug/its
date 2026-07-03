import type { Context } from "hono";
import type { FieldopsApp, FieldopsGates } from "./fieldops_gates";
import type { Env, Vars } from "./types";
import { auditStmtIfChanged } from "./audit";

// P2.6 — CREW → JOB ASSIGNMENT (cap.crew.assign; Manager + admin). Sets a person's STANDING
// placement (personnel.current_job, migration 0023) — "who is where". Distinct from
// cap.jobtracker.manage: a Manager assigns/moves crew WITHOUT the power to create jobs or
// tasks. ORTHOGONAL to time logging — personnel.current_job records placement only; a person
// placed on Job A may still log time against any active Job B (fieldops_time_write is
// unchanged), so this route never constrains, and is never constrained by, time entries.
//
// Model mirrors personnel link/unlink (fieldops_personnel_write): the placement is a SOFT ref
// (no FK) and, to avoid persisting a dangling placement, an ASSIGN's job-existence test lives
// INSIDE the UPDATE (… AND EXISTS(SELECT 1 FROM jobs … active=1)) so check + write are ONE atomic
// statement — a job closed concurrently can't leave a placement pointing at an inactive job.
// The predicate is `active = 1` (NOT lifecycle) deliberately: `active` is the portal-wide
// live-job flag — `/api/jobs` (the assign dropdown source), the submit flow, and push_jobs all
// use it; setLifecycle derives it (lifecycleToActive), and it correctly reflects DOWN-SYNCED
// smartsheet jobs whose `lifecycle` may stay at the schema default. So a job offered by the
// dropdown is always acceptable here. Mutation + its conditional audit_log row land in ONE D1
// batch (W4). Send-free.

type Ctx = Context<{ Bindings: Env; Variables: Vars }>;
const MAX_JOB_ID = 64;

function badId(c: Ctx): number | null {
  const id = parseInt(c.req.param("id") ?? "", 10);
  return isNaN(id) ? null : id;
}

export function registerCrewAssignRoutes(app: FieldopsApp, gates: FieldopsGates): void {
  // POST /api/fieldops/personnel/:id/assign — set/clear a person's standing job placement.
  //   { job_id: "<active job>" } → place on that job
  //   { job_id: null }           → clear the placement (unassign)
  app.post(
    "/api/fieldops/personnel/:id/assign",
    gates.requireSession,
    gates.requireCapability("cap.crew.assign"),
    async (c) => {
      const id = badId(c);
      if (id === null) return c.json({ error: "invalid_id" }, 400);
      let body: Record<string, unknown>;
      try {
        body = (await c.req.json()) as Record<string, unknown>;
      } catch {
        return c.json({ error: "bad_request" }, 400);
      }
      if (typeof body !== "object" || body === null || Array.isArray(body)) return c.json({ error: "bad_request" }, 400);

      // The job_id key must be present and be a string OR explicit null. `undefined` (key
      // absent) is ambiguous → 400 (mirrors the plain-object body guards elsewhere).
      const raw = body.job_id;
      if (raw !== null && typeof raw !== "string") return c.json({ error: "bad_request" }, 400);
      const jobId = typeof raw === "string" ? raw.trim() : null;
      if (jobId !== null && (jobId.length < 1 || jobId.length > MAX_JOB_ID)) return c.json({ error: "invalid_job_id" }, 400);

      const actor = c.get("session").username;

      if (jobId === null) {
        // ── unassign (clear placement) — idempotent on an already-unplaced active row ──
        const res = await c.env.DB.batch([
          c.env.DB.prepare("UPDATE personnel SET current_job = NULL WHERE id = ?1 AND active = 1").bind(id),
          auditStmtIfChanged(c, actor, "personnel_assign", String(id), { personnel_id: id, job_id: null }),
        ]);
        if ((res[0].meta.changes ?? 0) === 0) return c.json({ error: "not_found" }, 404);
        return c.json({ ok: true, id, job_id: null }, 200);
      }

      // ── assign (place on an ACTIVE job) — job-existence test is IN the WHERE (atomic, no dangling ref) ──
      const res = await c.env.DB.batch([
        c.env.DB
          .prepare(
            "UPDATE personnel SET current_job = ?2 WHERE id = ?1 AND active = 1 AND EXISTS (SELECT 1 FROM jobs WHERE job_id = ?2 AND active = 1)",
          )
          .bind(id, jobId),
        auditStmtIfChanged(c, actor, "personnel_assign", String(id), { personnel_id: id, job_id: jobId }),
      ]);
      if ((res[0].meta.changes ?? 0) === 0) {
        // Disambiguate (mirror /link): active personnel row present → the JOB is missing/inactive
        // (422 well-formed job_id, no such active job); no active personnel row → bad id (404).
        const row = await c.env.DB.prepare("SELECT id FROM personnel WHERE id = ?1 AND active = 1").bind(id).first();
        return row ? c.json({ error: "unknown_job" }, 422) : c.json({ error: "not_found" }, 404);
      }
      return c.json({ ok: true, id, job_id: jobId }, 200);
    },
  );
}
