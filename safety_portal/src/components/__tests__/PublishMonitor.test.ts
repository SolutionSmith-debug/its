import { describe, it, expect } from "vitest";
import { fmtTime } from "../PublishMonitor";

// D3 regression: publish_requests timestamps are unix SECONDS (migration 0010 `unixepoch()`).
// The monitor's old fmtTime did `new Date(seconds)`, which Date reads as MILLISECONDS, so a
// 2026 stamp rendered as "1/21/1970". The fix multiplies a numeric value by 1000.
describe("fmtTime (publish monitor timestamps)", () => {
  it("treats a numeric value as unix SECONDS, not milliseconds (the 1970 bug)", () => {
    const secs = 1_781_000_000; // ~mid-2026; new Date(secs) (the old bug) lands in Jan 1970
    const out = fmtTime(secs);
    expect(out).toContain("2026");
    expect(out).not.toContain("1970");
  });

  it("parses a string as an ISO timestamp", () => {
    expect(fmtTime("2026-06-09T05:14:30Z")).toContain("2026");
  });

  it("falls back to the raw value when unparseable", () => {
    expect(fmtTime("not-a-date")).toBe("not-a-date");
  });
});
