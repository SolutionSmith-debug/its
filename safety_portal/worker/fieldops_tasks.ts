import type { FieldopsApp, FieldopsGates } from "./fieldops_gates";
import type { MyTask, MyTasksResponse, ViewerTaskPlacement } from "./wire-types";

// Assigned-Tasks tab (P4 field-ops feature) S1 — "My Tasks" READ. The subcontractor / manager sees
// the one-off tasks assigned to THEM. "Assigned to me" resolves the session's account → its linked
// personnel row (personnel.username == users.username, migration 0014's nullable soft link) → the
// tasks WHERE personnel_id = that. A session with NO linked personnel (no personnel row carrying its
// username) sees an EMPTY list — not an error. Send-free (D1 read only); cap.tasks.own gated.
//
// R1 contract refinements:
//   • ORDER: open work FIRST (status CASE open < in_progress < done), tiebreak created_at DESC —
//     the old `status ASC` was lexicographic and floated DONE tasks to the top of the tab.
//   • `assigned_by` (stamped by the create route since 0014; NULL on historical rows) + created_at
//     give the row its context ("who gave me this, when").
//   • `linked: boolean` — whether the session has an ACTIVE linked personnel row — lets the UI
//     distinguish "no tasks" from "your account isn't linked to the roster" (see the R1 spec's
//     empty-state reason codes; the sibling daily surface returns `reason`, fieldops_checklist.ts).
//
// CS4 (#12 waterfall collapse): the response ALSO carries `viewer_placement` — the caller's OWN
// standing placement (their linked ACTIVE personnel row's current_job + one indexed jobs lookup
// for the project name), the same personnel resolution fieldops_scope.resolveActorPersonnel uses.
// The Daily tab used to resolve this by downloading a FULL Job Tracker list page
// (fetchJobList("active") → viewer_current_job) as stage 1 of a 2-stage waterfall; now the one
// endpoint the My Tasks page already reads carries it, and the jobs-list query leaves the daily
// path entirely.
//
// SECURITY NOTE (deliberate, reviewed): cap.tasks.own now returns the caller's OWN placement.
// This is SELF-INFORMATION — the viewer's own roster row id, own display name (W9: display name
// only, and only to its owner), and own current_job — resolved strictly from the session username;
// no parameter selects whose placement is returned, so there is NO cross-user exposure. Previously
// the same fact rode cap.jobtracker.read (viewer_current_job on the jobs list); a cap.tasks.own-only
// account learns nothing here about any other account, person, or job beyond its own placement's
// project name — which its holder could already see on every form they file against that job.

// Per-account bound. A person's own assigned tasks are few; this cap is a defensive ceiling, not a
// paginated surface (unlike the Job Tracker's keyset legs).
const MY_TASKS_CAP = 500;

export function registerMyTasksRoutes(app: FieldopsApp, gates: FieldopsGates): void {
  // GET /api/fieldops/tasks/mine — the caller's own assigned tasks across all jobs, with the job's
  // project name (LEFT JOIN jobs — a task's job_id is a soft ref, so a missing job → null name, never
  // a dropped row). Ordered open-first (CASE), created_at DESC. The personnel link is resolved by
  // matching personnel.username to the session username (authoritative from requireSession's D1 read);
  // no linked personnel → the JOIN yields nothing → empty list + linked:false.
  app.get(
    "/api/fieldops/tasks/mine",
    gates.requireSession,
    gates.requireCapability("cap.tasks.own"),
    async (c) => {
      const username = c.get("session").username;
      // The viewer's own ACTIVE personnel row (the same active=1 link + deterministic lowest-id
      // pick as fieldops_scope.resolveActorPersonnel) + their placement's project name via one
      // indexed jobs lookup (LEFT JOIN — current_job is a soft ref; a vanished job → null name).
      // Row present → linked:true; current_job present → viewer_placement (see the module-header
      // security note: self-information only).
      const viewerRow = await c.env.DB.prepare(
        `SELECT p.id AS personnel_id, p.name, p.current_job AS job_id, j.project_name
         FROM personnel p LEFT JOIN jobs j ON j.job_id = p.current_job
         WHERE p.username = ?1 AND p.active = 1
         ORDER BY p.id ASC LIMIT 1`,
      )
        .bind(username)
        .first<{ personnel_id: number; name: string; job_id: string | null; project_name: string | null }>();
      const viewerPlacement: ViewerTaskPlacement | null =
        viewerRow && viewerRow.job_id !== null
          ? {
              job_id: viewerRow.job_id,
              project_name: viewerRow.project_name,
              personnel_id: viewerRow.personnel_id,
              name: viewerRow.name,
            }
          : null;
      const sql = `
        SELECT t.id, t.job_id, j.project_name, t.description, t.status, t.created_at, t.assigned_by
        FROM task_assignments t
        JOIN personnel p ON p.id = t.personnel_id
        LEFT JOIN jobs j ON j.job_id = t.job_id
        WHERE p.username = ?1
        ORDER BY CASE t.status WHEN 'open' THEN 0 WHEN 'in_progress' THEN 1 ELSE 2 END ASC,
                 t.created_at DESC, t.id DESC
        LIMIT ?2
      `;
      const res = await c.env.DB.prepare(sql).bind(username, MY_TASKS_CAP).all<MyTask>();
      const payload: MyTasksResponse = {
        tasks: res.results ?? [],
        linked: viewerRow !== undefined && viewerRow !== null,
        viewer_placement: viewerPlacement,
      };
      return c.json(payload, 200);
    },
  );
}
