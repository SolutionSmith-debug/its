// Assigned-Tasks tab (P4 field-ops feature) S1 — "My Tasks" read client.
// Same-origin fetch with the session cookie (no auth header). The Worker re-gates on cap.tasks.own.
// Status changes reuse the existing setTaskStatus route (cap.tasks.own) — re-exported here so the
// page has a single import surface.
//
// (R1) Errors throw ApiError (src/lib/errorCopy.ts): err.message is human copy, err.code the raw
// wire code (e.g. 'forbidden_task' when a subcontractor targets a task that isn't theirs).
import { raiseApiError } from "./errorCopy";

export { setTaskStatus, type TaskStatus } from "./fieldops_jobtracker";

export interface MyTask {
  id: number;
  job_id: string;
  project_name: string | null;
  description: string;
  status: string;
  created_at: number;
  // R1: who assigned/last placed the task (actor username; stamped by the create + assign routes).
  // NULL on historical rows that predate stamping.
  assigned_by: string | null;
}

// R1 response contract: tasks arrive OPEN-FIRST (open < in_progress < done, newest first within a
// band — server CASE ordering). `linked` = whether the session has an ACTIVE linked personnel row —
// an unlinked account CANNOT have tasks, so the UI can explain the empty list ("your account isn't
// linked to the roster") instead of a bare "no tasks".
export interface MyTasksResponse {
  tasks: MyTask[];
  linked: boolean;
}

export async function fetchMyTasks(): Promise<MyTasksResponse> {
  const res = await fetch("/api/fieldops/tasks/mine", { credentials: "same-origin" });
  if (!res.ok) return raiseApiError(res);
  return ((await res.json()) as MyTasksResponse) ?? { tasks: [], linked: false };
}
