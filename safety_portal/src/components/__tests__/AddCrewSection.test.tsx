/**
 * AddCrewSection (R2 extraction) — section-level detail tests: pre-submit placement line,
 * crew-list load states (auxiliary — never blocks the form), duplicate-name warn, and the
 * crew-list refresh after a create. The cap gate + not_placed error path live in
 * pages/__tests__/FieldOpsMyTasks.test.tsx.
 */
import { cleanup, fireEvent, render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../lib/fieldops_personnel", () => ({ createCrew: vi.fn(), fetchMyCrew: vi.fn() }));

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
  it("renders the fetched crew list", async () => {
    vi.mocked(personnel.fetchMyCrew).mockResolvedValue(CREW);
    const { container } = render(<AddCrewSection />);
    await waitFor(() => expect(container.textContent ?? "").toContain("Your crew: Self Sub (electrical), Helper Hank"));
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
    await waitFor(() => expect(container.textContent ?? "").toContain("Your crew: Self Sub (electrical)"));
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
