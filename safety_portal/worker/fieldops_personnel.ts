import type { FieldopsApp, FieldopsGates } from "./fieldops_gates";
import { encodeCursor, decodeCursor } from "./cursor";

// Response shapes per BRIEF A
interface PersonnelRow {
  id: number;
  name: string;
  trade: string;
  username: string | null;
}

interface LatestEntry {
  personnel_id: number;
  job_id: string;
  project_name: string | null;
  hours: number | null;
  work_started_at: number | null;
  work_ended_at: number | null;
  recorded_at: number;
}

export function registerPersonnelRoutes(app: FieldopsApp, gates: FieldopsGates): void {
  // GET /api/fieldops/personnel — roster keyset page + latest-entry batch
  app.get(
    "/api/fieldops/personnel",
    gates.requireSession,
    gates.requireCapability("cap.personnel.read"),
    async (c) => {
      const q = c.req.query();
      const limitRaw = parseInt(q.limit || "50");
      // Default 50, clamp [1,200]; non-numeric → default to 50 (not 400)
      let limit = Math.min(Math.max(isNaN(limitRaw) ? 50 : limitRaw, 1), 200);
      const cursor = decodeCursor(q.cursor);

      // Roster page (keyset on name ASC, id ASC, active=1)
      const sqlRoster = `
        SELECT id, name, trade, username
        FROM personnel
        WHERE active = 1
          AND (?1 IS NULL OR name > ?1 OR (name = ?1 AND id > ?2))
        ORDER BY name ASC, id ASC
        LIMIT ?3
      `;
      const rosterParams = cursor
        ? [cursor.n as string | null, cursor.i as number | null, limit]
        : [null, null, limit];

      const rosterRes = await c.env.DB.prepare(sqlRoster).bind(...rosterParams).all<{ id: number; name: string; trade: string; username: string | null }>();

      if (!rosterRes.results || rosterRes.results.length === 0) {
        return c.json({ personnel: [], latest_entries: [], next_cursor: null }, 200);
      }

      const pageIds = rosterRes.results.map((r) => r.id);

      // Latest entry per person in THIS PAGE ONLY (windowed batch, bounded by page size)
      if (pageIds.length === 0) {
        return c.json({ personnel: [], latest_entries: [], next_cursor: null }, 200);
      }

      const placeholders = pageIds.map(() => "?").join(",");
      const sqlLatest = `
        SELECT personnel_id, job_id, project_name, hours, work_started_at,
               work_ended_at, recorded_at
        FROM (
          SELECT t.personnel_id, t.job_id, j.project_name, t.hours, t.work_started_at,
                 t.work_ended_at, t.created_at AS recorded_at,
                 ROW_NUMBER() OVER (PARTITION BY t.personnel_id
                                    ORDER BY t.created_at DESC, t.uuid DESC) AS rn
          FROM time_entries t
          LEFT JOIN jobs j ON j.job_id = t.job_id
          WHERE t.personnel_id IN (${placeholders})
        ) WHERE rn = 1
      `;

      const latestRes = await c.env.DB.prepare(sqlLatest).bind(...pageIds).all<{
        personnel_id: number;
        job_id: string;
        project_name: string | null;
        hours: number | null;
        work_started_at: number | null;
        work_ended_at: number | null;
        recorded_at: number;
      }>();

      const latestMap = new Map<number, LatestEntry>();
      if (latestRes.results) {
        for (const row of latestRes.results) {
          latestMap.set(row.personnel_id, row);
        }
      }

      // Merge header + latest entry
      const personnel: PersonnelRow[] = rosterRes.results.map((row) => ({
        id: row.id,
        name: row.name,
        trade: row.trade,
        username: row.username,
      }));

      const last = rosterRes.results[rosterRes.results.length - 1];
      const nextCursor =
        rosterRes.results.length === limit
          ? encodeCursor({ n: last.name, i: last.id })
          : null;

      return c.json(
        { personnel, latest_entries: Array.from(latestMap.values()), next_cursor: nextCursor },
        200,
      );
    },
  );

  // GET /api/fieldops/personnel/:id — header + time entries (keyset paginated)
  app.get(
    "/api/fieldops/personnel/:id",
    gates.requireSession,
    gates.requireCapability("cap.personnel.read"),
    async (c) => {
      const idParam = c.req.param("id");
      const id = parseInt(idParam, 10);
      if (isNaN(id)) {
        return c.json({ error: "invalid_id" }, 400);
      }

      // Header by PK
      const sqlHeader = `SELECT id, name, username, trade FROM personnel WHERE id = ?`;
      const headerRes = await c.env.DB.prepare(sqlHeader).bind(id).first<{ id: number; name: string; username: string | null; trade: string }>();
      if (!headerRes) {
        return c.json({ error: "not_found" }, 404);
      }

      // Time entries keyset pagination (created_at DESC, uuid DESC)
      const q = c.req.query();
      const limitRaw = parseInt(q.limit || "50");
      const limit = Math.min(Math.max(isNaN(limitRaw) ? 50 : limitRaw, 1), 200);
      const cursor = decodeCursor(q.cursor);

      const sqlEntries = `
        SELECT t.uuid, t.job_id, j.project_name, t.hours, t.work_started_at,
               t.work_ended_at, t.created_at AS recorded_at, t.notes
        FROM time_entries t
        LEFT JOIN jobs j ON j.job_id = t.job_id
        WHERE t.personnel_id = ?1
          AND (?2 IS NULL OR t.created_at < ?2 OR (t.created_at = ?2 AND t.uuid < ?3))
        ORDER BY t.created_at DESC, t.uuid DESC
        LIMIT ?4
      `;

      const entryParams = cursor
        ? [
            id,
            cursor.c as number | null,
            cursor.u as string | null,
            limit,
          ]
        : [id, null, null, limit];

      const entriesRes = await c.env.DB.prepare(sqlEntries).bind(...entryParams).all<{
        uuid: string;
        job_id: string;
        project_name: string | null;
        hours: number | null;
        work_started_at: number | null;
        work_ended_at: number | null;
        recorded_at: number;
        notes: string | null;
      }>();

      let nextCursor: string | null = null;
      if (entriesRes.results && entriesRes.results.length === limit) {
        const last = entriesRes.results[entriesRes.results.length - 1];
        nextCursor = encodeCursor({ c: last.recorded_at, u: last.uuid });
      }

      return c.json(
        {
          personnel: {
            id: headerRes.id,
            name: headerRes.name,
            username: headerRes.username,
            trade: headerRes.trade,
            time_entries: entriesRes.results ?? [],
          },
          next_cursor: nextCursor,
        },
        200,
      );
    },
  );
}
