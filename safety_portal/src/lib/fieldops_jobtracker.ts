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
// convenience only. Create/close/progress/add-task → cap.jobtracker.manage; task status → cap.tasks.own.
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

// manage (cap.jobtracker.manage)
export async function createJob(body: {
  job_id: string;
  project_name: string;
  progress?: number;
  new_client?: NewJobClient;
}): Promise<{ job_id: string }> {
  return postJson<{ ok: boolean; job_id: string }>("/api/fieldops/job", body);
}
export async function closeJob(jobId: string): Promise<void> {
  await postJson(`/api/fieldops/job/${encodeURIComponent(jobId)}/close`, {});
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
