// SOP daily form (slice D2) — the Daily tab's filed-status read client.
// Same-origin fetch with the session cookie (no auth header). The Worker re-gates on cap.tasks.own;
// caps here drive UI affordances only. Send-free (D1 read only).
//
// (R1) Errors: throws ApiError (src/lib/errorCopy.ts) — err.message is HUMAN copy, err.code the raw
// wire code (e.g. 'invalid_date', 'not_found').
import { raiseApiError } from "./errorCopy";
import type { DailyFormStatus, DailyRequirementItem, DailyRequirementsResponse } from "../../worker/wire-types";

// Wire shapes — SINGLE-SOURCED in worker/wire-types.ts (the Worker types its c.json payloads with
// the same definitions, so a shape drift fails the typecheck on both sides); re-exported here so
// existing importers keep their path.
export type { DailyFormStatus, DailyRequirementItem, DailyRequirementKind, FiledEntry } from "../../worker/wire-types";

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

// The parent-form families the status endpoint reports — SINGLE-SOURCED with the Worker in
// src/shared/daily_families.ts (which carries the full doc); re-exported here so existing
// importers (FormRenderer) keep their path.
export { DAILY_STATUS_FAMILIES } from "../shared/daily_families";

/** The job's ACTIVE requirement items — rendered inside the daily form's `job_requirements`
 *  section (FormRenderer `requirements` prop). Worker-gated cap.tasks.own + the SAME per-job
 *  ownership scope as the status read (non-admin actors: own placement only, 403 forbidden_job). */
export async function fetchDailyRequirements(jobId: string): Promise<DailyRequirementItem[]> {
  const q = new URLSearchParams({ job_id: jobId });
  const res = await fetch(`/api/fieldops/daily-form/requirements?${q.toString()}`, { credentials: "same-origin" });
  if (!res.ok) return raiseApiError(res);
  const body = (await res.json()) as DailyRequirementsResponse;
  return body.items;
}
