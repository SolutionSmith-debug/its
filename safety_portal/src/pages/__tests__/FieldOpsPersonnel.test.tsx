/**
 * Field-ops Personnel page tests.
 * Mirrors FormRequestPage.test.tsx: vi.mock api + simple renders with screen queries.
 */
import { cleanup, render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../lib/fieldops_personnel", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../lib/fieldops_personnel")>();
  return {
    ...actual,
    fetchPersonnelList: vi.fn(),
    fetchPersonnelDetail: vi.fn(),
  };
});

import * as api from "../../lib/fieldops_personnel";
import { FieldOpsPersonnel } from "../FieldOpsPersonnel";

afterEach(cleanup);
beforeEach(() => vi.clearAllMocks());

const MOCK_PERSONNEL = [
  { id: 1, name: "Alice Chen", trade: "operator", username: "alice.chen" },
  { id: 2, name: "Bob Martinez", trade: "foreman", username: "bob.martinez" },
];

const MOCK_LATEST_ENTRIES: api.LatestEntry[] = [
  {
    personnel_id: 1,
    job_id: "JOB-A",
    project_name: "North Ridge",
    hours: 8.5,
    work_started_at: 1_700_000_000,
    work_ended_at: 1_700_004_800,
    recorded_at: 1_700_005_000,
  },
];

function clickRow(container: HTMLElement) {
  // The clickable data rows carry .dash-row--click; querySelector("tr") would grab the
  // <thead> header row (no onClick) and the detail view would never open.
  const row = container.querySelector(".dash-row--click");
  if (row) fireEvent(row, new MouseEvent("click", { bubbles: true }));
}

function clickButton(container: HTMLElement) {
  // Target the Load-more button specifically — the first <button> on the page is the back button.
  const btn = container.querySelector(".dash-load-more button");
  if (btn) fireEvent(btn, new MouseEvent("click", { bubbles: true }));
}

describe("FieldOpsPersonnel — list view", () => {
  it("renders personnel rows and latest entries", async () => {
    vi.mocked(api.fetchPersonnelList).mockResolvedValue({
      personnel: MOCK_PERSONNEL,
      latest_entries: MOCK_LATEST_ENTRIES,
      next_cursor: null,
    });

    const { container, getByText } = render(<FieldOpsPersonnel onBack={() => {}} />);

    await waitFor(() => expect(api.fetchPersonnelList).toHaveBeenCalled());
    expect(getByText("Alice Chen")).toBeTruthy();
    expect(getByText("Bob Martinez")).toBeTruthy();
    expect(container.textContent ?? "").toContain("North Ridge");
    expect(container.textContent ?? "").toContain("8.50");
  });

  it("shows 'No active personnel' when empty", async () => {
    vi.mocked(api.fetchPersonnelList).mockResolvedValue({
      personnel: [],
      latest_entries: [],
      next_cursor: null,
    });

    const { container } = render(<FieldOpsPersonnel onBack={() => {}} />);
    await waitFor(() => expect(container.querySelector(".dash-unavail")).toBeTruthy());
  });

  it("clicking a row opens detail view", async () => {
    vi.mocked(api.fetchPersonnelList).mockResolvedValue({
      personnel: MOCK_PERSONNEL,
      latest_entries: [],
      next_cursor: null,
    });
    vi.mocked(api.fetchPersonnelDetail).mockResolvedValue({
      personnel: {
        id: 1,
        name: "Alice Chen",
        username: "alice.chen",
        trade: "operator",
        time_entries: [],
      },
      next_cursor: null,
    });

    const { container, getByText } = render(<FieldOpsPersonnel onBack={() => {}} />);

    await waitFor(() => expect(api.fetchPersonnelList).toHaveBeenCalled());
    clickRow(container);

    // Detail header appears, "Back to personnel" shown
    await waitFor(() => {
      expect(container.querySelector(".dash-back-btn button")?.textContent).toContain("Back to personnel");
      expect(getByText("Alice Chen")).toBeTruthy();
    });
  });

  it("back button returns to list", async () => {
    vi.mocked(api.fetchPersonnelList).mockResolvedValue({
      personnel: MOCK_PERSONNEL,
      latest_entries: [],
      next_cursor: null,
    });

    const { container, getByText } = render(<FieldOpsPersonnel onBack={() => {}} />);

    await waitFor(() => expect(api.fetchPersonnelList).toHaveBeenCalled());
    clickRow(container);

    await waitFor(() =>
      expect(container.querySelector(".dash-back-btn button")?.textContent).toContain("Back to personnel"),
    );

    // Click back button
    const backBtn = container.querySelector(".dash-back-btn button")!;
    fireEvent(backBtn, new MouseEvent("click", { bubbles: true }));

    // Back button text should change to "← Back"
    await waitFor(() => {
      expect(getByText("← Back")).toBeTruthy();
    });
  });

  it("shows 'Load more' when next_cursor present", async () => {
    vi.mocked(api.fetchPersonnelList)
      .mockResolvedValueOnce({
        personnel: MOCK_PERSONNEL,
        latest_entries: [],
        next_cursor: "next-page-token",
      })
      .mockResolvedValueOnce({
        personnel: [{ id: 3, name: "Carol Davis", trade: "laborer", username: null }],
        latest_entries: [],
        next_cursor: null,
      });

    const { container } = render(<FieldOpsPersonnel onBack={() => {}} />);

    await waitFor(() => expect(api.fetchPersonnelList).toHaveBeenCalled());
    expect(container.querySelector(".dash-load-more button")?.textContent).toContain("Load more");

    // Click load more
    clickButton(container);
    await waitFor(() => {
      expect(api.fetchPersonnelList).toHaveBeenCalledWith("next-page-token");
    });
  });

  it("shows 'No time logged' when detail has no entries", async () => {
    vi.mocked(api.fetchPersonnelList).mockResolvedValue({
      personnel: MOCK_PERSONNEL,
      latest_entries: [],
      next_cursor: null,
    });
    vi.mocked(api.fetchPersonnelDetail).mockResolvedValue({
      personnel: {
        id: 1,
        name: "Alice Chen",
        username: "alice.chen",
        trade: "operator",
        time_entries: [],
      },
      next_cursor: null,
    });

    const { container } = render(<FieldOpsPersonnel onBack={() => {}} />);

    await waitFor(() => expect(api.fetchPersonnelList).toHaveBeenCalled());
    clickRow(container);

    await waitFor(() =>
      expect(container.querySelector(".dash-unavail")?.textContent).toContain("No time logged"),
    );
  });
});

// Simple helper for React Testing Library
function fireEvent<T extends Element>(element: T, event: Event) {
  element.dispatchEvent(event);
}
