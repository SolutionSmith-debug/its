// Per-job daily-form requirements (SOP daily form, slice D4) — ADMIN editor client.
// Same-origin fetch with the session cookie (no auth header). The Worker re-gates every write on
// cap.checklist.manage; caps here drive UI affordances only. Send-free (D1 reads/writes).
//
// The READ (the job's active items) lives in fieldops_daily_form.ts (fetchDailyRequirements) —
// it is the Daily tab's render surface too, gated cap.tasks.own + ownership scope; the admin
// editor reuses it (an admin passes the ownership check for any job) and gets the item ids it
// needs for edit / reorder / deactivate.
//
// (R1) Errors: postJson throws ApiError (src/lib/errorCopy.ts) — err.message is HUMAN copy,
// err.code the raw wire code (e.g. 'unknown_form_code', 'daily_tab_form_code', 'too_many_items').
import { raiseApiError } from "./errorCopy";
import type { DailyRequirementKind } from "./fieldops_daily_form";

export type { DailyRequirementItem, DailyRequirementKind } from "./fieldops_daily_form";
export { fetchDailyRequirements } from "./fieldops_daily_form";

/** The write payload for a requirement item (add / edit — the edit route REPLACES every field,
 *  so reorder re-writes send the full item with the new seq). Bounds re-enforced server-side. */
export interface RequirementInput {
  kind: DailyRequirementKind;
  label: string;
  seq?: number;
  form_code?: string; // form_link only: a catalog PARENT family (daily-tab parents refused)
  options?: string[]; // select only (D5): 1..20 non-empty choices, ≤120 chars each (Worker re-gates)
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

const BASE = "/api/fieldops/daily-form/job";

export function addRequirement(jobId: string, item: RequirementInput): Promise<{ ok: boolean; id: number | null }> {
  return postJson(`${BASE}/${encodeURIComponent(jobId)}/requirement`, item);
}

export function editRequirement(
  jobId: string,
  itemId: number,
  item: RequirementInput,
): Promise<{ ok: boolean; id: number }> {
  return postJson(`${BASE}/${encodeURIComponent(jobId)}/requirement/${itemId}/edit`, item);
}

/** Soft-delete: the item disappears from NEW renders; historical submissions keep their
 *  self-describing values array (the audit_log keeps the forensic record). */
export function deactivateRequirement(jobId: string, itemId: number): Promise<{ ok: boolean; id: number }> {
  return postJson(`${BASE}/${encodeURIComponent(jobId)}/requirement/${itemId}/deactivate`);
}
