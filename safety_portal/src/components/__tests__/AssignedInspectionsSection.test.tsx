/**
 * AssignedInspectionsSection (R2 extraction) — section-level detail tests: never-silent load
 * states, template_title heading, overdue treatment, humanized labels, completed collapse, and the
 * mutation/refetch try-split. Page-level integration lives in pages/__tests__/FieldOpsMyTasks.test.tsx.
 */
import { cleanup, fireEvent, render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../lib/fieldops_checklist", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../lib/fieldops_checklist")>();
  return { ...actual, fetchAssignedInspections: vi.fn(), completeChecklistItem: vi.fn(), uncompleteChecklistItem: vi.fn(), recordCountItem: vi.fn() };
});

import * as checklist from "../../lib/fieldops_checklist";
import { ApiError } from "../../lib/errorCopy";
import { AssignedInspectionsSection } from "../AssignedInspectionsSection";

afterEach(cleanup);
beforeEach(() => {
  vi.resetAllMocks();
});

const ITEM: checklist.ChecklistItemState = { id: 40, source_item_id: 1, item_type: "manual_attest", label: "Harness checked", form_code: null, target_count: null, status: "open", note: null, photo_ref: null, completed_by: null, completed_at: null, value_num: null, filed_by: null, photo_status: null, requires_photo: false };

function inspection(overrides: Partial<checklist.AssignedInstance> = {}, items: checklist.ChecklistItemState[] = [ITEM]): checklist.AssignedInspection {
  return {
    instance: { id: 30, job_id: "JOB-A", project_name: "Alpha", instance_date: "2099-07-10", status: "open", template_title: "Fall protection", created_at: 100, ...overrides },
    items,
  };
}

function respOk(inspections: checklist.AssignedInspection[]) {
  vi.mocked(checklist.fetchAssignedInspections).mockResolvedValue({ inspections, linked: true });
}

describe("AssignedInspectionsSection — load states (Mandatory B)", () => {
  it("shows a distinct loading state while the fetch is in flight", () => {
    vi.mocked(checklist.fetchAssignedInspections).mockReturnValue(new Promise(() => {}));
    const { container } = render(<AssignedInspectionsSection />);
    expect(container.textContent ?? "").toContain("Loading assigned inspections…");
  });

  it("a load failure shows the human error + a working Retry (previously an invisible section)", async () => {
    vi.mocked(checklist.fetchAssignedInspections)
      .mockRejectedValueOnce(new ApiError(null, 500))
      .mockResolvedValueOnce({ inspections: [inspection()], linked: true });
    const { container, getByLabelText } = render(<AssignedInspectionsSection />);
    await waitFor(() => expect(container.textContent ?? "").toContain("Something went wrong on the server"));
    fireEvent.click(getByLabelText("Retry loading assigned inspections"));
    await waitFor(() => expect(container.textContent ?? "").toContain("Fall protection"));
    expect(checklist.fetchAssignedInspections).toHaveBeenCalledTimes(2);
  });

  it("renders nothing on a CONFIRMED-empty response", async () => {
    respOk([]);
    const { container } = render(<AssignedInspectionsSection />);
    await waitFor(() => expect(checklist.fetchAssignedInspections).toHaveBeenCalled());
    await waitFor(() => expect(container.querySelector('[aria-label="Assigned inspections"]')).toBeNull());
    expect((container.textContent ?? "").trim()).toBe("");
  });
});

describe("AssignedInspectionsSection — headings + dates", () => {
  it("each inspection card is titled by template_title with #id demoted + humanized status", async () => {
    respOk([inspection()]);
    const { container } = render(<AssignedInspectionsSection />);
    await waitFor(() => expect(container.textContent ?? "").toContain("Fall protection"));
    const card = container.querySelector(".checklist-task-card")!;
    expect(card.querySelector(".dash-card__title")?.textContent).toBe("Fall protection");
    expect(card.textContent ?? "").toContain("#30"); // id demoted to the card sub-line
    expect(card.textContent ?? "").toContain("Open"); // labels.ts, not raw 'open'
    expect(card.textContent ?? "").toContain("due");
  });

  it("falls back to 'Inspection' when template_title is null (legacy instances)", async () => {
    respOk([inspection({ template_title: null })]);
    const { container } = render(<AssignedInspectionsSection />);
    await waitFor(() => expect(container.querySelector(".checklist-task-card")).not.toBeNull());
    expect(container.querySelector(".dash-card__title")!.textContent ?? "").toContain("Inspection");
  });

  it("an OPEN inspection past its due date gets an Overdue warn pill", async () => {
    respOk([inspection({ instance_date: "2020-01-01" })]);
    const { container } = render(<AssignedInspectionsSection />);
    await waitFor(() => expect(container.textContent ?? "").toContain("Overdue"));
    expect(container.querySelector(".dash-pill--warn")?.textContent).toBe("Overdue");
  });

  it("no Overdue pill when complete (even past due) or when due in the future", async () => {
    respOk([
      inspection({ id: 30, instance_date: "2020-01-01", status: "complete" }),
      inspection({ id: 31, instance_date: "2099-07-10", status: "open" }),
    ]);
    const { container } = render(<AssignedInspectionsSection />);
    await waitFor(() => expect(container.textContent ?? "").toContain("Fall protection"));
    expect(container.textContent ?? "").not.toContain("Overdue");
  });
});

describe("AssignedInspectionsSection — rows + try-split", () => {
  it("shows every item inline in an opened inspection — a done item keeps a visible Undo (toggle off)", async () => {
    respOk([
      inspection({}, [ITEM, { ...ITEM, id: 41, label: "Lanyard tagged", status: "done", completed_by: "sam", completed_at: 1 }]),
    ]);
    const { container, getByLabelText } = render(<AssignedInspectionsSection />);
    fireEvent.click(await waitFor(() => getByLabelText("Open Fall protection inspection")));
    // No collapse: both the open and the done item render in one list...
    await waitFor(() => expect(container.textContent ?? "").toContain("Lanyard tagged"));
    expect(container.querySelector("details.dash-completed")).toBeNull();
    expect(container.textContent ?? "").toContain("Harness checked");
    // ...and the done item exposes an Undo so the person can toggle it back open.
    expect(getByLabelText("Undo item 41")).not.toBeNull();
    expect(getByLabelText("Complete item 40")).not.toBeNull();
  });

  it("mutation success + refetch failure: success feedback, data kept, soft warn (never 'failed')", async () => {
    vi.mocked(checklist.fetchAssignedInspections)
      .mockResolvedValueOnce({ inspections: [inspection()], linked: true })
      .mockRejectedValue(new ApiError(null, 500)); // every refetch fails
    vi.mocked(checklist.completeChecklistItem).mockResolvedValue({ ok: true, id: 40, status: "done", instance_status: "complete" });
    const { getByLabelText, container } = render(<AssignedInspectionsSection />);
    fireEvent.click(await waitFor(() => getByLabelText("Open Fall protection inspection")));
    fireEvent.click(await waitFor(() => getByLabelText("Complete item 40")));
    await waitFor(() => expect(container.textContent ?? "").toContain("Inspection complete."));
    await waitFor(() => expect(container.textContent ?? "").toContain("Saved — but the list couldn't refresh"));
    expect(container.textContent ?? "").not.toContain("Update failed.");
    // The CompleteResult was applied locally: the item flipped to done (its Undo shows inline now) +
    // the instance reads complete.
    expect(getByLabelText("Undo item 40")).not.toBeNull();
    expect(container.textContent ?? "").toContain("Complete"); // humanized instance status
  });

  it("per-row busy: an in-flight completion disables only that row", async () => {
    respOk([inspection({}, [ITEM, { ...ITEM, id: 42, label: "Anchor point rated" }])]);
    vi.mocked(checklist.completeChecklistItem).mockReturnValue(new Promise(() => {})); // never settles
    const { getByLabelText } = render(<AssignedInspectionsSection />);
    fireEvent.click(await waitFor(() => getByLabelText("Open Fall protection inspection")));
    fireEvent.click(await waitFor(() => getByLabelText("Complete item 40")));
    await waitFor(() => expect((getByLabelText("Complete item 40") as HTMLButtonElement).disabled).toBe(true));
    expect((getByLabelText("Complete item 42") as HTMLButtonElement).disabled).toBe(false);
  });
});
