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
