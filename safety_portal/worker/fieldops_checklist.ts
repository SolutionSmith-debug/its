import type { Context } from "hono";
import type { Env, Vars } from "./types";
import type { FieldopsApp, FieldopsGates } from "./fieldops_gates";
import { auditStmt } from "./audit";
import catalog from "../catalog.json";

// Assigned-Tasks tab (P4 field-ops feature) S2 — the checklist ENGINE + the admin per-job template
// editor. One templates→instances engine (spec Q6) serves the daily "Progress Report" checklist and
// (later, S6) the inspection library. S2 owns ONLY the template side: the global daily_default row +
// per-job job_override rows. Instance generation / completion / rollup are S3–S5. All routes are
// cap.checklist.manage (admin; seeded 0013), send-free (D1 only), bound-param, mutation+audit in one
// D1 batch — the same discipline as fieldops_task_write / fieldops_crew_assign.
//
// THE MERGE (load-bearing — GET /checklist/job/:job_id): a job's EFFECTIVE daily checklist =
//   [ daily_default items NOT suppressed by the job's override ] ∪ [ the job_override's own items ]
// ordered by seq. A job with no job_override row → just the daily_default items. Editing the default
// propagates to every un-overridden job; a per-job ADD = an item on the job_override template; a
// per-job REMOVE of a default item = a suppression marker (a job_override item whose
// suppresses_default_item_id points at the hidden default item). Computed, never stored.

const MAX_LABEL = 256;
const MAX_FORM_CODE = 64;
const MAX_SEQ = 100_000;
const MAX_COUNT = 100_000;
// S3 completion inputs (bounded per Invariant 2 — untrusted body).
const MAX_NOTE = 2000;
const MAX_PHOTO_REF = 256;
// S4 count completion: value_num upper bound (shares the MAX_COUNT ceiling used for target_count).
const MAX_VALUE = 100_000;
// S4 loop-closure sentinel — a form_linked/inspection item is closed by a matching SUBMISSION, not a
// human action, so its completed_by is this marker rather than a username.
const AUTO_COMPLETED_BY = "(auto)";
// Item types that auto-close on a matching submission (loop-closure) and CANNOT be manually completed.
const AUTO_CLOSE_TYPES = new Set(["form_linked", "inspection"]);
// S5 rollup — the Daily Report the daily checklist rolls up into (catalog 'daily-report' parent family;
// matches the daily_default seed's source_form_code). Used for the rolled_up_submission_uuid reconcile
// AND the assembled draft's form_code. A submission matches iff its form_code EQUALS this OR is a
// versioned variant (`|| '-v%'`) — the SAME family match as S4 loop-closure.
const DAILY_REPORT_FORM = "daily-report";
// S5 rollup-draft assembly — bound ceiling on the crew / equipment legs (a day's roster/equipment is
// small; this is a defensive cap, not a paginated surface).
const ROLLUP_LEG_CAP = 200;

const ITEM_TYPES = new Set(["form_linked", "manual_attest", "count", "inspection"]);
// form_code identifies the target form — required for the two form-bearing types.
const FORM_REQUIRED = new Set(["form_linked", "inspection"]);
// (R1) The REAL form catalog, bundled into the Worker build (same import index.ts already uses for
// publish validation). Checklist items store the PARENT family code (the loop-closure convention
// above), so item writes validate against the parent set — a free-text typo used to create an item
// the assignee could NEVER complete (no submission would ever family-match it). 422 unknown_form_code.
const CATALOG_PARENT_CODES: ReadonlySet<string> = new Set(
  (catalog as { parents: { parent_form_code: string }[] }).parents.map((p) => p.parent_form_code),
);
const CAP_CHECKLIST = "cap.checklist.manage";
// S3 surfacing + completion are the OWNER's tab (a placed manager), gated by cap.tasks.own — the same
// cap the "My Tasks" read (fieldops_tasks.ts) uses. Distinct from the admin authoring cap above.
const CAP_TASKS_OWN = "cap.tasks.own";
// S6 inspection-library: a template title bound + a due-date format (a Pacific calendar date, the same
// 'YYYY-MM-DD' shape the daily instance_date uses — no time component, no offset).
const MAX_TITLE = 256;
const DUE_DATE_RE = /^\d{4}-\d{2}-\d{2}$/;

// THE MERGE (S2, load-bearing) — a job's EFFECTIVE daily checklist =
//   [ daily_default items NOT suppressed by the job ] ∪ [ the job_override's own added items ], seq-ordered.
// ?1 = job_id, bound ONCE but referenced twice (positional re-use). Suppression markers
// (suppresses_default_item_id NOT NULL) are excluded from both legs. `origin` distinguishes a default
// (suppressable) item from an override (deletable) one for the editor UI.
// REUSED by BOTH the S2 per-job editor route (GET /checklist/job/:job_id) AND S3 daily-instance
// snapshot generation, so a generated instance captures exactly the items the editor shows.
const EFFECTIVE_MERGE_SQL = `
        SELECT di.id AS source_item_id, di.seq AS seq, di.item_type AS item_type, di.label AS label,
               di.form_code AS form_code, di.target_count AS target_count, di.config_json AS config_json,
               'default' AS origin
        FROM checklist_items di
        JOIN checklist_templates dt ON dt.id = di.template_id AND dt.kind = 'daily_default'
        WHERE di.suppresses_default_item_id IS NULL
          AND di.id NOT IN (
            SELECT s.suppresses_default_item_id
            FROM checklist_items s
            JOIN checklist_templates ot ON ot.id = s.template_id AND ot.kind = 'job_override' AND ot.job_id = ?1
            WHERE s.suppresses_default_item_id IS NOT NULL
          )
        UNION ALL
        SELECT oi.id AS source_item_id, oi.seq AS seq, oi.item_type AS item_type, oi.label AS label,
               oi.form_code AS form_code, oi.target_count AS target_count, oi.config_json AS config_json,
               'override' AS origin
        FROM checklist_items oi
        JOIN checklist_templates ot ON ot.id = oi.template_id AND ot.kind = 'job_override' AND ot.job_id = ?1
        WHERE oi.suppresses_default_item_id IS NULL
        ORDER BY seq ASC, source_item_id ASC
      `;

// A merged effective item (the row shape EFFECTIVE_MERGE_SQL yields), used by S3 snapshot generation.
interface MergedItem {
  source_item_id: number;
  seq: number;
  item_type: string;
  label: string | null;
  form_code: string | null;
  target_count: number | null;
  config_json: string | null;
}

interface ItemRow {
  id: number;
  seq: number;
  item_type: string;
  label: string | null;
  form_code: string | null;
  target_count: number | null;
  config_json: string | null;
}

// The normalized, validated item payload the write routes persist.
interface ParsedItem {
  seq: number;
  item_type: string;
  label: string;
  form_code: string | null;
  target_count: number | null;
  config_json: string | null;
}

// Parse + validate a checklist-item write body. Returns the normalized item or a JSON error Response.
// Shared by the add-default / edit-default / add-job routes so the bounds + type rules live in one place.
function parseItem(
  c: Context<{ Bindings: Env; Variables: Vars }>,
  body: Record<string, unknown>,
): ParsedItem | Response {
  const itemType = typeof body.item_type === "string" ? body.item_type : "";
  if (!ITEM_TYPES.has(itemType)) return c.json({ error: "invalid_item_type" }, 400);

  const label = typeof body.label === "string" ? body.label.trim() : "";
  if (label.length < 1 || label.length > MAX_LABEL) return c.json({ error: "invalid_label" }, 400);

  // seq optional (default 0); when present must be a non-negative integer within bounds.
  let seq = 0;
  if (body.seq !== undefined) {
    if (typeof body.seq !== "number" || !Number.isInteger(body.seq) || body.seq < 0 || body.seq > MAX_SEQ) {
      return c.json({ error: "invalid_seq" }, 400);
    }
    seq = body.seq;
  }

  // form_code required for form_linked / inspection; ignored (stored null) otherwise.
  let formCode: string | null = null;
  if (FORM_REQUIRED.has(itemType)) {
    formCode = typeof body.form_code === "string" ? body.form_code.trim() : "";
    if (formCode.length < 1 || formCode.length > MAX_FORM_CODE) return c.json({ error: "form_code_required" }, 400);
    // (R1) Must be a REAL catalog parent code — otherwise the item can never auto-close.
    if (!CATALOG_PARENT_CODES.has(formCode)) return c.json({ error: "unknown_form_code" }, 422);
  }

  // target_count required for count; ignored (stored null) otherwise.
  let targetCount: number | null = null;
  if (itemType === "count") {
    const tc = body.target_count;
    if (typeof tc !== "number" || !Number.isInteger(tc) || tc < 1 || tc > MAX_COUNT) {
      return c.json({ error: "invalid_target_count" }, 400);
    }
    targetCount = tc;
  }

  return { seq, item_type: itemType, label, form_code: formCode, target_count: targetCount, config_json: null };
}

// Parse the JSON body, rejecting a non-object (null/array/primitive) before any property access —
// mirrors the guard in fieldops_task_write.
async function readBody(
  c: Context<{ Bindings: Env; Variables: Vars }>,
): Promise<Record<string, unknown> | Response> {
  let body: unknown;
  try {
    body = await c.req.json();
  } catch {
    return c.json({ error: "bad_request" }, 400);
  }
  if (typeof body !== "object" || body === null || Array.isArray(body)) return c.json({ error: "bad_request" }, 400);
  return body as Record<string, unknown>;
}

// Resolve the single global daily_default template id (seeded id=1 in migration 0026; resolved by
// query rather than hardcoded so the seed's id is not load-bearing). Null only if the seed is missing.
async function getDailyDefaultTemplateId(c: Context<{ Bindings: Env; Variables: Vars }>): Promise<number | null> {
  const row = await c.env.DB.prepare(
    "SELECT id FROM checklist_templates WHERE kind = 'daily_default' ORDER BY id ASC LIMIT 1",
  ).first<{ id: number }>();
  return row?.id ?? null;
}

// Get-or-create the job's single job_override template row, returning its id. The partial unique index
// (idx_checklist_templates_job_override) makes the INSERT OR IGNORE idempotent + race-safe: a lost
// check-then-act race is swallowed and the follow-up SELECT reads the winner's row.
async function ensureJobOverrideTemplate(
  c: Context<{ Bindings: Env; Variables: Vars }>,
  jobId: string,
): Promise<number> {
  await c.env.DB.prepare(
    "INSERT OR IGNORE INTO checklist_templates (kind, job_id, active) VALUES ('job_override', ?1, 1)",
  )
    .bind(jobId)
    .run();
  const row = await c.env.DB.prepare(
    "SELECT id FROM checklist_templates WHERE kind = 'job_override' AND job_id = ?1",
  )
    .bind(jobId)
    .first<{ id: number }>();
  return row!.id;
}

// job must exist (active or not — a checklist can be authored on any real job). Returns the error
// Response, or null on success.
async function requireJob(
  c: Context<{ Bindings: Env; Variables: Vars }>,
  jobId: string,
): Promise<Response | null> {
  if (jobId.length < 1 || jobId.length > 64) return c.json({ error: "invalid_job_id" }, 400);
  const job = await c.env.DB.prepare("SELECT job_id FROM jobs WHERE job_id = ?1").bind(jobId).first();
  if (!job) return c.json({ error: "not_found" }, 404);
  return null;
}

// ── S6 generic-inspection library (a MANY-template generalization of the S2 single daily_default) ──
// The S2 item CRUD was scoped to the ONE daily_default template (getDailyDefaultTemplateId); S6 authors
// MANY generic_inspection templates and assigns them ad-hoc. The item write/edit/delete routes below
// reuse parseItem + the same batch(mutation, conditional-audit) shape — the only new thing is that the
// template_id is a caller-supplied library template rather than the singleton default.

// Load a generic_inspection template by id, or a JSON error Response (400 bad id / 404 unknown /
// 404 wrong-kind — a daily_default/job_override id is NOT a library template and must not be editable
// through the inspection routes). Returns the row's id + title + active on success.
async function requireGenericTemplate(
  c: Context<{ Bindings: Env; Variables: Vars }>,
  rawId: string,
): Promise<{ id: number; title: string | null; active: number } | Response> {
  const tplId = parseInt(rawId, 10);
  if (isNaN(tplId)) return c.json({ error: "invalid_id" }, 400);
  const row = await c.env.DB.prepare(
    "SELECT id, title, active FROM checklist_templates WHERE id = ?1 AND kind = 'generic_inspection'",
  )
    .bind(tplId)
    .first<{ id: number; title: string | null; active: number }>();
  if (!row) return c.json({ error: "not_found" }, 404);
  return row;
}

// ── S3 daily-instance generation (Worker-on-read) ──────────────────────────────────────────────────
// instance_date is the LOCAL (Pacific) calendar date 'YYYY-MM-DD'. The Worker's clock is UTC, so we
// format `now` in the America/Los_Angeles zone (Intl carries full IANA tz data on Workers) — otherwise
// a submission logged during the evening Pacific hours would land on the next UTC day and split "today"
// across two instances. One canonical "today" per Pacific work day keeps the UNIQUE key stable.
function pacificToday(now: Date = new Date()): string {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: "America/Los_Angeles",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(now);
  const part = (t: string) => parts.find((p) => p.type === t)?.value ?? "";
  return `${part("year")}-${part("month")}-${part("day")}`;
}

// Resolve the acting session → its linked personnel row (personnel.username == users.username; the
// nullable soft link from migration 0014). Returns the personnel id + current_job placement, or null
// when the account has no active linked personnel row. active=1 so a retired roster person can't own
// a live instance. LIMIT 1 on the (unconstrained) username link — deterministic lowest id.
async function resolveActorPersonnel(
  c: Context<{ Bindings: Env; Variables: Vars }>,
): Promise<{ id: number; current_job: string | null } | null> {
  const username = c.get("session").username;
  const row = await c.env.DB.prepare(
    "SELECT id, current_job FROM personnel WHERE username = ?1 AND active = 1 ORDER BY id ASC LIMIT 1",
  )
    .bind(username)
    .first<{ id: number; current_job: string | null }>();
  return row ?? null;
}

// (R1) WHY the daily section is empty — the three generation preconditions below, each mapped to a
// wire reason code so the UI can explain instead of showing a lying blank ("no tasks" vs "your
// account isn't linked" vs "you aren't placed on a job"). Returned by /checklist/mine as `reason`
// (null when an instance is returned).
type DailyEmptyReason = "not_manager" | "no_personnel_link" | "not_placed";

// Materialize TODAY's daily checklist instance for the logged-in user — MANAGER-ONLY, idempotent,
// send-free. Returns the instance id (existing or just-created) + its job/date, or the R1 reason
// code when the actor is NOT a placed manager (a submitter, or a manager with no current_job) → the
// tab shows no daily section, with the reason. Generation gates on THREE conditions, all required:
//   (1) the account role is 'manager' (read fresh from D1 by requireSession — c.get("role"));
//   (2) the account has an active LINKED personnel row (the daily instance's assignee); AND
//   (3) that personnel is PLACED on a job (personnel.current_job set) — the daily instance's job.
// Idempotency rests on the checklist_instances UNIQUE(kind, job_id, assignee_personnel_id,
// instance_date): INSERT OR IGNORE creates at most one row per (job, manager, Pacific-day). The
// EFFECTIVE-item snapshot into checklist_item_states runs ONLY when this call actually inserted
// (meta.changes === 1) — a re-open the same day returns the existing instance + its states with NO
// duplicate rows. The snapshot uses EFFECTIVE_MERGE_SQL (the SAME default⊕override merge the S2 editor
// shows) so a later template edit never mutates an in-flight instance (source_item_id records lineage).
async function generateDailyInstance(
  c: Context<{ Bindings: Env; Variables: Vars }>,
): Promise<{ instanceId: number; jobId: string; instanceDate: string } | { reason: DailyEmptyReason }> {
  if (c.get("role") !== "manager") return { reason: "not_manager" };
  const person = await resolveActorPersonnel(c);
  if (!person) return { reason: "no_personnel_link" };
  if (!person.current_job) return { reason: "not_placed" };
  const jobId = person.current_job;
  const personnelId = person.id;
  const today = pacificToday();

  const ins = await c.env.DB.prepare(
    "INSERT OR IGNORE INTO checklist_instances (kind, job_id, assignee_personnel_id, instance_date, status) VALUES ('daily', ?1, ?2, ?3, 'open')",
  )
    .bind(jobId, personnelId, today)
    .run();

  const inst = await c.env.DB.prepare(
    "SELECT id FROM checklist_instances WHERE kind='daily' AND job_id=?1 AND assignee_personnel_id=?2 AND instance_date=?3",
  )
    .bind(jobId, personnelId, today)
    .first<{ id: number }>();
  const instanceId = inst!.id;

  // FIRST creation only → snapshot the effective merged items. A lost INSERT-OR-IGNORE race (a second
  // concurrent read) sees changes()===0 and skips, reading the winner's states — no duplicates.
  if ((ins.meta.changes ?? 0) === 1) {
    const merged = await c.env.DB.prepare(EFFECTIVE_MERGE_SQL).bind(jobId).all<MergedItem>();
    const rows = merged.results ?? [];
    if (rows.length > 0) {
      await c.env.DB.batch(
        rows.map((it) =>
          c.env.DB
            .prepare(
              "INSERT INTO checklist_item_states (instance_id, source_item_id, item_type, label, form_code, target_count, status) VALUES (?1,?2,?3,?4,?5,?6,'open')",
            )
            .bind(instanceId, it.source_item_id, it.item_type, it.label, it.form_code, it.target_count),
        ),
      );
    }
  }
  return { instanceId, jobId, instanceDate: today };
}

// ── S4 loop-closure (form_linked / inspection auto-check) ──────────────────────────────────────────
// A form_linked / inspection item closes when a SUBMISSION exists for (this instance's job, the item's
// form-code FAMILY, this instance's date) — true loop-closure, not a manual action. The FAMILY match
// mirrors the catalog's parent→variant convention (catalog.json): the checklist item stores the PARENT
// form_code (e.g. 'daily-report') while a submission carries the versioned VARIANT (e.g.
// 'daily-report-v1'). So a submission matches iff its form_code EQUALS the item's parent form_code OR
// is a versioned variant of it (`= parent OR LIKE parent || '-v%'`). Form codes use hyphens (never `_`),
// so no LIKE metacharacter escaping is needed. The `-v` anchor keeps the wildcard precise: 'daily-report'
// matches 'daily-report-v1' but never 'daily-report-extra'.
//
// Runs on EVERY /checklist/mine read (idempotent): it only flips OPEN→done (WHERE status<>'done'), so an
// already-closed item — auto or manual — is untouched, and a submission never "un-closes" an item. The
// UPDATE is bound-param (job_id + instance_date passed positionally, not spliced) and correlates the
// EXISTS subquery on the target row's own form_code. Persisted (not computed) so S5's rollup + the
// instance-complete recompute observe the closure. ?1 = instance id, ?2 = job_id, ?3 = instance_date.
//
// (R1, spec Q3) DATE SEMANTICS split by instance kind. A DAILY item matches a submission filed ON the
// instance's day (`=` — "today's checklist closes on today's filings", unchanged). An INSPECTION
// instance's date is a DUE date, so a filing ON OR BEFORE it satisfies the item (`<=`) — before this,
// filing the linked form a day early left the inspection permanently open. `dateOp` is one of two
// module-CONSTANT literals (never caller/user input), so the template splice is bound-param-safe.
function buildAutoCheckSql(dateOp: "=" | "<="): string {
  return `
        UPDATE checklist_item_states
        SET status = 'done', completed_by = '${AUTO_COMPLETED_BY}', completed_at = unixepoch()
        WHERE instance_id = ?1
          AND item_type IN ('form_linked', 'inspection')
          AND status <> 'done'
          AND form_code IS NOT NULL
          AND EXISTS (
            SELECT 1 FROM submissions sub
            WHERE sub.job_id = ?2
              AND sub.work_date ${dateOp} ?3
              AND (sub.form_code = checklist_item_states.form_code
                   OR sub.form_code LIKE checklist_item_states.form_code || '-v%')
          )
      `;
}
const AUTO_CHECK_SQL_DAILY = buildAutoCheckSql("=");
const AUTO_CHECK_SQL_INSPECTION = buildAutoCheckSql("<=");

// S5 rolled-up linkage (reconcile — the SAME submission-existence pattern S4 uses for loop-closure).
// Stamp checklist_instances.rolled_up_submission_uuid with the day's Daily Report submission for this
// (job, date) — matched on the 'daily-report' PARENT FAMILY (= parent OR versioned variant, exactly the
// S4 form-code match). So once the manager files (or auto-files) the Daily Report, the instance shows it
// as rolled-up — no new stamp route needed. Set-ONCE (WHERE rolled_up_submission_uuid IS NULL) + guarded
// by EXISTS so a read with no daily-report submission is a pure no-op; picks the most-recent submission
// deterministically. Bound-param: ?1 = instance id, ?2 = job_id, ?3 = instance_date, ?4 = 'daily-report'.
const ROLLUP_LINK_SQL = `
        UPDATE checklist_instances
        SET rolled_up_submission_uuid = (
          SELECT sub.submission_uuid FROM submissions sub
          WHERE sub.job_id = ?2 AND sub.work_date = ?3
            AND (sub.form_code = ?4 OR sub.form_code LIKE ?4 || '-v%')
          ORDER BY sub.created_at DESC, sub.submission_uuid DESC
          LIMIT 1
        )
        WHERE id = ?1
          AND kind = 'daily'
          AND rolled_up_submission_uuid IS NULL
          AND EXISTS (
            SELECT 1 FROM submissions sub
            WHERE sub.job_id = ?2 AND sub.work_date = ?3
              AND (sub.form_code = ?4 OR sub.form_code LIKE ?4 || '-v%')
          )
      `;

// Reconcile the instance's form_linked/inspection items against the day's submissions (loop-closure),
// stamp the rolled-up Daily Report link (S5), then recompute the instance status — in ONE batch so a
// just-auto-closed item is reflected. Idempotent: re-running with no new submissions changes nothing
// (each UPDATE matches zero rows, the recompute re-writes the same status). Called each read of
// /checklist/mine, right after generation.
async function reconcileFormLinked(
  c: Context<{ Bindings: Env; Variables: Vars }>,
  instanceId: number,
  jobId: string,
  instanceDate: string,
  kind: "daily" | "inspection" = "daily",
): Promise<void> {
  // Q3: daily = exact-date match; inspection = on-or-before the due date. The rollup leg is a no-op
  // for inspection instances (its WHERE is kind='daily') — harmless to keep in the one batch.
  const autoCheckSql = kind === "inspection" ? AUTO_CHECK_SQL_INSPECTION : AUTO_CHECK_SQL_DAILY;
  await c.env.DB.batch([
    c.env.DB.prepare(autoCheckSql).bind(instanceId, jobId, instanceDate),
    c.env.DB.prepare(ROLLUP_LINK_SQL).bind(instanceId, jobId, instanceDate, DAILY_REPORT_FORM),
    recomputeInstanceStatusStmt(c, instanceId),
  ]);
}

// (R1) Item-state read with `filed_by` ATTRIBUTION for auto-closed items. An item whose
// completed_by = '(auto)' was closed by a SUBMISSION, not a person — this derives WHO filed it, so
// the UI can say "closed by Sam's Daily Report" instead of "(auto)". Best-effort by construction:
// the closure itself is an EXISTS (no submission id is stored), so we re-run the SAME family match
// and pick the most-recent matching submission, mapping its submitted_as → the personnel display
// name ONLY — no raw-username fallback (security review W9: a raw account id could surface an
// unrelated/orphaned identity to the assignee; an unmatched account yields NULL and the UI keeps
// its generic "(auto)" caption). `dateOp` mirrors the Q3 reconcile
// split (daily `=`, inspection `<=`) — module-constant literal, never caller input, bound-param
// otherwise. ?1 = instance id, ?2 = job_id, ?3 = instance_date (NULLs → filed_by NULL, never a row
// drop). ORDER BY id ASC == snapshot insertion order == the seq order the snapshot emitted.
function buildItemStatesSql(dateOp: "=" | "<="): string {
  return `
        SELECT id, source_item_id, item_type, label, form_code, target_count, status, note, photo_ref,
               completed_by, completed_at, value_num,
               CASE WHEN status = 'done' AND completed_by = '${AUTO_COMPLETED_BY}' AND form_code IS NOT NULL THEN (
                 SELECT (SELECT p.name FROM personnel p WHERE p.username = sub.submitted_as ORDER BY p.id ASC LIMIT 1)
                 FROM submissions sub
                 WHERE sub.job_id = ?2
                   AND sub.work_date ${dateOp} ?3
                   AND (sub.form_code = checklist_item_states.form_code
                        OR sub.form_code LIKE checklist_item_states.form_code || '-v%')
                 ORDER BY sub.created_at DESC, sub.submission_uuid DESC
                 LIMIT 1
               ) ELSE NULL END AS filed_by
        FROM checklist_item_states
        WHERE instance_id = ?1
        ORDER BY id ASC
      `;
}
const ITEM_STATES_SQL_DAILY = buildItemStatesSql("=");
const ITEM_STATES_SQL_INSPECTION = buildItemStatesSql("<=");

// Recompute-instance-status statement (built, not run): 'complete' iff NO item_state on the instance
// is still open, else 'open'. Appended LAST in a completion batch (after the item-state mutation +
// its audit), so the just-applied change is reflected. ?1 = instance id.
function recomputeInstanceStatusStmt(c: Context<{ Bindings: Env; Variables: Vars }>, instanceId: number) {
  return c.env.DB
    .prepare(
      "UPDATE checklist_instances SET status = CASE WHEN NOT EXISTS (SELECT 1 FROM checklist_item_states WHERE instance_id=?1 AND status<>'done') THEN 'complete' ELSE 'open' END WHERE id=?1",
    )
    .bind(instanceId);
}

// Load an item_state + its owning instance's assignee for the completion routes. Returns the row
// (incl. item_type + target_count so the caller can branch per-type), or a JSON error Response (404
// unknown, 403 not-your-instance). Ownership is scoped to the ACTOR's linked personnel id: a manager
// can only touch items on THEIR OWN daily instance. Per-type completability (manual_attest = check,
// count = value ≥ target, form_linked/inspection = auto-close-only reject) is decided by the CALLER
// (S4), not here — this only enforces existence + ownership.
async function loadOwnedItemState(
  c: Context<{ Bindings: Env; Variables: Vars }>,
  stateId: number,
): Promise<{ id: number; instance_id: number; item_type: string; target_count: number | null } | Response> {
  const person = await resolveActorPersonnel(c);
  // No linked personnel → the actor owns no instance → forbidden (not 404: the row may well exist).
  if (!person) return c.json({ error: "forbidden" }, 403);
  const st = await c.env.DB.prepare(
    "SELECT s.id, s.item_type, s.target_count, s.instance_id, i.assignee_personnel_id FROM checklist_item_states s JOIN checklist_instances i ON i.id = s.instance_id WHERE s.id = ?1",
  )
    .bind(stateId)
    .first<{ id: number; item_type: string; target_count: number | null; instance_id: number; assignee_personnel_id: number | null }>();
  if (!st) return c.json({ error: "not_found" }, 404);
  if (st.assignee_personnel_id !== person.id) return c.json({ error: "forbidden" }, 403);
  return { id: st.id, instance_id: st.instance_id, item_type: st.item_type, target_count: st.target_count };
}

export function registerChecklistRoutes(app: FieldopsApp, gates: FieldopsGates): void {
  // ── GET /api/fieldops/checklist/default — the daily_default template + its items (the editor's
  // "edit the default" surface). ───────────────────────────────────────────────────────────────────
  app.get(
    "/api/fieldops/checklist/default",
    gates.requireSession,
    gates.requireCapability(CAP_CHECKLIST),
    async (c) => {
      const tplId = await getDailyDefaultTemplateId(c);
      if (tplId === null) return c.json({ template: null, items: [] }, 200);
      const tpl = await c.env.DB.prepare(
        "SELECT id, kind, title, source_form_code, active FROM checklist_templates WHERE id = ?1",
      )
        .bind(tplId)
        .first();
      const items = await c.env.DB.prepare(
        "SELECT id, seq, item_type, label, form_code, target_count, config_json FROM checklist_items WHERE template_id = ?1 AND suppresses_default_item_id IS NULL ORDER BY seq ASC, id ASC",
      )
        .bind(tplId)
        .all<ItemRow>();
      return c.json({ template: tpl, items: items.results ?? [] }, 200);
    },
  );

  // ── POST /api/fieldops/checklist/default/item — add an item to the daily_default template. ────────
  app.post(
    "/api/fieldops/checklist/default/item",
    gates.requireSession,
    gates.requireCapability(CAP_CHECKLIST),
    async (c) => {
      const body = await readBody(c);
      if (body instanceof Response) return body;
      const item = parseItem(c, body);
      if (item instanceof Response) return item;
      const tplId = await getDailyDefaultTemplateId(c);
      if (tplId === null) return c.json({ error: "no_default_template" }, 409);
      const actor = c.get("session").username;
      const res = await c.env.DB.batch([
        c.env.DB
          .prepare(
            "INSERT INTO checklist_items (template_id, seq, item_type, label, form_code, target_count, config_json) VALUES (?1,?2,?3,?4,?5,?6,?7) RETURNING id",
          )
          .bind(tplId, item.seq, item.item_type, item.label, item.form_code, item.target_count, item.config_json),
        auditStmt(c, actor, "checklist_default_item_add", String(tplId), { ...item }),
      ]);
      const newId = (res[0].results?.[0] as { id: number } | undefined)?.id ?? null;
      return c.json({ ok: true, id: newId }, 201);
    },
  );

  // ── POST /api/fieldops/checklist/default/item/:item_id/edit — replace a default item's fields. ────
  app.post(
    "/api/fieldops/checklist/default/item/:item_id/edit",
    gates.requireSession,
    gates.requireCapability(CAP_CHECKLIST),
    async (c) => {
      const itemId = parseInt(c.req.param("item_id"), 10);
      if (isNaN(itemId)) return c.json({ error: "invalid_id" }, 400);
      const body = await readBody(c);
      if (body instanceof Response) return body;
      const item = parseItem(c, body);
      if (item instanceof Response) return item;
      const tplId = await getDailyDefaultTemplateId(c);
      if (tplId === null) return c.json({ error: "no_default_template" }, 409);
      const actor = c.get("session").username;
      // Scope the UPDATE to the default template + real content rows (suppresses_default_item_id IS
      // NULL) so this can't rewrite a suppression marker or a job_override item. changes()=0 → 404.
      const res = await c.env.DB.batch([
        c.env.DB
          .prepare(
            "UPDATE checklist_items SET seq=?2, item_type=?3, label=?4, form_code=?5, target_count=?6, config_json=?7 WHERE id=?1 AND template_id=?8 AND suppresses_default_item_id IS NULL",
          )
          .bind(itemId, item.seq, item.item_type, item.label, item.form_code, item.target_count, item.config_json, tplId),
        c.env.DB
          .prepare(
            "INSERT INTO audit_log (actor_username, action, target_username, detail) SELECT ?1,?2,?3,?4 WHERE changes()=1",
          )
          .bind(actor, "checklist_default_item_edit", String(itemId), JSON.stringify({ item_id: itemId, ...item })),
      ]);
      if ((res[0].meta.changes ?? 0) === 0) return c.json({ error: "not_found" }, 404);
      return c.json({ ok: true, id: itemId }, 200);
    },
  );

  // ── POST /api/fieldops/checklist/default/item/:item_id/delete — remove a default item (+ any
  // job_override suppression markers pointing at it, so no orphaned markers linger). ─────────────────
  app.post(
    "/api/fieldops/checklist/default/item/:item_id/delete",
    gates.requireSession,
    gates.requireCapability(CAP_CHECKLIST),
    async (c) => {
      const itemId = parseInt(c.req.param("item_id"), 10);
      if (isNaN(itemId)) return c.json({ error: "invalid_id" }, 400);
      const tplId = await getDailyDefaultTemplateId(c);
      if (tplId === null) return c.json({ error: "no_default_template" }, 409);
      const actor = c.get("session").username;
      // (W4) SQLite changes() reflects only the LAST completed statement, and the conditional audit
      // must gate on the ITEM delete — so run the (unaudited) orphan-marker cleanup FIRST, leaving the
      // item DELETE as the statement immediately before the audit INSERT. Matches the canonical
      // single-mutation-then-changes()=1-audit shape used across the worker.
      const res = await c.env.DB.batch([
        c.env.DB.prepare("DELETE FROM checklist_items WHERE suppresses_default_item_id = ?1").bind(itemId),
        c.env.DB
          .prepare("DELETE FROM checklist_items WHERE id=?1 AND template_id=?2 AND suppresses_default_item_id IS NULL")
          .bind(itemId, tplId),
        c.env.DB
          .prepare(
            "INSERT INTO audit_log (actor_username, action, target_username, detail) SELECT ?1,?2,?3,?4 WHERE changes()=1",
          )
          .bind(actor, "checklist_default_item_delete", String(itemId), JSON.stringify({ item_id: itemId })),
      ]);
      if ((res[1].meta.changes ?? 0) === 0) return c.json({ error: "not_found" }, 404);
      return c.json({ ok: true, id: itemId }, 200);
    },
  );

  // ── GET /api/fieldops/checklist/job/:job_id — the job's EFFECTIVE merged daily checklist. ─────────
  // This is what the per-job editor renders: default items (minus this job's suppressions) ∪ the job's
  // own added items, ordered by seq. Also returns `suppressed` (the default items currently hidden for
  // this job) so the editor can offer an "unhide" affordance.
  app.get(
    "/api/fieldops/checklist/job/:job_id",
    gates.requireSession,
    gates.requireCapability(CAP_CHECKLIST),
    async (c) => {
      const jobId = c.req.param("job_id");
      const jobErr = await requireJob(c, jobId);
      if (jobErr) return jobErr;

      // THE MERGE (EFFECTIVE_MERGE_SQL, module-level — also reused by S3 snapshot generation). The
      // default leg excludes any default item whose id is in this job's override suppression set; the
      // override leg adds the job's own content items. origin distinguishes default (suppressable)
      // from override (deletable) for the UI.
      const merged = await c.env.DB.prepare(EFFECTIVE_MERGE_SQL).bind(jobId).all();

      // The default items this job currently hides (so the editor can offer "unhide").
      const suppressedSql = `
        SELECT di.id AS source_item_id, di.seq AS seq, di.item_type AS item_type, di.label AS label,
               di.form_code AS form_code, di.target_count AS target_count
        FROM checklist_items di
        JOIN checklist_templates dt ON dt.id = di.template_id AND dt.kind = 'daily_default'
        WHERE di.id IN (
          SELECT s.suppresses_default_item_id
          FROM checklist_items s
          JOIN checklist_templates ot ON ot.id = s.template_id AND ot.kind = 'job_override' AND ot.job_id = ?1
          WHERE s.suppresses_default_item_id IS NOT NULL
        )
        ORDER BY di.seq ASC, di.id ASC
      `;
      const suppressed = await c.env.DB.prepare(suppressedSql).bind(jobId).all();

      return c.json({ job_id: jobId, items: merged.results ?? [], suppressed: suppressed.results ?? [] }, 200);
    },
  );

  // ── POST /api/fieldops/checklist/job/:job_id/item — add a job-specific item (creates the
  // job_override template lazily on first customization). ───────────────────────────────────────────
  app.post(
    "/api/fieldops/checklist/job/:job_id/item",
    gates.requireSession,
    gates.requireCapability(CAP_CHECKLIST),
    async (c) => {
      const jobId = c.req.param("job_id");
      const jobErr = await requireJob(c, jobId);
      if (jobErr) return jobErr;
      const body = await readBody(c);
      if (body instanceof Response) return body;
      const item = parseItem(c, body);
      if (item instanceof Response) return item;
      const tplId = await ensureJobOverrideTemplate(c, jobId);
      const actor = c.get("session").username;
      const res = await c.env.DB.batch([
        c.env.DB
          .prepare(
            "INSERT INTO checklist_items (template_id, seq, item_type, label, form_code, target_count, config_json) VALUES (?1,?2,?3,?4,?5,?6,?7) RETURNING id",
          )
          .bind(tplId, item.seq, item.item_type, item.label, item.form_code, item.target_count, item.config_json),
        auditStmt(c, actor, "checklist_job_item_add", jobId, { job_id: jobId, ...item }),
      ]);
      const newId = (res[0].results?.[0] as { id: number } | undefined)?.id ?? null;
      return c.json({ ok: true, id: newId }, 201);
    },
  );

  // ── POST /api/fieldops/checklist/job/:job_id/item/:override_item_id/delete — remove one of the
  // job's OWN added items (not a default — defaults are hidden via /suppress). ──────────────────────
  app.post(
    "/api/fieldops/checklist/job/:job_id/item/:override_item_id/delete",
    gates.requireSession,
    gates.requireCapability(CAP_CHECKLIST),
    async (c) => {
      const jobId = c.req.param("job_id");
      const jobErr = await requireJob(c, jobId);
      if (jobErr) return jobErr;
      const itemId = parseInt(c.req.param("override_item_id"), 10);
      if (isNaN(itemId)) return c.json({ error: "invalid_id" }, 400);
      const actor = c.get("session").username;
      // Scope to this job's override template + a real content row (not a suppression marker), so a
      // manager can't delete another job's item or a default item through this route. changes()=0 → 404.
      const res = await c.env.DB.batch([
        c.env.DB
          .prepare(
            "DELETE FROM checklist_items WHERE id=?1 AND suppresses_default_item_id IS NULL AND template_id IN (SELECT id FROM checklist_templates WHERE kind='job_override' AND job_id=?2)",
          )
          .bind(itemId, jobId),
        c.env.DB
          .prepare(
            "INSERT INTO audit_log (actor_username, action, target_username, detail) SELECT ?1,?2,?3,?4 WHERE changes()=1",
          )
          .bind(actor, "checklist_job_item_delete", jobId, JSON.stringify({ job_id: jobId, item_id: itemId })),
      ]);
      if ((res[0].meta.changes ?? 0) === 0) return c.json({ error: "not_found" }, 404);
      return c.json({ ok: true, id: itemId }, 200);
    },
  );

  // ── POST /api/fieldops/checklist/job/:job_id/item/:default_item_id/suppress — hide a default item
  // for this job (writes a suppression marker on the job_override template). ─────────────────────────
  app.post(
    "/api/fieldops/checklist/job/:job_id/item/:default_item_id/suppress",
    gates.requireSession,
    gates.requireCapability(CAP_CHECKLIST),
    async (c) => {
      const jobId = c.req.param("job_id");
      const jobErr = await requireJob(c, jobId);
      if (jobErr) return jobErr;
      const defaultItemId = parseInt(c.req.param("default_item_id"), 10);
      if (isNaN(defaultItemId)) return c.json({ error: "invalid_id" }, 400);

      // The target must be a real content item on the daily_default template (not a marker, not a
      // job item) — else there's nothing meaningful to hide.
      const defTplId = await getDailyDefaultTemplateId(c);
      if (defTplId === null) return c.json({ error: "no_default_template" }, 409);
      const target = await c.env.DB.prepare(
        "SELECT id FROM checklist_items WHERE id=?1 AND template_id=?2 AND suppresses_default_item_id IS NULL",
      )
        .bind(defaultItemId, defTplId)
        .first();
      if (!target) return c.json({ error: "not_found" }, 404);

      const tplId = await ensureJobOverrideTemplate(c, jobId);
      // Idempotent: skip if this job already suppresses the item (no duplicate marker). The merge's
      // NOT IN would dedupe anyway, but a single marker keeps unsuppress a clean single-row delete.
      const existing = await c.env.DB.prepare(
        "SELECT id FROM checklist_items WHERE template_id=?1 AND suppresses_default_item_id=?2",
      )
        .bind(tplId, defaultItemId)
        .first();
      if (existing) return c.json({ ok: true, suppressed: defaultItemId, already: true }, 200);

      const actor = c.get("session").username;
      await c.env.DB.batch([
        c.env.DB
          .prepare(
            "INSERT INTO checklist_items (template_id, seq, item_type, label, suppresses_default_item_id) VALUES (?1, 0, 'manual_attest', '(suppressed)', ?2)",
          )
          .bind(tplId, defaultItemId),
        auditStmt(c, actor, "checklist_job_suppress", jobId, { job_id: jobId, default_item_id: defaultItemId }),
      ]);
      return c.json({ ok: true, suppressed: defaultItemId }, 201);
    },
  );

  // ── POST /api/fieldops/checklist/job/:job_id/item/:default_item_id/unsuppress — un-hide a default
  // item for this job (drops the suppression marker). ───────────────────────────────────────────────
  app.post(
    "/api/fieldops/checklist/job/:job_id/item/:default_item_id/unsuppress",
    gates.requireSession,
    gates.requireCapability(CAP_CHECKLIST),
    async (c) => {
      const jobId = c.req.param("job_id");
      const jobErr = await requireJob(c, jobId);
      if (jobErr) return jobErr;
      const defaultItemId = parseInt(c.req.param("default_item_id"), 10);
      if (isNaN(defaultItemId)) return c.json({ error: "invalid_id" }, 400);
      const actor = c.get("session").username;
      const res = await c.env.DB.batch([
        c.env.DB
          .prepare(
            "DELETE FROM checklist_items WHERE suppresses_default_item_id=?1 AND template_id IN (SELECT id FROM checklist_templates WHERE kind='job_override' AND job_id=?2)",
          )
          .bind(defaultItemId, jobId),
        c.env.DB
          .prepare(
            "INSERT INTO audit_log (actor_username, action, target_username, detail) SELECT ?1,?2,?3,?4 WHERE changes()=1",
          )
          .bind(actor, "checklist_job_unsuppress", jobId, JSON.stringify({ job_id: jobId, default_item_id: defaultItemId })),
      ]);
      if ((res[0].meta.changes ?? 0) === 0) return c.json({ error: "not_found" }, 404);
      return c.json({ ok: true, unsuppressed: defaultItemId }, 200);
    },
  );

  // ══ S3 — daily-checklist SURFACING + manual_attest COMPLETION (cap.tasks.own — the owner's tab) ══

  // ── GET /api/fieldops/checklist/mine — TODAY's daily checklist for the logged-in placed manager. ──
  // Runs generation on read (materializes + snapshots the instance if absent, idempotent on the UNIQUE
  // key). Returns { instance: {id, job_id, project_name, instance_date, status, …} | null, items: [...],
  // reason }. `instance` is NULL (empty daily section) when the actor isn't a placed manager (a
  // submitter, or an unplaced one) — with the R1 `reason` code ('not_manager' | 'no_personnel_link' |
  // 'not_placed') so the UI can explain WHY; reason is null when an instance is returned.
  app.get(
    "/api/fieldops/checklist/mine",
    gates.requireSession,
    gates.requireCapability(CAP_TASKS_OWN),
    async (c) => {
      const gen = await generateDailyInstance(c);
      if ("reason" in gen) return c.json({ instance: null, items: [], reason: gen.reason }, 200);
      // S4 loop-closure: reconcile form_linked/inspection items against the day's submissions BEFORE
      // reading them back, so a form filed since the last open shows as done (and the instance status
      // reflects it). Idempotent on re-read; persisted so S5's rollup sees the closure.
      await reconcileFormLinked(c, gen.instanceId, gen.jobId, gen.instanceDate, "daily");
      // rolled_up_submission_uuid (S5): non-null once a Daily Report has been filed for this job+date
      // (set by the reconcile above). The SPA shows "Daily Report filed ✓" when present, else the
      // "Review & file" affordance on a complete instance. (R1) project_name joined (a raw job id is
      // not a heading) + rolled_up_by = WHO filed the rolled-up Daily Report (personnel display name
      // ONLY — no raw-username fallback, per security review W9; unmatched account → NULL).
      const instance = await c.env.DB.prepare(
        `SELECT i.id, i.job_id, j.project_name, i.instance_date, i.status, i.rolled_up_submission_uuid,
                (SELECT (SELECT p.name FROM personnel p WHERE p.username = sub.submitted_as ORDER BY p.id ASC LIMIT 1)
                 FROM submissions sub WHERE sub.submission_uuid = i.rolled_up_submission_uuid) AS rolled_up_by
         FROM checklist_instances i
         LEFT JOIN jobs j ON j.job_id = i.job_id
         WHERE i.id = ?1`,
      )
        .bind(gen.instanceId)
        .first();
      // Item states + R1 filed_by attribution (daily = exact-date submission match).
      const items = await c.env.DB.prepare(ITEM_STATES_SQL_DAILY)
        .bind(gen.instanceId, gen.jobId, gen.instanceDate)
        .all();
      return c.json({ instance, items: items.results ?? [], reason: null }, 200);
    },
  );

  // ══ S5 — auto-rollup → Daily Report (assemble a DRAFT; the manager reviews/confirms + files) ══════
  // ── GET /api/fieldops/checklist/mine/rollup-draft — a best-effort Daily Report draft assembled from
  // the day's data, returned ONLY when the actor's daily instance is COMPLETE (else 409). READ-ONLY
  // (no INSERT/UPDATE): it never materializes an instance — a complete instance already exists (the
  // manager only reaches this after /mine showed it complete). The manager reviews/edits the draft in
  // the prefilled Daily Report form and files it via the existing /api/submit path (send-free; the Mac
  // intake files to Box/Smartsheet). The filed submission then stamps rolled_up_submission_uuid via the
  // /mine reconcile — NO send path here (Invariant 1). Data → daily-report-v1 fields:
  //   • header.job_name    ← the job's project_name (fallback: job_id)
  //   • header.report_date ← the instance date
  //   • header.prepared_by ← the placed manager's personnel name
  //   • crew_progress[]    ← the crew placed on the job (name/trade seeded; manpower + progress BLANK)
  //   • equipment_on_site[]← the equipment currently on the job (type seeded; owner/inspector BLANK)
  //   • comments           ← a FACTUAL (non-narrative) checklist + filed-forms summary
  // Narrative/subjective fields (weather, temp, tomorrow's goals, deliveries, visitors, and every
  // per-row detail column) are LEFT BLANK — the SPA merges the draft over the form's empty defaults, so
  // an omitted key stays empty for the manager to fill. Nothing is fabricated. Bound-param throughout.
  app.get(
    "/api/fieldops/checklist/mine/rollup-draft",
    gates.requireSession,
    gates.requireCapability(CAP_TASKS_OWN),
    async (c) => {
      // Same gate as generation: manager role + an active linked personnel PLACED on a job.
      if (c.get("role") !== "manager") return c.json({ error: "no_instance" }, 409);
      const person = await resolveActorPersonnel(c);
      if (!person || !person.current_job) return c.json({ error: "no_instance" }, 409);
      const jobId = person.current_job;
      const today = pacificToday();

      const inst = await c.env.DB.prepare(
        "SELECT id, status FROM checklist_instances WHERE kind='daily' AND job_id=?1 AND assignee_personnel_id=?2 AND instance_date=?3",
      )
        .bind(jobId, person.id, today)
        .first<{ id: number; status: string }>();
      // No instance yet, or not COMPLETE → nothing to roll up (the button only shows on a complete one).
      if (!inst || inst.status !== "complete") return c.json({ error: "not_complete" }, 409);

      // Job project name + the placed manager's personnel name (the "Prepared By").
      const [jobRow, mgrRow] = await c.env.DB.batch([
        c.env.DB.prepare("SELECT project_name FROM jobs WHERE job_id=?1").bind(jobId),
        c.env.DB.prepare("SELECT name FROM personnel WHERE id=?1").bind(person.id),
      ]);
      const projectName = (jobRow.results?.[0] as { project_name: string | null } | undefined)?.project_name ?? null;
      const managerName = (mgrRow.results?.[0] as { name: string | null } | undefined)?.name ?? "";

      // Crew placed on the job + equipment currently on the job (the jobtracker equipment-on-site
      // window: candidates ever on the job → each one's latest read → kept iff still THIS job) + the
      // instance's item states (for the summary) + the day's filed form families.
      const equipSql = `
        SELECT e.name, e.identifier
        FROM (
          SELECT equipment_id, job_id,
                 ROW_NUMBER() OVER (PARTITION BY equipment_id ORDER BY recorded_at DESC, id DESC) rn
          FROM equipment_location
          WHERE equipment_id IN (SELECT DISTINCT equipment_id FROM equipment_location WHERE job_id = ?1)
        ) loc JOIN equipment e ON e.id = loc.equipment_id
        WHERE loc.rn = 1 AND loc.job_id = ?1
        ORDER BY e.name ASC
        LIMIT ?2
      `;
      const [crewRes, equipRes, stateRes, subRes] = await c.env.DB.batch([
        c.env.DB.prepare("SELECT name, trade FROM personnel WHERE current_job=?1 AND active=1 ORDER BY name ASC LIMIT ?2").bind(jobId, ROLLUP_LEG_CAP),
        c.env.DB.prepare(equipSql).bind(jobId, ROLLUP_LEG_CAP),
        c.env.DB.prepare("SELECT label, item_type, status, value_num FROM checklist_item_states WHERE instance_id=?1 ORDER BY id ASC").bind(inst.id),
        c.env.DB.prepare("SELECT DISTINCT form_code FROM submissions WHERE job_id=?1 AND work_date=?2 ORDER BY form_code ASC").bind(jobId, today),
      ]);
      const crew = (crewRes.results ?? []) as { name: string | null; trade: string | null }[];
      const equipment = (equipRes.results ?? []) as { name: string | null; identifier: string | null }[];
      const states = (stateRes.results ?? []) as { label: string | null; item_type: string; status: string; value_num: number | null }[];
      const filedForms = ((subRes.results ?? []) as { form_code: string }[]).map((r) => r.form_code);

      // FACTUAL (non-narrative) summary: checklist item outcomes + count values + which form families
      // were filed today. Data-derived — NOT fabricated prose. The manager edits it before filing.
      const itemLines = states
        .map((s) => {
          const cnt = s.item_type === "count" && s.value_num !== null ? ` (recorded ${s.value_num})` : "";
          return `- ${s.label ?? "(item)"}: ${s.status}${cnt}`;
        })
        .join("\n");
      const comments =
        `Daily checklist completed for ${projectName ?? jobId} (${jobId}) on ${today}.\n` +
        `Forms filed today: ${filedForms.length ? filedForms.join(", ") : "none"}.\n` +
        (itemLines ? `Checklist items:\n${itemLines}` : "Checklist items: none.");

      const values: Record<string, unknown> = {
        job_name: projectName ?? jobId,
        report_date: today,
        prepared_by: managerName,
        comments,
      };
      // Only seed repeating tables that have data; an omitted key falls back to the form's empty default
      // row (see the SPA merge) so we never fabricate blank crew/equipment rows.
      if (crew.length > 0) {
        values.crew_progress = crew.map((p) => ({
          crew_subcontractor: p.trade ? `${p.name ?? ""} (${p.trade})` : (p.name ?? ""),
          manpower: "",
          todays_progress: "",
        }));
      }
      if (equipment.length > 0) {
        values.equipment_on_site = equipment.map((e) => ({
          owner_rental: "",
          equipment_type: e.identifier ? `${e.name ?? ""} (${e.identifier})` : (e.name ?? ""),
          inspector: "",
          inspection_detail: "",
        }));
      }

      return c.json({ job_id: jobId, work_date: today, form_code: DAILY_REPORT_FORM, values }, 200);
    },
  );

  // ── POST /api/fieldops/checklist/item-state/:id/complete — mark an item done (S4: per-type). ──────
  // Ownership-scoped (the item's instance assignee MUST be the actor's linked personnel — else 403).
  // Per-type completion:
  //   • manual_attest — a check with optional bounded { note, photo_ref }.
  //   • count         — requires { value_num }; done iff value_num >= target_count, else 400
  //                     'below_target' (value recorded, item stays open).
  //   • form_linked / inspection — NOT manually completable: they close via a matching submission
  //     (loop-closure, /checklist/mine reconcile). Manual complete → 400 'auto_close_only'.
  // Mutation + audit + instance-status recompute in ONE batch.
  app.post(
    "/api/fieldops/checklist/item-state/:id/complete",
    gates.requireSession,
    gates.requireCapability(CAP_TASKS_OWN),
    async (c) => {
      const stateId = parseInt(c.req.param("id"), 10);
      if (isNaN(stateId)) return c.json({ error: "invalid_id" }, 400);

      // Body is OPTIONAL for manual_attest (a bare check carries none); count REQUIRES value_num. A
      // missing/blank body is empty, not a 400 — the per-type checks below decide what's required.
      let body: Record<string, unknown> = {};
      try {
        const b: unknown = await c.req.json();
        if (typeof b === "object" && b !== null && !Array.isArray(b)) body = b as Record<string, unknown>;
      } catch {
        /* no body → empty */
      }
      let note: string | null = null;
      if (body.note !== undefined && body.note !== null) {
        if (typeof body.note !== "string" || body.note.length > MAX_NOTE) return c.json({ error: "invalid_note" }, 400);
        note = body.note;
      }
      let photoRef: string | null = null;
      if (body.photo_ref !== undefined && body.photo_ref !== null) {
        if (typeof body.photo_ref !== "string" || body.photo_ref.length > MAX_PHOTO_REF) {
          return c.json({ error: "invalid_photo_ref" }, 400);
        }
        photoRef = body.photo_ref;
      }

      const owned = await loadOwnedItemState(c, stateId);
      if (owned instanceof Response) return owned;
      const actor = c.get("session").username;

      // form_linked / inspection close via a submission, never a manual action — refuse a manual complete.
      if (AUTO_CLOSE_TYPES.has(owned.item_type)) return c.json({ error: "auto_close_only" }, 400);

      // count: value_num required + numeric + bounded; done when it meets the target, OR (R1) when
      // the shortfall is explicitly ACKNOWLEDGED with a required explanatory note.
      let valueNum: number | null = null;
      let belowTargetAck = false;
      if (owned.item_type === "count") {
        const v = body.value_num;
        if (typeof v !== "number" || !Number.isFinite(v) || v < 0 || v > MAX_VALUE) {
          return c.json({ error: "invalid_value_num" }, 400);
        }
        valueNum = v;
        if (owned.target_count !== null && v < owned.target_count) {
          // (R1) Acknowledged shortfall: `{ acknowledge_below_target: true, note }` completes the item
          // BELOW target — the A1 "a below-target count permanently blocks the checklist" fix. The
          // note is REQUIRED (the shortfall must be explained on the record); the distinct audit
          // action below keeps an acknowledged shortfall forensically separate from a met target.
          if (body.acknowledge_below_target === true) {
            if (note === null || note.trim().length === 0) return c.json({ error: "note_required" }, 400);
            belowTargetAck = true;
          } else {
            // Below target WITHOUT acknowledgment → record the value but leave the item OPEN (not a
            // completion), exactly as before. (W4) Audit the value write in the SAME batch — matches
            // every other mutation in this file, so a repeated below-target overwrite leaves a
            // forensic trail rather than silently clobbering value_num.
            await c.env.DB.batch([
              c.env.DB.prepare("UPDATE checklist_item_states SET value_num=?2 WHERE id=?1").bind(stateId, valueNum),
              auditStmt(c, actor, "checklist_item_value_recorded", String(stateId), {
                item_state_id: stateId,
                instance_id: owned.instance_id,
                value_num: valueNum,
                target_count: owned.target_count,
              }),
              recomputeInstanceStatusStmt(c, owned.instance_id),
            ]);
            return c.json({ error: "below_target", id: stateId, value_num: valueNum, target_count: owned.target_count }, 400);
          }
        }
      }

      // Pre-checked existence + ownership + type, so the UPDATE applies → unconditional audit (mirrors
      // the suppress route). Recompute the instance status LAST so it reflects this completion. An
      // acknowledged-below-target completion audits under its OWN action, carrying value + target.
      await c.env.DB.batch([
        c.env.DB
          .prepare(
            "UPDATE checklist_item_states SET status='done', completed_by=?2, completed_at=unixepoch(), note=?3, photo_ref=?4, value_num=COALESCE(?5, value_num) WHERE id=?1",
          )
          .bind(stateId, actor, note, photoRef, valueNum),
        belowTargetAck
          ? auditStmt(c, actor, "checklist_item_complete_below_target", String(stateId), {
              item_state_id: stateId,
              instance_id: owned.instance_id,
              value_num: valueNum,
              target_count: owned.target_count,
              acknowledged_below_target: true,
            })
          : auditStmt(c, actor, "checklist_item_complete", String(stateId), { item_state_id: stateId, instance_id: owned.instance_id, value_num: valueNum }),
        recomputeInstanceStatusStmt(c, owned.instance_id),
      ]);
      const inst = await c.env.DB.prepare("SELECT status FROM checklist_instances WHERE id=?1")
        .bind(owned.instance_id)
        .first<{ status: string }>();
      return c.json(
        { ok: true, id: stateId, status: "done", value_num: valueNum, instance_status: inst?.status ?? "open", acknowledged_below_target: belowTargetAck },
        200,
      );
    },
  );

  // ── POST /api/fieldops/checklist/item-state/:id/uncomplete — toggle a manually-completed item back
  // to open (clears the completion stamp + count value). Ownership-scoped. form_linked/inspection are
  // auto-closed by a submission, not manual, so un-completing one is refused (400 'auto_close_only') —
  // the item re-closes on the next reconcile anyway; a human toggle would be meaningless. ────────────
  app.post(
    "/api/fieldops/checklist/item-state/:id/uncomplete",
    gates.requireSession,
    gates.requireCapability(CAP_TASKS_OWN),
    async (c) => {
      const stateId = parseInt(c.req.param("id"), 10);
      if (isNaN(stateId)) return c.json({ error: "invalid_id" }, 400);
      const owned = await loadOwnedItemState(c, stateId);
      if (owned instanceof Response) return owned;
      if (AUTO_CLOSE_TYPES.has(owned.item_type)) return c.json({ error: "auto_close_only" }, 400);
      const actor = c.get("session").username;
      await c.env.DB.batch([
        c.env.DB
          .prepare(
            "UPDATE checklist_item_states SET status='open', completed_by=NULL, completed_at=NULL, value_num=NULL WHERE id=?1",
          )
          .bind(stateId),
        auditStmt(c, actor, "checklist_item_uncomplete", String(stateId), { item_state_id: stateId, instance_id: owned.instance_id }),
        recomputeInstanceStatusStmt(c, owned.instance_id),
      ]);
      const inst = await c.env.DB.prepare("SELECT status FROM checklist_instances WHERE id=?1")
        .bind(owned.instance_id)
        .first<{ status: string }>();
      return c.json({ ok: true, id: stateId, status: "open", instance_status: inst?.status ?? "open" }, 200);
    },
  );

  // ══ S6 — generic-inspection library (author MANY templates) + admin compose/assign ══════════════
  // The inspection library is the FOURTH consumer of the one checklist engine (spec Q6/Q8): admins
  // author generic_inspection templates (title + items, reusing parseItem), then ASSIGN one to a
  // manager OR subcontractor ad-hoc — creating a kind='inspection' instance that SNAPSHOTS the
  // template's items into checklist_item_states (the SAME snapshot the S3 daily generation does). The
  // assignee surfaces + completes it in their Assigned-Tasks tab via the EXISTING S3/S4 complete/
  // uncomplete routes (ownership = instance.assignee = actor, kind-agnostic — see loadOwnedItemState).
  // Authoring/assign = cap.checklist.manage (admin); the assignee fetch = cap.tasks.own.

  // ── GET /api/fieldops/checklist/inspections — list the generic_inspection library (+ item counts). ─
  app.get(
    "/api/fieldops/checklist/inspections",
    gates.requireSession,
    gates.requireCapability(CAP_CHECKLIST),
    async (c) => {
      const res = await c.env.DB.prepare(
        `SELECT t.id, t.title, t.active, t.created_at,
                (SELECT COUNT(*) FROM checklist_items i
                 WHERE i.template_id = t.id AND i.suppresses_default_item_id IS NULL) AS item_count
         FROM checklist_templates t
         WHERE t.kind = 'generic_inspection'
         ORDER BY t.created_at DESC, t.id DESC`,
      ).all();
      return c.json({ templates: res.results ?? [] }, 200);
    },
  );

  // ── POST /api/fieldops/checklist/inspection — create a generic_inspection library template. ───────
  app.post(
    "/api/fieldops/checklist/inspection",
    gates.requireSession,
    gates.requireCapability(CAP_CHECKLIST),
    async (c) => {
      const body = await readBody(c);
      if (body instanceof Response) return body;
      const title = typeof body.title === "string" ? body.title.trim() : "";
      if (title.length < 1 || title.length > MAX_TITLE) return c.json({ error: "invalid_title" }, 400);
      const actor = c.get("session").username;
      const res = await c.env.DB.batch([
        c.env.DB
          .prepare("INSERT INTO checklist_templates (kind, job_id, title, active) VALUES ('generic_inspection', NULL, ?1, 1) RETURNING id")
          .bind(title),
        auditStmt(c, actor, "checklist_inspection_create", null, { title }),
      ]);
      const newId = (res[0].results?.[0] as { id: number } | undefined)?.id ?? null;
      return c.json({ ok: true, id: newId }, 201);
    },
  );

  // ── GET /api/fieldops/checklist/inspection/:template_id — one library template + its items. ───────
  app.get(
    "/api/fieldops/checklist/inspection/:template_id",
    gates.requireSession,
    gates.requireCapability(CAP_CHECKLIST),
    async (c) => {
      const tpl = await requireGenericTemplate(c, c.req.param("template_id"));
      if (tpl instanceof Response) return tpl;
      const items = await c.env.DB.prepare(
        "SELECT id, seq, item_type, label, form_code, target_count, config_json FROM checklist_items WHERE template_id = ?1 AND suppresses_default_item_id IS NULL ORDER BY seq ASC, id ASC",
      )
        .bind(tpl.id)
        .all<ItemRow>();
      return c.json({ template: tpl, items: items.results ?? [] }, 200);
    },
  );

  // ── POST /api/fieldops/checklist/inspection/:template_id/edit — rename / (de)activate a template. ─
  app.post(
    "/api/fieldops/checklist/inspection/:template_id/edit",
    gates.requireSession,
    gates.requireCapability(CAP_CHECKLIST),
    async (c) => {
      const tpl = await requireGenericTemplate(c, c.req.param("template_id"));
      if (tpl instanceof Response) return tpl;
      const body = await readBody(c);
      if (body instanceof Response) return body;
      const title = typeof body.title === "string" ? body.title.trim() : "";
      if (title.length < 1 || title.length > MAX_TITLE) return c.json({ error: "invalid_title" }, 400);
      // active optional; when present must be a boolean-ish 0/1.
      let active = tpl.active;
      if (body.active !== undefined) {
        if (typeof body.active !== "boolean") return c.json({ error: "invalid_active" }, 400);
        active = body.active ? 1 : 0;
      }
      const actor = c.get("session").username;
      await c.env.DB.batch([
        c.env.DB
          .prepare("UPDATE checklist_templates SET title = ?2, active = ?3 WHERE id = ?1 AND kind = 'generic_inspection'")
          .bind(tpl.id, title, active),
        auditStmt(c, actor, "checklist_inspection_edit", String(tpl.id), { template_id: tpl.id, title, active }),
      ]);
      return c.json({ ok: true, id: tpl.id }, 200);
    },
  );

  // ── POST /api/fieldops/checklist/inspection/:template_id/delete — drop a library template + items. ─
  // Instances already assigned keep working: an inspection instance references its snapshot item-states
  // (checklist_item_states.source_item_id is lineage only, no FK), so deleting the template + its
  // authoring items never touches a live assigned instance.
  app.post(
    "/api/fieldops/checklist/inspection/:template_id/delete",
    gates.requireSession,
    gates.requireCapability(CAP_CHECKLIST),
    async (c) => {
      const tpl = await requireGenericTemplate(c, c.req.param("template_id"));
      if (tpl instanceof Response) return tpl;
      const actor = c.get("session").username;
      // (W4) items first (unaudited), then the template as the statement immediately before the audit.
      await c.env.DB.batch([
        c.env.DB.prepare("DELETE FROM checklist_items WHERE template_id = ?1").bind(tpl.id),
        c.env.DB.prepare("DELETE FROM checklist_templates WHERE id = ?1 AND kind = 'generic_inspection'").bind(tpl.id),
        auditStmt(c, actor, "checklist_inspection_delete", String(tpl.id), { template_id: tpl.id }),
      ]);
      return c.json({ ok: true, id: tpl.id }, 200);
    },
  );

  // ── POST /api/fieldops/checklist/inspection/:template_id/item — add an item to a library template. ─
  app.post(
    "/api/fieldops/checklist/inspection/:template_id/item",
    gates.requireSession,
    gates.requireCapability(CAP_CHECKLIST),
    async (c) => {
      const tpl = await requireGenericTemplate(c, c.req.param("template_id"));
      if (tpl instanceof Response) return tpl;
      const body = await readBody(c);
      if (body instanceof Response) return body;
      const item = parseItem(c, body);
      if (item instanceof Response) return item;
      const actor = c.get("session").username;
      const res = await c.env.DB.batch([
        c.env.DB
          .prepare(
            "INSERT INTO checklist_items (template_id, seq, item_type, label, form_code, target_count, config_json) VALUES (?1,?2,?3,?4,?5,?6,?7) RETURNING id",
          )
          .bind(tpl.id, item.seq, item.item_type, item.label, item.form_code, item.target_count, item.config_json),
        auditStmt(c, actor, "checklist_inspection_item_add", String(tpl.id), { template_id: tpl.id, ...item }),
      ]);
      const newId = (res[0].results?.[0] as { id: number } | undefined)?.id ?? null;
      return c.json({ ok: true, id: newId }, 201);
    },
  );

  // ── POST /api/fieldops/checklist/inspection/:template_id/item/:item_id/edit — replace item fields. ─
  app.post(
    "/api/fieldops/checklist/inspection/:template_id/item/:item_id/edit",
    gates.requireSession,
    gates.requireCapability(CAP_CHECKLIST),
    async (c) => {
      const tpl = await requireGenericTemplate(c, c.req.param("template_id"));
      if (tpl instanceof Response) return tpl;
      const itemId = parseInt(c.req.param("item_id"), 10);
      if (isNaN(itemId)) return c.json({ error: "invalid_id" }, 400);
      const body = await readBody(c);
      if (body instanceof Response) return body;
      const item = parseItem(c, body);
      if (item instanceof Response) return item;
      const actor = c.get("session").username;
      // Scope the UPDATE to THIS template's own content rows so it can't rewrite another template's
      // item. changes()=0 → 404 (unknown item on this template).
      const res = await c.env.DB.batch([
        c.env.DB
          .prepare(
            "UPDATE checklist_items SET seq=?2, item_type=?3, label=?4, form_code=?5, target_count=?6, config_json=?7 WHERE id=?1 AND template_id=?8 AND suppresses_default_item_id IS NULL",
          )
          .bind(itemId, item.seq, item.item_type, item.label, item.form_code, item.target_count, item.config_json, tpl.id),
        c.env.DB
          .prepare(
            "INSERT INTO audit_log (actor_username, action, target_username, detail) SELECT ?1,?2,?3,?4 WHERE changes()=1",
          )
          .bind(actor, "checklist_inspection_item_edit", String(itemId), JSON.stringify({ template_id: tpl.id, item_id: itemId, ...item })),
      ]);
      if ((res[0].meta.changes ?? 0) === 0) return c.json({ error: "not_found" }, 404);
      return c.json({ ok: true, id: itemId }, 200);
    },
  );

  // ── POST /api/fieldops/checklist/inspection/:template_id/item/:item_id/delete — remove one item. ──
  app.post(
    "/api/fieldops/checklist/inspection/:template_id/item/:item_id/delete",
    gates.requireSession,
    gates.requireCapability(CAP_CHECKLIST),
    async (c) => {
      const tpl = await requireGenericTemplate(c, c.req.param("template_id"));
      if (tpl instanceof Response) return tpl;
      const itemId = parseInt(c.req.param("item_id"), 10);
      if (isNaN(itemId)) return c.json({ error: "invalid_id" }, 400);
      const actor = c.get("session").username;
      const res = await c.env.DB.batch([
        c.env.DB.prepare("DELETE FROM checklist_items WHERE id=?1 AND template_id=?2").bind(itemId, tpl.id),
        c.env.DB
          .prepare(
            "INSERT INTO audit_log (actor_username, action, target_username, detail) SELECT ?1,?2,?3,?4 WHERE changes()=1",
          )
          .bind(actor, "checklist_inspection_item_delete", String(itemId), JSON.stringify({ template_id: tpl.id, item_id: itemId })),
      ]);
      if ((res[0].meta.changes ?? 0) === 0) return c.json({ error: "not_found" }, 404);
      return c.json({ ok: true, id: itemId }, 200);
    },
  );

  // ── POST /api/fieldops/checklist/assign — assign a generic_inspection to a manager/subcontractor. ─
  // Body { template_id (a generic_inspection), assignee_personnel_id (an ACTIVE personnel), job_id?,
  // due_date? ('YYYY-MM-DD') }. Creates a kind='inspection' instance (assignee, instance_date=due_date
  // or NULL, job_id optional) + SNAPSHOTS the template's items into checklist_item_states.
  //
  // RE-ASSIGN / DEDUP: the UNIQUE(kind, job_id, assignee_personnel_id, instance_date) governs. With a
  // NULL job_id or NULL due_date, SQLite treats NULLs as DISTINCT → repeat assignments are ALLOWED
  // (each is a fresh instance snapshotting the template as it stands — an inspection can recur). When
  // BOTH job_id and due_date are set, a duplicate exact assignment is DEDUPED: INSERT OR IGNORE …
  // RETURNING yields no row on collision → 409 'already_assigned'. Snapshot runs ONLY when the INSERT
  // actually created the row (RETURNING id present) — no orphaned/duplicate states on a deduped repeat.
  app.post(
    "/api/fieldops/checklist/assign",
    gates.requireSession,
    gates.requireCapability(CAP_CHECKLIST),
    async (c) => {
      const body = await readBody(c);
      if (body instanceof Response) return body;

      const templateId = typeof body.template_id === "number" && Number.isInteger(body.template_id) ? body.template_id : NaN;
      if (isNaN(templateId)) return c.json({ error: "invalid_template_id" }, 400);
      const assigneeId =
        typeof body.assignee_personnel_id === "number" && Number.isInteger(body.assignee_personnel_id)
          ? body.assignee_personnel_id
          : NaN;
      if (isNaN(assigneeId)) return c.json({ error: "invalid_assignee" }, 400);

      // Optional job_id: when present must be a real job (any lifecycle — a checklist can target any job).
      let jobId: string | null = null;
      if (body.job_id !== undefined && body.job_id !== null) {
        if (typeof body.job_id !== "string") return c.json({ error: "invalid_job_id" }, 400);
        const jobErr = await requireJob(c, body.job_id);
        if (jobErr) return jobErr;
        jobId = body.job_id;
      }

      // Optional due_date: a Pacific calendar date (same shape as the daily instance_date).
      let dueDate: string | null = null;
      if (body.due_date !== undefined && body.due_date !== null && body.due_date !== "") {
        if (typeof body.due_date !== "string" || !DUE_DATE_RE.test(body.due_date)) {
          return c.json({ error: "invalid_due_date" }, 400);
        }
        dueDate = body.due_date;
      }

      // The template must be a generic_inspection (not a daily_default/job_override/specific one).
      // (R1) title fetched too — it is SNAPSHOTTED onto the instance (template_title, migration 0029)
      // so the assignee's tab shows the authored name, not "Inspection #<id>", even after the library
      // template is renamed or deleted.
      const tpl = await c.env.DB.prepare(
        "SELECT id, title FROM checklist_templates WHERE id = ?1 AND kind = 'generic_inspection'",
      )
        .bind(templateId)
        .first<{ id: number; title: string | null }>();
      if (!tpl) return c.json({ error: "template_not_found" }, 404);

      // (R1) ASSIGN-TIME VALIDATION — refuse assignments that create work nobody can ever complete:
      //   • a 0-item template snapshots an EMPTY instance (permanently 'open', invisible work) → 422;
      //   • form_linked/inspection items auto-close ONLY on a (job, date) submission match, so a
      //     template containing any needs BOTH a job and a due date at assign time → 422.
      const comp = await c.env.DB.prepare(
        "SELECT COUNT(*) AS n, SUM(CASE WHEN item_type IN ('form_linked','inspection') THEN 1 ELSE 0 END) AS form_bearing FROM checklist_items WHERE template_id = ?1 AND suppresses_default_item_id IS NULL",
      )
        .bind(templateId)
        .first<{ n: number; form_bearing: number | null }>();
      if ((comp?.n ?? 0) === 0) return c.json({ error: "empty_template" }, 422);
      if ((comp?.form_bearing ?? 0) > 0 && (jobId === null || dueDate === null)) {
        return c.json({ error: "job_and_date_required" }, 422);
      }

      // The assignee must be a real ACTIVE roster person (a retired person can't own a live instance).
      const person = await c.env.DB.prepare("SELECT id FROM personnel WHERE id = ?1 AND active = 1")
        .bind(assigneeId)
        .first<{ id: number }>();
      if (!person) return c.json({ error: "assignee_not_found" }, 404);

      const actor = c.get("session").username;
      // (W4) Create the instance + its audit ATOMICALLY in ONE batch — the instance is created iff the
      // assign is audited (no window where a row exists with no forensic record). The audit keys on the
      // natural (template/assignee/job/date) tuple, NOT the surrogate id, because D1's batch() can't
      // thread a RETURNING id into a later statement's binds. Deduped on the UNIQUE key when job+date
      // are both set (NULLs are distinct → a no-job/no-date inspection may recur).
      const insBatch = await c.env.DB.batch([
        c.env.DB
          .prepare(
            "INSERT OR IGNORE INTO checklist_instances (kind, job_id, assignee_personnel_id, instance_date, status, template_title) VALUES ('inspection', ?1, ?2, ?3, 'open', ?4)",
          )
          .bind(jobId, assigneeId, dueDate, tpl.title),
        c.env.DB
          .prepare(
            "INSERT INTO audit_log (actor_username, action, target_username, detail) SELECT ?1,?2,?3,?4 WHERE changes()=1",
          )
          .bind(
            actor,
            "checklist_inspection_assign",
            String(assigneeId),
            JSON.stringify({ template_id: templateId, assignee_personnel_id: assigneeId, job_id: jobId, due_date: dueDate }),
          ),
      ]);
      const created = (insBatch[0].meta.changes ?? 0) === 1;

      // Resolve the instance (just-created, or the pre-existing one on the same natural key; IS = null-safe).
      const inst = await c.env.DB
        .prepare(
          "SELECT id FROM checklist_instances WHERE kind = 'inspection' AND assignee_personnel_id = ?1 AND job_id IS ?2 AND instance_date IS ?3 ORDER BY id DESC LIMIT 1",
        )
        .bind(assigneeId, jobId, dueDate)
        .first<{ id: number }>();
      if (!inst) return c.json({ error: "internal_error" }, 500);
      const instanceId = inst.id;

      // Snapshot the template's items — but only if this instance has NONE yet. So a genuine duplicate
      // (job+date already fully assigned) 409s, while an existing-but-EMPTY instance (a prior partial
      // that failed after the audited INSERT but before the snapshot) SELF-HEALS by backfilling rather
      // than orphaning a permanent un-completable row.
      let itemCount =
        (await c.env.DB.prepare("SELECT COUNT(*) AS n FROM checklist_item_states WHERE instance_id = ?1").bind(instanceId).first<{ n: number }>())?.n ?? 0;
      if (!created && itemCount > 0) return c.json({ error: "already_assigned" }, 409);
      if (itemCount === 0) {
        const srcItems = await c.env.DB.prepare(
          "SELECT id AS source_item_id, item_type, label, form_code, target_count FROM checklist_items WHERE template_id = ?1 AND suppresses_default_item_id IS NULL ORDER BY seq ASC, id ASC",
        )
          .bind(templateId)
          .all<{ source_item_id: number; item_type: string; label: string | null; form_code: string | null; target_count: number | null }>();
        const rows = srcItems.results ?? [];
        if (rows.length) {
          await c.env.DB.batch(
            rows.map((it) =>
              c.env.DB
                .prepare(
                  "INSERT INTO checklist_item_states (instance_id, source_item_id, item_type, label, form_code, target_count, status) VALUES (?1,?2,?3,?4,?5,?6,'open')",
                )
                .bind(instanceId, it.source_item_id, it.item_type, it.label, it.form_code, it.target_count),
            ),
          );
        }
        itemCount = rows.length;
      }
      return c.json({ ok: true, instance_id: instanceId, item_count: itemCount }, created ? 201 : 200);
    },
  );

  // ── GET /api/fieldops/checklist/assigned — the actor's ASSIGNED inspection instances + item-states. ─
  // Works for ANY linked personnel (manager OR subcontractor) — NOT gated on role, unlike the daily
  // /mine surface. Resolves session → linked ACTIVE personnel; no link → empty list. For each
  // inspection instance the actor is the assignee of, it reconciles form_linked/inspection items
  // against the day's submissions (S4 loop-closure) WHEN the instance carries both a job_id AND a
  // due_date (otherwise there's no (job, date) to match a submission) — reusing reconcileFormLinked
  // (whose daily-only rollup leg no-ops on kind='inspection'). Completion of these items reuses the
  // EXISTING S3/S4 /item-state/:id/complete + /uncomplete routes (ownership = instance.assignee = actor).
  app.get(
    "/api/fieldops/checklist/assigned",
    gates.requireSession,
    gates.requireCapability(CAP_TASKS_OWN),
    async (c) => {
      const person = await resolveActorPersonnel(c);
      // (R1) `linked` — same empty-state disambiguation flag as /tasks/mine: no ACTIVE linked
      // personnel row means the actor CANNOT have assignments (vs "linked but nothing assigned").
      if (!person) return c.json({ inspections: [], linked: false }, 200);

      // (R1) OPEN work first (status CASE, mirroring /tasks/mine — the old `status ASC` floated
      // 'complete' above 'open' lexicographically), tiebreak created_at DESC. template_title (0029,
      // snapshotted at assign time) gives the card its authored name.
      const instRes = await c.env.DB.prepare(
        `SELECT i.id, i.job_id, i.instance_date, i.status, i.created_at, i.template_title, j.project_name
         FROM checklist_instances i
         LEFT JOIN jobs j ON j.job_id = i.job_id
         WHERE i.kind = 'inspection' AND i.assignee_personnel_id = ?1
         ORDER BY CASE i.status WHEN 'open' THEN 0 WHEN 'in_progress' THEN 1 ELSE 2 END ASC,
                  i.created_at DESC, i.id DESC
         LIMIT 500`,
      )
        .bind(person.id)
        .all<{ id: number; job_id: string | null; instance_date: string | null; status: string; created_at: number; template_title: string | null; project_name: string | null }>();
      const instances = instRes.results ?? [];

      const inspections = [];
      for (const inst of instances) {
        // Loop-closure only makes sense with a concrete (job, date) to match a submission against.
        // (R1, Q3) kind='inspection' → the date is a DUE date; a filing ON OR BEFORE it closes the item.
        if (inst.job_id && inst.instance_date) {
          await reconcileFormLinked(c, inst.id, inst.job_id, inst.instance_date, "inspection");
        }
        // Item states + R1 filed_by attribution (inspection = on-or-before due-date match; NULL
        // job/date binds → filed_by NULL, rows unaffected).
        const items = await c.env.DB.prepare(ITEM_STATES_SQL_INSPECTION)
          .bind(inst.id, inst.job_id, inst.instance_date)
          .all();
        // Re-read the (possibly reconciled) instance status so a just-auto-closed item is reflected.
        const fresh = await c.env.DB.prepare("SELECT status FROM checklist_instances WHERE id = ?1")
          .bind(inst.id)
          .first<{ status: string }>();
        inspections.push({
          instance: {
            id: inst.id,
            job_id: inst.job_id,
            project_name: inst.project_name,
            instance_date: inst.instance_date,
            status: fresh?.status ?? inst.status,
            template_title: inst.template_title,
            created_at: inst.created_at,
          },
          items: items.results ?? [],
        });
      }
      return c.json({ inspections, linked: true }, 200);
    },
  );
}
