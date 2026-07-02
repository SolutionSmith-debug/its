/**
 * R5 — lib halves of the assignment lifecycle: the full-roster cursor loop (fetchFullRoster) and the
 * two new endpoint wrappers (fetchChecklistInstances / cancelChecklistInstance). The worker side is
 * covered by test/fieldops-checklist-lifecycle.test.ts against the real D1.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { PersonnelRow } from "../fieldops_personnel";

vi.mock("../fieldops_personnel", () => ({ fetchPersonnelList: vi.fn() }));

import { fetchPersonnelList } from "../fieldops_personnel";
import {
  cancelChecklistInstance,
  fetchChecklistInstances,
  fetchFullRoster,
  ROSTER_MAX_PAGES,
} from "../fieldops_checklist";

function person(id: number): PersonnelRow {
  return { id, name: `Person ${id}`, trade: "", username: null, current_job: null, current_job_name: null };
}

beforeEach(() => {
  vi.resetAllMocks();
});
afterEach(() => {
  vi.restoreAllMocks();
});

describe("fetchFullRoster — cursor loop", () => {
  it("pages the cursor to exhaustion and concatenates every page in order", async () => {
    vi.mocked(fetchPersonnelList)
      .mockResolvedValueOnce({ personnel: [person(1), person(2)], latest_entries: [], next_cursor: "c1" })
      .mockResolvedValueOnce({ personnel: [person(3)], latest_entries: [], next_cursor: "c2" })
      .mockResolvedValueOnce({ personnel: [person(4)], latest_entries: [], next_cursor: null });
    const roster = await fetchFullRoster();
    expect(roster.map((p) => p.id)).toEqual([1, 2, 3, 4]);
    expect(fetchPersonnelList).toHaveBeenCalledTimes(3);
    // The cursor threads page → page: first call cursorless, then c1, then c2.
    expect(vi.mocked(fetchPersonnelList).mock.calls.map((c) => c[0])).toEqual([undefined, "c1", "c2"]);
  });

  it("is bounded at ROSTER_MAX_PAGES even if the server keeps returning cursors (runaway guard)", async () => {
    vi.mocked(fetchPersonnelList).mockImplementation((cursor) =>
      Promise.resolve({
        personnel: [person(Number(cursor ?? "0") + 1)],
        latest_entries: [],
        next_cursor: String(Number(cursor ?? "0") + 1), // never ends
      }),
    );
    const roster = await fetchFullRoster();
    expect(fetchPersonnelList).toHaveBeenCalledTimes(ROSTER_MAX_PAGES);
    expect(roster).toHaveLength(ROSTER_MAX_PAGES);
  });

  it("a single page with no cursor returns immediately", async () => {
    vi.mocked(fetchPersonnelList).mockResolvedValue({ personnel: [person(1)], latest_entries: [], next_cursor: null });
    expect(await fetchFullRoster()).toHaveLength(1);
    expect(fetchPersonnelList).toHaveBeenCalledTimes(1);
  });
});

describe("fetchChecklistInstances / cancelChecklistInstance — endpoint wrappers", () => {
  it("GETs /checklist/instances with the status filter (default 'open')", async () => {
    // A fresh Response per call — a Response body is single-use.
    const spy = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation(() =>
        Promise.resolve(new Response(JSON.stringify({ instances: [], status_filter: "open" }), { status: 200 })),
      );
    await fetchChecklistInstances();
    expect(spy).toHaveBeenCalledWith("/api/fieldops/checklist/instances?status=open", { credentials: "same-origin" });
    await fetchChecklistInstances("all");
    expect(spy).toHaveBeenCalledWith("/api/fieldops/checklist/instances?status=all", { credentials: "same-origin" });
  });

  it("POSTs /checklist/instance/:id/cancel and surfaces a worker error as a thrown ApiError", async () => {
    const spy = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(new Response(JSON.stringify({ ok: true, id: 41 }), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ error: "not_found" }), { status: 404 }));
    const res = await cancelChecklistInstance(41);
    expect(res.ok).toBe(true);
    expect(spy.mock.calls[0][0]).toBe("/api/fieldops/checklist/instance/41/cancel");
    expect((spy.mock.calls[0][1] as RequestInit).method).toBe("POST");
    // Error half: the 404 becomes an ApiError with HUMAN copy (errorCopy map), never a silent pass.
    await expect(cancelChecklistInstance(999)).rejects.toThrow(/no longer exists/i);
  });
});
