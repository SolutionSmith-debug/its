import type { Context } from "hono";
import type { FieldopsApp, FieldopsGates } from "./fieldops_gates";
import type { Env, Vars } from "./types";
import { auditStmt, auditStmtIfChanged } from "./audit";
import { requireJob, requireJobScope } from "./fieldops_scope";
import type { ExpectedMaterialRow, ExpectedMaterialsResponse } from "./wire-types";

// Material receipts (M1) — per-job EXPECTED-materials list (`job_expected_materials`, migration
// 0031). The office records what a job is expecting; managers confirm receipt against that list.
// Send-free throughout (D1 status flips only — no transmission); bound params only; every mutation
// lands with its audit_log row in ONE D1 batch (W4).
//
//   • Expectation CRUD (cap.materials.manage — admin): add (catalog-pick OR free-text) / edit /
//     seq-reorder / deactivate (soft, active=0). material_id, when given, is validated against an
//     ACTIVE material_catalog row (the 0019 type vocabulary); description is REQUIRED when
//     material_id is null. Edits are confined to status='expected' rows — a received/incident row
//     is a historical receipt record and must not be rewritten (409 not_editable); reorder (seq)
//     stays allowed on any active row so the list keeps a coherent order.
//   • GET /api/fieldops/expected-materials?job_id — cap.materials.receive, PER-JOB ownership
//     scope for non-admins (the /daily-form/status pattern: the actor's linked ACTIVE
//     personnel.current_job must equal job_id, else 403 forbidden_job); cap.jobtracker.manage /
//     cap.materials.manage holders (admins) may query any job. Returns active rows in seq order
//     with display fields: the resolved catalog name for catalog rows, and received_by resolved
//     to the personnel DISPLAY NAME only (W9 — an unmatched account yields NULL, never the raw
//     username; the stored username never leaves the Worker).
//   • POST /api/fieldops/expected-material/:id/receive and .../:id/flag-incident —
//     cap.materials.receive + the SAME per-job scope. The status transition is guarded IN-WHERE
//     (status='expected'): the UPDATE flips expected→received (or →incident), stamps
//     received_at + the acting account, and the audit INSERT is conditional on changes()=1 in the
//     same batch — a repeat (or a lost race) is a clean 409 with NO second stamp and NO second
//     audit row. flag-incident additionally REQUIRES a note (why it's an incident).
//     M2 wires these two routes into the daily form (the D.13 deliveries region: "Confirm
//     receipt" + "Report material incident →" alongside filing the material-incident form);
//     in M1 they are exercised by tests + the read surface shows the resulting state.

type Ctx = Context<{ Bindings: Env; Variables: Vars }>;

const MAX_DESCRIPTION = 256;
const MAX_UNIT = 32;
const MAX_NOTE = 500;
const MAX_SEQ = 1_000_000;
const MAX_QTY = 1_000_000_000;
const DATE_RE = /^\d{4}-\d{2}-\d{2}$/;

// The two caps that bypass the per-job ownership scope (admins hold both; a manager/submitter
// holds neither) — the same admin set the /daily-form/status scope recognizes, plus
// cap.materials.manage (the office editor of this very list may naturally see any job's list).
const SCOPE_BYPASS_CAPS = ["cap.jobtracker.manage", "cap.materials.manage"] as const;

function badId(c: Ctx): number | null {
  const id = parseInt(c.req.param("id") ?? "", 10);
  return isNaN(id) ? null : id;
}

// requireJob / requireJobScope — shared scope machinery, see fieldops_scope.ts (extracted from the
// local copies this module used to carry; contracts unchanged; SCOPE_BYPASS_CAPS above stays THIS
// module's own divergent admin set, passed explicitly at each gate site).

// Shared expectation-field validation for create + update (content fields; seq is create-only —
// reorder has its own route). Returns the cleaned tuple or an error string. material_id is only
// SHAPE-checked here; its live-catalog check is async and runs at the call site.
type ExpectationFields = {
  material_id: number | null;
  description: string | null;
  qty: number | null;
  unit: string | null;
  expected_date: string | null;
};
function readExpectationFields(body: Record<string, unknown>): ExpectationFields | string {
  let material_id: number | null = null;
  if (body.material_id !== undefined && body.material_id !== null) {
    if (typeof body.material_id !== "number" || !Number.isInteger(body.material_id) || body.material_id < 1) {
      return "invalid_material_id";
    }
    material_id = body.material_id;
  }
  let description: string | null = null;
  if (body.description !== undefined && body.description !== null) {
    if (typeof body.description !== "string") return "invalid_description";
    const t = body.description.trim();
    if (t.length > MAX_DESCRIPTION) return "invalid_description";
    description = t.length ? t : null;
  }
  // Free-text rows carry their identity in description — required when no catalog pick.
  if (material_id === null && description === null) return "description_required";
  let qty: number | null = null;
  if (body.qty !== undefined && body.qty !== null && body.qty !== "") {
    if (typeof body.qty !== "number" || !Number.isFinite(body.qty) || body.qty <= 0 || body.qty > MAX_QTY) {
      return "invalid_qty";
    }
    qty = body.qty;
  }
  let unit: string | null = null;
  if (body.unit !== undefined && body.unit !== null) {
    if (typeof body.unit !== "string" || body.unit.length > MAX_UNIT) return "invalid_unit";
    const t = body.unit.trim();
    unit = t.length ? t : null;
  }
  let expected_date: string | null = null;
  if (body.expected_date !== undefined && body.expected_date !== null && body.expected_date !== "") {
    if (typeof body.expected_date !== "string" || !DATE_RE.test(body.expected_date)) {
      return "invalid_expected_date";
    }
    expected_date = body.expected_date;
  }
  return { material_id, description, qty, unit, expected_date };
}

// material_id, when given, must name an ACTIVE catalog type (a retired type must not gain new
// expectations; existing rows referencing a later-retired type are untouched — soft-ref posture).
async function catalogIdValid(c: Ctx, materialId: number | null): Promise<boolean> {
  if (materialId === null) return true;
  const row = await c.env.DB.prepare("SELECT id FROM material_catalog WHERE id = ?1 AND active = 1")
    .bind(materialId)
    .first();
  return row !== null;
}

async function readJsonBody(c: Ctx): Promise<Record<string, unknown> | null> {
  let body: unknown;
  try {
    body = await c.req.json();
  } catch {
    return null;
  }
  if (typeof body !== "object" || body === null || Array.isArray(body)) return null;
  return body as Record<string, unknown>;
}

// receive/flag-incident accept an OPTIONAL body ({} / absent both fine) — read text-first so an
// empty POST doesn't 400 on JSON.parse.
async function readOptionalJsonBody(c: Ctx): Promise<Record<string, unknown> | null> {
  const raw = await c.req.text();
  if (!raw.trim()) return {};
  let body: unknown;
  try {
    body = JSON.parse(raw);
  } catch {
    return null;
  }
  if (typeof body !== "object" || body === null || Array.isArray(body)) return null;
  return body as Record<string, unknown>;
}

// Shared action-field validation for receive + flag-incident: optional qty_received, note
// (REQUIRED for incident — enforced at the call site by noteRequired).
type ActionFields = { qty_received: number | null; note: string | null };
function readActionFields(body: Record<string, unknown>): ActionFields | string {
  let qty_received: number | null = null;
  if (body.qty_received !== undefined && body.qty_received !== null && body.qty_received !== "") {
    if (
      typeof body.qty_received !== "number" ||
      !Number.isFinite(body.qty_received) ||
      body.qty_received <= 0 ||
      body.qty_received > MAX_QTY
    ) {
      return "invalid_qty_received";
    }
    qty_received = body.qty_received;
  }
  let note: string | null = null;
  if (body.note !== undefined && body.note !== null) {
    if (typeof body.note !== "string" || body.note.length > MAX_NOTE) return "invalid_note";
    const t = body.note.trim();
    note = t.length ? t : null;
  }
  return { qty_received, note };
}

// The read row — single-sourced as ExpectedMaterialRow in wire-types.ts (the SPA re-exports the
// same type, so a drift here fails the typecheck on both sides).

export function registerExpectedMaterialsRoutes(app: FieldopsApp, gates: FieldopsGates): void {
  // ── GET /api/fieldops/expected-materials?job_id — the job's active expectation list. ─────────────
  // cap.materials.receive + the per-job ownership scope (bypass: jobtracker.manage /
  // materials.manage). Active rows, seq order. Display fields only: material_name is the resolved
  // catalog model_id (NULL for free-text rows); received_by_name is DISPLAY-NAME-ONLY (W9 — the
  // stored account username is never returned; an unmatched account yields NULL).
  app.get(
    "/api/fieldops/expected-materials",
    gates.requireSession,
    gates.requireCapability("cap.materials.receive"),
    async (c) => {
      const jobId = c.req.query("job_id") ?? "";
      const jobErr = await requireJob(c, jobId); // 400 bad shape / 404 unknown job
      if (jobErr) return jobErr;
      const scopeErr = await requireJobScope(c, jobId, SCOPE_BYPASS_CAPS); // 403 forbidden_job outside own placement
      if (scopeErr) return scopeErr;

      const res = await c.env.DB.prepare(
        `SELECT jem.id, jem.material_id,
                (SELECT mc.model_id FROM material_catalog mc WHERE mc.id = jem.material_id) AS material_name,
                jem.description, jem.qty, jem.unit, jem.expected_date, jem.status,
                jem.received_at,
                (SELECT p.name FROM personnel p WHERE p.username = jem.received_by ORDER BY p.id ASC LIMIT 1)
                  AS received_by_name,
                jem.qty_received, jem.note, jem.seq
         FROM job_expected_materials jem
         WHERE jem.job_id = ?1 AND jem.active = 1
         ORDER BY jem.seq ASC, jem.id ASC
         LIMIT 500`,
      )
        .bind(jobId)
        .all<ExpectedMaterialRow>();
      const payload: ExpectedMaterialsResponse = { expected_materials: res.results ?? [] };
      return c.json(payload, 200);
    },
  );

  // ── POST /api/fieldops/expected-material — add an expectation (office; cap.materials.manage). ────
  // Catalog-pick (material_id, validated ACTIVE) OR free-text (description required). seq accepted
  // at create (the UI seeds max+10); mutation + audit in ONE batch (W4).
  app.post(
    "/api/fieldops/expected-material",
    gates.requireSession,
    gates.requireCapability("cap.materials.manage"),
    async (c) => {
      const body = await readJsonBody(c);
      if (body === null) return c.json({ error: "bad_request" }, 400);
      const jobId = typeof body.job_id === "string" ? body.job_id : "";
      const jobErr = await requireJob(c, jobId);
      if (jobErr) return jobErr;
      const f = readExpectationFields(body);
      if (typeof f === "string") return c.json({ error: f }, 400);
      if (!(await catalogIdValid(c, f.material_id))) return c.json({ error: "invalid_material_id" }, 400);
      let seq = 0;
      if (body.seq !== undefined) {
        if (typeof body.seq !== "number" || !Number.isInteger(body.seq) || body.seq < 0 || body.seq > MAX_SEQ) {
          return c.json({ error: "invalid_seq" }, 400);
        }
        seq = body.seq;
      }

      const actor = c.get("session").username;
      const res = await c.env.DB.batch([
        c.env.DB
          .prepare(
            `INSERT INTO job_expected_materials (job_id, material_id, description, qty, unit, expected_date, seq)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7) RETURNING id`,
          )
          .bind(jobId, f.material_id, f.description, f.qty, f.unit, f.expected_date, seq),
        auditStmt(c, actor, "expected_material_create", jobId, {
          job_id: jobId,
          material_id: f.material_id,
          description: f.description,
          qty: f.qty,
        }),
      ]);
      const newId = (res[0].results?.[0] as { id: number } | undefined)?.id ?? null;
      return c.json({ ok: true, id: newId }, 201);
    },
  );

  // ── POST /api/fieldops/expected-material/:id/update — edit the content fields (office). ──────────
  // Full-replace of material_id/description/qty/unit/expected_date, confined to status='expected'
  // rows (a received/incident row is a receipt record — 409 not_editable). Conditional audit in the
  // same batch (WHERE changes()=1), so a no-op guard miss never writes a lying audit row.
  app.post(
    "/api/fieldops/expected-material/:id/update",
    gates.requireSession,
    gates.requireCapability("cap.materials.manage"),
    async (c) => {
      const id = badId(c);
      if (id === null) return c.json({ error: "invalid_id" }, 400);
      const body = await readJsonBody(c);
      if (body === null) return c.json({ error: "bad_request" }, 400);
      const f = readExpectationFields(body);
      if (typeof f === "string") return c.json({ error: f }, 400);
      if (!(await catalogIdValid(c, f.material_id))) return c.json({ error: "invalid_material_id" }, 400);

      const actor = c.get("session").username;
      const res = await c.env.DB.batch([
        c.env.DB
          .prepare(
            `UPDATE job_expected_materials
             SET material_id = ?2, description = ?3, qty = ?4, unit = ?5, expected_date = ?6
             WHERE id = ?1 AND active = 1 AND status = 'expected'`,
          )
          .bind(id, f.material_id, f.description, f.qty, f.unit, f.expected_date),
        auditStmtIfChanged(c, actor, "expected_material_update", String(id), { expectation_id: id, material_id: f.material_id, description: f.description }),
      ]);
      if ((res[0].meta.changes ?? 0) === 0) {
        // 0 changes = unknown/deactivated (404) or already received/incident (409 — say which).
        const row = await c.env.DB.prepare("SELECT status FROM job_expected_materials WHERE id = ?1 AND active = 1").bind(id).first<{ status: string }>();
        return row ? c.json({ error: "not_editable", status: row.status }, 409) : c.json({ error: "not_found" }, 404);
      }
      return c.json({ ok: true, id }, 200);
    },
  );

  // ── POST /api/fieldops/expected-material/:id/seq — reorder (office). ─────────────────────────────
  // seq-only write, allowed on ANY active row (received/incident rows keep their place in a
  // reordered list — content edits stay locked above). The UI drives this with the checklist
  // planRenumber convention (10/20/30 renumber; one call per changed row).
  app.post(
    "/api/fieldops/expected-material/:id/seq",
    gates.requireSession,
    gates.requireCapability("cap.materials.manage"),
    async (c) => {
      const id = badId(c);
      if (id === null) return c.json({ error: "invalid_id" }, 400);
      const body = await readJsonBody(c);
      if (body === null) return c.json({ error: "bad_request" }, 400);
      if (typeof body.seq !== "number" || !Number.isInteger(body.seq) || body.seq < 0 || body.seq > MAX_SEQ) {
        return c.json({ error: "invalid_seq" }, 400);
      }

      const actor = c.get("session").username;
      const res = await c.env.DB.batch([
        c.env.DB
          .prepare("UPDATE job_expected_materials SET seq = ?2 WHERE id = ?1 AND active = 1")
          .bind(id, body.seq),
        auditStmtIfChanged(c, actor, "expected_material_reorder", String(id), { expectation_id: id, seq: body.seq }),
      ]);
      if ((res[0].meta.changes ?? 0) === 0) return c.json({ error: "not_found" }, 404);
      return c.json({ ok: true, id }, 200);
    },
  );

  // ── POST /api/fieldops/expected-material/:id/delete — deactivate (office; soft, idempotent). ─────
  // active=0 keeps the row (a received/incident row is history). Mirrors the catalog retire shape:
  // second call → 200 already_inactive with NO second audit.
  app.post(
    "/api/fieldops/expected-material/:id/delete",
    gates.requireSession,
    gates.requireCapability("cap.materials.manage"),
    async (c) => {
      const id = badId(c);
      if (id === null) return c.json({ error: "invalid_id" }, 400);
      const actor = c.get("session").username;
      const res = await c.env.DB.batch([
        c.env.DB.prepare("UPDATE job_expected_materials SET active = 0 WHERE id = ?1 AND active = 1").bind(id),
        auditStmtIfChanged(c, actor, "expected_material_deactivate", String(id), { expectation_id: id }),
      ]);
      if ((res[0].meta.changes ?? 0) === 0) {
        const row = await c.env.DB.prepare("SELECT id FROM job_expected_materials WHERE id = ?1").bind(id).first();
        return row ? c.json({ ok: true, id, already_inactive: true }, 200) : c.json({ error: "not_found" }, 404);
      }
      return c.json({ ok: true, id }, 200);
    },
  );

  // Shared receive / flag-incident implementation. Both are cap.materials.receive + the per-job
  // ownership scope; both flip status FROM 'expected' with the guard IN the WHERE clause and the
  // audit conditional on changes()=1 in the SAME batch (W4) — a repeat is a 409 with exactly one
  // stamp and exactly one audit row ever written. M2 wires both into the daily form.
  async function actionExpectation(
    c: Ctx,
    nextStatus: "received" | "incident",
    auditAction: string,
    noteRequired: boolean,
  ): Promise<Response> {
    const id = badId(c);
    if (id === null) return c.json({ error: "invalid_id" }, 400);
    const body = await readOptionalJsonBody(c);
    if (body === null) return c.json({ error: "bad_request" }, 400);
    const f = readActionFields(body);
    if (typeof f === "string") return c.json({ error: f }, 400);
    if (noteRequired && f.note === null) return c.json({ error: "note_required" }, 400);

    // Resolve the row first — its job_id anchors the ownership-scope check.
    const row = await c.env.DB.prepare(
      "SELECT id, job_id FROM job_expected_materials WHERE id = ?1 AND active = 1",
    )
      .bind(id)
      .first<{ id: number; job_id: string }>();
    if (!row) return c.json({ error: "not_found" }, 404);
    const scopeErr = await requireJobScope(c, row.job_id, SCOPE_BYPASS_CAPS);
    if (scopeErr) return scopeErr;

    const actor = c.get("session").username;
    const res = await c.env.DB.batch([
      c.env.DB
        .prepare(
          `UPDATE job_expected_materials
           SET status = ?2, received_at = unixepoch(), received_by = ?3, qty_received = ?4, note = ?5
           WHERE id = ?1 AND active = 1 AND status = 'expected'`,
        )
        .bind(id, nextStatus, actor, f.qty_received, f.note),
      auditStmtIfChanged(c, actor, auditAction, row.job_id, { expectation_id: id, job_id: row.job_id, qty_received: f.qty_received }),
    ]);
    if ((res[0].meta.changes ?? 0) === 0) return c.json({ error: "already_actioned" }, 409);
    return c.json({ ok: true, id, status: nextStatus }, 200);
  }

  // ── POST /api/fieldops/expected-material/:id/receive — confirm receipt (manager/field). ──────────
  app.post(
    "/api/fieldops/expected-material/:id/receive",
    gates.requireSession,
    gates.requireCapability("cap.materials.receive"),
    (c) => actionExpectation(c, "received", "expected_material_receive", false),
  );

  // ── POST /api/fieldops/expected-material/:id/flag-incident — flag a delivery problem. ────────────
  // note REQUIRED (what's wrong). M2 pairs this with filing the material-incident form.
  app.post(
    "/api/fieldops/expected-material/:id/flag-incident",
    gates.requireSession,
    gates.requireCapability("cap.materials.receive"),
    (c) => actionExpectation(c, "incident", "expected_material_incident", true),
  );
}
