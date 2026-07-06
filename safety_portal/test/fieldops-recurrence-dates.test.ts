import { describe, it, expect } from "vitest";
import { enumerateCadenceDates, pacificDateString } from "../worker/fieldops_recurrence";

// ─────────────────────────────────────────────────────────────────────────────
// Recurring checklists (#16) — the PURE cadence date-math (no D1). These are the
// correctness core: idempotent enumeration + calendar-day stepping + the catch-up
// bound. Kept in their own suite so a math regression is obvious and isolated.
// ─────────────────────────────────────────────────────────────────────────────

describe("enumerateCadenceDates", () => {
  it("daily: every date anchor..today (inclusive), no watermark", () => {
    expect(enumerateCadenceDates("daily", "2026-07-01", "2026-07-05", null).dates).toEqual([
      "2026-07-01",
      "2026-07-02",
      "2026-07-03",
      "2026-07-04",
      "2026-07-05",
    ]);
  });

  it("daily: watermark excludes dates at/before it (strictly after)", () => {
    expect(enumerateCadenceDates("daily", "2026-07-01", "2026-07-05", "2026-07-03").dates).toEqual([
      "2026-07-04",
      "2026-07-05",
    ]);
  });

  it("weekly: anchor + 7·k (keeps the anchor's weekday)", () => {
    expect(enumerateCadenceDates("weekly", "2026-07-01", "2026-07-20", null).dates).toEqual([
      "2026-07-01",
      "2026-07-08",
      "2026-07-15",
    ]);
  });

  it("biweekly: anchor + 14·k", () => {
    expect(enumerateCadenceDates("biweekly", "2026-07-01", "2026-08-01", null).dates).toEqual([
      "2026-07-01",
      "2026-07-15",
      "2026-07-29",
    ]);
  });

  it("monthly: keeps the anchor day-of-month, CLAMPED to short months (Jan-31 → Feb-28 → Mar-31)", () => {
    // Wide lookback so the default 45-day catch-up cap doesn't clip the Jan/Feb occurrences (this
    // test isolates the month-length clamping, not the cap).
    expect(enumerateCadenceDates("monthly", "2026-01-31", "2026-04-15", null, 400).dates).toEqual([
      "2026-01-31",
      "2026-02-28",
      "2026-03-31",
    ]);
  });

  it("returns nothing for a FUTURE anchor (today < anchor)", () => {
    expect(enumerateCadenceDates("daily", "2026-08-01", "2026-07-05", null).dates).toEqual([]);
  });

  it("bounds catch-up to maxLookbackDays and flags capped (older dates dropped, today still included)", () => {
    const r = enumerateCadenceDates("daily", "2026-01-01", "2026-07-05", null, 45);
    expect(r.capped).toBe(true);
    expect(r.dates).toHaveLength(45);
    expect(r.dates[r.dates.length - 1]).toBe("2026-07-05"); // today never dropped
    expect(r.dates[0]).toBe("2026-05-22"); // 45 dates inclusive ending 07-05 → first = today − 44
  });

  it("not capped when the window fits inside maxLookbackDays", () => {
    const r = enumerateCadenceDates("daily", "2026-07-01", "2026-07-05", null, 45);
    expect(r.capped).toBe(false);
    expect(r.dates).toHaveLength(5);
  });
});

describe("pacificDateString (DST-correct Pacific calendar date)", () => {
  it("PDT (summer, UTC−7): 09:00 UTC is same-day", () => {
    expect(pacificDateString(Date.UTC(2026, 6, 5, 16, 0, 0))).toBe("2026-07-05");
  });
  it("PDT: an early-UTC instant is still the PREVIOUS Pacific day", () => {
    expect(pacificDateString(Date.UTC(2026, 6, 5, 6, 0, 0))).toBe("2026-07-04"); // 23:00 PDT Jul-04
  });
  it("PST (winter, UTC−8): the offset shifts correctly", () => {
    expect(pacificDateString(Date.UTC(2026, 0, 15, 7, 0, 0))).toBe("2026-01-14"); // 23:00 PST Jan-14
  });
});
