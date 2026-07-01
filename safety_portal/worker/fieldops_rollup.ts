import type { MiddlewareHandler } from "hono";
import type { FieldopsApp } from "./fieldops_gates";
import type { Env, Vars } from "./types";

// P6 — progress rollup numbers. A SEND-FREE, READ-ONLY D1 aggregation over the structured
// field-ops tables for ONE job over the Sat→Fri epoch window. It is the read counterpart the
// progress weekly compile (progress_weekly_generate) fetches through the F02-allowlisted
// shared.portal_client and renders as the packet's numbers page.
//
// Invariant 1 (External Send Gate): ZERO external transmission — this only READS D1 and returns
// JSON to the caller (the Mac daemon initiated the request). No fetch, no email. Invariant 2:
// every query param is validated + bounded up front (reject the WHOLE request on any bad param),
// all D1 access is parameter-bound, and the equipment list is row-capped.
//
// NO progress-% (operator decision 2026-06-30: a single current jobs.progress value is a
// misleading guess, not a measurement) — the `SELECT progress FROM jobs` aggregation and the
// `progress_pct` response field are deliberately absent. Materials is a null M2 placeholder.

// Row cap on the DISTINCT equipment leg — bounds the response for a pathological job/week without
// changing the honest small-N result. A code constant, not user input.
const EQUIPMENT_CAP = 500;

/** Digits-only non-negative epoch-seconds parse; null for undefined/empty/negative/decimal/sci. */
function parseEpoch(raw: string | undefined): number | null {
  if (raw === undefined || raw === "" || !/^\d+$/.test(raw)) return null;
  const n = Number(raw);
  return Number.isSafeInteger(n) ? n : null;
}

export function registerProgressRollupRoutes(
  app: FieldopsApp,
  requireInternalToken: MiddlewareHandler<{ Bindings: Env; Variables: Vars }>,
): void {
  // GET /api/internal/progress-rollup?job_id=&from=&to= — the Mac-daemon read (bearer-gated,
  // same PORTAL_INTERNAL_API_TOKEN privilege class as /api/internal/pending). NO new secret.
  app.get("/api/internal/progress-rollup", requireInternalToken, async (c) => {
    const jobId = c.req.query("job_id") ?? "";
    const from = parseEpoch(c.req.query("from"));
    const to = parseEpoch(c.req.query("to"));

    // Validate up front; reject the WHOLE request on any bad param (mirror /api/internal/sync's
    // validate-then-reject discipline — a partial/garbage window must never silently return zeros).
    if (!jobId || jobId.length > 64) return c.json({ error: "invalid_job_id" }, 400);
    if (from === null || to === null) return c.json({ error: "invalid_window" }, 400);
    if (to <= from) return c.json({ error: "invalid_window" }, 400);

    // Labor hours: SUM over the window, AMEND-COLLAPSED (exclude any row that a later row amends —
    // the 0015 append-only edit chain). Event-date window: the field-reported work_started_at,
    // falling back to the server-authoritative created_at when the crew reported no start time.
    const laborSql = `
      SELECT COALESCE(SUM(t.hours), 0) AS labor_hours
      FROM time_entries t
      WHERE t.job_id = ?1
        AND COALESCE(t.work_started_at, t.created_at) >= ?2
        AND COALESCE(t.work_started_at, t.created_at) < ?3
        AND NOT EXISTS (SELECT 1 FROM time_entries x WHERE x.amends_uuid = t.uuid)
    `;
    // Equipment on site: DISTINCT equipment with a location read on this job in the window
    // (idx_equipment_location_job on (job_id, recorded_at)). Windowed on the server-authoritative
    // recorded_at (a field-reported read_at cannot shift equipment across a week boundary).
    const equipSql = `
      SELECT DISTINCT e.name, e.kind
      FROM equipment_location l
      JOIN equipment e ON e.id = l.equipment_id
      WHERE l.job_id = ?1 AND l.recorded_at >= ?2 AND l.recorded_at < ?3
      ORDER BY e.name ASC
      LIMIT ?4
    `;
    // Open tasks: the current bounded status != 'done' count (idx_task_assignments_job). NOT
    // windowed — task_assignments has no completed_at, so "open now" is the only honest count.
    const openTasksSql = `
      SELECT COUNT(*) AS open_tasks FROM task_assignments WHERE job_id = ?1 AND status != 'done'
    `;

    const [laborRes, equipRes, tasksRes] = await c.env.DB.batch([
      c.env.DB.prepare(laborSql).bind(jobId, from, to),
      c.env.DB.prepare(equipSql).bind(jobId, from, to, EQUIPMENT_CAP),
      c.env.DB.prepare(openTasksSql).bind(jobId),
    ]);

    const laborHours = (laborRes.results?.[0] as { labor_hours: number } | undefined)?.labor_hours ?? 0;
    const equipment = (equipRes.results ?? []) as { name: string; kind: string | null }[];
    const openTasks = (tasksRes.results?.[0] as { open_tasks: number } | undefined)?.open_tasks ?? 0;

    return c.json(
      {
        job_id: jobId,
        window: { from, to },
        labor_hours: laborHours,
        equipment,
        open_tasks: openTasks,
        materials: null, // M2 placeholder — material_list not built yet
        generated_at: Math.floor(Date.now() / 1000),
      },
      200,
    );
  });
}
