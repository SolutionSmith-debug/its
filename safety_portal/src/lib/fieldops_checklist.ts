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
  // S5: the auto-filed / manager-filed Daily Report submission this instance rolled up into. Non-null
  // once a daily-report (family) submission exists for the instance's job+date (server reconcile).
  rolled_up_submission_uuid: string | null;
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

// ── S5 — auto-rollup → Daily Report ────────────────────────────────────────────────────────────────
// A best-effort Daily Report DRAFT assembled from the day's data (job/crew/equipment/date/manager +
// a factual checklist summary). Returned only for a COMPLETE daily instance (else the Worker 409s).
// `values` is a FormRenderer FormValues object keyed to daily-report-v1 (header keys + repeating-table
// row arrays + comments); the FormFillPage merges it over the form's empty defaults. NO send happens
// here — the manager reviews/edits and files via the normal /api/submit path.
export interface RollupDraft {
  job_id: string;
  work_date: string;
  form_code: string; // 'daily-report' (the catalog parent family)
  values: Record<string, unknown>;
}

export function fetchRollupDraft(): Promise<RollupDraft> {
  return getJson<RollupDraft>(`${BASE}/mine/rollup-draft`);
}

// ── S6 — generic-inspection library (admin authoring + assign; cap.checklist.manage) ────────────────
// A library of generic_inspection templates the admin authors (title + items, reusing the same
// ItemInput shape) and ASSIGNS ad-hoc to a manager/subcontractor. Distinct from the daily_default:
// MANY templates, no job_override merge. The Worker re-gates every call on cap.checklist.manage.

// A library template header + its item count (GET /checklist/inspections list row).
export interface InspectionTemplate {
  id: number;
  title: string | null;
  active: number;
  created_at: number;
  item_count: number;
}

// One library template + its items (GET /checklist/inspection/:id).
export interface InspectionDetail {
  template: { id: number; title: string | null; active: number };
  items: DefaultItem[];
}

export function fetchInspectionTemplates(): Promise<{ templates: InspectionTemplate[] }> {
  return getJson<{ templates: InspectionTemplate[] }>(`${BASE}/inspections`);
}

export function fetchInspectionTemplate(templateId: number): Promise<InspectionDetail> {
  return getJson<InspectionDetail>(`${BASE}/inspection/${templateId}`);
}

export function createInspectionTemplate(title: string): Promise<{ ok: boolean; id: number | null }> {
  return postJson(`${BASE}/inspection`, { title });
}

export function editInspectionTemplate(
  templateId: number,
  patch: { title: string; active?: boolean },
): Promise<{ ok: boolean; id: number }> {
  return postJson(`${BASE}/inspection/${templateId}/edit`, patch);
}

export function deleteInspectionTemplate(templateId: number): Promise<{ ok: boolean; id: number }> {
  return postJson(`${BASE}/inspection/${templateId}/delete`);
}

export function addInspectionItem(templateId: number, item: ItemInput): Promise<{ ok: boolean; id: number | null }> {
  return postJson(`${BASE}/inspection/${templateId}/item`, item);
}

export function editInspectionItem(
  templateId: number,
  itemId: number,
  item: ItemInput,
): Promise<{ ok: boolean; id: number }> {
  return postJson(`${BASE}/inspection/${templateId}/item/${itemId}/edit`, item);
}

export function deleteInspectionItem(templateId: number, itemId: number): Promise<{ ok: boolean; id: number }> {
  return postJson(`${BASE}/inspection/${templateId}/item/${itemId}/delete`);
}

// Assign a generic_inspection template to a person (optional job + due date). Returns the new instance
// id + snapshotted item count. Throws 'already_assigned' on an exact (job+date) duplicate.
export interface AssignInput {
  template_id: number;
  assignee_personnel_id: number;
  job_id?: string;
  due_date?: string;
}

export function assignInspection(input: AssignInput): Promise<{ ok: boolean; instance_id: number; item_count: number }> {
  return postJson(`${BASE}/assign`, input);
}

// ── S6 — the assignee's Assigned-Tasks tab surface (cap.tasks.own; manager OR subcontractor) ────────
// The inspection instances assigned to the logged-in person + their item states. Completion reuses the
// existing completeChecklistItem / recordCountItem / uncompleteChecklistItem calls (the item-state
// routes are ownership-scoped, kind-agnostic).
export interface AssignedInstance {
  id: number;
  job_id: string | null;
  project_name: string | null;
  instance_date: string | null;
  status: "open" | "complete";
}

export interface AssignedInspection {
  instance: AssignedInstance;
  items: ChecklistItemState[];
}

export function fetchAssignedInspections(): Promise<{ inspections: AssignedInspection[] }> {
  return getJson<{ inspections: AssignedInspection[] }>(`${BASE}/assigned`);
}
