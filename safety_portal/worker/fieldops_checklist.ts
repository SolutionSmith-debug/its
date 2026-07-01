import type { Context } from "hono";
import type { Env, Vars } from "./types";
import type { FieldopsApp, FieldopsGates } from "./fieldops_gates";
import { auditStmt } from "./audit";

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

const ITEM_TYPES = new Set(["form_linked", "manual_attest", "count", "inspection"]);
// form_code identifies the target form — required for the two form-bearing types.
const FORM_REQUIRED = new Set(["form_linked", "inspection"]);
const CAP_CHECKLIST = "cap.checklist.manage";

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

      // THE MERGE. ?1 = job_id (repeated positional bind). The default leg excludes any default item
      // whose id appears in this job's override suppression set; the override leg adds the job's own
      // content items (suppression markers — suppresses_default_item_id NOT NULL — are excluded from
      // both legs). origin distinguishes default (suppressable) from override (deletable) for the UI.
      const mergeSql = `
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
      const merged = await c.env.DB.prepare(mergeSql).bind(jobId).all();

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
}
