import type { FieldopsApp, FieldopsGates } from "./fieldops_gates";
import { encodeCursor, decodeCursor } from "./cursor";

// Response shapes per BRIEF B (equipment tab)
interface EquipmentRow {
  id: number;
  name: string;
  kind: string | null;
  identifier: string | null;
  status: "fmc" | "degraded" | "down"; // denormalized snapshot
  status_note: string | null;
}

interface LocationRecord {
  equipment_id: number;
  id: number;
  label: string | null;
  lat: number | null;
  lon: number | null;
  read_at: number | null;
  recorded_at: number;
  job_id: string | null;
}

interface InspectionRecord {
  equipment_id: number;
  uuid: string;
  form_code: string;
  version: number;
  performed_at: number | null;
  recorded_at: number;
  job_id: string | null;
}

interface LogRecord {
  equipment_id: number;
  log_type: string;
  value_num: number | null;
  detail: string | null;
  status_value: string | null;
  performed_at: number | null;
  recorded_at: number;
  uuid: string;
}

export function registerEquipmentRoutes(app: FieldopsApp, gates: FieldopsGates): void {
  // GET /api/fieldops/equipment — fleet keyset page + windowed batches over page ids only
  app.get(
    "/api/fieldops/equipment",
    gates.requireSession,
    gates.requireCapability("cap.equipment.field"),
    async (c) => {
      const q = c.req.query();
      const limitRaw = parseInt(q.limit || "50");
      let limit = Math.min(Math.max(isNaN(limitRaw) ? 50 : limitRaw, 1), 200);
      const cursor = decodeCursor(q.cursor);

      // Fleet page (keyset on name ASC, id ASC, active=1)
      const sqlFleet = `
        SELECT id, name, kind, identifier, status, status_note
        FROM equipment
        WHERE active = 1
          AND (?1 IS NULL OR name > ?1 OR (name = ?1 AND id > ?2))
        ORDER BY name ASC, id ASC
        LIMIT ?3
      `;
      const fleetParams = cursor
        ? [cursor.n as string | null, cursor.i as number | null, limit]
        : [null, null, limit];

      const fleetRes = await c.env.DB.prepare(sqlFleet).bind(...fleetParams).all<{
        id: number;
        name: string;
        kind: string | null;
        identifier: string | null;
        status: "fmc" | "degraded" | "down";
        status_note: string | null;
      }>();

      if (!fleetRes.results || fleetRes.results.length === 0) {
        return c.json({ equipment: [], next_cursor: null }, 200);
      }

      const pageIds = fleetRes.results.map((r) => r.id);

      // Three windowed batches over the page's ids ONLY (deterministic, bounded)
      if (pageIds.length === 0) {
        return c.json({ equipment: [], next_cursor: null }, 200);
      }

      const placeholders = pageIds.map(() => "?").join(",");
      
      // Latest location (idx_equipment_location_latest) - explicit columns to avoid alias issues
      const sqlLocation = `
        SELECT eq_loc.equipment_id, eq_loc.id, eq_loc.label, eq_loc.lat, eq_loc.lon, eq_loc.read_at,
               eq_loc.recorded_at, eq_loc.job_id
        FROM (
          SELECT el.id, el.equipment_id, el.label, el.lat, el.lon, el.read_at,
                 el.recorded_at, el.job_id,
                 ROW_NUMBER() OVER (PARTITION BY el.equipment_id
                                    ORDER BY el.recorded_at DESC, el.id DESC) rn
          FROM equipment_location el WHERE el.equipment_id IN (${placeholders})
        ) eq_loc WHERE eq_loc.rn = 1
      `;
      
      // Latest inspection (idx_inspections_equipment; scalar cols only). inspections has NO
      // recorded_at, so alias created_at AS recorded_at IN THE INNER (where created_at is in
      // scope) and expose job_id there — the outer can only see columns the subquery emits.
      const sqlInspection = `
        SELECT insp.equipment_id, insp.uuid, insp.form_code, insp.version, insp.performed_at,
               insp.recorded_at, insp.job_id
        FROM (
          SELECT i.equipment_id, i.uuid, i.form_code, i.version, i.performed_at,
                 i.created_at AS recorded_at, i.job_id,
                 ROW_NUMBER() OVER (PARTITION BY i.equipment_id
                                    ORDER BY i.created_at DESC, i.uuid DESC) rn
          FROM inspections i WHERE i.equipment_id IN (${placeholders})
        ) insp WHERE insp.rn = 1
      `;

      // Recent logs, bounded ≤5/unit (idx_equipment_logs_equipment). equipment_logs has NO
      // recorded_at, so alias created_at AS recorded_at IN THE INNER; alias the subquery (lg)
      // so the outer can reference it.
      const sqlLogs = `
        SELECT lg.equipment_id, lg.log_type, lg.value_num, lg.detail,
               lg.status_value, lg.performed_at, lg.recorded_at, lg.uuid
        FROM (
          SELECT el.equipment_id, el.log_type, el.value_num, el.detail,
                 el.status_value, el.performed_at, el.created_at AS recorded_at, el.uuid,
                 ROW_NUMBER() OVER (PARTITION BY el.equipment_id
                                    ORDER BY el.created_at DESC, el.uuid DESC) rn
          FROM equipment_logs el WHERE el.equipment_id IN (${placeholders})
        ) lg WHERE lg.rn <= 5
      `;

      // Run all three via D1 batch for single round-trip
      const [locRes, inspRes, logsRes] = await c.env.DB.batch([
        c.env.DB.prepare(sqlLocation).bind(...pageIds),
        c.env.DB.prepare(sqlInspection).bind(...pageIds),
        c.env.DB.prepare(sqlLogs).bind(...pageIds),
      ]);

      // Merge by equipment_id
      const eqMap = new Map<number, EquipmentRow & {
        location: LocationRecord | null;
        latest_inspection: InspectionRecord | null;
        recent_logs: LogRecord[];
      }>();

      for (const row of fleetRes.results ?? []) {
        eqMap.set(row.id, {
          id: row.id,
          name: row.name,
          kind: row.kind,
          identifier: row.identifier,
          status: row.status,
          status_note: row.status_note,
          location: null,
          latest_inspection: null,
          recent_logs: [],
        });
      }

      // Latest locations
      if (locRes.results) {
        for (const r of locRes.results as LocationRecord[]) {
          const eq = eqMap.get(r.equipment_id);
          if (eq) eq.location = r;
        }
      }

      // Latest inspection
      if (inspRes.results) {
        for (const r of inspRes.results as InspectionRecord[]) {
          const eq = eqMap.get(r.equipment_id);
          if (eq) eq.latest_inspection = r;
        }
      }

      // Recent logs (≤5 per unit)
      if (logsRes.results) {
        for (const r of logsRes.results as LogRecord[]) {
          const eq = eqMap.get(r.equipment_id);
          if (eq) eq.recent_logs.push(r);
        }
      }

      // Build response
      const equipment: (EquipmentRow & {
        location: LocationRecord | null;
        latest_inspection: InspectionRecord | null;
        recent_logs: LogRecord[];
      })[] = [];
      for (const row of fleetRes.results ?? []) {
        const eq = eqMap.get(row.id);
        if (eq) equipment.push(eq);
      }

      // next_cursor
      const last = fleetRes.results[fleetRes.results.length - 1];
      const nextCursor =
        fleetRes.results.length === limit
          ? encodeCursor({ n: last.name, i: last.id })
          : null;

      return c.json(
        { equipment, next_cursor: nextCursor },
        200,
      );
    },
  );

  // GET /api/fieldops/equipment/:id — header + three independently keyset-paginated history legs
  app.get(
    "/api/fieldops/equipment/:id",
    gates.requireSession,
    gates.requireCapability("cap.equipment.field"),
    async (c) => {
      const idParam = c.req.param("id");
      const id = parseInt(idParam, 10);
      if (isNaN(id)) {
        return c.json({ error: "invalid_id" }, 400);
      }

      // Header by PK (includes snapshot columns)
      const sqlHeader = `
        SELECT id, name, kind, identifier,
               status, status_note, status_changed_at, status_actor
        FROM equipment WHERE id = ?
      `;
      const headerRes = await c.env.DB.prepare(sqlHeader).bind(id).first<{
        id: number;
        name: string;
        kind: string | null;
        identifier: string | null;
        status: "fmc" | "degraded" | "down";
        status_note: string | null;
        status_changed_at: number | null;
        status_actor: string | null;
      }>();
      if (!headerRes) {
        return c.json({ error: "not_found" }, 404);
      }

      const q = c.req.query();
      const limitRaw = parseInt(q.limit || "50");
      const limit = Math.min(Math.max(isNaN(limitRaw) ? 50 : limitRaw, 1), 200);

      // Parse all three cursors
      const locCursorRaw = decodeCursor(q.loc_cursor);
      const inspCursorRaw = decodeCursor(q.insp_cursor);
      const logCursorRaw = decodeCursor(q.log_cursor);

      // Three independently keyset-paginated history legs (Promise.all)
      const sqlLocation = `
        SELECT id, label, lat, lon, read_at, recorded_at, job_id
        FROM equipment_location
        WHERE equipment_id = ?1
          AND (?2 IS NULL OR recorded_at < ?2 OR (recorded_at = ?2 AND id < ?3))
        ORDER BY recorded_at DESC, id DESC
        LIMIT ?4
      `;

      const sqlInspection = `
        SELECT uuid, form_code, version, performed_at,
               created_at AS recorded_at, job_id
        FROM inspections
        WHERE equipment_id = ?1
          AND (?2 IS NULL OR created_at < ?2 OR (created_at = ?2 AND uuid < ?3))
        ORDER BY created_at DESC, uuid DESC
        LIMIT ?4
      `;

      const sqlLogs = `
        SELECT uuid, log_type, value_num, detail,
               status_value, performed_at, created_at AS recorded_at
        FROM equipment_logs
        WHERE equipment_id = ?1
          AND (?2 IS NULL OR created_at < ?2 OR (created_at = ?2 AND uuid < ?3))
        ORDER BY created_at DESC, uuid DESC
        LIMIT ?4
      `;

      const locParams = locCursorRaw
        ? [
            id,
            locCursorRaw.c as number | null,
            locCursorRaw.i as number | null,
            limit,
          ]
        : [id, null, null, limit];

      const inspParams = inspCursorRaw
        ? [
            id,
            inspCursorRaw.c as number | null,
            inspCursorRaw.u as string | null,
            limit,
          ]
        : [id, null, null, limit];

      const logParams = logCursorRaw
        ? [
            id,
            logCursorRaw.c as number | null,
            logCursorRaw.u as string | null,
            limit,
          ]
        : [id, null, null, limit];

      const [locRes, inspRes, logsRes] = await c.env.DB.batch([
        c.env.DB.prepare(sqlLocation).bind(...locParams),
        c.env.DB.prepare(sqlInspection).bind(...inspParams),
        c.env.DB.prepare(sqlLogs).bind(...logParams),
      ]);

      // Compute next cursors
      let locNext: string | null = null;
      if (locRes.results && locRes.results.length === limit) {
        const last = locRes.results[locRes.results.length - 1] as { recorded_at: number; id: number };
        locNext = encodeCursor({ c: last.recorded_at, i: last.id });
      }

      let inspNext: string | null = null;
      if (inspRes.results && inspRes.results.length === limit) {
        const last = inspRes.results[inspRes.results.length - 1] as { recorded_at: number; uuid: string };
        inspNext = encodeCursor({ c: last.recorded_at, u: last.uuid });
      }

      let logNext: string | null = null;
      if (logsRes.results && logsRes.results.length === limit) {
        const last = logsRes.results[logsRes.results.length - 1] as { recorded_at: number; uuid: string };
        logNext = encodeCursor({ c: last.recorded_at, u: last.uuid });
      }

      // Shape matches the lib's EquipmentDetail = { header, locations, inspections, logs };
      // the SPA reads selectedEquipment.header / .locations / .inspections / .logs.
      return c.json(
        {
          equipment: {
            header: headerRes,
            locations: locRes.results ?? [],
            inspections: inspRes.results ?? [],
            logs: logsRes.results ?? [],
          },
          cursors: { loc: locNext, insp: inspNext, log: logNext },
        },
        200,
      );
    },
  );
}
