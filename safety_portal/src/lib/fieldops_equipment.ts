// Equipment read API client for Field Ops tab (BRIEF B).
// Same-origin fetch with session cookie; no auth header.

export interface LocationRecord {
  equipment_id: number;
  id?: number;
  label: string | null;
  lat: number | null;
  lon: number | null;
  read_at: number | null;
  recorded_at: number;
  job_id: string | null;
}

export interface InspectionRecord {
  equipment_id: number;
  uuid: string;
  form_code: string;
  version: number;
  performed_at: number | null;
  recorded_at: number;
  job_id: string | null;
}

export interface LogRecord {
  equipment_id: number;
  uuid: string;
  log_type: string;
  value_num: number | null;
  detail: string | null;
  status_value: string | null;
  performed_at: number | null;
  recorded_at: number;
}

export interface EquipmentHeader {
  id: number;
  name: string;
  kind: string | null;
  identifier: string | null;
  status: "fmc" | "degraded" | "down";
  status_note: string | null;
  status_changed_at: number | null;
  status_actor: string | null;
}

export interface EquipmentDetail {
  header: EquipmentHeader;
  locations: LocationRecord[];
  inspections: InspectionRecord[];
  logs: LogRecord[];
}

export interface EquipmentListResponse {
  equipment: (EquipmentHeader & {
    location: LocationRecord | null;
    latest_inspection: InspectionRecord | null;
    recent_logs: LogRecord[];
  })[];
  next_cursor: string | null;
}

export async function fetchEquipmentList(cursor?: string): Promise<EquipmentListResponse> {
  const q = new URLSearchParams();
  if (cursor) q.set("cursor", cursor);
  const res = await fetch(`/api/fieldops/equipment?${q.toString()}`, {
    credentials: "same-origin",
  });
  if (!res.ok) throw new Error("Could not load equipment.");
  return ((await res.json()) as { equipment: (EquipmentHeader & {
    location: LocationRecord | null;
    latest_inspection: InspectionRecord | null;
    recent_logs: LogRecord[];
  })[]; next_cursor: string | null }) ??
    { equipment: [], next_cursor: null };
}

export async function fetchEquipmentDetail(id: number, cursors?: {
  loc?: string;
  insp?: string;
  log?: string;
}): Promise<{ equipment: EquipmentDetail; cursors: { loc: string | null; insp: string | null; log: string | null } }> {
  const q = new URLSearchParams();
  if (cursors?.loc) q.set("loc_cursor", cursors.loc);
  if (cursors?.insp) q.set("insp_cursor", cursors.insp);
  if (cursors?.log) q.set("log_cursor", cursors.log);
  const res = await fetch(`/api/fieldops/equipment/${id}?${q.toString()}`, {
    credentials: "same-origin",
  });
  if (!res.ok) throw new Error("Could not load equipment detail.");
  return ((await res.json()) as { equipment: EquipmentDetail; cursors: { loc: string | null; insp: string | null; log: string | null } }) ??
    { equipment: { header: { id, name: "", kind: null, identifier: null, status: "fmc", status_note: null, status_changed_at: null, status_actor: null }, locations: [], inspections: [], logs: [] }, cursors: { loc: null, insp: null, log: null } };
}

// ── WRITE (P2.3; same-origin cookie POST) ───────────────────────────────────────────────────────
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

export type EquipStatus = "fmc" | "degraded" | "down";
export type LogType = "maintenance" | "fuel" | "hours";

// field actions (cap.equipment.field)
export async function setEquipmentStatus(id: number, body: { uuid: string; status: EquipStatus; status_note?: string }): Promise<void> {
  await postJson(`/api/fieldops/equipment/${id}/status`, body);
}
export async function logEquipmentMaintenance(id: number, body: { uuid: string; log_type: LogType; value_num?: number; detail?: string }): Promise<void> {
  await postJson(`/api/fieldops/equipment/${id}/log`, body);
}
export async function moveEquipment(id: number, body: { job_id: string; label?: string; lat?: number; lon?: number; read_at?: number }): Promise<void> {
  await postJson(`/api/fieldops/equipment/${id}/location`, body);
}

// roster admin (cap.equipment.manage)
export async function createEquipment(body: { name: string; kind?: string; identifier?: string; status?: EquipStatus; status_note?: string }): Promise<{ id: number }> {
  return postJson<{ ok: boolean; id: number }>("/api/fieldops/equipment", body);
}
export async function updateEquipment(id: number, body: { name: string; kind?: string; identifier?: string }): Promise<void> {
  await postJson(`/api/fieldops/equipment/${id}/update`, body);
}
export async function retireEquipment(id: number): Promise<void> {
  await postJson(`/api/fieldops/equipment/${id}/delete`, {});
}

// Minimal active-job options for the "move to job" picker (reads /api/fieldops/jobs; a field PM
// has cap.jobtracker.read per 0013, so this resolves — falls back to [] on any error/403).
export interface JobOption {
  job_id: string;
  project_name: string;
}
export async function fetchActiveJobOptions(): Promise<JobOption[]> {
  const res = await fetch("/api/fieldops/jobs?status=active", { credentials: "same-origin" });
  if (!res.ok) return [];
  const data = (await res.json()) as { jobs?: { job_id: string; project_name: string }[] };
  return (data.jobs ?? []).map((j) => ({ job_id: j.job_id, project_name: j.project_name }));
}
