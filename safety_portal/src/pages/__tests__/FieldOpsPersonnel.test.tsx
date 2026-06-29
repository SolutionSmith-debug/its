/**
 * Field-ops Personnel page tests.
 * Mirrors FormRequestPage.test.tsx: vi.mock api + simple renders with screen queries.
 */
import { cleanup, fireEvent, render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../lib/fieldops_personnel", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../lib/fieldops_personnel")>();
  return {
    ...actual,
    fetchPersonnelList: vi.fn(),
    fetchPersonnelDetail: vi.fn(),
    createPersonnel: vi.fn(),
    updatePersonnel: vi.fn(),
    linkPersonnelAccount: vi.fn(),
    unlinkPersonnelAccount: vi.fn(),
    retirePersonnel: vi.fn(),
  };
});

// PageShell (the shared header/back shell every page renders through) calls useAuth for its
// Sign-out button, and the Personnel page now reads `user.capabilities` to gate its manage UI.
// resetAllMocks + a default (no-caps) useAuth in beforeEach; manage tests override per-test.
vi.mock("../../lib/auth", () => ({ useAuth: vi.fn() }));

import * as api from "../../lib/fieldops_personnel";
import { FieldOpsPersonnel } from "../FieldOpsPersonnel";
import { useAuth } from "../../lib/auth";

afterEach(cleanup);
beforeEach(() => {
  vi.resetAllMocks();
  vi.mocked(useAuth).mockReturnValue({
    user: null,
    loading: false,
    login: vi.fn(async () => {}),
    logout: vi.fn(async () => {}),
  });
});

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
    // Self-contained: the detail view only renders once fetchPersonnelDetail resolves with a
    // personnel (the back-control lives there), so mock it here rather than leaning on cross-test
    // mock leakage (beforeEach now resetAllMocks).
    vi.mocked(api.fetchPersonnelDetail).mockResolvedValue({
      personnel: { id: 1, name: "Alice Chen", username: "alice.chen", trade: "operator", time_entries: [] },
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

    // We're back on the list: the detail's contextual "Back to personnel" control is
    // gone and the Personnel list heading is shown again. (PageShell now owns the
    // page-level "← Home", so the list no longer renders its own "← Back".)
    await waitFor(() => {
      expect(container.querySelector(".dash-back-btn")).toBeNull();
      expect(getByText("Personnel")).toBeTruthy();
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

describe("FieldOpsPersonnel — manage (cap.personnel.manage)", () => {
  function asManager() {
    vi.mocked(useAuth).mockReturnValue({
      user: { username: "admin.one", role: "admin", capabilities: ["cap.personnel.read", "cap.personnel.manage"] },
      loading: false,
      login: vi.fn(async () => {}),
      logout: vi.fn(async () => {}),
    });
  }

  it("a non-manage user does NOT see the Add-personnel form", async () => {
    vi.mocked(api.fetchPersonnelList).mockResolvedValue({ personnel: MOCK_PERSONNEL, latest_entries: [], next_cursor: null });
    const { container } = render(<FieldOpsPersonnel onBack={() => {}} />);
    await waitFor(() => expect(api.fetchPersonnelList).toHaveBeenCalled());
    expect(container.querySelector("form[aria-label='Add personnel']")).toBeNull();
  });

  it("admin sees the Add-personnel form; roster-only submit calls createPersonnel (no account) + reloads", async () => {
    asManager();
    vi.mocked(api.fetchPersonnelList).mockResolvedValue({ personnel: MOCK_PERSONNEL, latest_entries: [], next_cursor: null });
    vi.mocked(api.createPersonnel).mockResolvedValue({ id: 99 });
    const { container } = render(<FieldOpsPersonnel onBack={() => {}} />);
    const form = (await waitFor(() => container.querySelector("form[aria-label='Add personnel']")))!;
    fireEvent.change(form.querySelector("input[name='name']")!, { target: { value: "New Person" } });
    fireEvent.submit(form);
    await waitFor(() => expect(api.createPersonnel).toHaveBeenCalledWith(expect.objectContaining({ name: "New Person" })));
    expect(vi.mocked(api.createPersonnel).mock.calls[0][0].account).toBeUndefined();
    await waitFor(() => expect(vi.mocked(api.fetchPersonnelList).mock.calls.length).toBeGreaterThanOrEqual(2)); // initial + reload
  });

  it("with the account toggle on, submit includes an account object (default role submitter)", async () => {
    asManager();
    vi.mocked(api.fetchPersonnelList).mockResolvedValue({ personnel: [], latest_entries: [], next_cursor: null });
    vi.mocked(api.createPersonnel).mockResolvedValue({ id: 100 });
    const { container } = render(<FieldOpsPersonnel onBack={() => {}} />);
    const form = (await waitFor(() => container.querySelector("form[aria-label='Add personnel']")))!;
    fireEvent.change(form.querySelector("input[name='name']")!, { target: { value: "Acct Person" } });
    fireEvent.click(form.querySelector("input[name='withAccount']")!);
    fireEvent.change(form.querySelector("input[name='username']")!, { target: { value: "acct.person" } });
    fireEvent.change(form.querySelector("input[name='password']")!, { target: { value: "password123" } });
    fireEvent.submit(form);
    await waitFor(() =>
      expect(api.createPersonnel).toHaveBeenCalledWith(
        expect.objectContaining({
          name: "Acct Person",
          account: expect.objectContaining({ username: "acct.person", password: "password123", role: "submitter" }),
        }),
      ),
    );
  });

  it("Edit opens the edit form; submit calls updatePersonnel", async () => {
    asManager();
    vi.mocked(api.fetchPersonnelList).mockResolvedValue({ personnel: MOCK_PERSONNEL, latest_entries: [], next_cursor: null });
    vi.mocked(api.updatePersonnel).mockResolvedValue(undefined);
    const { container } = render(<FieldOpsPersonnel onBack={() => {}} />);
    const editBtn = await waitFor(() => {
      const b = Array.from(container.querySelectorAll("button")).find((x) => x.textContent === "Edit");
      expect(b).toBeTruthy();
      return b!;
    });
    fireEvent.click(editBtn); // row 1 = Alice (id 1)
    const form = (await waitFor(() => container.querySelector("form[aria-label='Edit personnel']")))!;
    fireEvent.change(form.querySelector("input[name='name']")!, { target: { value: "Alice C." } });
    fireEvent.submit(form);
    await waitFor(() => expect(api.updatePersonnel).toHaveBeenCalledWith(1, expect.objectContaining({ name: "Alice C." })));
  });

  it("Link account (unlinked person) opens the link form; submit calls linkPersonnelAccount", async () => {
    asManager();
    vi.mocked(api.fetchPersonnelList).mockResolvedValue({
      personnel: [{ id: 3, name: "Carol Davis", trade: "laborer", username: null }],
      latest_entries: [],
      next_cursor: null,
    });
    vi.mocked(api.linkPersonnelAccount).mockResolvedValue(undefined);
    const { container } = render(<FieldOpsPersonnel onBack={() => {}} />);
    const linkBtn = await waitFor(() => {
      const b = Array.from(container.querySelectorAll("button")).find((x) => x.textContent === "Link account");
      expect(b).toBeTruthy();
      return b!;
    });
    fireEvent.click(linkBtn);
    const form = (await waitFor(() => container.querySelector("form[aria-label='Link personnel account']")))!;
    fireEvent.change(form.querySelector("input[name='username']")!, { target: { value: "carol.davis" } });
    fireEvent.submit(form);
    await waitFor(() => expect(api.linkPersonnelAccount).toHaveBeenCalledWith(3, "carol.davis"));
  });

  it("Unlink account (linked person) calls unlinkPersonnelAccount", async () => {
    asManager();
    vi.mocked(api.fetchPersonnelList).mockResolvedValue({
      personnel: [{ id: 1, name: "Alice Chen", trade: "operator", username: "alice.chen" }],
      latest_entries: [],
      next_cursor: null,
    });
    vi.mocked(api.unlinkPersonnelAccount).mockResolvedValue(undefined);
    const { container } = render(<FieldOpsPersonnel onBack={() => {}} />);
    const unlinkBtn = await waitFor(() => {
      const b = Array.from(container.querySelectorAll("button")).find((x) => x.textContent === "Unlink account");
      expect(b).toBeTruthy();
      return b!;
    });
    fireEvent.click(unlinkBtn);
    await waitFor(() => expect(api.unlinkPersonnelAccount).toHaveBeenCalledWith(1));
  });

  it("Retire calls retirePersonnel (and does NOT open the detail view)", async () => {
    asManager();
    vi.mocked(api.fetchPersonnelList).mockResolvedValue({ personnel: MOCK_PERSONNEL, latest_entries: [], next_cursor: null });
    vi.mocked(api.retirePersonnel).mockResolvedValue(undefined);
    const { container } = render(<FieldOpsPersonnel onBack={() => {}} />);
    const retireBtn = await waitFor(() => {
      const b = Array.from(container.querySelectorAll("button")).find((x) => x.textContent === "Retire");
      expect(b).toBeTruthy();
      return b!;
    });
    fireEvent.click(retireBtn); // row 1 = Alice (id 1)
    await waitFor(() => expect(api.retirePersonnel).toHaveBeenCalledWith(1));
    // stopPropagation kept us on the list (no detail fetch)
    expect(api.fetchPersonnelDetail).not.toHaveBeenCalled();
  });
});
