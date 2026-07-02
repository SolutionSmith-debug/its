// Assigned-Tasks tab (P4 field-ops feature) S2 — checklist template editor client.
// Same-origin fetch with the session cookie (no auth header). The Worker re-gates every call on
// cap.checklist.manage; these caps drive UI affordances only. Send-free (D1 reads/writes).

export type ChecklistItemType = "form_linked" | "manual_attest" | "count" | "inspection";

// One item as returned by GET /checklist/default (the default template's own items).
export interface DefaultItem {
  id: number;
  seq: number;
  item_type: string;
  label: string | null;
  form_code: string | null;
  target_count: number | null;
  config_json: string | null;
}

export interface DefaultChecklist {
  template: {
    id: number;
    kind: string;
    title: string | null;
    source_form_code: string | null;
    active: number;
  } | null;
  items: DefaultItem[];
}

// One row of a job's EFFECTIVE (merged) checklist. `origin` tells the editor whether the row is a
// default item (suppressable) or one of the job's own added items (deletable).
export interface EffectiveItem {
  source_item_id: number;
  seq: number;
  item_type: string;
  label: string | null;
  form_code: string | null;
  target_count: number | null;
  config_json: string | null;
  origin: "default" | "override";
}

// A default item currently hidden for the job (can be un-hidden).
export interface SuppressedItem {
  source_item_id: number;
  seq: number;
  item_type: string;
  label: string | null;
  form_code: string | null;
  target_count: number | null;
}

export interface JobChecklist {
  job_id: string;
  items: EffectiveItem[];
  suppressed: SuppressedItem[];
}

// The write payload for an item (add-default / edit-default / add-job). Bounds re-enforced server-side.
export interface ItemInput {
  item_type: ChecklistItemType;
  label: string;
  seq?: number;
  form_code?: string;
  target_count?: number;
}

async function getJson<T>(url: string): Promise<T> {
  const res = await fetch(url, { credentials: "same-origin" });
  if (!res.ok) throw new Error(`Request failed (${res.status})`);
  return (await res.json()) as T;
}

async function postJson<T = { ok: boolean }>(url: string, body?: unknown): Promise<T> {
  const res = await fetch(url, {
    method: "POST",
    credentials: "same-origin",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body ?? {}),
  });
  if (!res.ok) {
    const err = (await res.json().catch(() => ({}))) as { error?: string };
    throw new Error(err.error ?? `Request failed (${res.status})`);
  }
  return (await res.json()) as T;
}

const BASE = "/api/fieldops/checklist";

// ── Default template ─────────────────────────────────────────────────────────────────────────────
export function fetchDefaultChecklist(): Promise<DefaultChecklist> {
  return getJson<DefaultChecklist>(`${BASE}/default`);
}

export function addDefaultItem(item: ItemInput): Promise<{ ok: boolean; id: number | null }> {
  return postJson(`${BASE}/default/item`, item);
}

export function editDefaultItem(itemId: number, item: ItemInput): Promise<{ ok: boolean; id: number }> {
  return postJson(`${BASE}/default/item/${itemId}/edit`, item);
}

export function deleteDefaultItem(itemId: number): Promise<{ ok: boolean; id: number }> {
  return postJson(`${BASE}/default/item/${itemId}/delete`);
}

// ── Per-job effective checklist + overrides ──────────────────────────────────────────────────────
export function fetchJobChecklist(jobId: string): Promise<JobChecklist> {
  return getJson<JobChecklist>(`${BASE}/job/${encodeURIComponent(jobId)}`);
}

export function addJobItem(jobId: string, item: ItemInput): Promise<{ ok: boolean; id: number | null }> {
  return postJson(`${BASE}/job/${encodeURIComponent(jobId)}/item`, item);
}

export function deleteJobItem(jobId: string, itemId: number): Promise<{ ok: boolean; id: number }> {
  return postJson(`${BASE}/job/${encodeURIComponent(jobId)}/item/${itemId}/delete`);
}

export function suppressDefaultItem(jobId: string, defaultItemId: number): Promise<{ ok: boolean }> {
  return postJson(`${BASE}/job/${encodeURIComponent(jobId)}/item/${defaultItemId}/suppress`);
}

export function unsuppressDefaultItem(jobId: string, defaultItemId: number): Promise<{ ok: boolean }> {
  return postJson(`${BASE}/job/${encodeURIComponent(jobId)}/item/${defaultItemId}/unsuppress`);
}

// ── S3 — the placed manager's daily "Progress Report" checklist (cap.tasks.own; the OWNER's tab) ────
// Distinct surface from the admin editor above: GET /checklist/mine runs Worker-on-read generation for
// a placed manager and returns { instance: null } for everyone else (a submitter, an unplaced manager)
// so the My-Tasks page hides the section entirely. Completion is manual_attest-only in S3 + scoped
// server-side to the actor's OWN daily instance.

export type ChecklistItemStatus = "open" | "done";

// One per-instance item state (the snapshot + completion row, migration 0026 checklist_item_states).
export interface ChecklistItemState {
  id: number;
  source_item_id: number | null;
  item_type: string;
  label: string | null;
  form_code: string | null;
  target_count: number | null;
  status: ChecklistItemStatus;
  note: string | null;
  photo_ref: string | null;
  completed_by: string | null;
  completed_at: number | null;
  value_num: number | null;
}

export interface DailyInstance {
  id: number;
  job_id: string;
  instance_date: string;
  status: "open" | "complete";
}

export interface MyChecklist {
  instance: DailyInstance | null;
  items: ChecklistItemState[];
}

export interface CompleteResult {
  ok: boolean;
  id: number;
  status: ChecklistItemStatus;
  value_num?: number | null;
  instance_status: "open" | "complete";
}

// Today's daily checklist for the logged-in placed manager (instance:null for anyone else).
export function fetchMyChecklist(): Promise<MyChecklist> {
  return getJson<MyChecklist>(`${BASE}/mine`);
}

// Mark a manual_attest item done (optional note/photo_ref). The Worker refuses form_linked/inspection
// items (they auto-close on a matching submission — see recordCountItem for count).
export function completeChecklistItem(
  stateId: number,
  opts?: { note?: string; photo_ref?: string },
): Promise<CompleteResult> {
  return postJson<CompleteResult>(`${BASE}/item-state/${stateId}/complete`, opts ?? {});
}

// Record a count item's value (P4 S4). The Worker completes it iff value_num >= target_count; below
// target it throws (the postJson error carries the server 'below_target' code). Same /complete route.
export function recordCountItem(stateId: number, valueNum: number): Promise<CompleteResult> {
  return postJson<CompleteResult>(`${BASE}/item-state/${stateId}/complete`, { value_num: valueNum });
}

// Toggle a manually-completed item (manual_attest / count) back to open. form_linked/inspection reject.
export function uncompleteChecklistItem(stateId: number): Promise<CompleteResult> {
  return postJson<CompleteResult>(`${BASE}/item-state/${stateId}/uncomplete`);
}
