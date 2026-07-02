/**
 * Assigned-Tasks tab (P4 field-ops feature) S2 — the Daily-checklist editor on the Job Tracker detail,
 * post-R4 extraction: the job detail keeps ONLY the per-job tailoring (add-for-this-job / hide /
 * unhide) with Shared vs This-job-only labeling and a cross-link to the consolidated Checklists area;
 * the shared-default editing (Edit-shared-default toggle, default add/delete) is GONE — it lives in
 * FieldOpsInspections now. Gated cap.checklist.manage. Mirrors FieldOpsJobTracker.test.tsx: mock every
 * lib the page imports before render, then drive the detail view.
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

  it("renders the merged checklist with Shared / This-job-only labeling for cap.checklist.manage", async () => {
    const { container } = await openDetail(["cap.checklist.manage"]);
    await waitFor(() => expect(checklist.fetchJobChecklist).toHaveBeenCalledWith("JOB-A"));
    const section = container.querySelector('[aria-label="Daily checklist"]')!;
    expect(section).not.toBeNull();
    expect(section.textContent).toContain("File the Daily Field Report");
    expect(section.textContent).toContain("Job-specific step");
    // R4: origin pills render the HUMAN labels (labels.ts originLabel), not raw default/override.
    const pills = Array.from(section.querySelectorAll(".dash-pill")).map((el) => el.textContent);
    expect(pills).toEqual(expect.arrayContaining(["Shared", "This job only"]));
    // The override row is deletable, the default row is suppressable.
    expect(container.querySelector('[aria-label="Hide File the Daily Field Report"]')).not.toBeNull();
    expect(container.querySelector('[aria-label="Remove Job-specific step"]')).not.toBeNull();
    // Suppressed default item offers an "Unhide".
    expect(container.querySelector('[aria-label="Unhide Record crew progress"]')).not.toBeNull();
  });

  it("the shared-default editor is GONE from the job detail; a cross-link names the Checklists area", async () => {
    const { container } = await openDetail(["cap.checklist.manage"]);
    await waitFor(() => expect(checklist.fetchJobChecklist).toHaveBeenCalled());
    const section = container.querySelector('[aria-label="Daily checklist"]')!;
    // No Edit-shared-default toggle, no default-editor fieldset, no default fetch.
    expect(section.textContent).not.toContain("Edit shared default");
    expect(container.querySelector('[aria-label="Default checklist"]')).toBeNull();
    expect(checklist.fetchDefaultChecklist).not.toHaveBeenCalled();
    // Cross-link copy: where the shared default IS edited now (the Home card, by its R7 name —
    // the card was renamed "Inspection checklists" → "Checklists" per Open Q4).
    expect(section.textContent).toContain("Edit the shared default itself in Checklists");
    expect(section.textContent).toContain("the “Checklists” card on Home");
  });

  it("adding a job item calls addJobItem with the drafted item + auto-suggested seq (max+10) + reloads", async () => {
    const { container, getByLabelText } = await openDetail(["cap.checklist.manage"]);
    await waitFor(() => expect(checklist.fetchJobChecklist).toHaveBeenCalledTimes(1));
    fireEvent.change(getByLabelText("Add checklist item label"), { target: { value: "Torque check" } });
    fireEvent.submit(container.querySelector('[aria-label="Add checklist item"]')!);
    await waitFor(() =>
      // effective items carry seq 10 + 25 → the new item lands at the end (35).
      expect(checklist.addJobItem).toHaveBeenCalledWith("JOB-A", expect.objectContaining({ item_type: "manual_attest", label: "Torque check", seq: 35 })),
    );
    await waitFor(() => expect(checklist.fetchJobChecklist).toHaveBeenCalledTimes(2)); // reloaded after the write
  });

  it("Hide fires suppressDefaultItem; Remove fires deleteJobItem; Unhide fires unsuppressDefaultItem", async () => {
    const { container } = await openDetail(["cap.checklist.manage"]);
    await waitFor(() => expect(checklist.fetchJobChecklist).toHaveBeenCalled());
    fireEvent.click(container.querySelector('[aria-label="Hide File the Daily Field Report"]')!);
    await waitFor(() => expect(checklist.suppressDefaultItem).toHaveBeenCalledWith("JOB-A", 1));
    // Remove is confirm-gated (R4 review): first tap opens the confirm, Confirm fires the delete.
    fireEvent.click(container.querySelector('[aria-label="Remove Job-specific step"]')!);
    expect(checklist.deleteJobItem).not.toHaveBeenCalled();
    fireEvent.click(container.querySelector('[aria-label="Confirm Remove Job-specific step"]')!);
    await waitFor(() => expect(checklist.deleteJobItem).toHaveBeenCalledWith("JOB-A", 7));
    fireEvent.click(container.querySelector('[aria-label="Unhide Record crew progress"]')!);
    await waitFor(() => expect(checklist.unsuppressDefaultItem).toHaveBeenCalledWith("JOB-A", 3));
  });

  it("form_linked draft reveals the shared catalog form-code SELECT (names shown, codes submitted)", async () => {
    const { container, getByLabelText } = await openDetail(["cap.checklist.manage"]);
    await waitFor(() => expect(checklist.fetchJobChecklist).toHaveBeenCalled());
    fireEvent.change(getByLabelText("Add checklist item type"), { target: { value: "form_linked" } });
    fireEvent.change(getByLabelText("Add checklist item label"), { target: { value: "Attach JHA" } });
    // R4: the shared ChecklistItemForm renders form_code as a catalog select, not free text.
    const codeSel = getByLabelText("Add checklist item form code") as HTMLSelectElement;
    expect(codeSel.tagName).toBe("SELECT");
    const opts = Array.from(codeSel.options).map((o) => ({ v: o.value, t: o.textContent }));
    expect(opts).toEqual(expect.arrayContaining([expect.objectContaining({ v: "jha", t: "Job Hazard Analysis" })]));
    fireEvent.change(codeSel, { target: { value: "jha" } });
    fireEvent.submit(container.querySelector('[aria-label="Add checklist item"]')!);
    await waitFor(() =>
      expect(checklist.addJobItem).toHaveBeenCalledWith("JOB-A", expect.objectContaining({ item_type: "form_linked", label: "Attach JHA", form_code: "jha" })),
    );
  });
});
