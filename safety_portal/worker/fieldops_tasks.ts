import type { FieldopsApp, FieldopsGates } from "./fieldops_gates";

// Assigned-Tasks tab (P4 field-ops feature) S1 — "My Tasks" READ. The subcontractor / manager sees
// the one-off tasks assigned to THEM. "Assigned to me" resolves the session's account → its linked
// personnel row (personnel.username == users.username, migration 0014's nullable soft link) → the
// tasks WHERE personnel_id = that. A session with NO linked personnel (no personnel row carrying its
// username) sees an EMPTY list — not an error. Send-free (D1 read only); cap.tasks.own gated.

// Per-account bound. A person's own assigned tasks are few; this cap is a defensive ceiling, not a
// paginated surface (unlike the Job Tracker's keyset legs).
const MY_TASKS_CAP = 500;

interface MyTaskRow {
  id: number;
  job_id: string;
  project_name: string | null;
  description: string;
  status: string;
  created_at: number;
}

export function registerMyTasksRoutes(app: FieldopsApp, gates: FieldopsGates): void {
  // GET /api/fieldops/tasks/mine — the caller's own assigned tasks across all jobs, with the job's
  // project name (LEFT JOIN jobs — a task's job_id is a soft ref, so a missing job → null name, never
  // a dropped row). Ordered status ASC, created_at DESC. The personnel link is resolved by matching
  // personnel.username to the session username (authoritative from requireSession's D1 read); no
  // linked personnel → the JOIN yields nothing → empty list.
  app.get(
    "/api/fieldops/tasks/mine",
    gates.requireSession,
    gates.requireCapability("cap.tasks.own"),
    async (c) => {
      const username = c.get("session").username;
      const sql = `
        SELECT t.id, t.job_id, j.project_name, t.description, t.status, t.created_at
        FROM task_assignments t
        JOIN personnel p ON p.id = t.personnel_id
        LEFT JOIN jobs j ON j.job_id = t.job_id
        WHERE p.username = ?1
        ORDER BY t.status ASC, t.created_at DESC
        LIMIT ?2
      `;
      const res = await c.env.DB.prepare(sql).bind(username, MY_TASKS_CAP).all<MyTaskRow>();
      return c.json({ tasks: res.results ?? [] }, 200);
    },
  );
}
