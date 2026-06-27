import type { Context } from "hono";
import type { FieldopsApp, FieldopsGates } from "./fieldops_gates";
import type { Env, Vars } from "./types";
import { auditStmt } from "./audit";

// P2.3 Slice 6 — EQUIPMENT ROSTER CRUD (create / edit / retire). cap.equipment.manage (admin-only).
// `equipment` is a PLAIN reference table: create INSERTs, edit UPDATEs in place, retire is a
// SOFT-delete (active=0) so historical logs/inspections/locations keep their FK target. Each
// mutation + its audit_log row land in ONE D1 batch (W4). Send-free (D1 only). No submit-as /
// integrity-bar edit chain here (that's the field-write surface; this is office roster management).

type Ctx = Context<{ Bindings: Env; Variables: Vars }>;
const MAX_NAME = 128;
const MAX_SHORT = 64;
const MAX_NOTE = 512;
const STATUSES = new Set(["fmc", "degraded", "down"]);

function badId(c: Ctx): number | null {
  const id = parseInt(c.req.param("id") ?? "", 10);
  return isNaN(id) ? null : id;
}

export function registerEquipmentRosterWriteRoutes(app: FieldopsApp, gates: FieldopsGates): void {
  // POST /api/fieldops/equipment — add a unit to the fleet roster.
  app.post(
    "/api/fieldops/equipment",
    gates.requireSession,
    gates.requireCapability("cap.equipment.manage"),
    async (c) => {
      let body: Record<string, unknown>;
      try {
        body = (await c.req.json()) as Record<string, unknown>;
      } catch {
        return c.json({ error: "bad_request" }, 400);
      }
      if (typeof body !== "object" || body === null || Array.isArray(body)) return c.json({ error: "bad_request" }, 400);

      const name = typeof body.name === "string" ? body.name.trim() : "";
      const kind = typeof body.kind === "string" ? body.kind : null;
      const identifier = typeof body.identifier === "string" ? body.identifier : null;
      const statusNote = typeof body.status_note === "string" ? body.status_note : null;
      const status = typeof body.status === "string" ? body.status : "fmc";
      if (name.length < 1 || name.length > MAX_NAME) return c.json({ error: "invalid_name" }, 400);
      if (kind !== null && kind.length > MAX_SHORT) return c.json({ error: "invalid_kind" }, 400);
      if (identifier !== null && identifier.length > MAX_SHORT) return c.json({ error: "invalid_identifier" }, 400);
      if (statusNote !== null && statusNote.length > MAX_NOTE) return c.json({ error: "invalid_status_note" }, 400);
      if (!STATUSES.has(status)) return c.json({ error: "invalid_status" }, 400);

      const actor = c.get("session").username;
      const res = await c.env.DB.batch([
        c.env.DB
          .prepare(
            `INSERT INTO equipment (name, kind, identifier, status, status_note, status_changed_at, status_actor)
             VALUES (?1, ?2, ?3, ?4, ?5, unixepoch(), ?6) RETURNING id`,
          )
          .bind(name, kind, identifier, status, statusNote, actor),
        auditStmt(c, actor, "equipment_create", name, { name, kind, identifier, status }),
      ]);
      const newId = (res[0].results?.[0] as { id: number } | undefined)?.id ?? null;
      return c.json({ ok: true, id: newId }, 201);
    },
  );

  // POST /api/fieldops/equipment/:id/update — edit roster fields (name/kind/identifier; NOT status).
  app.post(
    "/api/fieldops/equipment/:id/update",
    gates.requireSession,
    gates.requireCapability("cap.equipment.manage"),
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

      const name = typeof body.name === "string" ? body.name.trim() : "";
      const kind = typeof body.kind === "string" ? body.kind : null;
      const identifier = typeof body.identifier === "string" ? body.identifier : null;
      if (name.length < 1 || name.length > MAX_NAME) return c.json({ error: "invalid_name" }, 400);
      if (kind !== null && kind.length > MAX_SHORT) return c.json({ error: "invalid_kind" }, 400);
      if (identifier !== null && identifier.length > MAX_SHORT) return c.json({ error: "invalid_identifier" }, 400);

      const actor = c.get("session").username;
      const res = await c.env.DB.batch([
        c.env.DB.prepare("UPDATE equipment SET name = ?2, kind = ?3, identifier = ?4 WHERE id = ?1 AND active = 1").bind(id, name, kind, identifier),
        c.env.DB
          .prepare("INSERT INTO audit_log (actor_username, action, target_username, detail) SELECT ?1,?2,?3,?4 WHERE changes()=1")
          .bind(actor, "equipment_update", String(id), JSON.stringify({ equipment_id: id, name })),
      ]);
      if ((res[0].meta.changes ?? 0) === 0) return c.json({ error: "not_found" }, 404);
      return c.json({ ok: true, id }, 200);
    },
  );

  // POST /api/fieldops/equipment/:id/delete — SOFT-retire (active=0). Idempotent; preserves history.
  app.post(
    "/api/fieldops/equipment/:id/delete",
    gates.requireSession,
    gates.requireCapability("cap.equipment.manage"),
    async (c) => {
      const id = badId(c);
      if (id === null) return c.json({ error: "invalid_id" }, 400);
      const actor = c.get("session").username;
      const res = await c.env.DB.batch([
        c.env.DB.prepare("UPDATE equipment SET active = 0 WHERE id = ?1 AND active = 1").bind(id),
        c.env.DB
          .prepare("INSERT INTO audit_log (actor_username, action, target_username, detail) SELECT ?1,?2,?3,?4 WHERE changes()=1")
          .bind(actor, "equipment_retire", String(id), JSON.stringify({ equipment_id: id })),
      ]);
      if ((res[0].meta.changes ?? 0) === 0) {
        // 0 changes = either unknown id (404) or already-retired (idempotent 200).
        const row = await c.env.DB.prepare("SELECT id FROM equipment WHERE id = ?1").bind(id).first();
        return row ? c.json({ ok: true, id, already_retired: true }, 200) : c.json({ error: "not_found" }, 404);
      }
      return c.json({ ok: true, id }, 200);
    },
  );
}
