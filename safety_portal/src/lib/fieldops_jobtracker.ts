// Job Tracker read API client for Field Ops tab (BRIEF C).
// Same-origin fetch with session cookie; no auth header.

export interface CrewMember {
  id: number;
  name: string;
  trade: string | null;
}

export interface OpenTask {
  id: number;
  description: string;
  status: string;
  personnel_name: string | null;
}

export interface JobRow {
  job_id: string;
  project_name: string;
  status: string;
  progress: number;
  client_name: string | null;
  crew: CrewMember[];
  open_tasks: OpenTask[];
}

export interface JobListResponse {
  jobs: JobRow[];
  next_cursor: string | null;
}

export interface Task {
  id: number;
  description: string;
  status: string;
  created_at: number;
  personnel_id: number | null;
  personnel_name: string | null;
}

export interface JobTimeEntry {
  uuid: string;
  hours: number | null;
  work_started_at: number | null;
  work_ended_at: number | null;
  recorded_at: number;
  notes: string | null;
  personnel_name: string | null;
}

export interface EquipmentOnSite {
  id: number;
  name: string;
  kind: string | null;
  identifier: string | null;
  label: string | null;
  read_at: number | null;
}

export interface JobInspection {
  uuid: string;
  form_code: string;
  version: number;
  performed_at: number | null;
  recorded_at: number;
  equipment_name: string | null;
}

export interface JobClient {
  name: string;
  contact: string | null;
  phone: string | null;
  email: string | null;
}

export interface JobDetail {
  job_id: string;
  project_name: string;
  status: string;
  progress: number;
  client: JobClient | null;
  crew: CrewMember[];
  tasks: Task[];
  time_entries: JobTimeEntry[];
  equipment_on_site: EquipmentOnSite[];
  inspections: JobInspection[];
}

export interface JobDetailResponse {
  job: JobDetail;
  cursors: { tasks: string | null; time: string | null; insp: string | null };
}

export type JobStatusFilter = "active" | "closed" | "on_hold" | "all";

export async function fetchJobList(status?: JobStatusFilter, cursor?: string): Promise<JobListResponse> {
  const q = new URLSearchParams();
  if (status) q.set("status", status);
  if (cursor) q.set("cursor", cursor);
  const res = await fetch(`/api/fieldops/jobs?${q.toString()}`, { credentials: "same-origin" });
  if (!res.ok) throw new Error("Could not load jobs.");
  return ((await res.json()) as JobListResponse) ?? { jobs: [], next_cursor: null };
}

export async function fetchJobDetail(
  jobId: string,
  cursors?: { task?: string; time?: string; insp?: string },
): Promise<JobDetailResponse> {
  const q = new URLSearchParams();
  if (cursors?.task) q.set("task_cursor", cursors.task);
  if (cursors?.time) q.set("time_cursor", cursors.time);
  if (cursors?.insp) q.set("insp_cursor", cursors.insp);
  const res = await fetch(`/api/fieldops/jobs/${encodeURIComponent(jobId)}?${q.toString()}`, {
    credentials: "same-origin",
  });
  if (!res.ok) throw new Error("Could not load job detail.");
  return (await res.json()) as JobDetailResponse;
}

// ── WRITE (P2.3 routes; same-origin cookie POST) ─────────────────────────────────────────────────
// NB: the READ routes are PLURAL (/api/fieldops/jobs…); the P2.3 WRITE routes are SINGULAR
// (/api/fieldops/job…, /api/fieldops/task…). Mirror the worker exactly — see fieldops_job_write.ts /
// fieldops_task_write.ts. The Worker re-gates every call server-side; UI capability checks are
// convenience only. Create/close/add-task/reassign-task → cap.jobtracker.manage; task status → cap.tasks.own.
async function postJson<T = { ok: boolean }>(url: string, body: unknown): Promise<T> {
  const res = await fetch(url, {
    method: "POST",
    credentials: "same-origin",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const err = (await res.json().catch(() => ({}))) as { error?: string };
    throw new Error(err.error ?? `Request failed (${res.status})`);
  }
  return (await res.json()) as T;
}

export type TaskStatus = "open" | "in_progress" | "done";

// Optional inline client for a brand-new portal-origin job (worker INSERTs into `clients` first).
export interface NewJobClient {
  name: string;
  contact?: string;
  phone?: string;
  email?: string;
}

// P2.5 — the portal is the authoritative writer of a job's routing source-of-truth. lifecycle is
// the canonical job-state field (active|inactive|archived); the legacy `active`/`status` flags are
// derived by the worker. Routing block + CC arrays mirror fieldops_job_write.ts parseRouting:
// every field optional, each CC array ≤5 email-shaped strings. The worker re-validates + re-gates.
export type JobLifecycle = "active" | "inactive" | "archived";

export interface JobRouting {
  address?: string;
  stakeholder_name?: string;
  stakeholder_email?: string;
  stakeholder_phone?: string;
  safety_contact_name?: string;
  safety_contact_email?: string;
  safety_cc?: string[];
  progress_contact_name?: string;
  progress_contact_email?: string;
  progress_cc?: string[];
}

// manage (cap.jobtracker.manage)
// Slice 6: the portal ASSIGNS the canonical Job ID (the office employee no longer types one); the
// request carries only Project Name (+ optional client/routing), and the assigned JOB-###### is
// returned in the response.
export async function createJob(
  body: {
    project_name: string;
    progress?: number;
    new_client?: NewJobClient;
  } & JobRouting,
): Promise<{ job_id: string }> {
  return postJson<{ ok: boolean; job_id: string }>("/api/fieldops/job", body);
}
export async function closeJob(jobId: string): Promise<void> {
  await postJson(`/api/fieldops/job/${encodeURIComponent(jobId)}/close`, {});
}
// Set the canonical lifecycle (P2.5). Supersedes the bare /close in the UI; /close stays as a thin
// 'inactive' alias. The worker derives the legacy active/status flags and bumps the mirror version.
export async function setLifecycle(jobId: string, lifecycle: JobLifecycle): Promise<{ lifecycle: JobLifecycle }> {
  return postJson<{ ok: boolean; lifecycle: JobLifecycle }>(
    `/api/fieldops/job/${encodeURIComponent(jobId)}/lifecycle`,
    { lifecycle },
  );
}
// Edit the routing SoR block (address + stakeholder + safety/progress contacts + CC arrays). The
// worker FULL-OVERWRITES the routing fields (an omitted field → ''), so send the complete intended
// routing for the job. job_id/lifecycle/status are untouched.
export async function editContacts(jobId: string, routing: JobRouting): Promise<{ job_id: string }> {
  return postJson<{ ok: boolean; job_id: string }>(
    `/api/fieldops/job/${encodeURIComponent(jobId)}/contacts`,
    routing,
  );
}
export async function setJobProgress(jobId: string, progress: number): Promise<{ progress: number }> {
  return postJson<{ ok: boolean; progress: number }>(`/api/fieldops/job/${encodeURIComponent(jobId)}/progress`, { progress });
}
export async function addTask(
  jobId: string,
  body: { description: string; personnel_id?: number },
): Promise<{ id: number | null }> {
  return postJson<{ ok: boolean; id: number | null }>(`/api/fieldops/job/${encodeURIComponent(jobId)}/task`, body);
}

// field action (cap.tasks.own)
export async function setTaskStatus(taskId: number, status: TaskStatus): Promise<void> {
  await postJson(`/api/fieldops/task/${taskId}/status`, { status });
}

// (re)assign or clear a task's assignee (cap.jobtracker.manage — assigning who does a task is
// management, the same cap as add-task). `personnelId` null = unassign. The Worker validates the
// personnel_id is a real roster member and re-gates the capability.
export async function reassignTask(taskId: number, personnelId: number | null): Promise<void> {
  await postJson(`/api/fieldops/task/${taskId}/assign`, { personnel_id: personnelId });
}

// field action (cap.time.log). time_entries is an INTEGRITY-BAR table: the CLIENT generates `uuid`
// (idempotency / amend key) but the Worker stamps the server-authoritative record time — a forged
// body timestamp is ignored (see fieldops_time_write.ts). `hours` and `task_id` are optional; an
// omitted task → job-level time.
export async function logTime(body: {
  uuid: string;
  job_id: string;
  hours?: number;
  task_id?: number;
  personnel_id?: number;
  notes?: string;
}): Promise<{ uuid: string }> {
  return postJson<{ ok: boolean; uuid: string }>("/api/fieldops/time-entry", body);
}
