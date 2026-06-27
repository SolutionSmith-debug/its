// Personnel read API client for Field Ops tab (BRIEF A).
// Same-origin fetch with session cookie; no auth header.

export interface LatestEntry {
  personnel_id: number;
  job_id: string;
  project_name: string | null;
  hours: number | null;
  work_started_at: number | null;
  work_ended_at: number | null;
  recorded_at: number;
}

export interface PersonnelRow {
  id: number;
  name: string;
  trade: string;
  username: string | null;
}

export interface PersonnelListResponse {
  personnel: PersonnelRow[];
  latest_entries: LatestEntry[];
  next_cursor: string | null;
}

export async function fetchPersonnelList(cursor?: string): Promise<PersonnelListResponse> {
  const q = new URLSearchParams();
  if (cursor) q.set("cursor", cursor);
  const res = await fetch(`/api/fieldops/personnel?${q.toString()}`, {
    credentials: "same-origin",
  });
  if (!res.ok) throw new Error("Could not load personnel.");
  return ((await res.json()) as { personnel: PersonnelRow[]; latest_entries: LatestEntry[]; next_cursor: string | null }) ??
    { personnel: [], latest_entries: [], next_cursor: null };
}

export interface PersonnelDetail {
  id: number;
  name: string;
  username: string | null;
  trade: string;
  time_entries: TimeEntry[];
}

export interface TimeEntry {
  uuid: string;
  job_id: string;
  project_name: string | null;
  hours: number | null;
  work_started_at: number | null;
  work_ended_at: number | null;
  recorded_at: number;
  notes: string | null;
}

export async function fetchPersonnelDetail(id: number, cursor?: string): Promise<{ personnel: PersonnelDetail; next_cursor: string | null }> {
  const q = new URLSearchParams();
  if (cursor) q.set("cursor", cursor);
  const res = await fetch(`/api/fieldops/personnel/${id}?${q.toString()}`, {
    credentials: "same-origin",
  });
  if (!res.ok) throw new Error("Could not load personnel detail.");
  return ((await res.json()) as { personnel: PersonnelDetail; next_cursor: string | null }) ??
    { personnel: { id, name: "", username: null, trade: "", time_entries: [] }, next_cursor: null };
}
