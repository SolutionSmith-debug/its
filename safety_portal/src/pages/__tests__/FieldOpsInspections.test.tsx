/**
 * R4 — the consolidated admin "Checklists" page (FieldOpsInspections, view key unchanged).
 * Gated cap.checklist.manage. Mirrors FieldOpsChecklistEditor.test.tsx: mock the libs the page
 * imports, render, drive. Covers: both areas render; loading distinct from empty; default-checklist
 * CRUD (add with auto-seq, prefilled inline edit, reorder, confirm-gated delete); the catalog-driven
 * form_code select (names shown, codes submitted); library rename / deactivate / confirm-gated
 * delete; the per-template item editor (add/edit/reorder/remove) + assignee preview; assign; and
 * the HomePage card gate (HomePage untouched by R4 — R7 owns its copy).
 */
import { cleanup, fireEvent, render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../lib/fieldops_checklist", () => ({
  fetchDefaultChecklist: vi.fn(),
  addDefaultItem: vi.fn(),
  editDefaultItem: vi.fn(),
  deleteDefaultItem: vi.fn(),
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

// Crane pre-lift is INACTIVE — drives the badge + assign-picker-exclusion assertions.
const TEMPLATES: checklist.InspectionTemplate[] = [
  { id: 1, title: "Fall protection", active: 1, created_at: 100, item_count: 2 },
  { id: 2, title: "Crane pre-lift", active: 0, created_at: 90, item_count: 0 },
];

const TEMPLATE_ITEMS: checklist.DefaultItem[] = [
  { id: 11, seq: 10, item_type: "manual_attest", label: "Harness checked", form_code: null, target_count: null, config_json: null },
  { id: 12, seq: 20, item_type: "count", label: "Anchor points", form_code: null, target_count: 4, config_json: null },
];

const DEFAULT_CHECKLIST: checklist.DefaultChecklist = {
  template: { id: 1, kind: "daily_default", title: "Daily default", source_form_code: null, active: 1 },
  items: [
    { id: 21, seq: 10, item_type: "form_linked", label: "File the Daily Field Report", form_code: "daily-report", target_count: null, config_json: null },
    { id: 22, seq: 20, item_type: "manual_attest", label: "Walk the site", form_code: null, target_count: null, config_json: null },
  ],
};

afterEach(cleanup);
beforeEach(() => {
  vi.resetAllMocks();
  vi.mocked(useAuth).mockReturnValue(authWith(["cap.checklist.manage"]));
  vi.mocked(checklist.fetchDefaultChecklist).mockResolvedValue(DEFAULT_CHECKLIST);
  vi.mocked(checklist.fetchInspectionTemplates).mockResolvedValue({ templates: TEMPLATES });
  vi.mocked(checklist.fetchInspectionTemplate).mockResolvedValue({
    template: { id: 1, title: "Fall protection", active: 1 },
    items: TEMPLATE_ITEMS,
  });
  vi.mocked(checklist.editDefaultItem).mockResolvedValue({ ok: true, id: 22 });
  vi.mocked(checklist.editInspectionItem).mockResolvedValue({ ok: true, id: 11 });
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

describe("FieldOpsInspections — consolidated Checklists page", () => {
  it("renders BOTH areas under one heading: the default daily checklist and the inspection library", async () => {
    const { container } = render(<FieldOpsInspections onBack={() => {}} />);
    expect(container.textContent ?? "").toContain("Checklists");
    await waitFor(() => expect(container.textContent ?? "").toContain("Default daily checklist"));
    expect(container.querySelector('[aria-label="Default daily checklist"]')).not.toBeNull();
    expect(container.querySelector('[aria-label="Inspection library"]')).not.toBeNull();
    expect(container.textContent ?? "").toContain("File the Daily Field Report");
    expect(container.textContent ?? "").toContain("Walk the site");
    expect(container.textContent ?? "").toContain("Fall protection");
    expect(container.textContent ?? "").toContain("Crane pre-lift");
    // The "take effect tomorrow" snapshot copy ships in the default area.
    expect(container.textContent ?? "").toContain("take effect");
    expect(container.textContent ?? "").toContain("tomorrow");
  });

  it("renders loading states distinct from empty (no 'No … yet' flash while fetches are pending)", () => {
    vi.mocked(checklist.fetchDefaultChecklist).mockReturnValue(new Promise(() => {}));
    vi.mocked(checklist.fetchInspectionTemplates).mockReturnValue(new Promise(() => {}));
    const { container } = render(<FieldOpsInspections onBack={() => {}} />);
    expect(container.textContent ?? "").toContain("Loading default checklist");
    expect(container.textContent ?? "").toContain("Loading inspection checklists");
    expect(container.textContent ?? "").not.toContain("No default items yet");
    expect(container.textContent ?? "").not.toContain("No inspection checklists yet");
  });
});

describe("FieldOpsInspections — default daily checklist CRUD", () => {
  it("adding a default item auto-suggests seq = max+10 and fires addDefaultItem", async () => {
    vi.mocked(checklist.addDefaultItem).mockResolvedValue({ ok: true, id: 23 });
    const { getByLabelText } = render(<FieldOpsInspections onBack={() => {}} />);
    const input = await waitFor(() => getByLabelText("Add default item label") as HTMLInputElement);
    fireEvent.change(input, { target: { value: "Check the gate" } });
    fireEvent.submit(input.closest("form")!);
    await waitFor(() =>
      expect(checklist.addDefaultItem).toHaveBeenCalledWith(
        expect.objectContaining({ item_type: "manual_attest", label: "Check the gate", seq: 30 }),
      ),
    );
  });

  it("Edit opens a PREFILLED form (carrying the row's own seq) and Save fires editDefaultItem", async () => {
    const { getByLabelText } = render(<FieldOpsInspections onBack={() => {}} />);
    const editBtn = await waitFor(() => getByLabelText("Edit Walk the site"));
    fireEvent.click(editBtn);
    const labelInput = getByLabelText("Edit default item label") as HTMLInputElement;
    expect(labelInput.value).toBe("Walk the site"); // prefilled — a typo is fixable without re-typing
    fireEvent.change(labelInput, { target: { value: "Walk the whole site" } });
    fireEvent.submit(labelInput.closest("form")!);
    await waitFor(() =>
      expect(checklist.editDefaultItem).toHaveBeenCalledWith(
        22,
        expect.objectContaining({ label: "Walk the whole site", item_type: "manual_attest", seq: 20 }),
      ),
    );
  });

  it("cancelling an edit closes the form without any lib call", async () => {
    const { getByLabelText, queryByLabelText } = render(<FieldOpsInspections onBack={() => {}} />);
    fireEvent.click(await waitFor(() => getByLabelText("Edit Walk the site")));
    fireEvent.click(getByLabelText("Edit default item cancel"));
    expect(queryByLabelText("Edit default item label")).toBeNull();
    expect(checklist.editDefaultItem).not.toHaveBeenCalled();
  });

  it("Move up swaps the two rows' seq via editDefaultItem (existing edit route — no new routes)", async () => {
    const { getByLabelText } = render(<FieldOpsInspections onBack={() => {}} />);
    const upBtn = await waitFor(() => getByLabelText("Move Walk the site up"));
    fireEvent.click(upBtn);
    await waitFor(() => expect(checklist.editDefaultItem).toHaveBeenCalledTimes(2));
    expect(checklist.editDefaultItem).toHaveBeenCalledWith(22, expect.objectContaining({ seq: 10 }));
    expect(checklist.editDefaultItem).toHaveBeenCalledWith(21, expect.objectContaining({ seq: 20 }));
  });

  it("first/last rows cannot move off the ends", async () => {
    const { getByLabelText } = render(<FieldOpsInspections onBack={() => {}} />);
    const up = await waitFor(() => getByLabelText("Move File the Daily Field Report up") as HTMLButtonElement);
    const down = getByLabelText("Move Walk the site down") as HTMLButtonElement;
    expect(up.disabled).toBe(true);
    expect(down.disabled).toBe(true);
  });

  it("delete is confirm-gated with EVERY-job blast-radius copy; cancel leaves the data untouched", async () => {
    vi.mocked(checklist.deleteDefaultItem).mockResolvedValue({ ok: true, id: 22 });
    const { container, getByLabelText } = render(<FieldOpsInspections onBack={() => {}} />);
    const del = await waitFor(() => getByLabelText("Delete default Walk the site"));
    fireEvent.click(del);
    expect(container.textContent ?? "").toContain("EVERY job");
    // Cancel path — nothing fires.
    fireEvent.click(getByLabelText("Cancel Delete default Walk the site"));
    expect(checklist.deleteDefaultItem).not.toHaveBeenCalled();
    // Confirm path.
    fireEvent.click(getByLabelText("Delete default Walk the site"));
    fireEvent.click(getByLabelText("Confirm Delete default Walk the site"));
    await waitFor(() => expect(checklist.deleteDefaultItem).toHaveBeenCalledWith(22));
  });

  it("form_code is a catalog select — names shown, codes submitted", async () => {
    vi.mocked(checklist.addDefaultItem).mockResolvedValue({ ok: true, id: 24 });
    const { getByLabelText } = render(<FieldOpsInspections onBack={() => {}} />);
    const typeSel = await waitFor(() => getByLabelText("Add default item type") as HTMLSelectElement);
    // Human type labels, raw keys as values.
    const typeTexts = Array.from(typeSel.options).map((o) => o.textContent);
    expect(typeTexts).toEqual(expect.arrayContaining(["Check", "Count", "Form", "Inspection"]));
    fireEvent.change(typeSel, { target: { value: "form_linked" } });
    const codeSel = getByLabelText("Add default item form code") as HTMLSelectElement;
    const opts = Array.from(codeSel.options).map((o) => ({ v: o.value, t: o.textContent }));
    expect(opts).toEqual(expect.arrayContaining([expect.objectContaining({ v: "jha", t: "Job Hazard Analysis" })]));
    // No free-text form code anywhere — the select's values are real catalog parents only.
    fireEvent.change(codeSel, { target: { value: "jha" } });
    fireEvent.change(getByLabelText("Add default item label"), { target: { value: "Attach the JHA" } });
    fireEvent.submit(codeSel.closest("form")!);
    await waitFor(() =>
      expect(checklist.addDefaultItem).toHaveBeenCalledWith(
        expect.objectContaining({ item_type: "form_linked", label: "Attach the JHA", form_code: "jha" }),
      ),
    );
  });
});

describe("FieldOpsInspections — inspection library lifecycle", () => {
  it("creating a template fires createInspectionTemplate(title)", async () => {
    vi.mocked(checklist.createInspectionTemplate).mockResolvedValue({ ok: true, id: 3 });
    const { getByLabelText } = render(<FieldOpsInspections onBack={() => {}} />);
    const input = await waitFor(() => getByLabelText("New checklist title") as HTMLInputElement);
    fireEvent.change(input, { target: { value: "Excavation" } });
    fireEvent.submit(input.closest("form")!);
    await waitFor(() => expect(checklist.createInspectionTemplate).toHaveBeenCalledWith("Excavation"));
  });

  it("rename is inline: prefilled input, Save fires editInspectionTemplate(id, { title })", async () => {
    vi.mocked(checklist.editInspectionTemplate).mockResolvedValue({ ok: true, id: 1 });
    const { getByLabelText } = render(<FieldOpsInspections onBack={() => {}} />);
    fireEvent.click(await waitFor(() => getByLabelText("Rename Fall protection")));
    const input = getByLabelText("Rename Fall protection title") as HTMLInputElement;
    expect(input.value).toBe("Fall protection");
    fireEvent.change(input, { target: { value: "Fall safety" } });
    fireEvent.submit(input.closest("form")!);
    await waitFor(() => expect(checklist.editInspectionTemplate).toHaveBeenCalledWith(1, { title: "Fall safety" }));
  });

  it("Deactivate/Reactivate use the edit route's active flag; inactive shows the not-assignable badge", async () => {
    vi.mocked(checklist.editInspectionTemplate).mockResolvedValue({ ok: true, id: 1 });
    const { container, getByLabelText } = render(<FieldOpsInspections onBack={() => {}} />);
    // The inactive template carries the badge.
    await waitFor(() => expect(container.textContent ?? "").toContain("inactive — not assignable"));
    fireEvent.click(getByLabelText("Deactivate Fall protection"));
    await waitFor(() =>
      expect(checklist.editInspectionTemplate).toHaveBeenCalledWith(1, { title: "Fall protection", active: false }),
    );
    fireEvent.click(getByLabelText("Reactivate Crane pre-lift"));
    await waitFor(() =>
      expect(checklist.editInspectionTemplate).toHaveBeenCalledWith(2, { title: "Crane pre-lift", active: true }),
    );
  });

  it("the assign picker excludes inactive templates", async () => {
    const { getByLabelText } = render(<FieldOpsInspections onBack={() => {}} />);
    const sel = await waitFor(() => getByLabelText("Checklist") as HTMLSelectElement);
    const texts = Array.from(sel.options).map((o) => o.textContent ?? "");
    expect(texts.some((t) => t.includes("Fall protection"))).toBe(true);
    expect(texts.some((t) => t.includes("Crane pre-lift"))).toBe(false);
  });

  it("template delete is confirm-gated with item-count + snapshot copy, offering Deactivate first; cancel is a no-op", async () => {
    vi.mocked(checklist.deleteInspectionTemplate).mockResolvedValue({ ok: true, id: 1 });
    const { container, getByLabelText } = render(<FieldOpsInspections onBack={() => {}} />);
    fireEvent.click(await waitFor(() => getByLabelText("Delete Fall protection")));
    const text = container.textContent ?? "";
    expect(text).toContain("2 items");
    expect(text).toContain("keep their snapshot");
    expect(text).toContain("Deactivate"); // the reversible choice is named first
    fireEvent.click(getByLabelText("Cancel Delete Fall protection"));
    expect(checklist.deleteInspectionTemplate).not.toHaveBeenCalled();
    fireEvent.click(getByLabelText("Delete Fall protection"));
    fireEvent.click(getByLabelText("Confirm Delete Fall protection"));
    await waitFor(() => expect(checklist.deleteInspectionTemplate).toHaveBeenCalledWith(1));
  });
});

describe("FieldOpsInspections — template item editor", () => {
  async function openEditor() {
    const utils = render(<FieldOpsInspections onBack={() => {}} />);
    fireEvent.click(await waitFor(() => utils.getByLabelText("Edit Fall protection")));
    await waitFor(() => expect(utils.container.textContent ?? "").toContain("Harness checked"));
    return utils;
  }

  it("add fires addInspectionItem with auto-suggested seq", async () => {
    vi.mocked(checklist.addInspectionItem).mockResolvedValue({ ok: true, id: 13 });
    const { getByLabelText } = await openEditor();
    fireEvent.change(getByLabelText("Add item label"), { target: { value: "Guardrails present" } });
    fireEvent.submit(getByLabelText("Add item"));
    await waitFor(() =>
      expect(checklist.addInspectionItem).toHaveBeenCalledWith(
        1,
        expect.objectContaining({ label: "Guardrails present", item_type: "manual_attest", seq: 30 }),
      ),
    );
  });

  it("Edit opens a prefilled form and Save fires editInspectionItem (count target carried over)", async () => {
    const { getByLabelText } = await openEditor();
    fireEvent.click(getByLabelText("Edit Anchor points"));
    const labelInput = getByLabelText("Edit item label") as HTMLInputElement;
    expect(labelInput.value).toBe("Anchor points");
    expect((getByLabelText("Edit item target count") as HTMLInputElement).value).toBe("4");
    fireEvent.change(labelInput, { target: { value: "Anchor points verified" } });
    fireEvent.submit(labelInput.closest("form")!);
    await waitFor(() =>
      expect(checklist.editInspectionItem).toHaveBeenCalledWith(
        1,
        12,
        expect.objectContaining({ label: "Anchor points verified", item_type: "count", target_count: 4, seq: 20 }),
      ),
    );
  });

  it("Move down swaps seq via editInspectionItem", async () => {
    const { getByLabelText } = await openEditor();
    fireEvent.click(getByLabelText("Move Harness checked down"));
    await waitFor(() => expect(checklist.editInspectionItem).toHaveBeenCalledTimes(2));
    expect(checklist.editInspectionItem).toHaveBeenCalledWith(1, 11, expect.objectContaining({ seq: 20 }));
    expect(checklist.editInspectionItem).toHaveBeenCalledWith(1, 12, expect.objectContaining({ seq: 10 }));
  });

  it("item removal is confirm-gated; cancel leaves it", async () => {
    vi.mocked(checklist.deleteInspectionItem).mockResolvedValue({ ok: true, id: 11 });
    const { getByLabelText } = await openEditor();
    fireEvent.click(getByLabelText("Remove Harness checked"));
    fireEvent.click(getByLabelText("Cancel Remove Harness checked"));
    expect(checklist.deleteInspectionItem).not.toHaveBeenCalled();
    fireEvent.click(getByLabelText("Remove Harness checked"));
    fireEvent.click(getByLabelText("Confirm Remove Harness checked"));
    await waitFor(() => expect(checklist.deleteInspectionItem).toHaveBeenCalledWith(1, 11));
  });

  it("an empty template shows the 'now add its items below' nudge", async () => {
    vi.mocked(checklist.fetchInspectionTemplate).mockResolvedValue({
      template: { id: 2, title: "Crane pre-lift", active: 0 },
      items: [],
    });
    const { container, getByLabelText } = render(<FieldOpsInspections onBack={() => {}} />);
    fireEvent.click(await waitFor(() => getByLabelText("Edit Crane pre-lift")));
    await waitFor(() => expect(container.textContent ?? "").toContain("now add its items below"));
  });

  it("Preview as assignee renders the items read-only through the assignee row component", async () => {
    const { container, getByLabelText } = await openEditor();
    fireEvent.click(getByLabelText("Preview as assignee"));
    await waitFor(() => expect(container.textContent ?? "").toContain("Read-only preview"));
    // Faithful: both items render through the assignee row component, every control disabled.
    // (Deliberately loose about ChecklistItemRow internals — R3 owns that component.)
    const preview = container.querySelector('[aria-label="Assignee preview"]')!;
    expect(preview.textContent).toContain("Harness checked");
    expect(preview.textContent).toContain("Anchor points");
    const buttons = Array.from(preview.querySelectorAll("button")) as HTMLButtonElement[];
    expect(buttons.length).toBeGreaterThan(0);
    expect(buttons.every((b) => b.disabled)).toBe(true);
    // Toggle back to editing.
    fireEvent.click(getByLabelText("Preview as assignee"));
    await waitFor(() => expect(getByLabelText("Add item label")).not.toBeNull());
  });
});

describe("FieldOpsInspections — assign", () => {
  it("assigns a template to a login-linked person (non-login people are not offered) with visible labels", async () => {
    vi.mocked(checklist.assignInspection).mockResolvedValue({ ok: true, instance_id: 7, item_count: 2 });
    const { getByLabelText, container } = render(<FieldOpsInspections onBack={() => {}} />);
    await waitFor(() => expect(container.querySelector('[aria-label="Assign an inspection"]')).not.toBeNull());
    // Stacked visible labels on the assign fields.
    const labels = Array.from(container.querySelectorAll(".field__label")).map((el) => el.textContent);
    expect(labels).toEqual(expect.arrayContaining(["Checklist", "Assign to", "Job (optional)", "Due date (optional)"]));
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
