// Personnel read API client for Field Ops tab (BRIEF A).
// Same-origin fetch with session cookie; no auth header.
//
// (R1) Write errors throw ApiError (src/lib/errorCopy.ts): err.message is HUMAN copy, err.code the
// raw wire code for page-level branching (e.g. 'not_placed', 'login_not_allowed' on createCrew).
import { raiseApiError } from "./errorCopy";

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
  /** P2.6 — standing crew→job placement ("who is where"); NULL = unplaced. */
  current_job: string | null;
  /** Resolved project name for `current_job` (worker-joined); NULL when unplaced or unresolved. */
  current_job_name?: string | null;
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
  /** P2.6 — standing crew→job placement; NULL = unplaced. */
  current_job: string | null;
  /** Resolved project name for `current_job` (worker-joined); NULL when unplaced or unresolved. */
  current_job_name?: string | null;
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
    { personnel: { id, name: "", username: null, trade: "", current_job: null, time_entries: [] }, next_cursor: null };
}

// ── WRITE (task #22; cap.personnel.manage; same-origin cookie POST) ───────────────────────────────
async function postJson<T = { ok: boolean }>(url: string, body: unknown): Promise<T> {
  const res = await fetch(url, {
    method: "POST",
    credentials: "same-origin",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) return raiseApiError(res);
  return (await res.json()) as T;
}

export type AccountRole = "submitter" | "manager" | "admin";

export interface NewAccount {
  username: string;
  password: string;
  role: AccountRole;
}

/** Create a roster person. When `account` is present the server ALSO creates a login account and
 *  links it (admin-only server-side); omit `account` for a non-login roster person. */
export async function createPersonnel(body: { name: string; trade?: string; account?: NewAccount }): Promise<{ id: number }> {
  return postJson<{ ok: boolean; id: number }>("/api/fieldops/personnel", body);
}
export async function updatePersonnel(id: number, body: { name: string; trade?: string }): Promise<void> {
  await postJson(`/api/fieldops/personnel/${id}/update`, body);
}
export async function linkPersonnelAccount(id: number, username: string): Promise<void> {
  await postJson(`/api/fieldops/personnel/${id}/link`, { username });
}
export async function unlinkPersonnelAccount(id: number): Promise<void> {
  await postJson(`/api/fieldops/personnel/${id}/unlink`, {});
}
export async function retirePersonnel(id: number): Promise<void> {
  await postJson(`/api/fieldops/personnel/${id}/retire`, {});
}
/** P2.6 — set (jobId string) or clear (jobId null) a person's standing job placement. Gated
 *  server-side on cap.crew.assign (Manager + admin). ORTHOGONAL to time logging — placement only. */
export async function assignPersonnel(id: number, jobId: string | null): Promise<void> {
  await postJson(`/api/fieldops/personnel/${id}/assign`, { job_id: jobId });
}

// ── Slice T — subcontractor scoped crew-create (cap.crew.create; server-gated) ────────────────────
/** Create a NON-LOGIN roster person auto-placed on the ACTOR's own current job. Server rejects any
 *  account/login payload (login-mint stays admin-only) and 422 `not_placed` when the actor isn't
 *  placed on a job. Throws ApiError — branch on err.code (e.g. 'not_placed'); err.message is copy. */
export async function createCrew(body: { name: string; trade?: string }): Promise<{ id: number; current_job: string }> {
  return postJson<{ ok: boolean; id: number; current_job: string }>("/api/fieldops/crew", body);
}

export interface MyCrewMember {
  id: number;
  name: string;
  trade: string | null;
  current_job: string | null;
  /** G2.3 — 1 when the ACTOR created this person via the scoped crew-create (created_by = actor);
   *  gates the Edit/Retire controls (the actor's own linked row is 0). Worker-computed so raw
   *  created_by usernames stay off the wire. Optional for cached/older responses. */
  created_by_me?: number;
}
/** The crew a subcontractor may log time for: their own linked personnel + anyone they created. Backs
 *  the time-log person picker so only server-acceptable people are offered. cap.crew.create-gated. */
export async function fetchMyCrew(): Promise<MyCrewMember[]> {
  const res = await fetch("/api/fieldops/crew/mine", { credentials: "same-origin" });
  if (!res.ok) throw new Error("Could not load your crew.");
  return ((await res.json()) as { personnel: MyCrewMember[] }).personnel ?? [];
}

// ── G2.3 — scoped crew EDIT / RETIRE (cap.crew.create; created_by = actor; Worker re-gates) ───────
/** Fix a typo on crew the actor CREATED (name/trade only; active rows only). 404 `not_found` covers
 *  unknown / retired / not-created-by-me alike (no roster oracle). Throws ApiError — branch on
 *  err.code; err.message is copy. */
export async function updateCrew(id: number, body: { name: string; trade?: string }): Promise<void> {
  await postJson(`/api/fieldops/crew/${id}/update`, body);
}
/** Soft-retire (active=0) crew the actor CREATED — the typo'd-duplicate escape hatch. The Worker
 *  refuses with 409 `crew_has_foreign_time` (someone ELSE logged time on them) or 409
 *  `crew_on_other_job` (the office moved them) — real workers escalate to the office. Idempotent on
 *  an already-retired owned row. */
export async function retireCrew(id: number): Promise<void> {
  await postJson(`/api/fieldops/crew/${id}/retire`, {});
}
