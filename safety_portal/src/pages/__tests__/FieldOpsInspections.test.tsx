/**
 * Assigned-Tasks tab (P4 field-ops feature) S6 — the admin Inspection-checklists library + assign UI.
 * Gated cap.checklist.manage. Mirrors FieldOpsChecklistEditor.test.tsx: mock the libs the page imports,
 * render, drive. Covers: the HomePage card gate, the library list/create, the item editor, and assign.
 */
import { cleanup, fireEvent, render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../lib/fieldops_checklist", () => ({
  fetchInspectionTemplates: vi.fn(),
  fetchInspectionTemplate: vi.fn(),
  createInspectionTemplate: vi.fn(),
  editInspectionTemplate: vi.fn(),
  deleteInspectionTemplate: vi.fn(),
  addInspectionItem: vi.fn(),
  editInspectionItem: vi.fn(),
  deleteInspectionItem: vi.fn(),
  assignInspection: vi.fn(),
}));
vi.mock("../../lib/fieldops_personnel", () => ({ fetchPersonnelList: vi.fn() }));
vi.mock("../../lib/fieldops_jobtracker", () => ({ fetchJobList: vi.fn() }));
vi.mock("../../lib/auth", () => ({ useAuth: vi.fn() }));

import * as checklist from "../../lib/fieldops_checklist";
import { fetchPersonnelList } from "../../lib/fieldops_personnel";
import { fetchJobList } from "../../lib/fieldops_jobtracker";
import { FieldOpsInspections } from "../FieldOpsInspections";
import { HomePage } from "../HomePage";
import { useAuth } from "../../lib/auth";

function authWith(capabilities: string[]) {
  return {
    user: { username: "admin", role: "admin" as const, capabilities },
    loading: false,
    login: vi.fn(),
    logout: vi.fn(),
  };
}

const TEMPLATES: checklist.InspectionTemplate[] = [
  { id: 1, title: "Fall protection", active: 1, created_at: 100, item_count: 2 },
  { id: 2, title: "Crane pre-lift", active: 1, created_at: 90, item_count: 0 },
];

afterEach(cleanup);
beforeEach(() => {
  vi.resetAllMocks();
  vi.mocked(useAuth).mockReturnValue(authWith(["cap.checklist.manage"]));
  vi.mocked(checklist.fetchInspectionTemplates).mockResolvedValue({ templates: TEMPLATES });
  vi.mocked(checklist.fetchInspectionTemplate).mockResolvedValue({
    template: { id: 1, title: "Fall protection", active: 1 },
    items: [
      { id: 11, seq: 10, item_type: "manual_attest", label: "Harness checked", form_code: null, target_count: null, config_json: null },
    ],
  });
  vi.mocked(fetchPersonnelList).mockResolvedValue({
    personnel: [
      { id: 5, name: "Sam Sub", trade: "Laborer", username: "sub.sam", current_job: "JOB-A" },
      { id: 6, name: "No Login", trade: "", username: null, current_job: null },
    ],
    latest_entries: [],
    next_cursor: null,
  });
  vi.mocked(fetchJobList).mockResolvedValue({ jobs: [{ job_id: "JOB-A", project_name: "Alpha", status: "active", progress: 0, client_name: null, crew: [], open_tasks: [] }], next_cursor: null });
});

describe("FieldOpsInspections — library", () => {
  it("lists the generic_inspection templates", async () => {
    const { container } = render(<FieldOpsInspections onBack={() => {}} />);
    await waitFor(() => expect(container.textContent ?? "").toContain("Fall protection"));
    expect(container.textContent ?? "").toContain("Crane pre-lift");
  });

  it("creating a template fires createInspectionTemplate(title)", async () => {
    vi.mocked(checklist.createInspectionTemplate).mockResolvedValue({ ok: true, id: 3 });
    const { getByLabelText } = render(<FieldOpsInspections onBack={() => {}} />);
    const input = await waitFor(() => getByLabelText("New checklist title") as HTMLInputElement);
    fireEvent.change(input, { target: { value: "Excavation" } });
    fireEvent.submit(input.closest("form")!);
    await waitFor(() => expect(checklist.createInspectionTemplate).toHaveBeenCalledWith("Excavation"));
  });

  it("selecting a template shows its item editor and add fires addInspectionItem", async () => {
    vi.mocked(checklist.addInspectionItem).mockResolvedValue({ ok: true, id: 12 });
    const { getByLabelText, container } = render(<FieldOpsInspections onBack={() => {}} />);
    const editBtn = await waitFor(() => getByLabelText("Edit Fall protection"));
    fireEvent.click(editBtn);
    await waitFor(() => expect(container.textContent ?? "").toContain("Harness checked"));
    const labelInput = getByLabelText("Add item label") as HTMLInputElement;
    fireEvent.change(labelInput, { target: { value: "Guardrails present" } });
    fireEvent.submit(labelInput.closest("form")!);
    await waitFor(() =>
      expect(checklist.addInspectionItem).toHaveBeenCalledWith(1, expect.objectContaining({ label: "Guardrails present", item_type: "manual_attest" })),
    );
  });
});

describe("FieldOpsInspections — assign", () => {
  it("assigns a template to a login-linked person (non-login people are not offered)", async () => {
    vi.mocked(checklist.assignInspection).mockResolvedValue({ ok: true, instance_id: 7, item_count: 2 });
    const { getByLabelText, container } = render(<FieldOpsInspections onBack={() => {}} />);
    await waitFor(() => expect(container.querySelector('[aria-label="Assign an inspection"]')).not.toBeNull());
    // The assignee select offers Sam (login) but not "No Login".
    const assignee = getByLabelText("Assignee") as HTMLSelectElement;
    const optTexts = Array.from(assignee.options).map((o) => o.textContent ?? "");
    expect(optTexts.some((t) => t.includes("Sam Sub"))).toBe(true);
    expect(optTexts.some((t) => t.includes("No Login"))).toBe(false);

    fireEvent.change(getByLabelText("Checklist"), { target: { value: "1" } });
    fireEvent.change(assignee, { target: { value: "5" } });
    fireEvent.submit(getByLabelText("Assign form"));
    await waitFor(() =>
      expect(checklist.assignInspection).toHaveBeenCalledWith({ template_id: 1, assignee_personnel_id: 5 }),
    );
  });

  it("includes job_id + due_date when chosen", async () => {
    vi.mocked(checklist.assignInspection).mockResolvedValue({ ok: true, instance_id: 8, item_count: 1 });
    const { getByLabelText } = render(<FieldOpsInspections onBack={() => {}} />);
    await waitFor(() => getByLabelText("Assign form"));
    fireEvent.change(getByLabelText("Checklist"), { target: { value: "1" } });
    fireEvent.change(getByLabelText("Assignee"), { target: { value: "5" } });
    fireEvent.change(getByLabelText("Job (optional)"), { target: { value: "JOB-A" } });
    fireEvent.change(getByLabelText("Due date (optional)"), { target: { value: "2026-07-10" } });
    fireEvent.submit(getByLabelText("Assign form"));
    await waitFor(() =>
      expect(checklist.assignInspection).toHaveBeenCalledWith({ template_id: 1, assignee_personnel_id: 5, job_id: "JOB-A", due_date: "2026-07-10" }),
    );
  });
});

describe("HomePage — Inspection checklists card gate", () => {
  it("renders the card for a holder of cap.checklist.manage", () => {
    vi.mocked(useAuth).mockReturnValue(authWith(["cap.checklist.manage"]));
    const { container } = render(<HomePage onNavigate={() => {}} />);
    expect(container.textContent ?? "").toContain("Inspection checklists");
  });

  it("hides the card without cap.checklist.manage", () => {
    vi.mocked(useAuth).mockReturnValue(authWith(["cap.tasks.own"]));
    const { container } = render(<HomePage onNavigate={() => {}} />);
    expect(container.textContent ?? "").not.toContain("Inspection checklists");
  });
});
