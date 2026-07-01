// Assigned-Tasks tab (P4 field-ops feature) S1 — "My Tasks" read client.
// Same-origin fetch with the session cookie (no auth header). The Worker re-gates on cap.tasks.own.
// Status changes reuse the existing setTaskStatus route (cap.tasks.own) — re-exported here so the
// page has a single import surface.
export { setTaskStatus, type TaskStatus } from "./fieldops_jobtracker";

export interface MyTask {
  id: number;
  job_id: string;
  project_name: string | null;
  description: string;
  status: string;
  created_at: number;
}

export interface MyTasksResponse {
  tasks: MyTask[];
}

export async function fetchMyTasks(): Promise<MyTasksResponse> {
  const res = await fetch("/api/fieldops/tasks/mine", { credentials: "same-origin" });
  if (!res.ok) throw new Error("Could not load your tasks.");
  return ((await res.json()) as MyTasksResponse) ?? { tasks: [] };
}
