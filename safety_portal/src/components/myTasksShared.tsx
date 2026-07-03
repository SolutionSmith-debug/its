import type { ReactNode } from "react";

/**
 * R2 — shared local helpers for the My Tasks page and its extracted sections
 * (DailyReportTab / AssignedInspectionsSection / AddCrewSection).
 *
 * Deliberately page-scoped (NOT src/lib): these encode My-Tasks copy + render conventions
 * (loading/error/Retry blocks, per-row inline feedback, the Completed disclosure, Pacific
 * day math for the rollover banner). Promote to src/lib only when a second page needs them.
 */

/** Today's date in America/Los_Angeles as YYYY-MM-DD (en-CA gives ISO ordering). The daily
 *  checklist's `instance_date` is a Pacific business date — comparing against UTC would flip
 *  the "new day" banner up to 8 hours early/late. */
export function pacificToday(): string {
  return new Intl.DateTimeFormat("en-CA", {
    timeZone: "America/Los_Angeles",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(new Date());
}

/** YYYY-MM-DD → localized date. Parsed as LOCAL date parts — `new Date("2026-07-10")` is UTC
 *  midnight and renders the PREVIOUS day in negative-offset timezones. Non-ISO input echoes. */
export function fmtDate(isoDate: string | null | undefined): string {
  if (!isoDate) return "";
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(isoDate);
  if (!m) return isoDate;
  return new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3])).toLocaleDateString();
}

/** Epoch SECONDS (D1 unixepoch — same convention as the Job Tracker's fmtDateTime) → localized date. */
export function fmtEpochDate(epochSeconds: number | null | undefined): string {
  if (!epochSeconds) return "";
  return new Date(epochSeconds * 1000).toLocaleDateString();
}

/** The one roster-link explanation (R1 `linked:false` on /tasks/mine + /checklist/assigned, and the
 *  daily `no_personnel_link` reason) — identical copy everywhere it surfaces. */
export const ROSTER_LINK_COPY =
  "Your account isn't linked to a roster person — ask the office to link you (they can do it from the Personnel page).";

export function errMsg(err: unknown, fallback: string): string {
  return err instanceof Error ? err.message : fallback;
}

/** Distinct loading state (Mandatory B: loading ≠ empty). */
export function SectionLoading({ label }: { label: string }) {
  return (
    <div className="muted" role="status">
      {label}
    </div>
  );
}

/** Load-failure state: human copy + a WORKING Retry (Mandatory B — no dead banners). `what`
 *  disambiguates the Retry button when several sections error at once. */
export function SectionError({ message, onRetry, what }: { message: string; onRetry: () => void; what: string }) {
  return (
    <div className="banner banner--err" role="alert">
      {message}{" "}
      <button type="button" className="btn btn--secondary" aria-label={`Retry ${what}`} onClick={onRetry}>
        Retry
      </button>
    </div>
  );
}

/** Soft warn for "the data on screen loaded, but a refresh failed" — previous data stays up
 *  (Mandatory B: a successful mutation is never reported as failed just because the follow-up
 *  refetch broke). Distinct from SectionError: content keeps rendering below it. */
export function SectionRefreshWarn({ message, onRetry, what }: { message: string; onRetry: () => void; what: string }) {
  return (
    <div className="dash-unavail" role="status">
      {message}{" "}
      <button type="button" className="btn btn--secondary" aria-label={`Retry ${what}`} onClick={onRetry}>
        Retry
      </button>
    </div>
  );
}

export interface RowFeedback {
  ok: boolean;
  text: string;
}

/** Per-row inline feedback pill (Mandatory B: feedback lands NEXT TO the row acted on, not only
 *  in a top banner that can sit off-screen). */
export function InlineRowMsg({ msg }: { msg: RowFeedback }) {
  return (
    <span className={msg.ok ? "dash-pill dash-pill--ok" : "dash-pill dash-pill--danger"} role="status">
      {msg.text}
    </span>
  );
}

/** Completed items collapse under a disclosure; open/in-progress render by default. Renders
 *  nothing at count 0 so an all-open list carries no empty "Completed (0)" chrome. */
export function CompletedDisclosure({ count, children }: { count: number; children: ReactNode }) {
  if (count === 0) return null;
  return (
    <details className="dash-completed">
      <summary className="dash-card__sub" style={{ cursor: "pointer" }}>
        Completed ({count})
      </summary>
      {children}
    </details>
  );
}
