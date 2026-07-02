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
