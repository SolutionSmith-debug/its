// SOP daily form (slice D2) — the Daily tab's filed-status read client.
// Same-origin fetch with the session cookie (no auth header). The Worker re-gates on cap.tasks.own;
// caps here drive UI affordances only. Send-free (D1 read only).
//
// (R1) Errors: throws ApiError (src/lib/errorCopy.ts) — err.message is HUMAN copy, err.code the raw
// wire code (e.g. 'invalid_date', 'not_found').
import { raiseApiError } from "./errorCopy";

/** The latest submission for one parent-form family on (job, date). `filed_by_name` is the
 *  personnel DISPLAY NAME resolved through submitted_as — NULL when the account has no roster
 *  link (never a raw username; the W9 posture — the UI drops the "by …" clause on NULL). */
export interface FiledEntry {
  filed_at: number; // epoch seconds (submissions.created_at)
  filed_by_name: string | null;
}

/** GET /api/fieldops/daily-form/status response. `filed` is keyed by PARENT form family
 *  (jha / visitor-sign-in / incident-report / daily-report) — a family with no submission for
 *  (job, date) is simply absent. `daily_filed` mirrors filed["daily-report"] (the banner's key). */
export interface DailyFormStatus {
  filed: Record<string, FiledEntry>;
  daily_filed: FiledEntry | null;
}

/** Filed-per-family status for (job, date) — drives the Daily tab's form_link "Filed ✓" indicators
 *  and the "already filed today" banner. The family match (parent OR versioned variant) runs
 *  server-side; callers pass the PARENT code exactly as the form_link sections carry it. */
export async function fetchDailyFormStatus(jobId: string, date: string): Promise<DailyFormStatus> {
  const q = new URLSearchParams({ job_id: jobId, date });
  const res = await fetch(`/api/fieldops/daily-form/status?${q.toString()}`, { credentials: "same-origin" });
  if (!res.ok) return raiseApiError(res);
  return (await res.json()) as DailyFormStatus;
}

// ── Per-job daily-form requirements (slice D4) ─────────────────────────────────────────────────

/** The parent-form families the status endpoint reports (client mirror of the Worker's
 *  DAILY_STATUS_FAMILIES module constant — fieldops_checklist.ts). A form_link REQUIREMENT whose
 *  form_code is outside this set still deep-links fine, but has NO live filed indicator — the
 *  renderer notes that instead of showing a lying blank. */
export const DAILY_STATUS_FAMILIES: readonly string[] = [
  "jha",
  "visitor-sign-in",
  "incident-report",
  "daily-report",
];

/** The closed requirement-item vocabulary (D1 job_daily_requirements.kind, migration 0030). */
export type DailyRequirementKind = "note" | "confirm" | "text" | "form_link";

/** One admin-authored per-job requirement item, as served by
 *  GET /api/fieldops/daily-form/requirements (active items only, seq order, bounded). */
export interface DailyRequirementItem {
  id: number;
  seq: number;
  kind: DailyRequirementKind;
  label: string;
  form_code: string | null; // form_link only: a catalog PARENT family code
}

/** The job's ACTIVE requirement items — rendered inside the daily form's `job_requirements`
 *  section (FormRenderer `requirements` prop). Worker-gated cap.tasks.own + the SAME per-job
 *  ownership scope as the status read (non-admin actors: own placement only, 403 forbidden_job). */
export async function fetchDailyRequirements(jobId: string): Promise<DailyRequirementItem[]> {
  const q = new URLSearchParams({ job_id: jobId });
  const res = await fetch(`/api/fieldops/daily-form/requirements?${q.toString()}`, { credentials: "same-origin" });
  if (!res.ok) return raiseApiError(res);
  const body = (await res.json()) as { job_id: string; items: DailyRequirementItem[] };
  return body.items;
}
