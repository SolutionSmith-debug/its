/**
 * AddCrewSection (R2 extraction) — section-level detail tests: pre-submit placement line,
 * crew-list load states (auxiliary — never blocks the form), duplicate-name warn, and the
 * crew-list refresh after a create. The cap gate + not_placed error path live in
 * pages/__tests__/FieldOpsMyTasks.test.tsx.
 */
import { cleanup, fireEvent, render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../lib/fieldops_personnel", () => ({
  createCrew: vi.fn(),
  fetchMyCrew: vi.fn(),
  // G2.3 — scoped crew edit/retire
  updateCrew: vi.fn(),
  retireCrew: vi.fn(),
}));

import * as personnel from "../../lib/fieldops_personnel";
import { AddCrewSection } from "../AddCrewSection";

afterEach(cleanup);
beforeEach(() => {
  vi.resetAllMocks();
  vi.mocked(personnel.fetchMyCrew).mockResolvedValue([]);
});

const CREW: personnel.MyCrewMember[] = [
  { id: 1, name: "Self Sub", trade: "electrical", current_job: "JOB-X" },
  { id: 2, name: "Helper Hank", trade: null, current_job: "JOB-X" },
];

describe("AddCrewSection — placement line (precondition BEFORE submit)", () => {
  it("prefers the placement hint props (project name) when provided", async () => {
    const { container } = render(<AddCrewSection placementJob="JOB-A" placementProject="Alpha" />);
    await waitFor(() => expect(container.textContent ?? "").toContain("You're placed on Alpha — new crew will be placed there too."));
  });

  it("falls back to the crew lib's current_job when no hint is given", async () => {
    vi.mocked(personnel.fetchMyCrew).mockResolvedValue(CREW);
    const { container } = render(<AddCrewSection />);
    await waitFor(() => expect(container.textContent ?? "").toContain("You're placed on JOB-X — new crew will be placed there too."));
  });

  it("explains the placement precondition when the loaded crew shows no placement", async () => {
    vi.mocked(personnel.fetchMyCrew).mockResolvedValue([{ id: 1, name: "Self Sub", trade: null, current_job: null }]);
    const { container } = render(<AddCrewSection />);
    await waitFor(() => expect(container.textContent ?? "").toContain("don't appear to be placed on a job yet"));
  });
});

describe("AddCrewSection — crew list (auxiliary fetch, Mandatory B)", () => {
  it("renders the fetched crew list (G2.3: as rows, still under the 'Your crew:' heading)", async () => {
    vi.mocked(personnel.fetchMyCrew).mockResolvedValue(CREW);
    const { container } = render(<AddCrewSection />);
    await waitFor(() => expect(container.textContent ?? "").toContain("Your crew:"));
    expect(container.textContent ?? "").toContain("Self Sub (electrical)");
    expect(container.textContent ?? "").toContain("Helper Hank");
  });

  it("a crew fetch failure warns with a working Retry and keeps the form usable", async () => {
    vi.mocked(personnel.fetchMyCrew)
      .mockRejectedValueOnce(new Error("Could not load your crew."))
      .mockResolvedValueOnce(CREW);
    const { container, getByLabelText } = render(<AddCrewSection />);
    await waitFor(() => expect(container.textContent ?? "").toContain("Could not load your crew."));
    // The add form is still present and usable.
    expect(getByLabelText("Add crew form")).not.toBeNull();
    fireEvent.click(getByLabelText("Retry loading your crew"));
    await waitFor(() => expect(container.textContent ?? "").toContain("Your crew:"));
    expect(container.textContent ?? "").toContain("Self Sub (electrical)");
  });
});

describe("AddCrewSection — duplicate-name warn + create", () => {
  it("warns (does not block) when the typed name matches an existing crew member, case-insensitively", async () => {
    vi.mocked(personnel.fetchMyCrew).mockResolvedValue(CREW);
    const { container, getByLabelText } = render(<AddCrewSection />);
    await waitFor(() => expect(container.textContent ?? "").toContain("Your crew:"));
    const form = getByLabelText("Add crew form") as HTMLFormElement;
    fireEvent.change(form.querySelector('input[placeholder="Name"]')!, { target: { value: "  helper hank " } });
    await waitFor(() => expect(container.textContent ?? "").toContain('named "Helper Hank"'));
    // Warn, not block: submitting still calls createCrew.
    vi.mocked(personnel.createCrew).mockResolvedValue({ id: 9, current_job: "JOB-X" });
    fireEvent.submit(form);
    await waitFor(() => expect(personnel.createCrew).toHaveBeenCalledWith({ name: "helper hank", trade: undefined }));
  });

  it("after a create: shows the created person and refreshes the crew list", async () => {
    vi.mocked(personnel.fetchMyCrew)
      .mockResolvedValueOnce(CREW)
      .mockResolvedValueOnce([...CREW, { id: 9, name: "New Nick", trade: null, current_job: "JOB-X" }]);
    vi.mocked(personnel.createCrew).mockResolvedValue({ id: 9, current_job: "JOB-X" });
    const { container, getByLabelText } = render(<AddCrewSection />);
    await waitFor(() => expect(container.textContent ?? "").toContain("Your crew:"));
    const form = getByLabelText("Add crew form") as HTMLFormElement;
    fireEvent.change(form.querySelector('input[placeholder="Name"]')!, { target: { value: "New Nick" } });
    fireEvent.submit(form);
    await waitFor(() => expect(container.textContent ?? "").toContain("Added New Nick to your crew on JOB-X."));
    await waitFor(() => expect(personnel.fetchMyCrew).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(container.textContent ?? "").toContain("New Nick"));
  });

  it("an empty name never posts", async () => {
    const { container, getByLabelText } = render(<AddCrewSection />);
    const form = await waitFor(() => getByLabelText("Add crew form") as HTMLFormElement);
    fireEvent.submit(form);
    await waitFor(() => expect(container.textContent ?? "").toContain("Enter a name."));
    expect(personnel.createCrew).not.toHaveBeenCalled();
  });
});

// ── G2.3 — scoped crew Edit/Retire (gated on created_by_me; the Worker re-gates) ──────────────────
const G23_CREW: personnel.MyCrewMember[] = [
  { id: 1, name: "Self Sub", trade: "electrical", current_job: "JOB-X", created_by_me: 0 },
  { id: 2, name: "Tpyo Guy", trade: "labor", current_job: "JOB-X", created_by_me: 1 },
];

describe("AddCrewSection — G2.3 scoped edit/retire", () => {
  it("Edit/Retire render ONLY on created_by_me rows (the actor's own linked row gets none)", async () => {
    vi.mocked(personnel.fetchMyCrew).mockResolvedValue(G23_CREW);
    const { container, queryByLabelText } = render(<AddCrewSection />);
    await waitFor(() => expect(container.textContent ?? "").toContain("Tpyo Guy"));
    expect(queryByLabelText("Edit Tpyo Guy")).not.toBeNull();
    expect(queryByLabelText("Retire Tpyo Guy")).not.toBeNull();
    expect(queryByLabelText("Edit Self Sub")).toBeNull();
    expect(queryByLabelText("Retire Self Sub")).toBeNull();
  });

  it("Edit opens the prefilled mini-form; Save calls updateCrew and refreshes the list", async () => {
    vi.mocked(personnel.fetchMyCrew)
      .mockResolvedValueOnce(G23_CREW)
      .mockResolvedValueOnce([G23_CREW[0], { ...G23_CREW[1], name: "Typo Guy", trade: "laborer" }]);
    vi.mocked(personnel.updateCrew).mockResolvedValue(undefined);
    const { container, getByLabelText } = render(<AddCrewSection />);
    await waitFor(() => expect(container.textContent ?? "").toContain("Tpyo Guy"));
    fireEvent.click(getByLabelText("Edit Tpyo Guy"));
    const nameInput = getByLabelText("Edit name for Tpyo Guy") as HTMLInputElement;
    const tradeInput = getByLabelText("Edit trade for Tpyo Guy") as HTMLInputElement;
    expect(nameInput.value).toBe("Tpyo Guy"); // prefilled
    expect(tradeInput.value).toBe("labor");
    fireEvent.change(nameInput, { target: { value: "Typo Guy" } });
    fireEvent.change(tradeInput, { target: { value: "laborer" } });
    fireEvent.click(Array.from(container.querySelectorAll("button")).find((b) => b.textContent === "Save")!);
    await waitFor(() => expect(personnel.updateCrew).toHaveBeenCalledWith(2, { name: "Typo Guy", trade: "laborer" }));
    await waitFor(() => expect(container.textContent ?? "").toContain("Typo Guy (laborer)")); // refreshed
  });

  it("an edit failure surfaces the error copy inline (never silent) and keeps the form open", async () => {
    vi.mocked(personnel.fetchMyCrew).mockResolvedValue(G23_CREW);
    vi.mocked(personnel.updateCrew).mockRejectedValue(new Error("That item no longer exists — refresh and try again."));
    const { container, getByLabelText } = render(<AddCrewSection />);
    await waitFor(() => expect(container.textContent ?? "").toContain("Tpyo Guy"));
    fireEvent.click(getByLabelText("Edit Tpyo Guy"));
    fireEvent.click(Array.from(container.querySelectorAll("button")).find((b) => b.textContent === "Save")!);
    await waitFor(() => expect(container.textContent ?? "").toContain("That item no longer exists"));
    expect(getByLabelText("Edit name for Tpyo Guy")).not.toBeNull(); // still editing
  });

  it("Retire confirm-gates, calls retireCrew, and refreshes; a cancelled confirm never posts", async () => {
    vi.mocked(personnel.fetchMyCrew)
      .mockResolvedValueOnce(G23_CREW)
      .mockResolvedValueOnce([G23_CREW[0]]);
    vi.mocked(personnel.retireCrew).mockResolvedValue(undefined);
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValueOnce(false).mockReturnValueOnce(true);
    const { container, getByLabelText } = render(<AddCrewSection />);
    await waitFor(() => expect(container.textContent ?? "").toContain("Tpyo Guy"));
    fireEvent.click(getByLabelText("Retire Tpyo Guy"));
    expect(personnel.retireCrew).not.toHaveBeenCalled(); // declined confirm
    fireEvent.click(getByLabelText("Retire Tpyo Guy"));
    await waitFor(() => expect(personnel.retireCrew).toHaveBeenCalledWith(2));
    await waitFor(() => expect(container.textContent ?? "").not.toContain("Tpyo Guy")); // refreshed away
    confirmSpy.mockRestore();
  });

  it("a retire 409 (foreign time / other job) surfaces the office-routing copy inline", async () => {
    vi.mocked(personnel.fetchMyCrew).mockResolvedValue(G23_CREW);
    vi.mocked(personnel.retireCrew).mockRejectedValue(
      new Error("Someone else has logged time for this person — ask the office to retire them."),
    );
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);
    const { container, getByLabelText } = render(<AddCrewSection />);
    await waitFor(() => expect(container.textContent ?? "").toContain("Tpyo Guy"));
    fireEvent.click(getByLabelText("Retire Tpyo Guy"));
    await waitFor(() => expect(container.textContent ?? "").toContain("ask the office to retire them"));
    expect(container.textContent ?? "").toContain("Tpyo Guy"); // row intact
    confirmSpy.mockRestore();
  });
});
