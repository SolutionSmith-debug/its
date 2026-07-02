/**
 * DailyChecklistSection (R2 extraction) — section-level detail tests: never-silent load states,
 * reason-coded empty states, day-rollover lockout, completed collapse, per-row busy, count
 * validation, onLoaded reporting. The page-level integration (tabs, auto-switch, refresh, the
 * mutation/refetch try-split through the page) lives in pages/__tests__/FieldOpsMyTasks.test.tsx.
 */
import { cleanup, fireEvent, render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../lib/fieldops_checklist", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../lib/fieldops_checklist")>();
  return { ...actual, fetchMyChecklist: vi.fn(), completeChecklistItem: vi.fn(), uncompleteChecklistItem: vi.fn(), recordCountItem: vi.fn(), fetchRollupDraft: vi.fn() };
});

import * as checklist from "../../lib/fieldops_checklist";
import { ApiError } from "../../lib/errorCopy";
import { pacificToday } from "../myTasksShared";
import { DailyChecklistSection } from "../DailyChecklistSection";

afterEach(cleanup);
beforeEach(() => {
  vi.resetAllMocks();
});

const TODAY = pacificToday();
const INSTANCE = { id: 7, job_id: "JOB-A", project_name: "Alpha", instance_date: TODAY, status: "open" as const, rolled_up_submission_uuid: null, rolled_up_by: null };
const ITEM_A: checklist.ChecklistItemState = { id: 12, source_item_id: 2, item_type: "manual_attest", label: "Record crew progress", form_code: null, target_count: null, status: "open", note: null, photo_ref: null, completed_by: null, completed_at: null, value_num: null, filed_by: null };
const ITEM_B: checklist.ChecklistItemState = { id: 13, source_item_id: 3, item_type: "manual_attest", label: "Walk the perimeter", form_code: null, target_count: null, status: "open", note: null, photo_ref: null, completed_by: null, completed_at: null, value_num: null, filed_by: null };
const COUNT_ITEM: checklist.ChecklistItemState = { id: 20, source_item_id: 5, item_type: "count", label: "Log deliveries", form_code: null, target_count: 3, status: "open", note: null, photo_ref: null, completed_by: null, completed_at: null, value_num: null, filed_by: null };

describe("DailyChecklistSection — load states (Mandatory B)", () => {
  it("shows a distinct loading state while the fetch is in flight", () => {
    vi.mocked(checklist.fetchMyChecklist).mockReturnValue(new Promise(() => {}));
    const { container } = render(<DailyChecklistSection />);
    expect(container.textContent ?? "").toContain("Loading today's checklist…");
    expect(container.querySelector('[aria-label="Daily checklist status"]')).toBeNull();
  });

  it("a load failure shows the human error + a working Retry (no false empty)", async () => {
    vi.mocked(checklist.fetchMyChecklist)
      .mockRejectedValueOnce(new ApiError(null, 500))
      .mockResolvedValueOnce({ instance: INSTANCE, items: [ITEM_A], reason: null });
    const { container, getByLabelText } = render(<DailyChecklistSection />);
    await waitFor(() => expect(container.textContent ?? "").toContain("Something went wrong on the server"));
    // Error and empty are mutually exclusive: no reason-copy card while errored.
    expect(container.querySelector('[aria-label="Daily checklist status"]')).toBeNull();
    fireEvent.click(getByLabelText("Retry loading today's checklist"));
    await waitFor(() => expect(container.querySelector('[aria-label="Today\'s checklist"]')).not.toBeNull());
    expect(checklist.fetchMyChecklist).toHaveBeenCalledTimes(2);
  });

  it("reports each load up via onLoaded (drives the parent auto-switch)", async () => {
    vi.mocked(checklist.fetchMyChecklist).mockResolvedValue({ instance: INSTANCE, items: [ITEM_A], reason: null });
    const onLoaded = vi.fn();
    render(<DailyChecklistSection onLoaded={onLoaded} />);
    await waitFor(() => expect(onLoaded).toHaveBeenCalledWith({ instance: INSTANCE, reason: null }));
  });
});

describe("DailyChecklistSection — reason-coded empty states (Mandatory A)", () => {
  const cases: Array<[checklist.DailyEmptyReason | null, string]> = [
    ["not_manager", "crew-lead managers"],
    ["no_personnel_link", "isn't linked to a roster person"],
    ["not_placed", "not placed on a job yet"],
    [null, "no daily checklist for you today"],
  ];
  for (const [reason, copy] of cases) {
    it(`explains reason=${String(reason)}`, async () => {
      vi.mocked(checklist.fetchMyChecklist).mockResolvedValue({ instance: null, items: [], reason });
      const { container } = render(<DailyChecklistSection />);
      await waitFor(() => expect(container.querySelector('[aria-label="Daily checklist status"]')).not.toBeNull());
      expect(container.textContent ?? "").toContain(copy);
      expect(container.querySelector('[aria-label="Today\'s checklist"]')).toBeNull();
    });
  }
});

describe("DailyChecklistSection — day rollover", () => {
  it("a stale instance_date shows the new-day banner and disables completion controls", async () => {
    vi.mocked(checklist.fetchMyChecklist).mockResolvedValue({
      instance: { ...INSTANCE, instance_date: "2020-01-01" },
      items: [ITEM_A],
      reason: null,
    });
    const { container, getByLabelText } = render(<DailyChecklistSection />);
    await waitFor(() => expect(container.textContent ?? "").toContain("A new day has started"));
    expect((getByLabelText("Complete item 12") as HTMLButtonElement).disabled).toBe(true);
    // The banner's Refresh control actually refetches.
    fireEvent.click(getByLabelText("Refresh today's checklist"));
    await waitFor(() => expect(checklist.fetchMyChecklist).toHaveBeenCalledTimes(2));
  });

  it("today's instance keeps controls live (no banner)", async () => {
    vi.mocked(checklist.fetchMyChecklist).mockResolvedValue({ instance: INSTANCE, items: [ITEM_A], reason: null });
    const { container, getByLabelText } = render(<DailyChecklistSection />);
    await waitFor(() => expect(container.querySelector('[aria-label="Today\'s checklist"]')).not.toBeNull());
    expect(container.textContent ?? "").not.toContain("A new day has started");
    expect((getByLabelText("Complete item 12") as HTMLButtonElement).disabled).toBe(false);
  });
});

describe("DailyChecklistSection — rows", () => {
  it("completed items collapse under 'Completed (N)'; open items render by default", async () => {
    vi.mocked(checklist.fetchMyChecklist).mockResolvedValue({
      instance: INSTANCE,
      items: [ITEM_A, { ...ITEM_B, status: "done", completed_by: "mgr.mo", completed_at: 1 }],
      reason: null,
    });
    const { container } = render(<DailyChecklistSection />);
    await waitFor(() => expect(container.textContent ?? "").toContain("Completed (1)"));
    const details = container.querySelector("details.dash-completed")!;
    expect(details.hasAttribute("open")).toBe(false);
    expect(details.textContent ?? "").toContain("Walk the perimeter");
    // The open item renders outside the disclosure.
    const openList = container.querySelector("section > ul.dash-tasklist")!;
    expect(openList.textContent ?? "").toContain("Record crew progress");
  });

  it("per-row busy: an in-flight completion disables only that row", async () => {
    vi.mocked(checklist.fetchMyChecklist).mockResolvedValue({ instance: INSTANCE, items: [ITEM_A, ITEM_B], reason: null });
    vi.mocked(checklist.completeChecklistItem).mockReturnValue(new Promise(() => {})); // never settles
    const { getByLabelText } = render(<DailyChecklistSection />);
    const btn = await waitFor(() => getByLabelText("Complete item 12"));
    fireEvent.click(btn);
    await waitFor(() => expect((getByLabelText("Complete item 12") as HTMLButtonElement).disabled).toBe(true));
    expect((getByLabelText("Complete item 13") as HTMLButtonElement).disabled).toBe(false);
  });

  it("a failed completion shows the human error inline at the row (not only a top banner)", async () => {
    vi.mocked(checklist.fetchMyChecklist).mockResolvedValue({ instance: INSTANCE, items: [ITEM_A], reason: null });
    vi.mocked(checklist.completeChecklistItem).mockRejectedValue(new ApiError("no_instance", 404));
    const { getByLabelText, container } = render(<DailyChecklistSection />);
    fireEvent.click(await waitFor(() => getByLabelText("Complete item 12")));
    await waitFor(() => expect(container.textContent ?? "").toContain("There's no daily checklist for you today."));
    const row = container.querySelector("ul.dash-tasklist li")!;
    expect(row.textContent ?? "").toContain("There's no daily checklist for you today.");
  });

  it("a non-numeric count shows 'Enter a number.' at the row without calling the API", async () => {
    vi.mocked(checklist.fetchMyChecklist).mockResolvedValue({ instance: INSTANCE, items: [COUNT_ITEM], reason: null });
    const { getByLabelText, container } = render(<DailyChecklistSection />);
    const record = await waitFor(() => getByLabelText("Record item 20"));
    fireEvent.click(record); // empty input → NaN
    await waitFor(() => expect(container.textContent ?? "").toContain("Enter a number."));
    expect(checklist.recordCountItem).not.toHaveBeenCalled();
  });
});
