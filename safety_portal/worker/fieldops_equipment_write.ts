import type { Context } from "hono";
import type { FieldopsApp, FieldopsGates } from "./fieldops_gates";
import type { Env, Vars } from "./types";
import { auditStmt, isUniqueViolation } from "./audit";
import { normalizeUsername } from "./auth";

// P2.3 Slice 4 — EQUIPMENT FIELD WRITE (readiness status + location move). cap.equipment.field
// (submitter + admin). Send-free (D1 only).
//
// STATUS is a DUAL write: append an append-only equipment_logs row (log_type='status', FULL
// integrity bar) AND update the denormalized snapshot columns on `equipment` — both in ONE D1
// batch so "snapshot fast, history honest" can never diverge. LOCATION is a plain append-only
// equipment_location insert (auto-id, no uuid/amends). Each route also writes its audit_log row
// in the same batch (W4).

type Ctx = Context<{ Bindings: Env; Variables: Vars }>;
const SUBMIT_AS = "cap.submit_as";
const MAX_ID = 64;
const MAX_NOTE = 512;
const MAX_LABEL = 256;
const EQUIP_STATUSES = new Set(["fmc", "degraded", "down"]);
const LOG_TYPES = new Set(["maintenance", "fuel", "hours"]);
const MAX_DETAIL = 2000;

// Dual-attribution resolution (normalize + existence/disabled check), mirroring /api/submit. A
// submit-as (attributing to another account) needs cap.submit_as AND a real, ENABLED, normalized
// portal user. Returns the attributed account, or an error Response the caller returns verbatim.
async function resolveAttribution(c: Ctx, submittedAs: string | null): Promise<{ attributed: string } | { res: Response }> {
  const actor = c.get("session").username;
  if (!submittedAs) return { attributed: actor };
  const target = normalizeUsername(submittedAs);
  if (target === actor) return { attributed: actor };
  if (!c.get("capabilities").has(SUBMIT_AS)) return { res: c.json({ error: "forbidden" }, 403) };
  if (!target) return { res: c.json({ error: "invalid_submitted_as" }, 400) };
  const u = await c.env.DB.prepare("SELECT disabled FROM users WHERE username = ?").bind(target).first<{ disabled: number }>();
  if (!u || u.disabled) return { res: c.json({ error: "unknown_attributed_user" }, 422) };
  return { attributed: target };
}

function badId(c: Ctx): number | null {
  const id = parseInt(c.req.param("id") ?? "", 10);
  return isNaN(id) ? null : id;
}

export function registerEquipmentFieldWriteRoutes(app: FieldopsApp, gates: FieldopsGates): void {
  // POST /api/fieldops/equipment/:id/status — readiness change (dual write: log + snapshot).
  app.post(
    "/api/fieldops/equipment/:id/status",
    gates.requireSession,
    gates.requireCapability("cap.equipment.field"),
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

      const uuid = typeof body.uuid === "string" ? body.uuid : "";
      const status = typeof body.status === "string" ? body.status : "";
      const statusNote = typeof body.status_note === "string" ? body.status_note : null;
      const amendsUuid = typeof body.amends_uuid === "string" ? body.amends_uuid : null;
      const submittedAs = typeof body.submitted_as === "string" ? body.submitted_as : null;
      const performedAt = typeof body.performed_at === "number" && Number.isFinite(body.performed_at) ? body.performed_at : null;
      if (!uuid || uuid.length > MAX_ID) return c.json({ error: "invalid_uuid" }, 400);
      if (!EQUIP_STATUSES.has(status)) return c.json({ error: "invalid_status" }, 400);
      if (statusNote !== null && statusNote.length > MAX_NOTE) return c.json({ error: "invalid_status_note" }, 400);
      if (amendsUuid !== null && amendsUuid.length > MAX_ID) return c.json({ error: "invalid_amends_uuid" }, 400);

      const att = await resolveAttribution(c, submittedAs);
      if ("res" in att) return att.res;
      const actor = c.get("session").username;

      const eq = await c.env.DB.prepare("SELECT id FROM equipment WHERE id = ?1 AND active = 1").bind(id).first();
      if (!eq) return c.json({ error: "not_found" }, 404);

      const action = amendsUuid ? "equipment_status_edit" : "equipment_status";
      try {
        await c.env.DB.batch([
          // append-only integrity-bar log row (created_at/edited_at omitted → server DEFAULT)
          c.env.DB
            .prepare(
              `INSERT INTO equipment_logs
                 (uuid, equipment_id, log_type, value_num, detail, status_value, performed_at, actor_username, submitted_as, amends_uuid)
               VALUES (?1, ?2, 'status', NULL, ?3, ?4, ?5, ?6, ?7, ?8)`,
            )
            .bind(uuid, id, statusNote, status, performedAt, actor, att.attributed, amendsUuid),
          // denormalized current snapshot on the roster row (server-stamped change time + actor)
          c.env.DB
            .prepare("UPDATE equipment SET status = ?2, status_note = ?3, status_changed_at = unixepoch(), status_actor = ?4 WHERE id = ?1")
            .bind(id, status, statusNote, att.attributed),
          auditStmt(c, actor, action, att.attributed, { equipment_id: id, status, uuid }),
        ]);
      } catch (e) {
        if (isUniqueViolation(e)) return c.json({ error: "uuid_conflict" }, 409);
        throw e;
      }
      return c.json({ ok: true, uuid }, 201);
    },
  );

  // POST /api/fieldops/equipment/:id/location — record a point-in-time move to a job site.
  app.post(
    "/api/fieldops/equipment/:id/location",
    gates.requireSession,
    gates.requireCapability("cap.equipment.field"),
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

      const jobId = typeof body.job_id === "string" ? body.job_id : "";
      const label = typeof body.label === "string" ? body.label : null;
      const submittedAs = typeof body.submitted_as === "string" ? body.submitted_as : null;
      const lat = typeof body.lat === "number" && Number.isFinite(body.lat) ? body.lat : null;
      const lon = typeof body.lon === "number" && Number.isFinite(body.lon) ? body.lon : null;
      const readAt = typeof body.read_at === "number" && Number.isFinite(body.read_at) ? body.read_at : null;
      if (!jobId || jobId.length > MAX_ID) return c.json({ error: "invalid_job_id" }, 400);
      if (label !== null && label.length > MAX_LABEL) return c.json({ error: "invalid_label" }, 400);

      const att = await resolveAttribution(c, submittedAs);
      if ("res" in att) return att.res;
      const actor = c.get("session").username;

      const eq = await c.env.DB.prepare("SELECT id FROM equipment WHERE id = ?1 AND active = 1").bind(id).first();
      if (!eq) return c.json({ error: "not_found" }, 404);
      const job = await c.env.DB.prepare("SELECT job_id FROM jobs WHERE job_id = ?1 AND active = 1").bind(jobId).first();
      if (!job) return c.json({ error: "unknown_job" }, 422);

      // Append-only point-in-time read: server recorded_at (DEFAULT) vs field-reported read_at.
      await c.env.DB.batch([
        c.env.DB
          .prepare(
            `INSERT INTO equipment_location (equipment_id, job_id, label, lat, lon, read_at, actor_username, submitted_as)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8)`,
          )
          .bind(id, jobId, label, lat, lon, readAt, actor, att.attributed),
        auditStmt(c, actor, "equipment_move", String(id), { equipment_id: id, job_id: jobId }),
      ]);
      return c.json({ ok: true, equipment_id: id, job_id: jobId }, 201);
    },
  );

  // POST /api/fieldops/equipment/:id/log — maintenance / fuel / hours event. Append-only integrity
  // bar (server timestamps, amends_uuid edit chain, dual attribution); status_value NULL (not a
  // status change — that's the /status route). Mutation + audit in one batch (W4).
  app.post(
    "/api/fieldops/equipment/:id/log",
    gates.requireSession,
    gates.requireCapability("cap.equipment.field"),
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

      const uuid = typeof body.uuid === "string" ? body.uuid : "";
      const logType = typeof body.log_type === "string" ? body.log_type : "";
      const detail = typeof body.detail === "string" ? body.detail : null;
      const amendsUuid = typeof body.amends_uuid === "string" ? body.amends_uuid : null;
      const submittedAs = typeof body.submitted_as === "string" ? body.submitted_as : null;
      const valueNum = typeof body.value_num === "number" && Number.isFinite(body.value_num) ? body.value_num : null;
      const performedAt = typeof body.performed_at === "number" && Number.isFinite(body.performed_at) ? body.performed_at : null;
      if (!uuid || uuid.length > MAX_ID) return c.json({ error: "invalid_uuid" }, 400);
      if (!LOG_TYPES.has(logType)) return c.json({ error: "invalid_log_type" }, 400);
      if (detail !== null && detail.length > MAX_DETAIL) return c.json({ error: "invalid_detail" }, 400);
      if (amendsUuid !== null && amendsUuid.length > MAX_ID) return c.json({ error: "invalid_amends_uuid" }, 400);

      const att = await resolveAttribution(c, submittedAs);
      if ("res" in att) return att.res;
      const actor = c.get("session").username;

      const eq = await c.env.DB.prepare("SELECT id FROM equipment WHERE id = ?1 AND active = 1").bind(id).first();
      if (!eq) return c.json({ error: "not_found" }, 404);

      const action = amendsUuid ? "equipment_log_edit" : "equipment_log_create";
      try {
        await c.env.DB.batch([
          c.env.DB
            .prepare(
              `INSERT INTO equipment_logs
                 (uuid, equipment_id, log_type, value_num, detail, status_value, performed_at, actor_username, submitted_as, amends_uuid)
               VALUES (?1, ?2, ?3, ?4, ?5, NULL, ?6, ?7, ?8, ?9)`,
            )
            .bind(uuid, id, logType, valueNum, detail, performedAt, actor, att.attributed, amendsUuid),
          auditStmt(c, actor, action, att.attributed, { equipment_id: id, log_type: logType, uuid }),
        ]);
      } catch (e) {
        if (isUniqueViolation(e)) return c.json({ error: "uuid_conflict" }, 409);
        throw e;
      }
      return c.json({ ok: true, uuid }, 201);
    },
  );
}
