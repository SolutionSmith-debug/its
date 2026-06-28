import type { FieldopsApp, FieldopsGates } from "./fieldops_gates";
import { encodeCursor, decodeCursor } from "./cursor";

// P3 Materials (M1) — material_catalog READ. cap.materials.receive (submitter + admin), so a
// field PM can browse the type vocabulary when receiving against the Material List (M2). Keyset
// page on (model_id ASC, id ASC); active rows only by default, ?all=1 includes soft-retired
// (admin editor). Send-free; bound params only; the cursor is decoded fail-safe and always bound.

interface CatalogRow {
  id: number;
  model_id: string;
  manufacturer: string | null;
  category: string;
  key_specs: string | null;
  unit_cost: number | null;
  source_files: string | null; // JSON array string (provenance)
  active: number;
}

export function registerMaterialsRoutes(app: FieldopsApp, gates: FieldopsGates): void {
  // GET /api/fieldops/materials — keyset page of the catalog (active by default; &all=1 = include retired)
  app.get(
    "/api/fieldops/materials",
    gates.requireSession,
    gates.requireCapability("cap.materials.receive"),
    async (c) => {
      const q = c.req.query();
      const limitRaw = parseInt(q.limit || "50");
      const limit = Math.min(Math.max(isNaN(limitRaw) ? 50 : limitRaw, 1), 200);
      const includeAll = q.all === "1";
      const cursor = decodeCursor(q.cursor);

      const sql = `
        SELECT id, model_id, manufacturer, category, key_specs, unit_cost, source_files, active
        FROM material_catalog
        WHERE (?1 = 1 OR active = 1)
          AND (?2 IS NULL OR model_id > ?2 OR (model_id = ?2 AND id > ?3))
        ORDER BY model_id ASC, id ASC
        LIMIT ?4
      `;
      const params = cursor
        ? [includeAll ? 1 : 0, cursor.m as string | null, cursor.i as number | null, limit]
        : [includeAll ? 1 : 0, null, null, limit];

      const res = await c.env.DB.prepare(sql).bind(...params).all<CatalogRow>();
      const rows = res.results ?? [];

      const last = rows[rows.length - 1];
      const nextCursor = rows.length === limit && last ? encodeCursor({ m: last.model_id, i: last.id }) : null;

      return c.json({ materials: rows, next_cursor: nextCursor }, 200);
    },
  );
}
