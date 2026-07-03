/**
 * Day-phase mapping for the daily SOP's chronological day-rail (design-refinement
 * pass, 2026-07) — PRESENTATIONAL ONLY.
 *
 * The daily form's guidance sections already carry the SOP's own section wording
 * verbatim in their headings ("7:30 AM — Arrive On Site…", "A. Morning Kickoff…",
 * "D. Throughout the Day…", "END OF DAY…"). This pure function maps that heading
 * text to the time-of-day eyebrow the rail renders above the five PHASE-OPENING
 * sections — and null for every section that merely continues the current phase
 * (those get the rail, no eyebrow) and for every non-SOP heading.
 *
 * Deliberately NOT a definition change and NOT fill state: the mapping is derived
 * at render time from the existing headings, consumed by FormRenderer only when the
 * host passes the optional `dayRail` prop (the Daily tab). The five openers are
 * worded identically across daily-report v2–v5 (the SOP's own phase markers), so
 * prefix-matching the lettered/timed openers is stable; an unrecognized heading
 * simply renders without an eyebrow — the rail never lies and never throws.
 */

/** The five day-phase eyebrow labels, in SOP day order. */
export type DayPhase =
  | "7:30 AM"
  | "MORNING KICKOFF"
  | "THROUGH THE DAY"
  | "CHECK-INS"
  | "END OF DAY";

/** Heading text → the day-phase eyebrow it opens, or null (continuation / non-phase). */
export function dayPhaseFor(heading: string): DayPhase | null {
  const h = heading.trim().toUpperCase();
  if (h.startsWith("7:30 AM")) return "7:30 AM";
  if (h.startsWith("A. MORNING KICKOFF")) return "MORNING KICKOFF";
  if (h.startsWith("D. THROUGHOUT THE DAY")) return "THROUGH THE DAY";
  if (h.startsWith("E. CHECK-INS")) return "CHECK-INS";
  if (h.startsWith("END OF DAY")) return "END OF DAY";
  return null;
}
