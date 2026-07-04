import type { Context } from "hono";
import type { Env, Vars } from "./types";
import type { FieldopsApp, FieldopsGates } from "./fieldops_gates";
import { auditStmtIfChanged } from "./audit";
import { requireDailyReportRole, requireJob, requireJobScope } from "./fieldops_scope";
import { DAILY_REPORT_FORM } from "./fieldops_checklist";
import { DAILY_STATUS_FAMILIES } from "../src/shared/daily_families";
import type { DailyFormStatus, DailyRequirementItem, DailyRequirementsResponse, FiledEntry } from "./wire-types";
import catalog from "../catalog.json";

// SOP daily form slice D4 — per-job daily-form REQUIREMENTS (migration 0030
// job_daily_requirements; slice D5 / migration 0032 widened the kind vocabulary to seven —
// number/date/select, the latter carrying an admin-authored JSON `options` list — so an admin
// can add ANY answer type, not just free text). The BASE daily form is a git-owned definition (daily-report-v4 carries a
// placeholder `job_requirements` section); this module owns the D1-backed ADDITIVE overlay an admin
// authors per job ("as specific requirements develop or are outlined by the client"): the Daily tab
// fetches the job's items at render time, injects them into that section, and the manager's answers
// file WITH the submission (values.job_requirements — self-describing, so later edits never mutate
// a filed record). Send-free throughout (D1 only); mutation + audit in ONE batch (the W4 pattern),
// bound-param, same discipline as fieldops_checklist.
//
// Two capability surfaces, mirroring the checklist split:
//   • admin CRUD  — cap.checklist.manage (the Job Tracker job-detail editor);
//   • the tab read — cap.tasks.own + the SAME per-job ownership scope as /daily-form/status
//     (security review posture: a non-admin actor reads ONLY their own placement — 403
//     forbidden_job otherwise; cap.jobtracker.manage / cap.checklist.manage holders any job)
//     + the daily-report ROLE gate (directive 2026-07-03, fieldops_scope.requireDailyReportRole):
//     cap.tasks.own is held by all three roles, so both daily-form reads additionally require
//     role ∈ {manager, admin} — a placed subcontractor 403s (forbidden_role) instead of reading
//     the daily surface it can no longer render.

const MAX_LABEL = 256; // same bound as checklist item labels (0026 discipline)
const MAX_FORM_CODE = 64;
const MAX_SEQ = 100_000;
// Defensive ceilings: the read is bounded (never an unbounded dump) and the add route refuses to
// grow a job's ACTIVE list past the same ceiling — an authenticated-admin resource-exhaustion
// vector otherwise (the publishValidation MAX_* bound rationale).
const REQUIREMENTS_LIMIT = 200;
// select-kind option-list bounds (D5, migration 0032): a pick-one list bigger than 20 is a UI
// smell, not a requirement; 120 chars covers any realistic option label. Both refused loudly.
const MAX_OPTIONS = 20;
const MAX_OPTION_LEN = 120;

// The closed item vocabulary BOTH renderers (SPA FormRenderer + Python form_pdf via the filed
// values array) understand. note = read-only guidance; confirm = checkbox; text = free answer;
// form_link = deep link to another form type; number/date = typed answers (filed as strings,
// like text); select = pick-one from the item's admin-authored `options` (D5, migration 0032).
const KINDS = new Set(["note", "confirm", "text", "form_link", "number", "date", "select"]);

// form_link targets store the catalog PARENT family code (the S4 loop-closure convention — the
// filed-indicator family match runs on parents). Two validation sets from the bundled manifest:
//   • must EXIST in the catalog (a typo would render a dead link) — 422 unknown_form_code;
//   • must NOT be a launch:"daily-tab" parent (the daily form deep-linking back into the daily tab
//     itself is circular; mirrors the ChecklistItemForm picker exclusion) — 422 daily_tab_form_code.
interface CatalogParent {
  parent_form_code: string;
  launch?: string;
}
const CATALOG_PARENTS = (catalog as { parents: CatalogParent[] }).parents;
const CATALOG_PARENT_CODES: ReadonlySet<string> = new Set(CATALOG_PARENTS.map((p) => p.parent_form_code));
const DAILY_TAB_PARENT_CODES: ReadonlySet<string> = new Set(
  CATALOG_PARENTS.filter((p) => p.launch === "daily-tab").map((p) => p.parent_form_code),
);

/** Does a submitted form_code belong to a launch:"daily-tab" parent family? Matched on the S4
 *  family convention (form_code = parent OR a versioned variant `parent + '-v…'`) — the SAME
 *  match /daily-form/status runs in SQL, so "daily-report-v5" and a bare "daily-report" both
 *  count. Consumed by /api/submit (worker/index.ts) for the daily-report role gate (operator
 *  directive 2026-07-03): the daily field report is a manager/admin surface, and the SPA hiding
 *  the Daily tab is never the boundary — the submit choke point is. */
export function isDailyTabFamilyForm(formCode: string): boolean {
  for (const parent of DAILY_TAB_PARENT_CODES) {
    if (formCode === parent || formCode.startsWith(`${parent}-v`)) return true;
  }
  return false;
}

const CAP_MANAGE = "cap.checklist.manage"; // admin authoring (same cap as the checklist editors)
const CAP_TASKS_OWN = "cap.tasks.own"; // the tab read (the placed manager's surface)
// The caps that bypass the per-job ownership scope on this surface (see fieldops_scope.requireJobScope
// — the same admin set for BOTH daily-form reads below; expected-materials diverges to
// materials.manage on purpose).
const SCOPE_BYPASS_CAPS = ["cap.jobtracker.manage", "cap.checklist.manage"] as const;
// Work-date shape for /daily-form/status — a Pacific calendar date, the same 'YYYY-MM-DD' shape the
// checklist instance_date uses (no time component, no offset).
const DATE_RE = /^\d{4}-\d{2}-\d{2}$/;

// One requirement row as served to BOTH surfaces (the tab render and the admin editor) —
// single-sourced as DailyRequirementItem in wire-types.ts (the SPA re-exports the same type).

// The normalized, validated write payload. `options` is the JSON-serialized option array as
// stored in D1 (select only; NULL otherwise) — serialization happens HERE so both write routes
// bind the same validated string.
interface ParsedRequirement {
  seq: number;
  kind: string;
  label: string;
  form_code: string | null;
  options: string | null;
}

// Parse + validate a requirement write body — the bounds + kind rules in one place (shared by add
// and edit, mirroring fieldops_checklist.parseItem). Returns the normalized item or a JSON error
// Response.
function parseRequirement(
  c: Context<{ Bindings: Env; Variables: Vars }>,
  body: Record<string, unknown>,
): ParsedRequirement | Response {
  const kind = typeof body.kind === "string" ? body.kind : "";
  if (!KINDS.has(kind)) return c.json({ error: "invalid_kind" }, 400);

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

  // form_code: required for form_link (catalog-parent-validated, daily-tab parents refused);
  // ignored (stored null) for every other kind.
  let formCode: string | null = null;
  if (kind === "form_link") {
    formCode = typeof body.form_code === "string" ? body.form_code.trim() : "";
    if (formCode.length < 1 || formCode.length > MAX_FORM_CODE) return c.json({ error: "form_code_required" }, 400);
    if (!CATALOG_PARENT_CODES.has(formCode)) return c.json({ error: "unknown_form_code" }, 422);
    if (DAILY_TAB_PARENT_CODES.has(formCode)) return c.json({ error: "daily_tab_form_code" }, 422);
  }

  // options (D5): REQUIRED for select — 1..MAX_OPTIONS non-empty strings, each ≤ MAX_OPTION_LEN
  // after trim, stored as a JSON array. A non-select kind carrying options is a 400 (a shape
  // error the admin editor never produces — stricter than form_code's ignore-on-other-kinds
  // because a silently-dropped option list would LOOK saved in the editor's optimistic draft).
  let options: string | null = null;
  if (kind === "select") {
    const raw = body.options;
    if (!Array.isArray(raw) || raw.length < 1) return c.json({ error: "options_required" }, 400);
    if (raw.length > MAX_OPTIONS) return c.json({ error: "invalid_options" }, 400);
    const cleaned: string[] = [];
    for (const o of raw) {
      const v = typeof o === "string" ? o.trim() : "";
      if (v.length < 1 || v.length > MAX_OPTION_LEN) return c.json({ error: "invalid_options" }, 400);
      cleaned.push(v);
    }
    options = JSON.stringify(cleaned);
  } else if (body.options !== undefined && body.options !== null) {
    return c.json({ error: "options_not_allowed" }, 400);
  }

  return { seq, kind, label, form_code: formCode, options };
}

// Parse the JSON body, rejecting a non-object before any property access (the fieldops_task_write
// guard, verbatim shape).
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

// requireJob / the ownership-scope gate — shared scope machinery, see fieldops_scope.ts (extracted
// from the local copies this module used to carry; contracts unchanged).

// D1 stores `options` as JSON TEXT (0032); the wire serves it PARSED. Defensive: a row whose
// stored JSON doesn't parse to a string array (unreachable through parseRequirement, but the
// read must never 500 on one bad row) serves options=null — the renderer then shows the label
// with no choices rather than dropping the whole job's list.
function parseStoredOptions(raw: unknown): string[] | null {
  if (typeof raw !== "string" || raw.length === 0) return null;
  try {
    const parsed: unknown = JSON.parse(raw);
    if (Array.isArray(parsed) && parsed.every((o) => typeof o === "string")) return parsed as string[];
  } catch {
    /* fall through */
  }
  return null;
}

// Active requirements for a job, display-ordered and bounded — the ONE read both surfaces share.
async function activeRequirements(
  c: Context<{ Bindings: Env; Variables: Vars }>,
  jobId: string,
): Promise<DailyRequirementItem[]> {
  const rows = await c.env.DB.prepare(
    "SELECT id, seq, kind, label, form_code, options FROM job_daily_requirements WHERE job_id = ?1 AND active = 1 ORDER BY seq ASC, id ASC LIMIT ?2",
  )
    .bind(jobId, REQUIREMENTS_LIMIT)
    .all<Omit<DailyRequirementItem, "options"> & { options: string | null }>();
  return (rows.results ?? []).map((r) => ({ ...r, options: parseStoredOptions(r.options) }));
}

export function registerDailyRequirementsRoutes(app: FieldopsApp, gates: FieldopsGates): void {
  // ── GET /api/fieldops/daily-form/status?job_id=…&date=… — LATEST submission per parent family. ────
  // (D2; moved here from fieldops_checklist.ts — it never belonged to the checklist engine, it only
  // lived there for the now-shared fieldops_scope.ts gate helpers.)
  // Backs the Daily tab's form_link "Filed ✓ <time> by <name>" indicators + the "already filed today"
  // banner. For each family in DAILY_STATUS_FAMILIES, returns the newest submission for
  // (job_id, work_date = date) matched on the S4 family convention (form_code = parent OR a versioned
  // variant `parent || '-v%'` — the SAME match the loop-closure reconcile uses). `filed_by_name` is the
  // personnel DISPLAY NAME resolved through submitted_as — no raw-username fallback (the W9 posture;
  // an unmatched account yields NULL and the UI drops the "by …" clause). Read-only, bound-param.
  // OWNERSHIP SCOPE (security review BLOCK fix): cap.tasks.own alone would let ANY portal account
  // probe other jobs' filing activity (incident reports especially). The read is confined to the
  // actor's OWN placement (linked ACTIVE personnel.current_job === job_id — the same resolution the
  // daily generation used); cap.jobtracker.manage / cap.checklist.manage holders (admins) may query
  // any job. Job existence + date shape validated so an unknown job 404s.
  app.get(
    "/api/fieldops/daily-form/status",
    gates.requireSession,
    gates.requireCapability(CAP_TASKS_OWN),
    async (c) => {
      // Daily-report role gate (directive 2026-07-03) FIRST — cap.tasks.own is held by ALL three
      // roles (0013/0023 grants), so the cap alone let a placed subcontractor read this daily
      // surface. Role-checked before any job resolution so an ineligible role learns nothing
      // about job existence either.
      const roleErr = requireDailyReportRole(c);
      if (roleErr) return roleErr;
      const q = c.req.query();
      const date = q.date ?? "";
      if (!DATE_RE.test(date)) return c.json({ error: "invalid_date" }, 400);
      const jobId = q.job_id ?? "";
      const jobErr = await requireJob(c, jobId); // 400 bad shape / 404 unknown job
      if (jobErr) return jobErr;

      // Per-job ownership scope (see header comment): non-admin actors only read their OWN placement.
      const scopeErr = await requireJobScope(c, jobId, SCOPE_BYPASS_CAPS);
      if (scopeErr) return scopeErr;

      // One statement per family (a fixed, module-constant set of five) in a single D1 batch.
      // Newest-first tiebreak mirrors ROLLUP_LINK_SQL (created_at DESC, submission_uuid DESC).
      const statusSql = `
        SELECT sub.created_at AS filed_at,
               (SELECT p.name FROM personnel p WHERE p.username = sub.submitted_as ORDER BY p.id ASC LIMIT 1) AS filed_by_name
        FROM submissions sub
        WHERE sub.job_id = ?1 AND sub.work_date = ?2
          AND (sub.form_code = ?3 OR sub.form_code LIKE ?3 || '-v%')
        ORDER BY sub.created_at DESC, sub.submission_uuid DESC
        LIMIT 1
      `;
      const legs = await c.env.DB.batch(
        DAILY_STATUS_FAMILIES.map((family) => c.env.DB.prepare(statusSql).bind(jobId, date, family)),
      );
      const filed: Record<string, FiledEntry> = {};
      DAILY_STATUS_FAMILIES.forEach((family, i) => {
        const row = legs[i].results?.[0] as FiledEntry | undefined;
        if (row) filed[family] = { filed_at: row.filed_at, filed_by_name: row.filed_by_name ?? null };
      });
      // daily_filed = the daily-report family's entry, surfaced separately for the banner.
      const payload: DailyFormStatus = { filed, daily_filed: filed[DAILY_REPORT_FORM] ?? null };
      return c.json(payload, 200);
    },
  );

  // ── GET /api/fieldops/daily-form/requirements?job_id=… — the job's ACTIVE requirement items. ─────
  // Serves BOTH the Daily tab (renders them inside the daily form's job_requirements section) and
  // the admin job-detail editor (which needs the ids for edit/reorder/deactivate). cap.tasks.own +
  // the SAME per-job ownership scope as /daily-form/status: a non-admin actor may only read the job
  // they are PLACED on (linked ACTIVE personnel.current_job === job_id) — 403 forbidden_job
  // otherwise; cap.jobtracker.manage / cap.checklist.manage holders (admins) may query any job.
  // Requirement CONTENT is client-visible instructions, but cross-job probing is still refused (the
  // status-endpoint security-review posture, applied consistently).
  app.get(
    "/api/fieldops/daily-form/requirements",
    gates.requireSession,
    gates.requireCapability(CAP_TASKS_OWN),
    async (c) => {
      // Daily-report role gate (directive 2026-07-03) — same rationale as /daily-form/status
      // above; the admin job-detail editor rides through on role 'admin'.
      const roleErr = requireDailyReportRole(c);
      if (roleErr) return roleErr;
      const jobId = c.req.query("job_id") ?? "";
      const jobErr = await requireJob(c, jobId); // 400 bad shape / 404 unknown job
      if (jobErr) return jobErr;

      const scopeErr = await requireJobScope(c, jobId, SCOPE_BYPASS_CAPS);
      if (scopeErr) return scopeErr;

      const items = await activeRequirements(c, jobId);
      const payload: DailyRequirementsResponse = { job_id: jobId, items };
      return c.json(payload, 200);
    },
  );

  // ── POST /api/fieldops/daily-form/job/:job_id/requirement — add a requirement item (admin). ──────
  app.post(
    "/api/fieldops/daily-form/job/:job_id/requirement",
    gates.requireSession,
    gates.requireCapability(CAP_MANAGE),
    async (c) => {
      const jobId = c.req.param("job_id");
      const jobErr = await requireJob(c, jobId);
      if (jobErr) return jobErr;
      const body = await readBody(c);
      if (body instanceof Response) return body;
      const item = parseRequirement(c, body);
      if (item instanceof Response) return item;

      const actor = c.get("session").username;
      // Ceiling on the job's ACTIVE list (see REQUIREMENTS_LIMIT) — refused loudly, never trimmed.
      // CS4 TOCTOU fold (the tracked D4 fast-follow): the count predicate lives IN the INSERT's
      // WHERE (INSERT … SELECT … WHERE (SELECT COUNT(*) …) < limit), so check + write are one
      // atomic statement — two concurrent adds can no longer both pass a pre-count and exceed the
      // ceiling. Mutation + audit in ONE batch (W4); the audit rides changes()=1, so a refused add
      // audits nothing (exactly as the old pre-count-then-return did). changes()=0 → the same 409
      // too_many_items the pre-check produced.
      const res = await c.env.DB.batch([
        c.env.DB
          .prepare(
            `INSERT INTO job_daily_requirements (job_id, seq, kind, label, form_code, options)
             SELECT ?1,?2,?3,?4,?5,?6
             WHERE (SELECT COUNT(*) FROM job_daily_requirements WHERE job_id = ?1 AND active = 1) < ?7
             RETURNING id`,
          )
          .bind(jobId, item.seq, item.kind, item.label, item.form_code, item.options, REQUIREMENTS_LIMIT),
        auditStmtIfChanged(c, actor, "daily_requirement_add", jobId, { job_id: jobId, ...item }),
      ]);
      const newId = (res[0].results?.[0] as { id: number } | undefined)?.id ?? null;
      if (newId === null) return c.json({ error: "too_many_items" }, 409);
      return c.json({ ok: true, id: newId }, 201);
    },
  );

  // ── POST /api/fieldops/daily-form/job/:job_id/requirement/:item_id/edit — replace an item's
  // fields (label / kind / form_code / seq — reorder is a seq edit through this same route). ────────
  app.post(
    "/api/fieldops/daily-form/job/:job_id/requirement/:item_id/edit",
    gates.requireSession,
    gates.requireCapability(CAP_MANAGE),
    async (c) => {
      const jobId = c.req.param("job_id");
      const jobErr = await requireJob(c, jobId);
      if (jobErr) return jobErr;
      const itemId = parseInt(c.req.param("item_id"), 10);
      if (isNaN(itemId)) return c.json({ error: "invalid_id" }, 400);
      const body = await readBody(c);
      if (body instanceof Response) return body;
      const item = parseRequirement(c, body);
      if (item instanceof Response) return item;
      const actor = c.get("session").username;
      // Scoped to (id, THIS job, active): an item id from another job — or an already-deactivated
      // row — is a 404, never a cross-job write. Conditional audit rides changes()=1 (W4).
      const res = await c.env.DB.batch([
        c.env.DB
          .prepare(
            "UPDATE job_daily_requirements SET seq=?3, kind=?4, label=?5, form_code=?6, options=?7 WHERE id=?1 AND job_id=?2 AND active=1",
          )
          .bind(itemId, jobId, item.seq, item.kind, item.label, item.form_code, item.options),
        auditStmtIfChanged(c, actor, "daily_requirement_edit", jobId, { job_id: jobId, item_id: itemId, ...item }),
      ]);
      if ((res[0].meta.changes ?? 0) === 0) return c.json({ error: "not_found" }, 404);
      return c.json({ ok: true, id: itemId }, 200);
    },
  );

  // ── POST /api/fieldops/daily-form/job/:job_id/requirement/:item_id/deactivate — soft-delete. ─────
  // active=0 removes the item from NEW renders; historical submissions keep their self-describing
  // values array, and the audit_log keeps the forensic record (no hard delete).
  app.post(
    "/api/fieldops/daily-form/job/:job_id/requirement/:item_id/deactivate",
    gates.requireSession,
    gates.requireCapability(CAP_MANAGE),
    async (c) => {
      const jobId = c.req.param("job_id");
      const jobErr = await requireJob(c, jobId);
      if (jobErr) return jobErr;
      const itemId = parseInt(c.req.param("item_id"), 10);
      if (isNaN(itemId)) return c.json({ error: "invalid_id" }, 400);
      const actor = c.get("session").username;
      const res = await c.env.DB.batch([
        c.env.DB
          .prepare("UPDATE job_daily_requirements SET active=0 WHERE id=?1 AND job_id=?2 AND active=1")
          .bind(itemId, jobId),
        auditStmtIfChanged(c, actor, "daily_requirement_deactivate", jobId, { job_id: jobId, item_id: itemId }),
      ]);
      if ((res[0].meta.changes ?? 0) === 0) return c.json({ error: "not_found" }, 404);
      return c.json({ ok: true, id: itemId }, 200);
    },
  );
}
