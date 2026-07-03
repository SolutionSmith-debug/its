import type { FieldopsApp, FieldopsGates } from "./fieldops_gates";
import { encodeCursor, decodeCursor } from "./cursor";

// Response shapes per BRIEF A (P2.6: + current_job standing crew→job placement)
interface PersonnelRow {
  id: number;
  name: string;
  trade: string;
  username: string | null;
  /** P2.6 — standing job placement ("who is where"); NULL = unplaced. Soft-ref to jobs.job_id. */
  current_job: string | null;
  /** Resolved project_name for current_job (LEFT JOIN jobs); NULL when unplaced or job absent. */
  current_job_name: string | null;
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

      // Roster page (keyset on name ASC, id ASC, active=1). LEFT JOIN jobs to resolve the standing
      // placement's project_name (current_job_name). The join is 1:1 on personnel.current_job →
      // jobs.job_id (a soft ref), so it never expands the keyset page; personnel columns are qualified
      // because `active` exists on both tables.
      const sqlRoster = `
        SELECT personnel.id, personnel.name, personnel.trade, personnel.username, personnel.current_job,
               cj.project_name AS current_job_name
        FROM personnel
        LEFT JOIN jobs cj ON cj.job_id = personnel.current_job
        WHERE personnel.active = 1
          AND (?1 IS NULL OR personnel.name > ?1 OR (personnel.name = ?1 AND personnel.id > ?2))
        ORDER BY personnel.name ASC, personnel.id ASC
        LIMIT ?3
      `;
      const rosterParams = cursor
        ? [cursor.n as string | null, cursor.i as number | null, limit]
        : [null, null, limit];

      const rosterRes = await c.env.DB.prepare(sqlRoster).bind(...rosterParams).all<{ id: number; name: string; trade: string; username: string | null; current_job: string | null; current_job_name: string | null }>();

      if (!rosterRes.results || rosterRes.results.length === 0) {
        return c.json({ personnel: [], latest_entries: [], next_cursor: null }, 200);
      }

      const pageIds = rosterRes.results.map((r) => r.id);

      // Latest entry per person in THIS PAGE ONLY (windowed batch, bounded by page size)
      if (pageIds.length === 0) {
        return c.json({ personnel: [], latest_entries: [], next_cursor: null }, 200);
      }

      const placeholders = pageIds.map(() => "?").join(",");
      // (G2.3) HEADS ONLY — a superseded (amended) entry must not surface as someone's "latest"
      // (NOT EXISTS, never NOT IN — the NULL-poisoning class; idx_time_entries_amends keys it).
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
            AND NOT EXISTS (SELECT 1 FROM time_entries x WHERE x.amends_uuid = t.uuid)
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
        current_job: row.current_job,
        current_job_name: row.current_job_name,
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

      // Header by PK. LEFT JOIN jobs resolves the standing placement's project_name (current_job_name);
      // personnel columns are qualified (`active`, and defensively the rest, exist on both tables).
      const sqlHeader = `
        SELECT personnel.id, personnel.name, personnel.username, personnel.trade, personnel.current_job,
               cj.project_name AS current_job_name
        FROM personnel
        LEFT JOIN jobs cj ON cj.job_id = personnel.current_job
        WHERE personnel.id = ?
      `;
      const headerRes = await c.env.DB.prepare(sqlHeader).bind(id).first<{ id: number; name: string; username: string | null; trade: string; current_job: string | null; current_job_name: string | null }>();
      if (!headerRes) {
        return c.json({ error: "not_found" }, 404);
      }

      // Time entries keyset pagination (created_at DESC, uuid DESC)
      const q = c.req.query();
      const limitRaw = parseInt(q.limit || "50");
      const limit = Math.min(Math.max(isNaN(limitRaw) ? 50 : limitRaw, 1), 200);
      const cursor = decodeCursor(q.cursor);

      // (G2.3) HEADS ONLY — the person's history lists each entry's newest version once (NOT
      // EXISTS, never NOT IN — the NULL-poisoning class; idx_time_entries_amends keys it).
      const sqlEntries = `
        SELECT t.uuid, t.job_id, j.project_name, t.hours, t.work_started_at,
               t.work_ended_at, t.created_at AS recorded_at, t.notes
        FROM time_entries t
        LEFT JOIN jobs j ON j.job_id = t.job_id
        WHERE t.personnel_id = ?1
          AND NOT EXISTS (SELECT 1 FROM time_entries x WHERE x.amends_uuid = t.uuid)
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
            current_job: headerRes.current_job,
            current_job_name: headerRes.current_job_name,
            time_entries: entriesRes.results ?? [],
          },
          next_cursor: nextCursor,
        },
        200,
      );
    },
  );
}
