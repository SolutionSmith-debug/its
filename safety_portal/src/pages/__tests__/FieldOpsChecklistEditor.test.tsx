/**
 * Assigned-Tasks tab (P4 field-ops feature) S2 — the Daily-checklist editor on the Job Tracker detail.
 * Gated cap.checklist.manage. Renders the job's EFFECTIVE merged checklist (default ⊕ this job's
 * overrides); add-item / hide (suppress) / remove fire the right fieldops_checklist lib calls. Mirrors
 * FieldOpsJobTracker.test.tsx: mock every lib the page imports before render, then drive the detail view.
 */
import { cleanup, fireEvent, render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../lib/fieldops_jobtracker", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../lib/fieldops_jobtracker")>();
  return {
    ...actual,
    fetchJobList: vi.fn(),
    fetchJobDetail: vi.fn(),
    createJob: vi.fn(),
    closeJob: vi.fn(),
    setLifecycle: vi.fn(),
    editContacts: vi.fn(),
    setJobProgress: vi.fn(),
    addTask: vi.fn(),
    setTaskStatus: vi.fn(),
    reassignTask: vi.fn(),
    logTime: vi.fn(),
  };
});
vi.mock("../../lib/fieldops_checklist", () => ({
  fetchJobChecklist: vi.fn(),
  fetchDefaultChecklist: vi.fn(),
  addJobItem: vi.fn(),
  deleteJobItem: vi.fn(),
  suppressDefaultItem: vi.fn(),
  unsuppressDefaultItem: vi.fn(),
  addDefaultItem: vi.fn(),
  editDefaultItem: vi.fn(),
  deleteDefaultItem: vi.fn(),
}));
vi.mock("../../lib/auth", () => ({ useAuth: vi.fn() }));
vi.mock("../../lib/fieldops_personnel", () => ({ fetchPersonnelList: vi.fn(), assignPersonnel: vi.fn() }));
vi.mock("../../lib/fieldops_equipment", () => ({ fetchEquipmentList: vi.fn(), moveEquipment: vi.fn() }));

import * as api from "../../lib/fieldops_jobtracker";
import * as checklist from "../../lib/fieldops_checklist";
import { fetchPersonnelList } from "../../lib/fieldops_personnel";
import { fetchEquipmentList } from "../../lib/fieldops_equipment";
import { useAuth } from "../../lib/auth";
import { FieldOpsJobTracker } from "../FieldOpsJobTracker";

function authWith(capabilities: string[]) {
  return {
    user: { username: "u", role: "admin" as const, capabilities },
    loading: false,
    login: vi.fn(),
    logout: vi.fn(),
  };
}

const JOBS = [{ job_id: "JOB-A", project_name: "Alpha", status: "active", progress: 0, client_name: null, crew: [], open_tasks: [] }];
const DETAIL = {
  job_id: "JOB-A",
  project_name: "Alpha",
  status: "active",
  progress: 0,
  client: null,
  crew: [],
  tasks: [],
  time_entries: [],
  equipment_on_site: [],
  inspections: [],
};
const NO_CURSORS = { tasks: null, time: null, insp: null };

const JOB_CHECKLIST = {
  job_id: "JOB-A",
  items: [
    { source_item_id: 1, seq: 10, item_type: "form_linked", label: "File the Daily Field Report", form_code: "daily-report", target_count: null, config_json: null, origin: "default" as const },
    { source_item_id: 7, seq: 25, item_type: "manual_attest", label: "Job-specific step", form_code: null, target_count: null, config_json: null, origin: "override" as const },
  ],
  suppressed: [
    { source_item_id: 3, seq: 20, item_type: "manual_attest", label: "Record crew progress", form_code: null, target_count: null },
  ],
};

beforeEach(() => {
  vi.resetAllMocks();
  vi.mocked(fetchPersonnelList).mockResolvedValue({ personnel: [], latest_entries: [], next_cursor: null });
  vi.mocked(fetchEquipmentList).mockResolvedValue({ equipment: [], next_cursor: null });
  vi.mocked(api.fetchJobList).mockResolvedValue({ jobs: JOBS, next_cursor: null });
  vi.mocked(api.fetchJobDetail).mockResolvedValue({ job: DETAIL, cursors: NO_CURSORS });
  vi.mocked(checklist.fetchJobChecklist).mockResolvedValue(JOB_CHECKLIST);
  vi.mocked(checklist.fetchDefaultChecklist).mockResolvedValue({ template: null, items: [] });
  vi.mocked(checklist.addJobItem).mockResolvedValue({ ok: true, id: 9 });
  vi.mocked(checklist.suppressDefaultItem).mockResolvedValue({ ok: true });
  vi.mocked(checklist.unsuppressDefaultItem).mockResolvedValue({ ok: true });
  vi.mocked(checklist.deleteJobItem).mockResolvedValue({ ok: true, id: 7 });
});
afterEach(cleanup);

async function openDetail(caps: string[]) {
  vi.mocked(useAuth).mockReturnValue(authWith(caps));
  const utils = render(<FieldOpsJobTracker onBack={() => {}} />);
  await waitFor(() => expect(utils.container.querySelectorAll(".dash-card--click")).toHaveLength(1));
  fireEvent.click(utils.container.querySelector(".dash-card--click")!);
  await waitFor(() => expect(api.fetchJobDetail).toHaveBeenCalledWith("JOB-A"));
  return utils;
}

describe("FieldOpsJobTracker — Daily checklist editor (S2)", () => {
  it("is absent without cap.checklist.manage", async () => {
    const { container } = await openDetail(["cap.jobtracker.manage"]);
    expect(container.querySelector('[aria-label="Daily checklist"]')).toBeNull();
    expect(checklist.fetchJobChecklist).not.toHaveBeenCalled();
  });

  it("renders the merged checklist (default + override rows) for cap.checklist.manage", async () => {
    const { container } = await openDetail(["cap.checklist.manage"]);
    await waitFor(() => expect(checklist.fetchJobChecklist).toHaveBeenCalledWith("JOB-A"));
    const section = container.querySelector('[aria-label="Daily checklist"]')!;
    expect(section).not.toBeNull();
    expect(section.textContent).toContain("File the Daily Field Report");
    expect(section.textContent).toContain("Job-specific step");
    // The override row is deletable, the default row is suppressable.
    expect(container.querySelector('[aria-label="Hide File the Daily Field Report"]')).not.toBeNull();
    expect(container.querySelector('[aria-label="Remove Job-specific step"]')).not.toBeNull();
    // Suppressed default item offers an "Unhide".
    expect(container.querySelector('[aria-label="Unhide Record crew progress"]')).not.toBeNull();
  });

  it("adding a job item calls addJobItem with the drafted item + reloads", async () => {
    const { container, getByLabelText } = await openDetail(["cap.checklist.manage"]);
    await waitFor(() => expect(checklist.fetchJobChecklist).toHaveBeenCalledTimes(1));
    fireEvent.change(getByLabelText("Add checklist item label"), { target: { value: "Torque check" } });
    fireEvent.submit(container.querySelector('[aria-label="Add checklist item"]')!);
    await waitFor(() =>
      expect(checklist.addJobItem).toHaveBeenCalledWith("JOB-A", expect.objectContaining({ item_type: "manual_attest", label: "Torque check" })),
    );
    await waitFor(() => expect(checklist.fetchJobChecklist).toHaveBeenCalledTimes(2)); // reloaded after the write
  });

  it("Hide fires suppressDefaultItem; Remove fires deleteJobItem; Unhide fires unsuppressDefaultItem", async () => {
    const { container } = await openDetail(["cap.checklist.manage"]);
    await waitFor(() => expect(checklist.fetchJobChecklist).toHaveBeenCalled());
    fireEvent.click(container.querySelector('[aria-label="Hide File the Daily Field Report"]')!);
    await waitFor(() => expect(checklist.suppressDefaultItem).toHaveBeenCalledWith("JOB-A", 1));
    fireEvent.click(container.querySelector('[aria-label="Remove Job-specific step"]')!);
    await waitFor(() => expect(checklist.deleteJobItem).toHaveBeenCalledWith("JOB-A", 7));
    fireEvent.click(container.querySelector('[aria-label="Unhide Record crew progress"]')!);
    await waitFor(() => expect(checklist.unsuppressDefaultItem).toHaveBeenCalledWith("JOB-A", 3));
  });

  it("form_linked draft reveals a form-code field the add uses", async () => {
    const { container, getByLabelText } = await openDetail(["cap.checklist.manage"]);
    await waitFor(() => expect(checklist.fetchJobChecklist).toHaveBeenCalled());
    fireEvent.change(getByLabelText("Add checklist item type"), { target: { value: "form_linked" } });
    fireEvent.change(getByLabelText("Add checklist item label"), { target: { value: "Attach JHA" } });
    fireEvent.change(getByLabelText("Add checklist item form code"), { target: { value: "jha" } });
    fireEvent.submit(container.querySelector('[aria-label="Add checklist item"]')!);
    await waitFor(() =>
      expect(checklist.addJobItem).toHaveBeenCalledWith("JOB-A", expect.objectContaining({ item_type: "form_linked", label: "Attach JHA", form_code: "jha" })),
    );
  });
});
