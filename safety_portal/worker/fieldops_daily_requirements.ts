import type { Context } from "hono";
import type { Env, Vars } from "./types";
import type { FieldopsApp, FieldopsGates } from "./fieldops_gates";
import { auditStmt } from "./audit";
import catalog from "../catalog.json";

// SOP daily form slice D4 — per-job daily-form REQUIREMENTS (migration 0030
// job_daily_requirements). The BASE daily form is a git-owned definition (daily-report-v4 carries a
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
//     forbidden_job otherwise; cap.jobtracker.manage / cap.checklist.manage holders any job).

const MAX_LABEL = 256; // same bound as checklist item labels (0026 discipline)
const MAX_FORM_CODE = 64;
const MAX_SEQ = 100_000;
// Defensive ceilings: the read is bounded (never an unbounded dump) and the add route refuses to
// grow a job's ACTIVE list past the same ceiling — an authenticated-admin resource-exhaustion
// vector otherwise (the publishValidation MAX_* bound rationale).
const REQUIREMENTS_LIMIT = 200;

// The closed item vocabulary BOTH renderers (SPA FormRenderer + Python form_pdf via the filed
// values array) understand. note = read-only guidance; confirm = checkbox; text = free answer;
// form_link = deep link to another form type.
const KINDS = new Set(["note", "confirm", "text", "form_link"]);

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

const CAP_MANAGE = "cap.checklist.manage"; // admin authoring (same cap as the checklist editors)
const CAP_TASKS_OWN = "cap.tasks.own"; // the tab read (the placed manager's surface)

// One requirement row as served to BOTH surfaces (the tab render and the admin editor).
interface RequirementRow {
  id: number;
  seq: number;
  kind: string;
  label: string;
  form_code: string | null;
}

// The normalized, validated write payload.
interface ParsedRequirement {
  seq: number;
  kind: string;
  label: string;
  form_code: string | null;
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
  // ignored (stored null) for note/confirm/text.
  let formCode: string | null = null;
  if (kind === "form_link") {
    formCode = typeof body.form_code === "string" ? body.form_code.trim() : "";
    if (formCode.length < 1 || formCode.length > MAX_FORM_CODE) return c.json({ error: "form_code_required" }, 400);
    if (!CATALOG_PARENT_CODES.has(formCode)) return c.json({ error: "unknown_form_code" }, 422);
    if (DAILY_TAB_PARENT_CODES.has(formCode)) return c.json({ error: "daily_tab_form_code" }, 422);
  }

  return { seq, kind, label, form_code: formCode };
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

// job must exist (active or not — requirements can be authored ahead on any real job). Returns the
// error Response, or null on success. Same contract as fieldops_checklist.requireJob.
async function requireJob(
  c: Context<{ Bindings: Env; Variables: Vars }>,
  jobId: string,
): Promise<Response | null> {
  if (jobId.length < 1 || jobId.length > 64) return c.json({ error: "invalid_job_id" }, 400);
  const job = await c.env.DB.prepare("SELECT job_id FROM jobs WHERE job_id = ?1").bind(jobId).first();
  if (!job) return c.json({ error: "not_found" }, 404);
  return null;
}

// The acting session's linked ACTIVE personnel row (the same resolution /daily-form/status uses for
// its ownership scope). LIMIT 1 on the unconstrained username link — deterministic lowest id.
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

// Active requirements for a job, display-ordered and bounded — the ONE read both surfaces share.
async function activeRequirements(
  c: Context<{ Bindings: Env; Variables: Vars }>,
  jobId: string,
): Promise<RequirementRow[]> {
  const rows = await c.env.DB.prepare(
    "SELECT id, seq, kind, label, form_code FROM job_daily_requirements WHERE job_id = ?1 AND active = 1 ORDER BY seq ASC, id ASC LIMIT ?2",
  )
    .bind(jobId, REQUIREMENTS_LIMIT)
    .all<RequirementRow>();
  return rows.results ?? [];
}

export function registerDailyRequirementsRoutes(app: FieldopsApp, gates: FieldopsGates): void {
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
      const jobId = c.req.query("job_id") ?? "";
      const jobErr = await requireJob(c, jobId); // 400 bad shape / 404 unknown job
      if (jobErr) return jobErr;

      const caps = c.get("capabilities");
      if (!caps.has("cap.jobtracker.manage") && !caps.has("cap.checklist.manage")) {
        const person = await resolveActorPersonnel(c);
        if (!person || person.current_job !== jobId) return c.json({ error: "forbidden_job" }, 403);
      }

      const items = await activeRequirements(c, jobId);
      return c.json({ job_id: jobId, items }, 200);
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

      // Ceiling on the job's ACTIVE list (see REQUIREMENTS_LIMIT) — refused loudly, never trimmed.
      const count = await c.env.DB.prepare(
        "SELECT COUNT(*) AS n FROM job_daily_requirements WHERE job_id = ?1 AND active = 1",
      )
        .bind(jobId)
        .first<{ n: number }>();
      if ((count?.n ?? 0) >= REQUIREMENTS_LIMIT) return c.json({ error: "too_many_items" }, 409);

      const actor = c.get("session").username;
      // Mutation + audit in ONE batch (W4) — the record is atomic with the change it describes.
      const res = await c.env.DB.batch([
        c.env.DB
          .prepare(
            "INSERT INTO job_daily_requirements (job_id, seq, kind, label, form_code) VALUES (?1,?2,?3,?4,?5) RETURNING id",
          )
          .bind(jobId, item.seq, item.kind, item.label, item.form_code),
        auditStmt(c, actor, "daily_requirement_add", jobId, { job_id: jobId, ...item }),
      ]);
      const newId = (res[0].results?.[0] as { id: number } | undefined)?.id ?? null;
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
            "UPDATE job_daily_requirements SET seq=?3, kind=?4, label=?5, form_code=?6 WHERE id=?1 AND job_id=?2 AND active=1",
          )
          .bind(itemId, jobId, item.seq, item.kind, item.label, item.form_code),
        c.env.DB
          .prepare(
            "INSERT INTO audit_log (actor_username, action, target_username, detail) SELECT ?1,?2,?3,?4 WHERE changes()=1",
          )
          .bind(actor, "daily_requirement_edit", jobId, JSON.stringify({ job_id: jobId, item_id: itemId, ...item })),
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
        c.env.DB
          .prepare(
            "INSERT INTO audit_log (actor_username, action, target_username, detail) SELECT ?1,?2,?3,?4 WHERE changes()=1",
          )
          .bind(actor, "daily_requirement_deactivate", jobId, JSON.stringify({ job_id: jobId, item_id: itemId })),
      ]);
      if ((res[0].meta.changes ?? 0) === 0) return c.json({ error: "not_found" }, 404);
      return c.json({ ok: true, id: itemId }, 200);
    },
  );
}
