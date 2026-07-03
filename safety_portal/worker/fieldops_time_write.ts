import type { FieldopsApp, FieldopsGates } from "./fieldops_gates";
import { auditStmt, auditStmtIfChanged, isUniqueViolation } from "./audit";
import { normalizeUsername } from "./auth";

// P2.3 Slice 1 — TIME-entry WRITE (the integrity-bar reference implementation).
// G2.3 — non-destructive AMEND/VOID (the /:uuid/amend route below; SPEC.md §2.3–2.4, §4.3–4.4).
//
// time_entries (migration 0015) is an INTEGRITY-BAR table: payroll/billing-grade, so a write
// here MUST follow all four rules:
//   1. RECORD time is server-authoritative — the INSERT OMITS created_at/edited_at so the schema
//      DEFAULT (unixepoch()) fires; a forged body timestamp is ignored. EVENT time
//      (work_started_at/work_ended_at) is a field-reported claim, stored verbatim, never authoritative.
//   2. APPEND-ONLY edit chain — an edit is a NEW row whose amends_uuid points at the prior uuid;
//      the original row is NEVER mutated. G2.3: the ONLY way to chain is the amend route, which
//      enforces recorder-or-manager authorship, head-only, same-job inheritance, and the void rule;
//      the create route REJECTS a body amends_uuid (400 use_amend_route) — the old raw pass-through
//      let any cap.time.log holder chain onto ANY uuid (dangling / cross-job / non-head / another
//      user's entry), silently bypassing every chain rule. No SPA caller ever sent it.
//   3. DUAL attribution — actor_username = the authenticated session user; submitted_as = the
//      attributed account (equals actor on a self-submit). Forging submitted_as != actor needs cap.submit_as.
//      An AMEND inherits the target's submitted_as (correcting the record doesn't re-attribute the
//      WORK) and stamps the corrector as actor_username.
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
      const submittedAs = asStr("submitted_as");
      const personnelId = asNum("personnel_id");
      const taskId = asNum("task_id");
      const workStartedAt = asNum("work_started_at");
      const workEndedAt = asNum("work_ended_at");
      const hours = asNum("hours");

      // G2.3 — chaining moved to POST /time-entry/:uuid/amend (head-only + authorship + same-job
      // enforced there). A body amends_uuid here is a category error, rejected loudly rather than
      // silently ignored so no client believes it amended via this route (see module header, rule 2).
      if (body.amends_uuid !== undefined) return c.json({ error: "use_amend_route" }, 400);

      if (!uuid || uuid.length > MAX_ID) return c.json({ error: "invalid_uuid" }, 400);
      if (!jobId || jobId.length > MAX_ID) return c.json({ error: "invalid_job_id" }, 400);
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

      // (4) MUTATION + AUDIT in ONE atomic batch. INSERT omits created_at/edited_at → server DEFAULT.
      try {
        await c.env.DB.batch([
          c.env.DB
            .prepare(
              `INSERT INTO time_entries
                 (uuid, job_id, personnel_id, task_id, work_started_at, work_ended_at, hours, notes,
                  actor_username, submitted_as, amends_uuid)
               VALUES (?1,?2,?3,?4,?5,?6,?7,?8,?9,?10,NULL)`,
            )
            .bind(uuid, jobId, personnelId, taskId, workStartedAt, workEndedAt, hours, notes, actor, attributed),
          auditStmt(c, actor, "time_entry_create", attributed, { uuid, job_id: jobId }),
        ]);
      } catch (e) {
        if (isUniqueViolation(e)) return c.json({ error: "uuid_conflict" }, 409); // replay dedupe
        throw e; // real DB error — surface, never silently swallow
      }

      return c.json({ ok: true, uuid }, 201);
    },
  );

  // ── G2.3 — POST /api/fieldops/time-entry/:uuid/amend — NON-DESTRUCTIVE correction/void ──────────
  // Creates a NEW row (fresh client uuid, amends_uuid = the target, job_id INHERITED from the
  // target — never client-chosen) carrying the FULL corrected entry; the target row is never
  // mutated. Full-replacement body: an omitted personnel_id/task_id means job-level, NOT "keep
  // old" (the SPA prefills the form from the old row). SPEC.md §2.3–2.4 / §4.4.
  //
  //   WHO   — the ORIGINAL RECORDER (target.actor_username === session actor), subject to their
  //           own live personnel scoping for the corrected subject, OR any cap.personnel.manage
  //           holder (manager/admin). Anyone else → 403 forbidden_amend.
  //   HEAD  — only the head of a chain can be amended. The probe is FOLDED INTO the INSERT
  //           (INSERT … SELECT … WHERE NOT EXISTS(… amends_uuid = target)) so it is atomic with
  //           the write: two concurrent amends of one head cannot fork the chain — the loser's
  //           SELECT matches nothing → changes()=0 → 409 not_head. (NOT EXISTS, never NOT IN —
  //           the NULL-poisoning class.)
  //   VOID  — hours = 0 + a REQUIRED non-empty reason (rides `notes`; no schema column needed).
  //           So amend bounds are [0, MAX_HOURS] where create's are (0, MAX_HOURS].
  //   JOB   — the target's job need NOT still be active: the epic exists because a wrong entry
  //           "can't be corrected by anyone", and a closed-job block would recreate exactly that.
  //           Safe: the job binding is inherited (no new placement on a closed job is possible)
  //           and every amend is chained + audited. Deliberate divergence from create.
  app.post(
    "/api/fieldops/time-entry/:uuid/amend",
    gates.requireSession,
    gates.requireCapability("cap.time.log"),
    async (c) => {
      const targetUuid = c.req.param("uuid") ?? "";
      if (!targetUuid || targetUuid.length > MAX_ID) return c.json({ error: "invalid_uuid" }, 400);

      // (1) BODY GUARD — same discipline as create (Invariant 2: untrusted).
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
      const notes = asStr("notes");
      const personnelId = asNum("personnel_id");
      const taskId = asNum("task_id");
      const workStartedAt = asNum("work_started_at");
      const workEndedAt = asNum("work_ended_at");
      const hours = asNum("hours");

      if (!uuid || uuid.length > MAX_ID) return c.json({ error: "invalid_uuid" }, 400);
      if (notes !== null && notes.length > MAX_NOTES) return c.json({ error: "invalid_notes" }, 400);
      // No job_id (inherited), no submitted_as (inherited), no amends_uuid (the :uuid param IS the
      // target) — reject the latter two loudly so a confused client can't believe they took effect.
      if (body.job_id !== undefined) return c.json({ error: "invalid_job_id" }, 400);
      if (body.submitted_as !== undefined || body.amends_uuid !== undefined) {
        return c.json({ error: "bad_request" }, 400);
      }
      // Amend bounds are [0, MAX_HOURS] — 0 is the VOID and requires a reason (below).
      if (hours === null || hours < 0 || hours > MAX_HOURS) return c.json({ error: "invalid_hours" }, 422);
      if (hours === 0 && (notes === null || notes.trim() === "")) {
        return c.json({ error: "void_requires_reason" }, 422);
      }

      // (2) TARGET + WHO — the amended entry must exist; the actor must be its recorder or hold
      // cap.personnel.manage. The 404 precedes the 403 (an entry that doesn't exist is not an
      // authorization question; uuids are client-minted and non-secret, so no oracle concern).
      const target = await c.env.DB.prepare(
        "SELECT uuid, job_id, actor_username, submitted_as FROM time_entries WHERE uuid = ?1",
      )
        .bind(targetUuid)
        .first<{ uuid: string; job_id: string; actor_username: string; submitted_as: string | null }>();
      if (!target) return c.json({ error: "not_found" }, 404);

      const actor = c.get("session").username;
      const canManage = c.get("capabilities").has("cap.personnel.manage");
      if (!canManage && target.actor_username !== actor) {
        return c.json({ error: "forbidden_amend" }, 403);
      }

      // (3) REFERENTIAL guards on the CORRECTED subject — identical to create: an active roster
      // member, with the subcontractor {self, created-by} scoping for a non-manager actor; a task
      // must belong to the ENTRY'S job (inherited target.job_id, not a body value).
      if (personnelId !== null) {
        const person = await c.env.DB.prepare(
          "SELECT username, created_by FROM personnel WHERE id = ?1 AND active = 1",
        )
          .bind(personnelId)
          .first<{ username: string | null; created_by: string | null }>();
        if (!person) return c.json({ error: "unknown_personnel" }, 422);
        if (!canManage && person.username !== actor && person.created_by !== actor) {
          return c.json({ error: "forbidden_personnel" }, 403);
        }
      }
      if (taskId !== null) {
        const task = await c.env.DB.prepare("SELECT id FROM task_assignments WHERE id = ?1 AND job_id = ?2")
          .bind(taskId, target.job_id)
          .first<{ id: number }>();
        if (!task) return c.json({ error: "unknown_task" }, 422);
      }

      // (4) HEAD-FOLDED INSERT + conditional audit in ONE atomic batch (W4). created_at/edited_at
      // omitted → server unixepoch() (integrity-bar rule 1). submitted_as inherited (rule 3, G2.3).
      try {
        const res = await c.env.DB.batch([
          c.env.DB
            .prepare(
              `INSERT INTO time_entries
                 (uuid, job_id, personnel_id, task_id, work_started_at, work_ended_at, hours, notes,
                  actor_username, submitted_as, amends_uuid)
               SELECT ?1,?2,?3,?4,?5,?6,?7,?8,?9,?10,?11
               WHERE NOT EXISTS (SELECT 1 FROM time_entries WHERE amends_uuid = ?11)`,
            )
            .bind(
              uuid,
              target.job_id,
              personnelId,
              taskId,
              workStartedAt,
              workEndedAt,
              hours,
              notes,
              actor,
              target.submitted_as,
              target.uuid,
            ),
          auditStmtIfChanged(c, actor, "time_entry_edit", target.submitted_as ?? actor, {
            uuid,
            job_id: target.job_id,
            amends_uuid: target.uuid,
            void: hours === 0,
          }),
        ]);
        if ((res[0].meta.changes ?? 0) === 0) return c.json({ error: "not_head" }, 409);
      } catch (e) {
        if (isUniqueViolation(e)) return c.json({ error: "uuid_conflict" }, 409); // replay dedupe
        throw e;
      }

      return c.json({ ok: true, uuid }, 201);
    },
  );
}
