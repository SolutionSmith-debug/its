/**
 * "My Tasks" page (P4 S1 + R2 two-tab restructure) + its HomePage card gate.
 * Mirrors FieldOpsPersonnel.test.tsx: vi.mock the lib + useAuth, render, query.
 *
 * R2: the page is two tabs (Assigned tasks / Daily checklist), never-silent (Mandatory B — every
 * fetch has loading / error+Retry / empty, mutually exclusive), reason-coded daily empty states
 * (Mandatory A), per-row busy + inline feedback, contextual Start/Done/Reopen buttons, completed
 * collapse, Refresh + focus refetch, and the mutation/refetch try-split. Section-level detail
 * tests live beside the extracted components (src/components/__tests__/).
 */
import { cleanup, fireEvent, render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../lib/fieldops_tasks", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../lib/fieldops_tasks")>();
  return { ...actual, fetchMyTasks: vi.fn(), setTaskStatus: vi.fn() };
});
vi.mock("../../lib/fieldops_checklist", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../lib/fieldops_checklist")>();
  return { ...actual, fetchMyChecklist: vi.fn(), completeChecklistItem: vi.fn(), uncompleteChecklistItem: vi.fn(), recordCountItem: vi.fn(), fetchRollupDraft: vi.fn(), fetchAssignedInspections: vi.fn() };
});
vi.mock("../../lib/fieldops_personnel", () => ({ createCrew: vi.fn(), fetchMyCrew: vi.fn() }));
vi.mock("../../lib/auth", () => ({ useAuth: vi.fn() }));

import * as api from "../../lib/fieldops_tasks";
import * as personnel from "../../lib/fieldops_personnel";
import * as checklist from "../../lib/fieldops_checklist";
import { ApiError } from "../../lib/errorCopy";
import { pacificToday } from "../../components/myTasksShared";
import { FieldOpsMyTasks } from "../FieldOpsMyTasks";
import { HomePage } from "../HomePage";
import { useAuth } from "../../lib/auth";

function authWith(capabilities: string[]) {
  return {
    user: { username: "sam", role: "submitter" as const, capabilities },
    loading: false,
    login: vi.fn(async () => {}),
    logout: vi.fn(async () => {}),
  };
}

afterEach(cleanup);
beforeEach(() => {
  vi.resetAllMocks();
  vi.mocked(useAuth).mockReturnValue(authWith(["cap.tasks.own"]));
  // Default: no daily checklist (not a placed manager) → the Daily tab shows the reason copy.
  vi.mocked(checklist.fetchMyChecklist).mockResolvedValue({ instance: null, items: [], reason: "not_manager" });
  // Default: no assigned inspections → the S6 section renders nothing.
  vi.mocked(checklist.fetchAssignedInspections).mockResolvedValue({ inspections: [], linked: true });
  // Default: empty crew list (AddCrewSection's auxiliary fetch).
  vi.mocked(personnel.fetchMyCrew).mockResolvedValue([]);
});

// The daily instance must carry TODAY's Pacific date — a past date is the day-rollover state,
// which deliberately disables every completion control (see the stale-day tests).
const TODAY = pacificToday();
const INSTANCE = { id: 7, job_id: "JOB-A", project_name: "Alpha", instance_date: TODAY, status: "open" as const, rolled_up_submission_uuid: null, rolled_up_by: null };
const CHECKLIST_ITEMS: checklist.ChecklistItemState[] = [
  { id: 11, source_item_id: 1, item_type: "form_linked", label: "File the Daily Field Report", form_code: "daily-report", target_count: null, status: "open", note: null, photo_ref: null, completed_by: null, completed_at: null, value_num: null, filed_by: null },
  { id: 12, source_item_id: 2, item_type: "manual_attest", label: "Record crew progress", form_code: null, target_count: null, status: "open", note: null, photo_ref: null, completed_by: null, completed_at: null, value_num: null, filed_by: null },
];

const TASKS: api.MyTask[] = [
  { id: 1, job_id: "JOB-A", project_name: "Alpha", description: "Dig footings", status: "open", created_at: 100, assigned_by: "boss.bob" },
  { id: 2, job_id: "JOB-A", project_name: "Alpha", description: "Pour slab", status: "in_progress", created_at: 90, assigned_by: null },
  { id: 3, job_id: "JOB-B", project_name: "Bravo", description: "Frame wall", status: "open", created_at: 80, assigned_by: null },
];

function tasksOk(tasks: api.MyTask[] = TASKS) {
  vi.mocked(api.fetchMyTasks).mockResolvedValue({ tasks, linked: true });
}

describe("FieldOpsMyTasks — tabs", () => {
  it("renders both tabs, Assigned tasks selected by default", async () => {
    tasksOk();
    const { getByRole, queryByRole } = render(<FieldOpsMyTasks onBack={() => {}} />);
    await waitFor(() => expect(getByRole("tab", { name: "Assigned tasks" })).not.toBeNull());
    expect(getByRole("tab", { name: "Assigned tasks" }).getAttribute("aria-selected")).toBe("true");
    expect(getByRole("tab", { name: "Daily checklist" }).getAttribute("aria-selected")).toBe("false");
    // Only the assigned panel is visible (the daily panel is mounted but hidden).
    expect(getByRole("tabpanel", { name: "Assigned tasks" })).not.toBeNull();
    expect(queryByRole("tabpanel", { name: "Daily checklist" })).toBeNull();
  });

  it("switches to the Daily checklist tab on click", async () => {
    tasksOk();
    const { getByRole, queryByRole } = render(<FieldOpsMyTasks onBack={() => {}} />);
    await waitFor(() => expect(getByRole("tab", { name: "Daily checklist" })).not.toBeNull());
    fireEvent.click(getByRole("tab", { name: "Daily checklist" }));
    expect(getByRole("tab", { name: "Daily checklist" }).getAttribute("aria-selected")).toBe("true");
    expect(getByRole("tabpanel", { name: "Daily checklist" })).not.toBeNull();
    expect(queryByRole("tabpanel", { name: "Assigned tasks" })).toBeNull();
  });

  it("auto-switches to Daily checklist when the actor has a daily instance and no open tasks", async () => {
    tasksOk([]); // no open one-off tasks
    vi.mocked(checklist.fetchMyChecklist).mockResolvedValue({ instance: INSTANCE, items: CHECKLIST_ITEMS, reason: null });
    const { getByRole } = render(<FieldOpsMyTasks onBack={() => {}} />);
    await waitFor(() => expect(getByRole("tab", { name: "Daily checklist" }).getAttribute("aria-selected")).toBe("true"));
    expect(getByRole("tabpanel", { name: "Daily checklist" })).not.toBeNull();
  });

  it("does NOT auto-switch while open tasks exist", async () => {
    tasksOk(); // has open tasks
    vi.mocked(checklist.fetchMyChecklist).mockResolvedValue({ instance: INSTANCE, items: CHECKLIST_ITEMS, reason: null });
    const { getByRole, container } = render(<FieldOpsMyTasks onBack={() => {}} />);
    await waitFor(() => expect(container.textContent ?? "").toContain("Dig footings"));
    expect(getByRole("tab", { name: "Assigned tasks" }).getAttribute("aria-selected")).toBe("true");
  });
});

describe("FieldOpsMyTasks — tasks list (Assigned tasks tab)", () => {
  it("renders my tasks grouped by job (project name), open-first with humanized status pills", async () => {
    tasksOk();
    const { container, getByRole } = render(<FieldOpsMyTasks onBack={() => {}} />);
    const panel = await waitFor(() => getByRole("tabpanel", { name: "Assigned tasks" }));
    // Two job groups (Alpha with 2 tasks, Bravo with 1) inside the tasks grid.
    await waitFor(() => expect(panel.querySelectorAll(".dash-grid .dash-section")).toHaveLength(2));
    const headings = Array.from(panel.querySelectorAll(".dash-detail__h2")).map((h) => h.textContent ?? "");
    expect(headings.some((h) => h.includes("Alpha") && h.includes("JOB-A"))).toBe(true);
    expect(headings.some((h) => h.includes("Bravo") && h.includes("JOB-B"))).toBe(true);
    const txt = panel.textContent ?? "";
    expect(txt).toContain("Dig footings");
    expect(txt).toContain("Pour slab");
    expect(txt).toContain("Frame wall");
    // Humanized status vocabulary (labels.ts) — raw snake_case never reaches the UI.
    expect(txt).toContain("In progress");
    expect(txt).not.toContain("in_progress");
    // Context line: assigner + created date.
    expect(txt).toContain("Assigned by boss.bob");
    // The raw status dropdown is gone (contextual buttons instead).
    expect(container.querySelector("select")).toBeNull();
    // The Alpha group holds two task rows.
    const alphaSection = Array.from(panel.querySelectorAll(".dash-grid .dash-section")).find((s) => (s.textContent ?? "").includes("Alpha"))!;
    expect(alphaSection.querySelectorAll(".dash-tasklist li")).toHaveLength(2);
  });

  it("contextual actions per status: open → Start + Done; in_progress → Done; done → Reopen", async () => {
    tasksOk([
      { ...TASKS[0], id: 1, status: "open" },
      { ...TASKS[1], id: 2, status: "in_progress" },
      { ...TASKS[2], id: 3, status: "done" },
    ]);
    const { getByLabelText, queryByLabelText } = render(<FieldOpsMyTasks onBack={() => {}} />);
    await waitFor(() => expect(getByLabelText("Start task 1")).not.toBeNull());
    expect(getByLabelText("Mark task 1 done")).not.toBeNull();
    expect(getByLabelText("Mark task 2 done")).not.toBeNull();
    expect(queryByLabelText("Start task 2")).toBeNull();
    expect(getByLabelText("Reopen task 3")).not.toBeNull();
    expect(queryByLabelText("Mark task 3 done")).toBeNull();
  });

  it("a status change fires setTaskStatus(taskId, status) and shows inline row feedback", async () => {
    tasksOk();
    vi.mocked(api.setTaskStatus).mockResolvedValue(undefined);
    const { getByLabelText, container } = render(<FieldOpsMyTasks onBack={() => {}} />);
    const btn = await waitFor(() => getByLabelText("Mark task 1 done"));
    fireEvent.click(btn);
    await waitFor(() => expect(api.setTaskStatus).toHaveBeenCalledWith(1, "done"));
    await waitFor(() => expect(container.textContent ?? "").toContain("Updated."));
  });

  it("a failed status change reverts the optimistic update and shows the human error inline", async () => {
    tasksOk();
    vi.mocked(api.setTaskStatus).mockRejectedValue(new ApiError("forbidden_task", 403));
    const { getByLabelText, container } = render(<FieldOpsMyTasks onBack={() => {}} />);
    const btn = await waitFor(() => getByLabelText("Mark task 1 done"));
    fireEvent.click(btn);
    // R1 human copy from errorCopy.ts surfaces; the row reverts to open (Done button back).
    await waitFor(() => expect(container.textContent ?? "").toContain("You can only update tasks assigned to you."));
    expect((getByLabelText("Mark task 1 done") as HTMLButtonElement).disabled).toBe(false);
  });

  it("per-row busy: an in-flight change disables only that row's controls", async () => {
    tasksOk();
    vi.mocked(api.setTaskStatus).mockReturnValue(new Promise(() => {})); // never settles
    const { getByLabelText } = render(<FieldOpsMyTasks onBack={() => {}} />);
    const btn = await waitFor(() => getByLabelText("Mark task 1 done"));
    fireEvent.click(btn);
    // Task 1 optimistically flips to done → its Reopen control renders, busy-disabled.
    await waitFor(() => expect((getByLabelText("Reopen task 1") as HTMLButtonElement).disabled).toBe(true));
    // Task 2's controls stay live.
    expect((getByLabelText("Mark task 2 done") as HTMLButtonElement).disabled).toBe(false);
  });

  it("completed tasks collapse under a 'Completed (N)' disclosure per job group", async () => {
    tasksOk([
      { ...TASKS[0], id: 1, status: "open" },
      { ...TASKS[1], id: 2, status: "done", description: "Pour slab" },
    ]);
    const { getByRole } = render(<FieldOpsMyTasks onBack={() => {}} />);
    const panel = await waitFor(() => getByRole("tabpanel", { name: "Assigned tasks" }));
    await waitFor(() => expect(panel.textContent ?? "").toContain("Completed (1)"));
    const details = panel.querySelector("details.dash-completed")!;
    expect(details).not.toBeNull();
    expect(details.hasAttribute("open")).toBe(false); // collapsed by default
    expect(details.textContent ?? "").toContain("Pour slab");
    // The open task renders OUTSIDE the disclosure.
    const openList = panel.querySelector(".dash-grid .dash-section > .dash-tasklist")!;
    expect(openList.textContent ?? "").toContain("Dig footings");
  });

  it("shows an empty state for a linked user with no assigned tasks", async () => {
    tasksOk([]);
    const { getByRole } = render(<FieldOpsMyTasks onBack={() => {}} />);
    const panel = await waitFor(() => getByRole("tabpanel", { name: "Assigned tasks" }));
    await waitFor(() => expect(panel.querySelector(".dash-unavail")).not.toBeNull());
    expect(panel.textContent ?? "").toContain("No tasks are assigned to you");
    expect(panel.querySelector(".dash-tasklist")).toBeNull();
  });

  it("linked:false explains the roster-link gap instead of a bare 'no tasks'", async () => {
    vi.mocked(api.fetchMyTasks).mockResolvedValue({ tasks: [], linked: false });
    const { getByRole } = render(<FieldOpsMyTasks onBack={() => {}} />);
    const panel = await waitFor(() => getByRole("tabpanel", { name: "Assigned tasks" }));
    await waitFor(() => expect(panel.textContent ?? "").toContain("isn't linked to a roster person"));
    expect(panel.textContent ?? "").not.toContain("No tasks are assigned to you");
  });

  it("shows a loading state distinct from empty while tasks are in flight", async () => {
    vi.mocked(api.fetchMyTasks).mockReturnValue(new Promise(() => {})); // never settles
    const { getByRole } = render(<FieldOpsMyTasks onBack={() => {}} />);
    const panel = await waitFor(() => getByRole("tabpanel", { name: "Assigned tasks" }));
    expect(panel.textContent ?? "").toContain("Loading your tasks…");
    expect(panel.textContent ?? "").not.toContain("No tasks are assigned to you");
  });

  it("a tasks fetch failure shows the human error + a working Retry (never a false empty)", async () => {
    vi.mocked(api.fetchMyTasks)
      .mockRejectedValueOnce(new ApiError(null, 500))
      .mockResolvedValueOnce({ tasks: TASKS, linked: true });
    const { getByRole, getByLabelText } = render(<FieldOpsMyTasks onBack={() => {}} />);
    const panel = await waitFor(() => getByRole("tabpanel", { name: "Assigned tasks" }));
    await waitFor(() => expect(panel.textContent ?? "").toContain("Something went wrong on the server"));
    // Error and empty are mutually exclusive.
    expect(panel.textContent ?? "").not.toContain("No tasks are assigned to you");
    fireEvent.click(getByLabelText("Retry loading your tasks"));
    await waitFor(() => expect(panel.textContent ?? "").toContain("Dig footings"));
    expect(api.fetchMyTasks).toHaveBeenCalledTimes(2);
  });
});

describe("FieldOpsMyTasks — Refresh + wake refetch", () => {
  it("the header Refresh control refetches the tasks AND every section", async () => {
    tasksOk();
    const { getByLabelText, container } = render(<FieldOpsMyTasks onBack={() => {}} />);
    await waitFor(() => expect(container.textContent ?? "").toContain("Dig footings"));
    expect(api.fetchMyTasks).toHaveBeenCalledTimes(1);
    expect(checklist.fetchMyChecklist).toHaveBeenCalledTimes(1);
    expect(checklist.fetchAssignedInspections).toHaveBeenCalledTimes(1);
    fireEvent.click(getByLabelText("Refresh"));
    await waitFor(() => expect(api.fetchMyTasks).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(checklist.fetchMyChecklist).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(checklist.fetchAssignedInspections).toHaveBeenCalledTimes(2));
  });

  it("refetches when the window regains focus (overnight-tab recovery)", async () => {
    tasksOk();
    const { container } = render(<FieldOpsMyTasks onBack={() => {}} />);
    await waitFor(() => expect(container.textContent ?? "").toContain("Dig footings"));
    expect(api.fetchMyTasks).toHaveBeenCalledTimes(1);
    fireEvent(window, new Event("focus"));
    await waitFor(() => expect(api.fetchMyTasks).toHaveBeenCalledTimes(2));
  });
});

describe("FieldOpsMyTasks — S3 daily checklist tab", () => {
  it("renders the Today's checklist section for a placed manager (instance present)", async () => {
    tasksOk([]);
    vi.mocked(checklist.fetchMyChecklist).mockResolvedValue({ instance: INSTANCE, items: CHECKLIST_ITEMS, reason: null });
    const { container, getByLabelText } = render(<FieldOpsMyTasks onBack={() => {}} />);
    await waitFor(() => expect(container.querySelector('[aria-label="Today\'s checklist"]')).not.toBeNull());
    const txt = container.textContent ?? "";
    expect(txt).toContain("Record crew progress");
    expect(txt).toContain("File the Daily Field Report");
    // manual_attest gets a complete control; form_linked does not (S4).
    expect(getByLabelText("Complete item 12")).not.toBeNull();
    expect(() => getByLabelText("Complete item 11")).toThrow();
  });

  it("explains the Daily tab instead of a blank when instance is null (reason codes, Mandatory A)", async () => {
    tasksOk([]);
    const cases: Array<[checklist.DailyEmptyReason, string]> = [
      ["not_manager", "crew-lead managers"],
      ["no_personnel_link", "isn't linked to a roster person"],
      ["not_placed", "not placed on a job yet"],
    ];
    for (const [reason, copy] of cases) {
      vi.mocked(checklist.fetchMyChecklist).mockResolvedValue({ instance: null, items: [], reason });
      const { container, unmount } = render(<FieldOpsMyTasks onBack={() => {}} />);
      await waitFor(() => expect(container.querySelector('[aria-label="Daily checklist status"]')).not.toBeNull());
      expect(container.textContent ?? "").toContain(copy);
      expect(container.querySelector('[aria-label="Today\'s checklist"]')).toBeNull();
      unmount();
    }
  });

  it("completing a manual_attest item fires completeChecklistItem", async () => {
    tasksOk([]);
    vi.mocked(checklist.fetchMyChecklist).mockResolvedValue({ instance: INSTANCE, items: CHECKLIST_ITEMS, reason: null });
    vi.mocked(checklist.completeChecklistItem).mockResolvedValue({ ok: true, id: 12, status: "done", instance_status: "open" });
    const { getByLabelText } = render(<FieldOpsMyTasks onBack={() => {}} />);
    const btn = await waitFor(() => getByLabelText("Complete item 12"));
    fireEvent.click(btn);
    await waitFor(() => expect(checklist.completeChecklistItem).toHaveBeenCalledWith(12, undefined));
  });

  it("mutation success + refetch failure: shows success, keeps the locally-applied data, soft-warns", async () => {
    tasksOk([]);
    vi.mocked(checklist.fetchMyChecklist)
      .mockResolvedValueOnce({ instance: INSTANCE, items: CHECKLIST_ITEMS, reason: null })
      .mockRejectedValue(new ApiError(null, 500)); // every refetch fails
    vi.mocked(checklist.completeChecklistItem).mockResolvedValue({ ok: true, id: 12, status: "done", instance_status: "open" });
    const { getByLabelText, container } = render(<FieldOpsMyTasks onBack={() => {}} />);
    const btn = await waitFor(() => getByLabelText("Complete item 12"));
    fireEvent.click(btn);
    // NEVER "Update failed." for a write that landed: success feedback + soft warn instead.
    await waitFor(() => expect(container.textContent ?? "").toContain("Item updated."));
    await waitFor(() => expect(container.textContent ?? "").toContain("Saved — but the checklist couldn't refresh"));
    expect(container.textContent ?? "").not.toContain("Update failed.");
    // The CompleteResult was applied locally: item 12 now sits in the Completed disclosure.
    expect(container.textContent ?? "").toContain("Completed (1)");
    expect(container.textContent ?? "").toContain("Record crew progress");
  });
});

describe("FieldOpsMyTasks — S4 loop-closure + count/inspection", () => {
  const FORM_LINKED: checklist.ChecklistItemState = CHECKLIST_ITEMS[0]; // id 11, form_linked, 'daily-report'
  const COUNT_ITEM: checklist.ChecklistItemState = {
    id: 20, source_item_id: 5, item_type: "count", label: "Log deliveries", form_code: null, target_count: 3,
    status: "open", note: null, photo_ref: null, completed_by: null, completed_at: null, value_num: null, filed_by: null,
  };

  it("a form_linked item renders a deep-link (not a checkbox) and fires onOpenForm pre-filled", async () => {
    tasksOk([]);
    vi.mocked(checklist.fetchMyChecklist).mockResolvedValue({ instance: INSTANCE, items: [FORM_LINKED], reason: null });
    const onOpenForm = vi.fn();
    const { getByLabelText, queryByLabelText } = render(<FieldOpsMyTasks onBack={() => {}} onOpenForm={onOpenForm} />);
    // No manual-check control for a form_linked item; a "Complete <label>" deep-link instead.
    await waitFor(() => expect(getByLabelText("Complete File the Daily Field Report")).not.toBeNull());
    expect(queryByLabelText("Complete item 11")).toBeNull();
    fireEvent.click(getByLabelText("Complete File the Daily Field Report"));
    // 'daily-report' is a no-variant parent → variantCode omitted; job + date come from the instance.
    expect(onOpenForm).toHaveBeenCalledWith({
      jobId: "JOB-A",
      parentCode: "daily-report",
      variantCode: undefined,
      workDate: TODAY,
    });
  });

  it("a done form_linked item shows a done badge (auto-checked) and no manual-complete control", async () => {
    tasksOk([]);
    vi.mocked(checklist.fetchMyChecklist).mockResolvedValue({
      instance: INSTANCE,
      items: [{ ...FORM_LINKED, status: "done", completed_by: "(auto)", filed_by: "Sam Submitter" }],
      reason: null,
    });
    const { container } = render(<FieldOpsMyTasks onBack={() => {}} onOpenForm={vi.fn()} />);
    await waitFor(() => expect(container.querySelector('[aria-label="Today\'s checklist"]')).not.toBeNull());
    // Done items collapse; the badge (and R1 filed-by caption) render inside the disclosure.
    const details = container.querySelector('[aria-label="Today\'s checklist"] details.dash-completed')!;
    expect(details).not.toBeNull();
    expect(details.querySelector(".dash-pill--ok")?.textContent).toBe("done");
    expect(details.textContent ?? "").toContain("filed by Sam Submitter");
  });

  it("a count item renders a number input + Record, firing recordCountItem(id, value)", async () => {
    tasksOk([]);
    vi.mocked(checklist.fetchMyChecklist).mockResolvedValue({ instance: INSTANCE, items: [COUNT_ITEM], reason: null });
    vi.mocked(checklist.recordCountItem).mockResolvedValue({ ok: true, id: 20, status: "done", value_num: 5, instance_status: "open" });
    const { getByLabelText } = render(<FieldOpsMyTasks onBack={() => {}} onOpenForm={vi.fn()} />);
    const input = await waitFor(() => getByLabelText("Count for item 20") as HTMLInputElement);
    expect(input.type).toBe("number");
    fireEvent.change(input, { target: { value: "5" } });
    fireEvent.click(getByLabelText("Record item 20"));
    await waitFor(() => expect(checklist.recordCountItem).toHaveBeenCalledWith(20, 5));
  });
});

describe("FieldOpsMyTasks — S5 auto-rollup → Daily Report", () => {
  const COMPLETE_INSTANCE = { ...INSTANCE, status: "complete" as const };
  const DONE_ITEM: checklist.ChecklistItemState = {
    ...CHECKLIST_ITEMS[1], status: "done", completed_by: "mgr.mo", completed_at: 1,
  };

  it("shows 'Review & file Daily Report' only when the instance is complete + NOT rolled up", async () => {
    tasksOk([]);
    vi.mocked(checklist.fetchMyChecklist).mockResolvedValue({ instance: COMPLETE_INSTANCE, items: [DONE_ITEM], reason: null });
    const { getByLabelText } = render(<FieldOpsMyTasks onBack={() => {}} onOpenForm={vi.fn()} />);
    await waitFor(() => expect(getByLabelText("Review and file Daily Report")).not.toBeNull());
  });

  it("does NOT show the review button while the instance is still open", async () => {
    tasksOk([]);
    vi.mocked(checklist.fetchMyChecklist).mockResolvedValue({ instance: INSTANCE, items: CHECKLIST_ITEMS, reason: null });
    const { queryByLabelText, container } = render(<FieldOpsMyTasks onBack={() => {}} onOpenForm={vi.fn()} />);
    await waitFor(() => expect(container.querySelector('[aria-label="Today\'s checklist"]')).not.toBeNull());
    expect(queryByLabelText("Review and file Daily Report")).toBeNull();
  });

  it("clicking Review & file fetches the draft and opens the Daily Report form pre-filled", async () => {
    tasksOk([]);
    vi.mocked(checklist.fetchMyChecklist).mockResolvedValue({ instance: COMPLETE_INSTANCE, items: [DONE_ITEM], reason: null });
    vi.mocked(checklist.fetchRollupDraft).mockResolvedValue({
      job_id: "JOB-A",
      work_date: TODAY,
      form_code: "daily-report",
      values: { job_name: "Alpha", report_date: TODAY, prepared_by: "Mo Manager", comments: "summary" },
    });
    const onOpenForm = vi.fn();
    const { getByLabelText } = render(<FieldOpsMyTasks onBack={() => {}} onOpenForm={onOpenForm} />);
    const btn = await waitFor(() => getByLabelText("Review and file Daily Report"));
    fireEvent.click(btn);
    await waitFor(() => expect(checklist.fetchRollupDraft).toHaveBeenCalled());
    // 'daily-report' is a no-variant parent → variantCode omitted; job/date from the draft; values carried.
    await waitFor(() =>
      expect(onOpenForm).toHaveBeenCalledWith({
        jobId: "JOB-A",
        parentCode: "daily-report",
        variantCode: undefined,
        workDate: TODAY,
        values: { job_name: "Alpha", report_date: TODAY, prepared_by: "Mo Manager", comments: "summary" },
      }),
    );
  });

  it("shows the 'Daily Report filed ✓' state (no review button) once rolled up", async () => {
    tasksOk([]);
    vi.mocked(checklist.fetchMyChecklist).mockResolvedValue({
      instance: { ...COMPLETE_INSTANCE, rolled_up_submission_uuid: "sub-123", rolled_up_by: "Mo Manager" },
      items: [DONE_ITEM],
      reason: null,
    });
    const { container, queryByLabelText } = render(<FieldOpsMyTasks onBack={() => {}} onOpenForm={vi.fn()} />);
    await waitFor(() => expect(container.querySelector('[aria-label="Daily Report filed"]')).not.toBeNull());
    expect(container.textContent ?? "").toContain("Daily Report filed");
    expect(container.textContent ?? "").toContain("by Mo Manager");
    expect(queryByLabelText("Review and file Daily Report")).toBeNull();
  });
});

describe("FieldOpsMyTasks — S6 assigned inspections", () => {
  const INSPECTION: checklist.AssignedInspection = {
    instance: { id: 30, job_id: "JOB-A", project_name: "Alpha", instance_date: "2099-07-10", status: "open", template_title: "Fall protection", created_at: 100 },
    items: [
      { id: 40, source_item_id: 1, item_type: "manual_attest", label: "Harness checked", form_code: null, target_count: null, status: "open", note: null, photo_ref: null, completed_by: null, completed_at: null, value_num: null, filed_by: null },
      { id: 41, source_item_id: 2, item_type: "form_linked", label: "File JHA", form_code: "jha", target_count: null, status: "open", note: null, photo_ref: null, completed_by: null, completed_at: null, value_num: null, filed_by: null },
    ],
  };

  it("renders the Assigned inspections section titled by template_title (#id demoted)", async () => {
    tasksOk([]);
    vi.mocked(checklist.fetchAssignedInspections).mockResolvedValue({ inspections: [INSPECTION], linked: true });
    const { container, getByLabelText } = render(<FieldOpsMyTasks onBack={() => {}} onOpenForm={vi.fn()} />);
    await waitFor(() => expect(container.querySelector('[aria-label="Assigned inspections"]')).not.toBeNull());
    const heading = container.querySelector('[aria-label="Assigned inspections"] h4')!;
    expect(heading.textContent ?? "").toContain("Fall protection");
    // The raw id survives only as demoted small text.
    expect(heading.querySelector(".dash-card__sub")?.textContent).toContain("#30");
    const txt = container.textContent ?? "";
    expect(txt).toContain("Harness checked");
    expect(txt).toContain("File JHA");
    // manual_attest gets a complete control; form_linked gets a deep-link (no manual-check).
    expect(getByLabelText("Complete item 40")).not.toBeNull();
    expect(() => getByLabelText("Complete item 41")).toThrow();
  });

  it("renders nothing when there are no assigned inspections (confirmed empty)", async () => {
    tasksOk([]);
    vi.mocked(checklist.fetchAssignedInspections).mockResolvedValue({ inspections: [], linked: true });
    const { container } = render(<FieldOpsMyTasks onBack={() => {}} onOpenForm={vi.fn()} />);
    await waitFor(() => expect(container.querySelector(".dash-unavail")).not.toBeNull());
    expect(container.querySelector('[aria-label="Assigned inspections"]')).toBeNull();
  });

  it("completing an assigned-inspection manual_attest item fires completeChecklistItem", async () => {
    tasksOk([]);
    vi.mocked(checklist.fetchAssignedInspections).mockResolvedValue({ inspections: [INSPECTION], linked: true });
    vi.mocked(checklist.completeChecklistItem).mockResolvedValue({ ok: true, id: 40, status: "done", instance_status: "open" });
    const { getByLabelText } = render(<FieldOpsMyTasks onBack={() => {}} onOpenForm={vi.fn()} />);
    const btn = await waitFor(() => getByLabelText("Complete item 40"));
    fireEvent.click(btn);
    await waitFor(() => expect(checklist.completeChecklistItem).toHaveBeenCalledWith(40, undefined));
  });

  it("a form_linked inspection item deep-links pre-filled from the instance's job + date", async () => {
    tasksOk([]);
    vi.mocked(checklist.fetchAssignedInspections).mockResolvedValue({ inspections: [INSPECTION], linked: true });
    const onOpenForm = vi.fn();
    const { getByLabelText } = render(<FieldOpsMyTasks onBack={() => {}} onOpenForm={onOpenForm} />);
    const link = await waitFor(() => getByLabelText("Complete File JHA"));
    fireEvent.click(link);
    // 'jha' resolves to its versioned variant; job + date come from the inspection instance.
    await waitFor(() => expect(onOpenForm).toHaveBeenCalledWith(expect.objectContaining({ jobId: "JOB-A", parentCode: "jha", workDate: "2099-07-10" })));
  });
});

describe("FieldOpsMyTasks — Slice T subcontractor Add crew", () => {
  it("hides the Add crew control without cap.crew.create", async () => {
    vi.mocked(useAuth).mockReturnValue(authWith(["cap.tasks.own"]));
    tasksOk([]);
    const { container } = render(<FieldOpsMyTasks onBack={() => {}} />);
    await waitFor(() => expect(container.querySelector(".dash-unavail")).not.toBeNull());
    expect(container.querySelector('[aria-label="Add crew"]')).toBeNull();
  });

  it("shows the Add crew control as a collapsed disclosure and posts to createCrew", async () => {
    vi.mocked(useAuth).mockReturnValue(authWith(["cap.tasks.own", "cap.crew.create"]));
    tasksOk([]);
    vi.mocked(personnel.createCrew).mockResolvedValue({ id: 5, current_job: "JOB-A" });
    const { container, getByLabelText } = render(<FieldOpsMyTasks onBack={() => {}} />);
    await waitFor(() => expect(container.querySelector('[aria-label="Add crew"]')).not.toBeNull());
    // Collapsed disclosure by default.
    const details = container.querySelector('details[aria-label="Add crew"]')!;
    expect(details).not.toBeNull();
    expect(details.hasAttribute("open")).toBe(false);
    const form = getByLabelText("Add crew form") as HTMLFormElement;
    fireEvent.change(form.querySelector('input[placeholder="Name"]')!, { target: { value: "Helper Hank" } });
    fireEvent.change(form.querySelector('input[placeholder="Trade (optional)"]')!, { target: { value: "laborer" } });
    fireEvent.submit(form);
    await waitFor(() => expect(personnel.createCrew).toHaveBeenCalledWith({ name: "Helper Hank", trade: "laborer" }));
    await waitFor(() => expect(container.textContent ?? "").toContain("Added Helper Hank to your crew on JOB-A"));
    // The crew list refreshes so the new person shows up.
    await waitFor(() => expect(personnel.fetchMyCrew).toHaveBeenCalledTimes(2));
  });

  it("surfaces a clear 'must be placed on a job' message on a 422 not_placed", async () => {
    vi.mocked(useAuth).mockReturnValue(authWith(["cap.tasks.own", "cap.crew.create"]));
    tasksOk([]);
    // R1: the libs throw ApiError (err.code = wire code, err.message = human copy) — the page
    // branches on err.code.
    vi.mocked(personnel.createCrew).mockRejectedValue(new ApiError("not_placed", 422));
    const { getByLabelText, container } = render(<FieldOpsMyTasks onBack={() => {}} />);
    const form = await waitFor(() => getByLabelText("Add crew form") as HTMLFormElement);
    fireEvent.change(form.querySelector('input[placeholder="Name"]')!, { target: { value: "Nope" } });
    fireEvent.submit(form);
    await waitFor(() => expect(container.textContent ?? "").toContain("must be placed on a job"));
  });
});

describe("HomePage — My Tasks card gate", () => {
  it("renders the My Tasks card for a holder of cap.tasks.own", () => {
    vi.mocked(useAuth).mockReturnValue(authWith(["cap.tasks.own"]));
    const { container } = render(<HomePage onNavigate={() => {}} />);
    expect(container.textContent ?? "").toContain("My Tasks");
  });

  it("hides the My Tasks card without cap.tasks.own", () => {
    vi.mocked(useAuth).mockReturnValue(authWith(["cap.form.submit"]));
    const { container } = render(<HomePage onNavigate={() => {}} />);
    expect(container.textContent ?? "").not.toContain("My Tasks");
  });
});
