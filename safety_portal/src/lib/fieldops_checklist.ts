// Assigned-Tasks tab (P4 field-ops feature) S2 — checklist template editor client.
// Same-origin fetch with the session cookie (no auth header). The Worker re-gates every call on
// cap.checklist.manage; these caps drive UI affordances only. Send-free (D1 reads/writes).
//
// (R1) Errors: getJson/postJson throw ApiError (src/lib/errorCopy.ts) — err.message is HUMAN copy,
// err.code is the raw wire code pages branch on (e.g. 'below_target', 'already_assigned').
import { ApiError, raiseApiError } from "./errorCopy";
import { fetchPersonnelList, type PersonnelRow } from "./fieldops_personnel";
import type {
  ChecklistItemStatus,
  AssignedInspectionsResponse,
  ItemPhotoUploadResult,
} from "../../worker/wire-types";
import type { PhotoValue } from "../forms/types";

export type ChecklistItemType = "form_linked" | "manual_attest" | "count" | "inspection";

// One checklist-template item (the shape GET /checklist/inspection/:id returns per item; named
// for the daily_default template it originally served — the retired-flow clients are gone but
// the S6 inspection surfaces still speak this shape).
export interface DefaultItem {
  id: number;
  seq: number;
  item_type: string;
  label: string | null;
  form_code: string | null;
  target_count: number | null;
  config_json: string | null;
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

// RETIRED-FLOW CLIENTS REMOVED (D2, SOP daily form → optimization #6, 2026-07): the admin
// "Default daily checklist" editor, the Job-Tracker per-job override editor, the manager daily
// checklist (GET /checklist/mine), and the S5 rollup-draft flow all retired with D2 — their 11
// thin client fns + result types sat here unused, a live autocomplete hazard for retired routes.
// Grep-verified zero importers before removal. UPDATE (operator approval 2026-07-03, B3): the two
// daily-generation WORKER routes (GET /checklist/mine + /mine/rollup-draft) were then ALSO deleted
// (tombstones in worker/fieldops_checklist.ts); the template-editor routes + the checklist ENGINE
// (assigned inspections) remain live.

// ── Per-item completion (shared by the assigned-inspections surface below) ──────────────────────
// Wire shapes — SINGLE-SOURCED in worker/wire-types.ts (the Worker types its c.json payloads with
// the same definitions, so a shape drift fails the typecheck on both sides); re-exported here so
// existing importers keep their path (the CS4 follow-up the wire-types header used to track).
export type {
  ChecklistItemStatus,
  ChecklistItemState,
  AssignedInstance,
  AssignedInspection,
  AssignedInspectionsResponse,
  ItemPhotoStatus,
  ItemPhotoUploadResult,
} from "../../worker/wire-types";

export interface CompleteResult {
  ok: boolean;
  id: number;
  status: ChecklistItemStatus;
  value_num?: number | null;
  instance_status: "open" | "complete";
  // R1: true when the completion was an acknowledged below-target count (see recordCountItem).
  acknowledged_below_target?: boolean;
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

// ── G1 Slice 1 — item-photo capture (record-only; Option D) ─────────────────────────────────────
// Attach ONE photo (the PhotoField-encoded PhotoValue) to a checklist item state. The photo is
// queued for the Mac §34 screen — it is never served back; the UI renders `photo_status` only.
// 409 err.code 'photo_already_attached' while a photo is pending/clean (one photo per item);
// a REFUSED photo may be retried. The Worker's bounds 400 carries the actionable machine reason
// in `detail` (photo_too_large / photo_bad_magic / …, the /api/submit convention) — prefer it
// over the generic 'invalid_photo' so the crew gets field-actionable copy.
export async function uploadItemPhoto(stateId: number, photo: PhotoValue): Promise<ItemPhotoUploadResult> {
  const res = await fetch(`${BASE}/item-state/${stateId}/photo`, {
    method: "POST",
    credentials: "same-origin",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ photo }),
  });
  if (!res.ok) {
    let detail: string | null = null;
    try {
      const body = (await res.clone().json()) as { error?: unknown; detail?: unknown };
      if (body.error === "invalid_photo" && typeof body.detail === "string") detail = body.detail;
    } catch {
      /* non-JSON body → the shared handler below */
    }
    if (detail) {
      console.warn(`API error ${res.status}: invalid_photo/${detail} (${res.url})`);
      throw new ApiError(detail, res.status);
    }
    return raiseApiError(res);
  }
  return (await res.json()) as ItemPhotoUploadResult;
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
// routes are ownership-scoped, kind-agnostic). The AssignedInstance / AssignedInspection /
// AssignedInspectionsResponse shapes (incl. the R1 `linked` + open-first ordering contract) are
// re-exported from worker/wire-types.ts above — one definition, both tsconfig scopes.

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
