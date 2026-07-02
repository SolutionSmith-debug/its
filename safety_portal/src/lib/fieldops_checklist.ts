// Assigned-Tasks tab (P4 field-ops feature) S2 — checklist template editor client.
// Same-origin fetch with the session cookie (no auth header). The Worker re-gates every call on
// cap.checklist.manage; these caps drive UI affordances only. Send-free (D1 reads/writes).
//
// (R1) Errors: getJson/postJson throw ApiError (src/lib/errorCopy.ts) — err.message is HUMAN copy,
// err.code is the raw wire code pages branch on (e.g. 'below_target', 'already_assigned').
import { raiseApiError } from "./errorCopy";
import { fetchPersonnelList, type PersonnelRow } from "./fieldops_personnel";

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
  if (!res.ok) return raiseApiError(res);
  return (await res.json()) as T;
}

async function postJson<T = { ok: boolean }>(url: string, body?: unknown): Promise<T> {
  const res = await fetch(url, {
    method: "POST",
    credentials: "same-origin",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body ?? {}),
  });
  if (!res.ok) return raiseApiError(res);
  return (await res.json()) as T;
}

const BASE = "/api/fieldops/checklist";

// ── Default template ─────────────────────────────────────────────────────────────────────────────
// DEPRECATED-FOR-DAILY (D2, SOP daily form): the admin "Default daily checklist" editor + the
// Job-Tracker per-job editor were retired — no SPA surface calls the default/job-override fns
// below anymore. The Worker routes they wrap STAY (§14/§49; the engine serves assigned
// inspections), so these thin clients are kept in-tree rather than deleted.
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
  // R1: WHO filed the submission that auto-closed this item (completed_by === '(auto)') — the
  // personnel display name, falling back to the raw attributed account. NULL for manually-completed
  // / still-open items, or when no matching submission is resolvable (best-effort attribution).
  filed_by: string | null;
}

// R1: WHY the daily section is empty — mirrors the server's three generation preconditions so the
// UI can explain instead of rendering a lying blank. null whenever `instance` is non-null.
export type DailyEmptyReason = "not_manager" | "no_personnel_link" | "not_placed";

export interface DailyInstance {
  id: number;
  job_id: string;
  // R1: the job's project name (LEFT JOIN; null if the job row is gone) — headings shouldn't show a
  // raw job id.
  project_name: string | null;
  instance_date: string;
  status: "open" | "complete";
  // S5: the auto-filed / manager-filed Daily Report submission this instance rolled up into. Non-null
  // once a daily-report (family) submission exists for the instance's job+date (server reconcile).
  rolled_up_submission_uuid: string | null;
  // R1: WHO filed that rolled-up Daily Report (display name, fallback raw account); null until rolled up.
  rolled_up_by: string | null;
}

export interface MyChecklist {
  instance: DailyInstance | null;
  items: ChecklistItemState[];
  reason: DailyEmptyReason | null;
}

export interface CompleteResult {
  ok: boolean;
  id: number;
  status: ChecklistItemStatus;
  value_num?: number | null;
  instance_status: "open" | "complete";
  // R1: true when the completion was an acknowledged below-target count (see recordCountItem).
  acknowledged_below_target?: boolean;
}

// Today's daily checklist for the logged-in placed manager (instance:null for anyone else).
// DEPRECATED-FOR-DAILY (D2): the Daily tab is the SOP form now (DailyReportTab reads
// /api/fieldops/daily-form/status instead) — no SPA caller remains. Kept with the preserved route.
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
// target it throws (the ApiError's err.code is 'below_target'; the value IS recorded, item stays
// open). (R1) Passing { acknowledgeBelowTarget: true, note } completes the item BELOW target — the
// note is REQUIRED server-side (err.code 'note_required' without one) and the completion audits
// under its own action. Same /complete route.
export function recordCountItem(
  stateId: number,
  valueNum: number,
  opts?: { acknowledgeBelowTarget?: boolean; note?: string },
): Promise<CompleteResult> {
  const body: Record<string, unknown> = { value_num: valueNum };
  if (opts?.acknowledgeBelowTarget) {
    body.acknowledge_below_target = true;
    if (opts.note !== undefined) body.note = opts.note;
  }
  return postJson<CompleteResult>(`${BASE}/item-state/${stateId}/complete`, body);
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

// DEPRECATED-FOR-DAILY (D2): the S5 "Review & file Daily Report" rollup flow retired with the
// checkbox checklist — the Daily tab fills the form directly. No SPA caller remains.
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
// id + snapshotted item count. Throws ApiError err.code 'already_assigned' on an exact (job+date)
// duplicate; (R1) 'empty_template' on a 0-item template, and 'job_and_date_required' when the
// template contains form_linked/inspection items but job_id + due_date aren't BOTH supplied.
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
  // R1: the assigned template's title, SNAPSHOTTED at assign time (migration 0029) — render this,
  // never "Inspection #<id>". NULL only on legacy instances the backfill couldn't resolve.
  template_title: string | null;
  created_at: number;
}

export interface AssignedInspection {
  instance: AssignedInstance;
  items: ChecklistItemState[];
}

// R1 response contract: `linked` = whether the session has an ACTIVE linked personnel row — an
// unlinked account CANNOT have assignments, so the UI can explain the empty list ("your account
// isn't linked to the roster") instead of a bare "no inspections". Instances arrive OPEN-FIRST
// (server CASE ordering), newest first within a status band.
export interface AssignedInspectionsResponse {
  inspections: AssignedInspection[];
  linked: boolean;
}

export function fetchAssignedInspections(): Promise<AssignedInspectionsResponse> {
  return getJson<AssignedInspectionsResponse>(`${BASE}/assigned`);
}

// ── R5 — assignment lifecycle (admin visibility + revocation; cap.checklist.manage) ─────────────────
// GET /checklist/instances lists OUTSTANDING inspection-kind instances (daily instances are
// auto-generated noise and excluded server-side); POST /instance/:id/cancel revokes one (hard delete
// of the instance + its item states; the audit_log keeps the forensic record). A cancelled assignment
// disappears from the assignee's /checklist/assigned on their next load.

export type InstanceStatusFilter = "open" | "complete" | "all";

// One admin assignments-list row (GET /checklist/instances). items_done/items_total is the item-state
// aggregate so the list can show progress without loading any instance detail.
export interface AdminInstanceRow {
  id: number;
  template_title: string | null;
  assignee_personnel_id: number | null;
  assignee_name: string | null;
  job_id: string | null;
  project_name: string | null;
  instance_date: string | null;
  status: "open" | "complete";
  created_at: number;
  items_total: number;
  items_done: number;
}

export function fetchChecklistInstances(
  status: InstanceStatusFilter = "open",
): Promise<{ instances: AdminInstanceRow[]; status_filter: InstanceStatusFilter }> {
  return getJson<{ instances: AdminInstanceRow[]; status_filter: InstanceStatusFilter }>(
    `${BASE}/instances?status=${status}`,
  );
}

// Cancel (revoke) an assigned inspection instance. Throws ApiError err.code 'not_found' when the id
// is unknown OR names a daily instance (not cancellable — it would regenerate on the next read).
export function cancelChecklistInstance(instanceId: number): Promise<{ ok: boolean; id: number }> {
  return postJson(`${BASE}/instance/${instanceId}/cancel`);
}

// R5 — the assign picker must offer the FULL active roster, not the first personnel page. The roster
// endpoint is keyset-paginated (default 50/page); this loops the cursor to exhaustion, bounded at
// maxPages as a runaway guard (10 × 50 = 500 people ≫ the 10–50-person firm this serves). NOTE the
// verified assign rule: POST /checklist/assign requires an ACTIVE personnel row only — a portal
// LOGIN is NOT required — so callers should offer every active person, unfiltered.
export const ROSTER_MAX_PAGES = 10;

export async function fetchFullRoster(maxPages: number = ROSTER_MAX_PAGES): Promise<PersonnelRow[]> {
  const all: PersonnelRow[] = [];
  let cursor: string | undefined;
  for (let page = 0; page < maxPages; page++) {
    const res = await fetchPersonnelList(cursor);
    all.push(...res.personnel);
    if (!res.next_cursor) break;
    cursor = res.next_cursor;
  }
  return all;
}
