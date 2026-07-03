/**
 * JobDailyRequirementsSection (SOP daily form, slice D4) — the "Daily form — job requirements"
 * admin editor mounted on the Job Tracker job detail (cap.checklist.manage; the mount itself is
 * gated by the page — this file tests the section's own behavior):
 *   • lists the job's items with HUMAN kind labels (+ the catalog form name for form_link);
 *   • add flow: kind select with human labels; the catalog form picker appears ONLY for the
 *     form_link kind and EXCLUDES launch:"daily-tab" parents (the daily form can't link itself);
 *   • inline edit (full-payload replace) + reorder via seq re-writes through the edit route;
 *   • remove = ConfirmDelete-gated DEACTIVATE (no lib call until the explicit Confirm);
 *   • never-silent: load failure → SectionError + working Retry; action failure → inline banner.
 */
import { cleanup, fireEvent, render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../lib/fieldops_daily_requirements", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../lib/fieldops_daily_requirements")>();
  return {
    ...actual,
    fetchDailyRequirements: vi.fn(),
    addRequirement: vi.fn(),
    editRequirement: vi.fn(),
    deactivateRequirement: vi.fn(),
  };
});

import {
  addRequirement,
  deactivateRequirement,
  editRequirement,
  fetchDailyRequirements,
  type DailyRequirementItem,
} from "../../lib/fieldops_daily_requirements";
import { ApiError } from "../../lib/errorCopy";
import { JobDailyRequirementsSection } from "../JobDailyRequirementsSection";

const ITEMS: DailyRequirementItem[] = [
  { id: 1, seq: 10, kind: "confirm", label: "Badge in at the client gate", form_code: null },
  { id: 2, seq: 20, kind: "form_link", label: "File the client JHA", form_code: "jha" },
];

afterEach(cleanup);
beforeEach(() => {
  vi.resetAllMocks();
  vi.mocked(fetchDailyRequirements).mockResolvedValue(ITEMS);
  vi.mocked(addRequirement).mockResolvedValue({ ok: true, id: 9 });
  vi.mocked(editRequirement).mockResolvedValue({ ok: true, id: 1 });
  vi.mocked(deactivateRequirement).mockResolvedValue({ ok: true, id: 1 });
});

describe("list + kinds", () => {
  it("fetches the job's items and lists them with human kind labels (+ the catalog form name for a link)", async () => {
    const { container } = render(<JobDailyRequirementsSection jobId="JOB-A" />);
    await waitFor(() => expect(container.textContent ?? "").toContain("Badge in at the client gate"));
    expect(fetchDailyRequirements).toHaveBeenCalledWith("JOB-A");
    expect(container.textContent ?? "").toContain("Confirm");
    // form_link meta shows the catalog display NAME, not the raw code.
    expect(container.textContent ?? "").toContain("Form link · Job Hazard Analysis");
  });

  it("zero items → the explanatory empty state (never a lying blank)", async () => {
    vi.mocked(fetchDailyRequirements).mockResolvedValue([]);
    const { container } = render(<JobDailyRequirementsSection jobId="JOB-A" />);
    await waitFor(() => expect(container.textContent ?? "").toContain("No job-specific requirements yet."));
  });

  it("a load failure shows SectionError with a WORKING Retry", async () => {
    vi.mocked(fetchDailyRequirements)
      .mockRejectedValueOnce(new ApiError(null, 500))
      .mockResolvedValue(ITEMS);
    const { container, getByLabelText } = render(<JobDailyRequirementsSection jobId="JOB-A" />);
    await waitFor(() => expect(getByLabelText("Retry loading job requirements")).not.toBeNull());
    fireEvent.click(getByLabelText("Retry loading job requirements"));
    await waitFor(() => expect(container.textContent ?? "").toContain("Badge in at the client gate"));
  });
});

describe("add flow", () => {
  it("adds with the drafted kind/label and the auto-suggested seq (max+10)", async () => {
    const { getByLabelText } = render(<JobDailyRequirementsSection jobId="JOB-A" />);
    await waitFor(() => expect(getByLabelText("Add requirement label")).not.toBeNull());
    fireEvent.change(getByLabelText("Add requirement label"), { target: { value: "New client rule" } });
    fireEvent.change(getByLabelText("Add requirement kind"), { target: { value: "text" } });
    fireEvent.submit(getByLabelText("Add requirement"));
    await waitFor(() =>
      expect(addRequirement).toHaveBeenCalledWith("JOB-A", { kind: "text", label: "New client rule", seq: 30 }),
    );
    expect(vi.mocked(fetchDailyRequirements).mock.calls.length).toBeGreaterThanOrEqual(2); // reloaded
  });

  it("the catalog form picker appears ONLY for form_link and EXCLUDES daily-tab parents", async () => {
    const { getByLabelText, queryByLabelText } = render(<JobDailyRequirementsSection jobId="JOB-A" />);
    await waitFor(() => expect(getByLabelText("Add requirement kind")).not.toBeNull());
    expect(queryByLabelText("Add requirement form code")).toBeNull(); // note (default) → no picker
    fireEvent.change(getByLabelText("Add requirement kind"), { target: { value: "form_link" } });
    const picker = getByLabelText("Add requirement form code") as HTMLSelectElement;
    const names = Array.from(picker.options).map((o) => o.textContent);
    expect(names).toContain("Job Hazard Analysis");
    // The daily-report parent is launch:"daily-tab" — a circular self-link is not offered.
    expect(names).not.toContain("Daily Field Report");
    fireEvent.change(getByLabelText("Add requirement label"), { target: { value: "File the client JHA" } });
    fireEvent.change(picker, { target: { value: "jha" } });
    fireEvent.submit(getByLabelText("Add requirement"));
    await waitFor(() =>
      expect(addRequirement).toHaveBeenCalledWith("JOB-A", {
        kind: "form_link", label: "File the client JHA", form_code: "jha", seq: 30,
      }),
    );
  });

  it("an add failure lands in the inline banner with the controls re-enabled (never silent)", async () => {
    vi.mocked(addRequirement).mockRejectedValue(new ApiError(null, 500));
    const { container, getByLabelText } = render(<JobDailyRequirementsSection jobId="JOB-A" />);
    await waitFor(() => expect(getByLabelText("Add requirement label")).not.toBeNull());
    fireEvent.change(getByLabelText("Add requirement label"), { target: { value: "x" } });
    fireEvent.submit(getByLabelText("Add requirement"));
    await waitFor(() => expect(container.querySelector(".banner--err")).not.toBeNull());
    expect((container.querySelector('form[aria-label="Add requirement"] button[type="submit"]') as HTMLButtonElement).disabled).toBe(false);
  });
});

describe("edit + reorder", () => {
  it("Edit opens the inline form prefilled and saves the FULL replace payload", async () => {
    const { getByLabelText } = render(<JobDailyRequirementsSection jobId="JOB-A" />);
    await waitFor(() => expect(getByLabelText("Edit requirement Badge in at the client gate")).not.toBeNull());
    fireEvent.click(getByLabelText("Edit requirement Badge in at the client gate"));
    const label = getByLabelText("Edit requirement label") as HTMLInputElement;
    expect(label.value).toBe("Badge in at the client gate"); // prefilled from the row
    fireEvent.change(label, { target: { value: "Badge in at gate 2" } });
    fireEvent.submit(getByLabelText("Edit requirement"));
    await waitFor(() =>
      expect(editRequirement).toHaveBeenCalledWith("JOB-A", 1, {
        kind: "confirm", label: "Badge in at gate 2", seq: 10,
      }),
    );
  });

  it("reorder = seq re-writes via the edit route for BOTH swapped rows (10/20 → 20/10 renumbered)", async () => {
    const { getByLabelText } = render(<JobDailyRequirementsSection jobId="JOB-A" />);
    await waitFor(() => expect(getByLabelText("Move requirement File the client JHA up")).not.toBeNull());
    fireEvent.click(getByLabelText("Move requirement File the client JHA up"));
    await waitFor(() => expect(editRequirement).toHaveBeenCalledTimes(2));
    expect(editRequirement).toHaveBeenCalledWith("JOB-A", 2, {
      kind: "form_link", label: "File the client JHA", form_code: "jha", seq: 10,
    });
    expect(editRequirement).toHaveBeenCalledWith("JOB-A", 1, {
      kind: "confirm", label: "Badge in at the client gate", seq: 20,
    });
  });

  it("the end rows' out-of-range moves are disabled (no dead writes)", async () => {
    const { getByLabelText } = render(<JobDailyRequirementsSection jobId="JOB-A" />);
    await waitFor(() => expect(getByLabelText("Move requirement Badge in at the client gate up")).not.toBeNull());
    expect((getByLabelText("Move requirement Badge in at the client gate up") as HTMLButtonElement).disabled).toBe(true);
    expect((getByLabelText("Move requirement File the client JHA down") as HTMLButtonElement).disabled).toBe(true);
  });
});

describe("ConfirmDelete-gated deactivate", () => {
  it("no lib call fires until the explicit Confirm; Cancel leaves everything untouched", async () => {
    const { getByLabelText, queryByLabelText } = render(<JobDailyRequirementsSection jobId="JOB-A" />);
    await waitFor(() => expect(getByLabelText("Remove requirement Badge in at the client gate")).not.toBeNull());
    fireEvent.click(getByLabelText("Remove requirement Badge in at the client gate"));
    expect(deactivateRequirement).not.toHaveBeenCalled(); // first tap only opens the confirm
    fireEvent.click(getByLabelText("Cancel Remove requirement Badge in at the client gate"));
    expect(deactivateRequirement).not.toHaveBeenCalled();
    fireEvent.click(getByLabelText("Remove requirement Badge in at the client gate"));
    fireEvent.click(getByLabelText("Confirm Remove requirement Badge in at the client gate"));
    await waitFor(() => expect(deactivateRequirement).toHaveBeenCalledWith("JOB-A", 1));
    expect(queryByLabelText("Confirm Remove requirement Badge in at the client gate")).toBeNull();
  });

  it("the confirm copy names the blast radius (already-filed reports keep their answers)", async () => {
    const { getByLabelText, container } = render(<JobDailyRequirementsSection jobId="JOB-A" />);
    await waitFor(() => expect(getByLabelText("Remove requirement Badge in at the client gate")).not.toBeNull());
    fireEvent.click(getByLabelText("Remove requirement Badge in at the client gate"));
    expect(container.textContent ?? "").toContain("Already-filed reports keep their answers.");
  });
});
