import type { Context } from "hono";
import type { FieldopsApp, FieldopsGates } from "./fieldops_gates";
import type { Env, Vars } from "./types";
import { auditStmt } from "./audit";

// P3 Materials (M1) — material_catalog CRUD. cap.materials.manage (admin-only).
// `material_catalog` is the datasheet-backed TYPE vocabulary the per-job Material List draws from
// (manifest model, M2). A PLAIN reference table (NOT integrity-bar): create INSERTs, edit UPDATEs
// in place, retire is a SOFT-delete (active=0) so a receipt/incident referencing a catalog_id
// keeps its target. Each mutation + its audit_log row land in ONE D1 batch (W4). Send-free
// (D1 only). The server owns the `id` (AUTOINCREMENT) — never client-supplied. Mirrors the
// equipment-roster writer; this is office reference-data management, not the field-write surface.

type Ctx = Context<{ Bindings: Env; Variables: Vars }>;
const MAX_MODEL = 128;
const MAX_NAME = 128;
const MAX_SHORT = 64;
const MAX_SPECS = 512;
const MAX_SRC_FILES = 50;
const MAX_SRC_LEN = 512;

function badId(c: Ctx): number | null {
  const id = parseInt(c.req.param("id") ?? "", 10);
  return isNaN(id) ? null : id;
}

// Shared field validation for create + update. Returns the cleaned tuple or an error string.
type Fields = { model_id: string; manufacturer: string | null; category: string; key_specs: string | null; unit_cost: number | null };
function readFields(body: Record<string, unknown>): Fields | string {
  const model_id = typeof body.model_id === "string" ? body.model_id.trim() : "";
  const manufacturer = typeof body.manufacturer === "string" ? body.manufacturer : null;
  const category = typeof body.category === "string" ? body.category.trim() : "";
  const key_specs = typeof body.key_specs === "string" ? body.key_specs : null;
  // unit_cost: optional reference price. null/absent ok; if present must be a finite number >= 0.
  let unit_cost: number | null = null;
  if (body.unit_cost !== undefined && body.unit_cost !== null && body.unit_cost !== "") {
    if (typeof body.unit_cost !== "number" || !Number.isFinite(body.unit_cost) || body.unit_cost < 0) return "invalid_unit_cost";
    unit_cost = body.unit_cost;
  }
  if (model_id.length < 1 || model_id.length > MAX_MODEL) return "invalid_model_id";
  if (manufacturer !== null && manufacturer.length > MAX_NAME) return "invalid_manufacturer";
  if (category.length < 1 || category.length > MAX_SHORT) return "invalid_category";
  if (key_specs !== null && key_specs.length > MAX_SPECS) return "invalid_key_specs";
  return { model_id, manufacturer, category, key_specs, unit_cost };
}

export function registerMaterialWriteRoutes(app: FieldopsApp, gates: FieldopsGates): void {
  // POST /api/fieldops/material — add a type to the catalog. source_files (provenance) optional.
  app.post(
    "/api/fieldops/material",
    gates.requireSession,
    gates.requireCapability("cap.materials.manage"),
    async (c) => {
      let body: Record<string, unknown>;
      try {
        body = (await c.req.json()) as Record<string, unknown>;
      } catch {
        return c.json({ error: "bad_request" }, 400);
      }
      if (typeof body !== "object" || body === null || Array.isArray(body)) return c.json({ error: "bad_request" }, 400);

      const f = readFields(body);
      if (typeof f === "string") return c.json({ error: f }, 400);

      // source_files: optional JSON-provenance array of strings (set at create, not edited).
      let sourceFiles = "[]";
      if (body.source_files !== undefined && body.source_files !== null) {
        if (!Array.isArray(body.source_files) || body.source_files.length > MAX_SRC_FILES) return c.json({ error: "invalid_source_files" }, 400);
        for (const s of body.source_files) {
          if (typeof s !== "string" || s.length > MAX_SRC_LEN) return c.json({ error: "invalid_source_files" }, 400);
        }
        sourceFiles = JSON.stringify(body.source_files);
      }

      const actor = c.get("session").username;
      const res = await c.env.DB.batch([
        c.env.DB
          .prepare(
            `INSERT INTO material_catalog (model_id, manufacturer, category, key_specs, unit_cost, source_files)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6) RETURNING id`,
          )
          .bind(f.model_id, f.manufacturer, f.category, f.key_specs, f.unit_cost, sourceFiles),
        auditStmt(c, actor, "material_catalog_create", f.model_id, { model_id: f.model_id, manufacturer: f.manufacturer, category: f.category }),
      ]);
      const newId = (res[0].results?.[0] as { id: number } | undefined)?.id ?? null;
      return c.json({ ok: true, id: newId }, 201);
    },
  );

  // POST /api/fieldops/material/:id/update — edit catalog fields (NOT source_files provenance).
  app.post(
    "/api/fieldops/material/:id/update",
    gates.requireSession,
    gates.requireCapability("cap.materials.manage"),
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

      const f = readFields(body);
      if (typeof f === "string") return c.json({ error: f }, 400);

      const actor = c.get("session").username;
      const res = await c.env.DB.batch([
        c.env.DB
          .prepare(
            `UPDATE material_catalog
             SET model_id = ?2, manufacturer = ?3, category = ?4, key_specs = ?5, unit_cost = ?6, updated_at = unixepoch()
             WHERE id = ?1 AND active = 1`,
          )
          .bind(id, f.model_id, f.manufacturer, f.category, f.key_specs, f.unit_cost),
        c.env.DB
          .prepare("INSERT INTO audit_log (actor_username, action, target_username, detail) SELECT ?1,?2,?3,?4 WHERE changes()=1")
          .bind(actor, "material_catalog_update", String(id), JSON.stringify({ catalog_id: id, model_id: f.model_id })),
      ]);
      if ((res[0].meta.changes ?? 0) === 0) return c.json({ error: "not_found" }, 404);
      return c.json({ ok: true, id }, 200);
    },
  );

  // POST /api/fieldops/material/:id/delete — SOFT-retire (active=0). Idempotent; preserves history.
  app.post(
    "/api/fieldops/material/:id/delete",
    gates.requireSession,
    gates.requireCapability("cap.materials.manage"),
    async (c) => {
      const id = badId(c);
      if (id === null) return c.json({ error: "invalid_id" }, 400);
      const actor = c.get("session").username;
      const res = await c.env.DB.batch([
        c.env.DB.prepare("UPDATE material_catalog SET active = 0, updated_at = unixepoch() WHERE id = ?1 AND active = 1").bind(id),
        c.env.DB
          .prepare("INSERT INTO audit_log (actor_username, action, target_username, detail) SELECT ?1,?2,?3,?4 WHERE changes()=1")
          .bind(actor, "material_catalog_retire", String(id), JSON.stringify({ catalog_id: id })),
      ]);
      if ((res[0].meta.changes ?? 0) === 0) {
        // 0 changes = unknown id (404) or already-retired (idempotent 200).
        const row = await c.env.DB.prepare("SELECT id FROM material_catalog WHERE id = ?1").bind(id).first();
        return row ? c.json({ ok: true, id, already_retired: true }, 200) : c.json({ error: "not_found" }, 404);
      }
      return c.json({ ok: true, id }, 200);
    },
  );
}
