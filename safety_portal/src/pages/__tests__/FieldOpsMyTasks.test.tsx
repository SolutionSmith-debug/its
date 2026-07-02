/**
 * Assigned-Tasks tab (P4 S1) — "My Tasks" page + its HomePage card gate.
 * Mirrors FieldOpsPersonnel.test.tsx: vi.mock the lib + useAuth, render, query.
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
  // Default: no daily checklist (not a placed manager) → the S3 section renders nothing.
  vi.mocked(checklist.fetchMyChecklist).mockResolvedValue({ instance: null, items: [], reason: null });
  // Default: no assigned inspections → the S6 section renders nothing.
  vi.mocked(checklist.fetchAssignedInspections).mockResolvedValue({ inspections: [], linked: true });
});

const INSTANCE = { id: 7, job_id: "JOB-A", project_name: "Alpha", instance_date: "2026-07-01", status: "open" as const, rolled_up_submission_uuid: null, rolled_up_by: null };
const CHECKLIST_ITEMS: checklist.ChecklistItemState[] = [
  { id: 11, source_item_id: 1, item_type: "form_linked", label: "File the Daily Field Report", form_code: "daily-report", target_count: null, status: "open", note: null, photo_ref: null, completed_by: null, completed_at: null, value_num: null, filed_by: null },
  { id: 12, source_item_id: 2, item_type: "manual_attest", label: "Record crew progress", form_code: null, target_count: null, status: "open", note: null, photo_ref: null, completed_by: null, completed_at: null, value_num: null, filed_by: null },
];

const TASKS: api.MyTask[] = [
  { id: 1, job_id: "JOB-A", project_name: "Alpha", description: "Dig footings", status: "open", created_at: 100, assigned_by: null },
  { id: 2, job_id: "JOB-A", project_name: "Alpha", description: "Pour slab", status: "in_progress", created_at: 90, assigned_by: null },
  { id: 3, job_id: "JOB-B", project_name: "Bravo", description: "Frame wall", status: "open", created_at: 80, assigned_by: null },
];

describe("FieldOpsMyTasks", () => {
  it("renders my tasks grouped by job (project name)", async () => {
    vi.mocked(api.fetchMyTasks).mockResolvedValue({ tasks: TASKS, linked: true });
    const { container } = render(<FieldOpsMyTasks onBack={() => {}} />);
    // Two job groups (Alpha with 2 tasks, Bravo with 1).
    await waitFor(() => expect(container.querySelectorAll(".dash-section")).toHaveLength(2));
    const headings = Array.from(container.querySelectorAll(".dash-detail__h2")).map((h) => h.textContent ?? "");
    expect(headings.some((h) => h.includes("Alpha") && h.includes("JOB-A"))).toBe(true);
    expect(headings.some((h) => h.includes("Bravo") && h.includes("JOB-B"))).toBe(true);
    const txt = container.textContent ?? "";
    expect(txt).toContain("Dig footings");
    expect(txt).toContain("Pour slab");
    expect(txt).toContain("Frame wall");
    // The Alpha group holds two task rows.
    const alphaSection = Array.from(container.querySelectorAll(".dash-section")).find((s) => (s.textContent ?? "").includes("Alpha"))!;
    expect(alphaSection.querySelectorAll(".dash-tasklist li")).toHaveLength(2);
  });

  it("a status change fires setTaskStatus(taskId, status)", async () => {
    vi.mocked(api.fetchMyTasks).mockResolvedValue({ tasks: TASKS, linked: true });
    vi.mocked(api.setTaskStatus).mockResolvedValue(undefined);
    const { getByLabelText } = render(<FieldOpsMyTasks onBack={() => {}} />);
    const select = await waitFor(() => getByLabelText("Set status for task 1") as HTMLSelectElement);
    expect(select.value).toBe("open");
    fireEvent.change(select, { target: { value: "done" } });
    await waitFor(() => expect(api.setTaskStatus).toHaveBeenCalledWith(1, "done"));
  });

  it("shows an empty state for a user with no assigned tasks (e.g. no linked personnel)", async () => {
    vi.mocked(api.fetchMyTasks).mockResolvedValue({ tasks: [], linked: true });
    const { container } = render(<FieldOpsMyTasks onBack={() => {}} />);
    await waitFor(() => expect(container.querySelector(".dash-unavail")).not.toBeNull());
    expect(container.textContent ?? "").toContain("No tasks are assigned to you");
    expect(container.querySelector(".dash-tasklist")).toBeNull();
  });
});

describe("FieldOpsMyTasks — S3 daily checklist section", () => {
  it("renders the Today's checklist section for a placed manager (instance present)", async () => {
    vi.mocked(api.fetchMyTasks).mockResolvedValue({ tasks: [], linked: true });
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

  it("hides the section entirely when instance is null (not a placed manager)", async () => {
    vi.mocked(api.fetchMyTasks).mockResolvedValue({ tasks: [], linked: true });
    vi.mocked(checklist.fetchMyChecklist).mockResolvedValue({ instance: null, items: [], reason: null });
    const { container } = render(<FieldOpsMyTasks onBack={() => {}} />);
    await waitFor(() => expect(container.querySelector(".dash-unavail")).not.toBeNull());
    expect(container.querySelector('[aria-label="Today\'s checklist"]')).toBeNull();
  });

  it("completing a manual_attest item fires completeChecklistItem", async () => {
    vi.mocked(api.fetchMyTasks).mockResolvedValue({ tasks: [], linked: true });
    vi.mocked(checklist.fetchMyChecklist).mockResolvedValue({ instance: INSTANCE, items: CHECKLIST_ITEMS, reason: null });
    vi.mocked(checklist.completeChecklistItem).mockResolvedValue({ ok: true, id: 12, status: "done", instance_status: "open" });
    const { getByLabelText } = render(<FieldOpsMyTasks onBack={() => {}} />);
    const btn = await waitFor(() => getByLabelText("Complete item 12"));
    fireEvent.click(btn);
    await waitFor(() => expect(checklist.completeChecklistItem).toHaveBeenCalledWith(12, undefined));
  });
});

describe("FieldOpsMyTasks — S4 loop-closure + count/inspection", () => {
  const FORM_LINKED: checklist.ChecklistItemState = CHECKLIST_ITEMS[0]; // id 11, form_linked, 'daily-report'
  const COUNT_ITEM: checklist.ChecklistItemState = {
    id: 20, source_item_id: 5, item_type: "count", label: "Log deliveries", form_code: null, target_count: 3,
    status: "open", note: null, photo_ref: null, completed_by: null, completed_at: null, value_num: null, filed_by: null,
  };

  it("a form_linked item renders a deep-link (not a checkbox) and fires onOpenForm pre-filled", async () => {
    vi.mocked(api.fetchMyTasks).mockResolvedValue({ tasks: [], linked: true });
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
      workDate: "2026-07-01",
    });
  });

  it("a done form_linked item shows a done badge (auto-checked) and no manual-complete control", async () => {
    vi.mocked(api.fetchMyTasks).mockResolvedValue({ tasks: [], linked: true });
    vi.mocked(checklist.fetchMyChecklist).mockResolvedValue({
      instance: INSTANCE,
      items: [{ ...FORM_LINKED, status: "done", completed_by: "(auto)" }],
      reason: null,
    });
    const { container } = render(<FieldOpsMyTasks onBack={() => {}} onOpenForm={vi.fn()} />);
    await waitFor(() => expect(container.querySelector('[aria-label="Today\'s checklist"]')).not.toBeNull());
    const li = container.querySelector(".dash-tasklist li")!;
    expect(li.querySelector(".dash-pill--ok")?.textContent).toBe("done");
  });

  it("a count item renders a number input + Record, firing recordCountItem(id, value)", async () => {
    vi.mocked(api.fetchMyTasks).mockResolvedValue({ tasks: [], linked: true });
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
    vi.mocked(api.fetchMyTasks).mockResolvedValue({ tasks: [], linked: true });
    vi.mocked(checklist.fetchMyChecklist).mockResolvedValue({ instance: COMPLETE_INSTANCE, items: [DONE_ITEM], reason: null });
    const { getByLabelText } = render(<FieldOpsMyTasks onBack={() => {}} onOpenForm={vi.fn()} />);
    await waitFor(() => expect(getByLabelText("Review and file Daily Report")).not.toBeNull());
  });

  it("does NOT show the review button while the instance is still open", async () => {
    vi.mocked(api.fetchMyTasks).mockResolvedValue({ tasks: [], linked: true });
    vi.mocked(checklist.fetchMyChecklist).mockResolvedValue({ instance: INSTANCE, items: CHECKLIST_ITEMS, reason: null });
    const { queryByLabelText, container } = render(<FieldOpsMyTasks onBack={() => {}} onOpenForm={vi.fn()} />);
    await waitFor(() => expect(container.querySelector('[aria-label="Today\'s checklist"]')).not.toBeNull());
    expect(queryByLabelText("Review and file Daily Report")).toBeNull();
  });

  it("clicking Review & file fetches the draft and opens the Daily Report form pre-filled", async () => {
    vi.mocked(api.fetchMyTasks).mockResolvedValue({ tasks: [], linked: true });
    vi.mocked(checklist.fetchMyChecklist).mockResolvedValue({ instance: COMPLETE_INSTANCE, items: [DONE_ITEM], reason: null });
    vi.mocked(checklist.fetchRollupDraft).mockResolvedValue({
      job_id: "JOB-A",
      work_date: "2026-07-01",
      form_code: "daily-report",
      values: { job_name: "Alpha", report_date: "2026-07-01", prepared_by: "Mo Manager", comments: "summary" },
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
        workDate: "2026-07-01",
        values: { job_name: "Alpha", report_date: "2026-07-01", prepared_by: "Mo Manager", comments: "summary" },
      }),
    );
  });

  it("shows the 'Daily Report filed ✓' state (no review button) once rolled up", async () => {
    vi.mocked(api.fetchMyTasks).mockResolvedValue({ tasks: [], linked: true });
    vi.mocked(checklist.fetchMyChecklist).mockResolvedValue({
      instance: { ...COMPLETE_INSTANCE, rolled_up_submission_uuid: "sub-123" },
      items: [DONE_ITEM],
      reason: null,
    });
    const { container, queryByLabelText } = render(<FieldOpsMyTasks onBack={() => {}} onOpenForm={vi.fn()} />);
    await waitFor(() => expect(container.querySelector('[aria-label="Daily Report filed"]')).not.toBeNull());
    expect(container.textContent ?? "").toContain("Daily Report filed");
    expect(queryByLabelText("Review and file Daily Report")).toBeNull();
  });
});

describe("FieldOpsMyTasks — S6 assigned inspections", () => {
  const INSPECTION: checklist.AssignedInspection = {
    instance: { id: 30, job_id: "JOB-A", project_name: "Alpha", instance_date: "2026-07-10", status: "open", template_title: "Fall protection", created_at: 100 },
    items: [
      { id: 40, source_item_id: 1, item_type: "manual_attest", label: "Harness checked", form_code: null, target_count: null, status: "open", note: null, photo_ref: null, completed_by: null, completed_at: null, value_num: null, filed_by: null },
      { id: 41, source_item_id: 2, item_type: "form_linked", label: "File JHA", form_code: "jha", target_count: null, status: "open", note: null, photo_ref: null, completed_by: null, completed_at: null, value_num: null, filed_by: null },
    ],
  };

  it("renders the Assigned inspections section with its items", async () => {
    vi.mocked(api.fetchMyTasks).mockResolvedValue({ tasks: [], linked: true });
    vi.mocked(checklist.fetchAssignedInspections).mockResolvedValue({ inspections: [INSPECTION], linked: true });
    const { container, getByLabelText } = render(<FieldOpsMyTasks onBack={() => {}} onOpenForm={vi.fn()} />);
    await waitFor(() => expect(container.querySelector('[aria-label="Assigned inspections"]')).not.toBeNull());
    const txt = container.textContent ?? "";
    expect(txt).toContain("Harness checked");
    expect(txt).toContain("File JHA");
    // manual_attest gets a complete control; form_linked gets a deep-link (no manual-check).
    expect(getByLabelText("Complete item 40")).not.toBeNull();
    expect(() => getByLabelText("Complete item 41")).toThrow();
  });

  it("renders nothing when there are no assigned inspections", async () => {
    vi.mocked(api.fetchMyTasks).mockResolvedValue({ tasks: [], linked: true });
    vi.mocked(checklist.fetchAssignedInspections).mockResolvedValue({ inspections: [], linked: true });
    const { container } = render(<FieldOpsMyTasks onBack={() => {}} onOpenForm={vi.fn()} />);
    await waitFor(() => expect(container.querySelector(".dash-unavail")).not.toBeNull());
    expect(container.querySelector('[aria-label="Assigned inspections"]')).toBeNull();
  });

  it("completing an assigned-inspection manual_attest item fires completeChecklistItem", async () => {
    vi.mocked(api.fetchMyTasks).mockResolvedValue({ tasks: [], linked: true });
    vi.mocked(checklist.fetchAssignedInspections).mockResolvedValue({ inspections: [INSPECTION], linked: true });
    vi.mocked(checklist.completeChecklistItem).mockResolvedValue({ ok: true, id: 40, status: "done", instance_status: "open" });
    const { getByLabelText } = render(<FieldOpsMyTasks onBack={() => {}} onOpenForm={vi.fn()} />);
    const btn = await waitFor(() => getByLabelText("Complete item 40"));
    fireEvent.click(btn);
    await waitFor(() => expect(checklist.completeChecklistItem).toHaveBeenCalledWith(40, undefined));
  });

  it("a form_linked inspection item deep-links pre-filled from the instance's job + date", async () => {
    vi.mocked(api.fetchMyTasks).mockResolvedValue({ tasks: [], linked: true });
    vi.mocked(checklist.fetchAssignedInspections).mockResolvedValue({ inspections: [INSPECTION], linked: true });
    const onOpenForm = vi.fn();
    const { getByLabelText } = render(<FieldOpsMyTasks onBack={() => {}} onOpenForm={onOpenForm} />);
    const link = await waitFor(() => getByLabelText("Complete File JHA"));
    fireEvent.click(link);
    // 'jha' resolves to its versioned variant; job + date come from the inspection instance.
    await waitFor(() => expect(onOpenForm).toHaveBeenCalledWith(expect.objectContaining({ jobId: "JOB-A", parentCode: "jha", workDate: "2026-07-10" })));
  });
});

describe("FieldOpsMyTasks — Slice T subcontractor Add crew", () => {
  it("hides the Add crew control without cap.crew.create", async () => {
    vi.mocked(useAuth).mockReturnValue(authWith(["cap.tasks.own"]));
    vi.mocked(api.fetchMyTasks).mockResolvedValue({ tasks: [], linked: true });
    const { container } = render(<FieldOpsMyTasks onBack={() => {}} />);
    await waitFor(() => expect(container.querySelector(".dash-unavail")).not.toBeNull());
    expect(container.querySelector('[aria-label="Add crew"]')).toBeNull();
  });

  it("shows the Add crew control for a subcontractor (cap.crew.create) and posts to createCrew", async () => {
    vi.mocked(useAuth).mockReturnValue(authWith(["cap.tasks.own", "cap.crew.create"]));
    vi.mocked(api.fetchMyTasks).mockResolvedValue({ tasks: [], linked: true });
    vi.mocked(personnel.createCrew).mockResolvedValue({ id: 5, current_job: "JOB-A" });
    const { container, getByLabelText } = render(<FieldOpsMyTasks onBack={() => {}} />);
    await waitFor(() => expect(container.querySelector('[aria-label="Add crew"]')).not.toBeNull());
    const form = getByLabelText("Add crew form") as HTMLFormElement;
    fireEvent.change(form.querySelector('input[placeholder="Name"]')!, { target: { value: "Helper Hank" } });
    fireEvent.change(form.querySelector('input[placeholder="Trade (optional)"]')!, { target: { value: "laborer" } });
    fireEvent.submit(form);
    await waitFor(() => expect(personnel.createCrew).toHaveBeenCalledWith({ name: "Helper Hank", trade: "laborer" }));
    await waitFor(() => expect(container.textContent ?? "").toContain("Added Helper Hank to your crew on JOB-A"));
  });

  it("surfaces a clear 'must be placed on a job' message on a 422 not_placed", async () => {
    vi.mocked(useAuth).mockReturnValue(authWith(["cap.tasks.own", "cap.crew.create"]));
    vi.mocked(api.fetchMyTasks).mockResolvedValue({ tasks: [], linked: true });
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
