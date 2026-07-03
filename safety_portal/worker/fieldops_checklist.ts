import type { Context } from "hono";
import type { Env, Vars } from "./types";
import type { FieldopsApp, FieldopsGates } from "./fieldops_gates";
import { auditStmt, auditStmtIfChanged } from "./audit";
import { requireJob, resolveActorPersonnel } from "./fieldops_scope";
import type { AssignedInspectionsResponse, ChecklistItemState } from "./wire-types";
import catalog from "../catalog.json";

// Assigned-Tasks tab (P4 field-ops feature) S2 — the checklist ENGINE + the admin per-job template
// editor. One templates→instances engine (spec Q6) originally served the daily "Progress Report"
// checklist and (S6) the inspection library; the DAILY flow was retired by D2 (the SOP daily form)
// and its generation surfaces (GET /checklist/mine + /mine/rollup-draft) were DELETED with operator
// approval 2026-07-03 — the inspection library is now the engine's live consumer. S2 owns ONLY the
// template side: the global daily_default row + per-job job_override rows. Item completion is
// S3–S4; the S5 rollup-link survives in the /assigned reconcile. Template routes are
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
// S5 rollup — the Daily Report family (catalog 'daily-report' parent; matches the daily_default
// seed's source_form_code). Used for the rolled_up_submission_uuid reconcile (ROLLUP_LINK_SQL). A
// submission matches iff its form_code EQUALS this OR is a versioned variant (`|| '-v%'`) — the
// SAME family match as S4 loop-closure. Exported: the /daily-form/status handler
// (fieldops_daily_requirements.ts) keys its `daily_filed` on it.
export const DAILY_REPORT_FORM = "daily-report";
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
// R5 admin assignments list — defensive page bound (newest first; an admin triaging outstanding
// inspections never needs more than the most recent few hundred; no pagination surface).
const INSTANCES_LIMIT = 300;
// R5 GET /checklist/instances ?status= filter values (anything else → 400, never silently coerced).
const INSTANCE_STATUS_FILTERS = new Set(["open", "complete", "all"]);

// THE MERGE (S2, load-bearing) — a job's EFFECTIVE daily checklist =
//   [ daily_default items NOT suppressed by the job ] ∪ [ the job_override's own added items ], seq-ordered.
// ?1 = job_id, bound ONCE but referenced twice (positional re-use). Suppression markers
// (suppresses_default_item_id NOT NULL) are excluded from both legs. `origin` distinguishes a default
// (suppressable) item from an override (deletable) one for the editor UI.
// Consumed by the S2 per-job editor route (GET /checklist/job/:job_id). (It was also the S3
// daily-instance snapshot source until the daily-generation surfaces were deleted — see the
// tombstone below the merge SQL.)
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

// requireJob / resolveActorPersonnel — shared scope machinery, see fieldops_scope.ts (extracted
// from the local copies this module used to carry; contracts unchanged).

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

// ══ TOMBSTONE (operator-approved deletion, 2026-07-03) ═════════════════════════════════════════════
// The S3 DAILY-instance generation machinery — `pacificToday`, `DailyEmptyReason`,
// `generateDailyInstance` (Worker-on-read materialization + EFFECTIVE_MERGE_SQL snapshot into
// checklist_item_states, kind='daily') — was DELETED here with its sole caller, GET
// /api/fieldops/checklist/mine (below). Deprecated-for-daily since D2 (the SOP daily form replaced
// the checkbox checklist), it still WROTE daily instances + snapshots when called — a junk-data
// footgun with zero SPA/Python callers. Operator approval 2026-07-03; recover from git history
// (this file, pre-deletion). The inspection engine (assign/assigned/instances/cancel/item-state)
// is untouched.

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
// Runs on EVERY /checklist/assigned read (idempotent): it only flips OPEN→done (WHERE status<>'done'),
// so an already-closed item — auto or manual — is untouched, and a submission never "un-closes" an item.
// The UPDATE is bound-param (job_id + instance_date passed positionally, not spliced) and correlates the
// EXISTS subquery on the target row's own form_code. Persisted (not computed) so S5's rollup + the
// instance-complete recompute observe the closure. ?1 = instance id, ?2 = job_id, ?3 = instance_date.
//
// (R1, spec Q3) DATE SEMANTICS: an INSPECTION instance's date is a DUE date, so a filing ON OR
// BEFORE it satisfies the item (`<=`) — before this, filing the linked form a day early left the
// inspection permanently open. (The `=` exact-date variant served the retired DAILY flow — its
// AUTO_CHECK_SQL_DAILY constant was deleted with /checklist/mine, 2026-07-03.) `dateOp` is a
// module-CONSTANT literal (never caller/user input), so the template splice is bound-param-safe.
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
const AUTO_CHECK_SQL_INSPECTION = buildAutoCheckSql("<=");

// S5 rolled-up linkage (reconcile — the SAME submission-existence pattern S4 uses for loop-closure).
// Stamp checklist_instances.rolled_up_submission_uuid with the day's Daily Report submission for this
// (job, date) — matched on the 'daily-report' PARENT FAMILY (= parent OR versioned variant, exactly the
// S4 form-code match). Kind-scoped to 'daily' — batched into the /checklist/assigned reconcile where it
// no-ops on inspection instances (harmless leg, kept for statement-shape parity with the retired daily
// reconcile). Set-ONCE (WHERE rolled_up_submission_uuid IS NULL) + guarded
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

// TOMBSTONE (operator-approved deletion, 2026-07-03): `reconcileFormLinked` — the 3-statement
// daily reconcile wrapper (auto-check → rollup-link → status recompute) — was DELETED with its sole
// caller, GET /checklist/mine. The inspection surface never called it: GET /checklist/assigned
// phase-batches the SAME three statements inline (AUTO_CHECK_SQL_INSPECTION + ROLLUP_LINK_SQL +
// recomputeInstanceStatusStmt), which is why those building blocks remain. Git history has the body.

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
// (The `=` daily variant, ITEM_STATES_SQL_DAILY, was deleted with /checklist/mine — 2026-07-03.)
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
// unknown, 403 not-your-instance). Ownership is scoped to the ACTOR's linked personnel id: an
// assignee can only touch items on THEIR OWN instance. Per-type completability (manual_attest = check,
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
        auditStmtIfChanged(c, actor, "checklist_default_item_edit", String(itemId), { item_id: itemId, ...item }),
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
        auditStmtIfChanged(c, actor, "checklist_default_item_delete", String(itemId), { item_id: itemId }),
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
        auditStmtIfChanged(c, actor, "checklist_job_item_delete", jobId, { job_id: jobId, item_id: itemId }),
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
        auditStmtIfChanged(c, actor, "checklist_job_unsuppress", jobId, { job_id: jobId, default_item_id: defaultItemId }),
      ]);
      if ((res[0].meta.changes ?? 0) === 0) return c.json({ error: "not_found" }, 404);
      return c.json({ ok: true, unsuppressed: defaultItemId }, 200);
    },
  );

  // ══ D2 (SOP daily form): GET /api/fieldops/daily-form/status MOVED to
  // fieldops_daily_requirements.ts (the daily-form module) — it never belonged to the checklist
  // engine; it only lived here for the (now-extracted, fieldops_scope.ts) gate helpers. ═══════════

  // ══ S3/S4 — manual_attest / count COMPLETION (cap.tasks.own — the owner's tab) ═══════════════════
  //
  // TOMBSTONE (operator-approved deletion, 2026-07-03): two daily-flow routes were DELETED here —
  //   • GET /api/fieldops/checklist/mine — the S3 daily surfacing read. Deprecated-for-daily since
  //     D2 (the SOP daily form replaced it; zero SPA/Python callers), but it still GENERATED a
  //     kind='daily' checklist_instances row + item-state snapshot on every call (the junk-data
  //     footgun that motivated the removal).
  //   • GET /api/fieldops/checklist/mine/rollup-draft — the S5 Daily-Report draft assembler,
  //     superseded by the SOP form's own prefill.
  // Both live in git history (this file, pre-deletion). The completion routes below and the whole
  // inspection engine (assign/assigned/instances/cancel) are UNTOUCHED.

  // ── POST /api/fieldops/checklist/item-state/:id/complete — mark an item done (S4: per-type). ──────
  // Ownership-scoped (the item's instance assignee MUST be the actor's linked personnel — else 403).
  // Per-type completion:
  //   • manual_attest — a check with optional bounded { note, photo_ref }.
  //   • count         — requires { value_num }; done iff value_num >= target_count, else 400
  //                     'below_target' (value recorded, item stays open).
  //   • form_linked / inspection — NOT manually completable: they close via a matching submission
  //     (loop-closure, the /checklist/assigned reconcile). Manual complete → 400 'auto_close_only'.
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
  // template's items into checklist_item_states (the same snapshot shape the retired S3 daily
  // generation used). The
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
        auditStmtIfChanged(c, actor, "checklist_inspection_item_edit", String(itemId), { template_id: tpl.id, item_id: itemId, ...item }),
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
        auditStmtIfChanged(c, actor, "checklist_inspection_item_delete", String(itemId), { template_id: tpl.id, item_id: itemId }),
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
        auditStmtIfChanged(c, actor, "checklist_inspection_assign", String(assigneeId), { template_id: templateId, assignee_personnel_id: assigneeId, job_id: jobId, due_date: dueDate }),
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
  // Works for ANY linked personnel (manager OR subcontractor) — NOT gated on role (unlike the
  // retired daily /mine surface). Resolves session → linked ACTIVE personnel; no link → empty list.
  // For each inspection instance the actor is the assignee of, it reconciles form_linked/inspection
  // items against the day's submissions (S4 loop-closure) WHEN the instance carries both a job_id
  // AND a due_date (otherwise there's no (job, date) to match a submission) — the 3-statement
  // reconcile (auto-check → rollup-link → status recompute), phase-batched below (the daily-only
  // rollup leg no-ops on kind='inspection').
  // Completion of these items reuses the EXISTING S3/S4 /item-state/:id/complete + /uncomplete
  // routes (ownership = instance.assignee = actor).
  app.get(
    "/api/fieldops/checklist/assigned",
    gates.requireSession,
    gates.requireCapability(CAP_TASKS_OWN),
    async (c) => {
      const person = await resolveActorPersonnel(c);
      // (R1) `linked` — same empty-state disambiguation flag as /tasks/mine: no ACTIVE linked
      // personnel row means the actor CANNOT have assignments (vs "linked but nothing assigned").
      if (!person) return c.json({ inspections: [], linked: false } satisfies AssignedInspectionsResponse, 200);

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
      if (instances.length === 0) {
        return c.json({ inspections: [], linked: true } satisfies AssignedInspectionsResponse, 200);
      }

      // PHASE-BATCHED (was 3 sequential D1 round trips PER instance — O(N) on a LIMIT-500 live
      // read path). Same statements, same SQL strings, same per-instance order; only the round-trip
      // composition changes: first ONE batch carrying every instance's 3-statement reconcile
      // (auto-check → rollup-link → status recompute — the retired daily reconcile's exact
      // statements, in its exact order; D1 runs a batch sequentially in one transaction, and every statement is
      // instance-scoped so cross-instance interleaving cannot change any outcome), then ONE batch
      // of the per-instance reads. 3N+2 round trips → 4.
      //
      // Loop-closure only makes sense with a concrete (job, date) to match a submission against —
      // instances missing either get NO reconcile legs (but still get their reads, as before).
      // (R1, Q3) kind='inspection' → the date is a DUE date; a filing ON OR BEFORE it closes the item.
      const reconcileStmts = [];
      for (const inst of instances) {
        if (inst.job_id && inst.instance_date) {
          reconcileStmts.push(
            c.env.DB.prepare(AUTO_CHECK_SQL_INSPECTION).bind(inst.id, inst.job_id, inst.instance_date),
            c.env.DB.prepare(ROLLUP_LINK_SQL).bind(inst.id, inst.job_id, inst.instance_date, DAILY_REPORT_FORM),
            recomputeInstanceStatusStmt(c, inst.id),
          );
        }
      }
      if (reconcileStmts.length > 0) await c.env.DB.batch(reconcileStmts);

      // Item states + R1 filed_by attribution (inspection = on-or-before due-date match; NULL
      // job/date binds → filed_by NULL, rows unaffected) + the fresh (possibly just-reconciled)
      // instance status — two read legs per instance, indexed back by position (2i / 2i+1).
      const readLegs = await c.env.DB.batch(
        instances.flatMap((inst) => [
          c.env.DB.prepare(ITEM_STATES_SQL_INSPECTION).bind(inst.id, inst.job_id, inst.instance_date),
          c.env.DB.prepare("SELECT status FROM checklist_instances WHERE id = ?1").bind(inst.id),
        ]),
      );
      const payload: AssignedInspectionsResponse = {
        inspections: instances.map((inst, i) => {
          const fresh = readLegs[2 * i + 1].results?.[0] as { status: string } | undefined;
          return {
            instance: {
              id: inst.id,
              job_id: inst.job_id,
              project_name: inst.project_name,
              instance_date: inst.instance_date,
              status: (fresh?.status ?? inst.status) as "open" | "complete",
              template_title: inst.template_title,
              created_at: inst.created_at,
            },
            items: (readLegs[2 * i].results ?? []) as ChecklistItemState[],
          };
        }),
        linked: true,
      };
      return c.json(payload, 200);
    },
  );

  // ══ R5 — assignment LIFECYCLE: admin visibility + revocation (cap.checklist.manage) ══════════════
  // Before R5 an assignment was fire-and-forget: POST /assign created the instance and no admin
  // surface could ever list or revoke it (a mistaken assignment was invisible and irrevocable — the
  // A2/A4 finding). These two routes close the loop: the admin lists outstanding inspection-kind
  // instances and cancels one. Both are cap.checklist.manage (the same admin cap as /assign),
  // send-free (D1 only), bound-param.

  // ── GET /api/fieldops/checklist/instances — the admin "outstanding assignments" list. ────────────
  // INSPECTION-kind ONLY: legacy kind='daily' rows (auto-generated per placed manager × day by the
  // retired /mine route, deleted 2026-07-03) are historical noise nobody assigned — listing them
  // here as "assignments" would be a lie. Each row carries the snapshot title, the assignee's
  // personnel name, the job's project name, the due date (instance_date), status, and the item
  // aggregate (done/total) so the admin can see progress without opening anything. `?status=` filters
  // open (default — the working set) | complete | all; unknown values 400 (never a silent default).
  // Bounded LIMIT + newest-first (created_at DESC): the triage surface, not an archive query.
  app.get(
    "/api/fieldops/checklist/instances",
    gates.requireSession,
    gates.requireCapability(CAP_CHECKLIST),
    async (c) => {
      const filter = c.req.query("status") ?? "open";
      if (!INSTANCE_STATUS_FILTERS.has(filter)) return c.json({ error: "invalid_status_filter" }, 400);
      // The status predicate switches between two FIXED SQL strings; the filter value itself is only
      // ever a bound parameter (never spliced).
      const baseSql = `
        SELECT i.id, i.template_title, i.assignee_personnel_id, p.name AS assignee_name,
               i.job_id, j.project_name, i.instance_date, i.status, i.created_at,
               (SELECT COUNT(*) FROM checklist_item_states s WHERE s.instance_id = i.id) AS items_total,
               (SELECT COUNT(*) FROM checklist_item_states s
                WHERE s.instance_id = i.id AND s.status = 'done') AS items_done
        FROM checklist_instances i
        LEFT JOIN personnel p ON p.id = i.assignee_personnel_id
        LEFT JOIN jobs j ON j.job_id = i.job_id
        WHERE i.kind = 'inspection'`;
      const res =
        filter === "all"
          ? await c.env.DB.prepare(`${baseSql} ORDER BY i.created_at DESC, i.id DESC LIMIT ?1`)
              .bind(INSTANCES_LIMIT)
              .all()
          : await c.env.DB.prepare(
              `${baseSql} AND i.status = ?1 ORDER BY i.created_at DESC, i.id DESC LIMIT ?2`,
            )
              .bind(filter, INSTANCES_LIMIT)
              .all();
      return c.json({ instances: res.results ?? [], status_filter: filter }, 200);
    },
  );

  // ── POST /api/fieldops/checklist/instance/:id/cancel — revoke an assigned inspection. ────────────
  // INSPECTION-kind only — a legacy kind='daily' instance (auto-generated by the retired /mine
  // route, deleted 2026-07-03) is
  // NOT cancellable through this route and 404s indistinguishably from an unknown id (the same
  // wrong-kind-is-not-found posture as requireGenericTemplate). HARD DELETE, not a soft
  // status='cancelled': no consumer of historical inspection instances exists (verified — the only
  // readers of checklist_instances are this file's routes: /assigned scopes to the assignee, the
  // list above scopes to live rows, and the S5 rollup is kind='daily'), a soft state would leak into
  // the UNIQUE(kind, job, assignee, date) dedupe key blocking a legitimate RE-assign of the same
  // (job, date), and the audit_log rows (assign + this cancel) already preserve the forensic history.
  // Completed instances are cancellable too (admin cleanup) — the UI names the discard in its confirm.
  // (W4) item-states delete first (unaudited), the INSTANCE delete is the statement immediately
  // before the audit INSERT, so `changes()=1` gates the audit on the row actually going away; a
  // concurrent double-cancel loses the race, audits nothing, and 404s.
  app.post(
    "/api/fieldops/checklist/instance/:id/cancel",
    gates.requireSession,
    gates.requireCapability(CAP_CHECKLIST),
    async (c) => {
      const instanceId = parseInt(c.req.param("id"), 10);
      if (isNaN(instanceId)) return c.json({ error: "invalid_id" }, 400);
      // Pre-read for the audit detail (title/assignee/job/date live on the row being destroyed).
      const row = await c.env.DB.prepare(
        `SELECT i.id, i.kind, i.template_title, i.assignee_personnel_id, i.job_id, i.instance_date,
                i.status, p.name AS assignee_name
         FROM checklist_instances i
         LEFT JOIN personnel p ON p.id = i.assignee_personnel_id
         WHERE i.id = ?1`,
      )
        .bind(instanceId)
        .first<{
          id: number;
          kind: string;
          template_title: string | null;
          assignee_personnel_id: number | null;
          job_id: string | null;
          instance_date: string | null;
          status: string;
          assignee_name: string | null;
        }>();
      if (!row || row.kind !== "inspection") return c.json({ error: "not_found" }, 404);
      const actor = c.get("session").username;
      const res = await c.env.DB.batch([
        c.env.DB.prepare("DELETE FROM checklist_item_states WHERE instance_id = ?1").bind(instanceId),
        c.env.DB
          .prepare("DELETE FROM checklist_instances WHERE id = ?1 AND kind = 'inspection'")
          .bind(instanceId),
        auditStmtIfChanged(c, actor, "checklist_inspection_cancel", row.assignee_personnel_id !== null ? String(row.assignee_personnel_id) : null, {
              instance_id: instanceId,
              template_title: row.template_title,
              assignee_personnel_id: row.assignee_personnel_id,
              assignee_name: row.assignee_name,
              job_id: row.job_id,
              instance_date: row.instance_date,
              status_at_cancel: row.status,
            }),
      ]);
      if ((res[1].meta.changes ?? 0) === 0) return c.json({ error: "not_found" }, 404);
      return c.json({ ok: true, id: instanceId }, 200);
    },
  );
}
