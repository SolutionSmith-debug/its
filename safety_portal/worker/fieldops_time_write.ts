import type { FieldopsApp, FieldopsGates } from "./fieldops_gates";
import { auditStmt, isUniqueViolation } from "./audit";
import { normalizeUsername } from "./auth";

// P2.3 Slice 1 — TIME-entry WRITE (the integrity-bar reference implementation).
//
// time_entries (migration 0015) is an INTEGRITY-BAR table: payroll/billing-grade, so a write
// here MUST follow all four rules:
//   1. RECORD time is server-authoritative — the INSERT OMITS created_at/edited_at so the schema
//      DEFAULT (unixepoch()) fires; a forged body timestamp is ignored. EVENT time
//      (work_started_at/work_ended_at) is a field-reported claim, stored verbatim, never authoritative.
//   2. APPEND-ONLY edit chain — an edit is a NEW row whose amends_uuid points at the prior uuid;
//      the original row is NEVER mutated.
//   3. DUAL attribution — actor_username = the authenticated session user; submitted_as = the
//      attributed account (equals actor on a self-submit). Forging submitted_as != actor needs cap.submit_as.
//   4. mutation + audit_log row land in ONE atomic D1 batch (W4).
//
// Send-free (D1 only): imports only the gate types + audit helpers (Invariant 1 holds by construction).

const SUBMIT_AS = "cap.submit_as";
const MAX_ID = 64;
const MAX_NOTES = 2000;
// (R1) hours is REQUIRED and bounded to one work day. Unit is HOURS (time_entries.hours REAL,
// migration 0015). Before this, a completely empty submit created an un-editable NULL-hours
// payroll-grade row (the A3 finding); now missing/non-numeric/NaN/<=0/>24 → 422 invalid_hours.
// work_started_at/work_ended_at stay optional field-reported claims, stored verbatim.
const MAX_HOURS = 24;

export function registerTimeWriteRoutes(app: FieldopsApp, gates: FieldopsGates): void {
  app.post(
    "/api/fieldops/time-entry",
    gates.requireSession,
    gates.requireCapability("cap.time.log"),
    async (c) => {
      // (1) BODY GUARD — parse → reject non-object → per-field type/bound (Invariant 2: untrusted).
      let body: Record<string, unknown>;
      try {
        body = (await c.req.json()) as Record<string, unknown>;
      } catch {
        return c.json({ error: "bad_request" }, 400);
      }
      if (typeof body !== "object" || body === null || Array.isArray(body)) {
        return c.json({ error: "bad_request" }, 400);
      }

      const asStr = (k: string): string | null => (typeof body[k] === "string" ? (body[k] as string) : null);
      const asNum = (k: string): number | null =>
        typeof body[k] === "number" && Number.isFinite(body[k] as number) ? (body[k] as number) : null;

      const uuid = asStr("uuid");
      const jobId = asStr("job_id");
      const notes = asStr("notes");
      const amendsUuid = asStr("amends_uuid");
      const submittedAs = asStr("submitted_as");
      const personnelId = asNum("personnel_id");
      const taskId = asNum("task_id");
      const workStartedAt = asNum("work_started_at");
      const workEndedAt = asNum("work_ended_at");
      const hours = asNum("hours");

      if (!uuid || uuid.length > MAX_ID) return c.json({ error: "invalid_uuid" }, 400);
      if (!jobId || jobId.length > MAX_ID) return c.json({ error: "invalid_job_id" }, 400);
      if (amendsUuid !== null && amendsUuid.length > MAX_ID) return c.json({ error: "invalid_amends_uuid" }, 400);
      if (notes !== null && notes.length > MAX_NOTES) return c.json({ error: "invalid_notes" }, 400);
      // (R1) hours required + bounded (0, MAX_HOURS]. asNum already maps missing/non-number/NaN → null.
      if (hours === null || hours <= 0 || hours > MAX_HOURS) return c.json({ error: "invalid_hours" }, 422);

      // (2) ATTRIBUTION — a submit-as (attributing to another account) requires cap.submit_as AND a
      //     real, ENABLED portal user as the target, normalized + existence-checked exactly like
      //     /api/submit (index.ts). A payroll-grade row must never be attributed to a phantom or
      //     unnormalized account — that would corrupt the very attribution chain the integrity bar
      //     exists to prove. The cap check precedes the user lookup (no existence oracle for non-admins).
      const actor = c.get("session").username;
      let attributed = actor; // default: self-submit
      if (submittedAs !== null && submittedAs !== "") {
        const target = normalizeUsername(submittedAs);
        if (target !== actor) {
          if (!c.get("capabilities").has(SUBMIT_AS)) return c.json({ error: "forbidden" }, 403);
          if (!target) return c.json({ error: "invalid_submitted_as" }, 400);
          const u = await c.env.DB.prepare("SELECT disabled FROM users WHERE username = ?")
            .bind(target)
            .first<{ disabled: number }>();
          if (!u || u.disabled) return c.json({ error: "unknown_attributed_user" }, 422);
          attributed = target;
        }
      }

      // (3) REFERENTIAL guards — bound params only. Job must exist + be active (not closed).
      const job = await c.env.DB.prepare("SELECT job_id FROM jobs WHERE job_id = ?1 AND active = 1")
        .bind(jobId)
        .first<{ job_id: string }>();
      if (!job) return c.json({ error: "unknown_job" }, 422);

      // A personnel_id, if given, must be an ACTIVE roster member (soft ref re-validated, per 0016
      // note; retired personnel — active=0 — can't have new time logged against them, matching the
      // task-write + crew-assign guards). Bound param.
      //
      // SUBCONTRACTOR SCOPING (Slice T): an actor holding cap.time.log but NOT cap.personnel.manage
      // (a subcontractor — managers/admins hold cap.personnel.manage and stay UNRESTRICTED) may log
      // time only against their OWN linked personnel OR a personnel they created (created_by = actor).
      // A well-formed, ACTIVE personnel that is neither → 403 forbidden_personnel (existence 422 first,
      // so a non-subcontractor-owned id is distinguishable from a bogus one — same shape as the
      // task-write subcontractor-target guard). personnel_id NULL (job-level / self) is always allowed.
      if (personnelId !== null) {
        const scoped = c.get("capabilities").has("cap.time.log") && !c.get("capabilities").has("cap.personnel.manage");
        const person = await c.env.DB.prepare(
          "SELECT username, created_by FROM personnel WHERE id = ?1 AND active = 1",
        )
          .bind(personnelId)
          .first<{ username: string | null; created_by: string | null }>();
        if (!person) return c.json({ error: "unknown_personnel" }, 422);
        if (scoped && person.username !== actor && person.created_by !== actor) {
          return c.json({ error: "forbidden_personnel" }, 403);
        }
      }

      // A task_id, if given, must belong to THIS job (soft ref re-validated, per 0016 note).
      if (taskId !== null) {
        const task = await c.env.DB.prepare("SELECT id FROM task_assignments WHERE id = ?1 AND job_id = ?2")
          .bind(taskId, jobId)
          .first<{ id: number }>();
        if (!task) return c.json({ error: "unknown_task" }, 422);
      }

      const action = amendsUuid ? "time_entry_edit" : "time_entry_create";

      // (4) MUTATION + AUDIT in ONE atomic batch. INSERT omits created_at/edited_at → server DEFAULT.
      try {
        await c.env.DB.batch([
          c.env.DB
            .prepare(
              `INSERT INTO time_entries
                 (uuid, job_id, personnel_id, task_id, work_started_at, work_ended_at, hours, notes,
                  actor_username, submitted_as, amends_uuid)
               VALUES (?1,?2,?3,?4,?5,?6,?7,?8,?9,?10,?11)`,
            )
            .bind(uuid, jobId, personnelId, taskId, workStartedAt, workEndedAt, hours, notes, actor, attributed, amendsUuid),
          auditStmt(c, actor, action, attributed, { uuid, job_id: jobId, amends_uuid: amendsUuid }),
        ]);
      } catch (e) {
        if (isUniqueViolation(e)) return c.json({ error: "uuid_conflict" }, 409); // amend/replay dedupe
        throw e; // real DB error — surface, never silently swallow
      }

      return c.json({ ok: true, uuid }, 201);
    },
  );
}
