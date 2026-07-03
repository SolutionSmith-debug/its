/**
 * R3 semantics under the G2.5 URL router — the original five history scenarios, preserved:
 *
 *   1. Every top-level view change pushes a history entry (now: the destination URL).
 *   2. popstate restores the popped entry's view (parsed from the restored URL).
 *   3. openForm captures the originating route; FormFillPage gets returnTo { label, onReturn }
 *      and the return lands back on the captured route (My Tasks), with a fresh history entry.
 *   4. A non-deep-link fill (home card) gets NO returnTo — default flow unchanged.
 *   5. THE DIRTY GUARD (must survive G2.5 exactly): a dirty form (reported up via onDirtyChange)
 *      confirms before a popstate discard; declining re-pushes the fill URL and stays.
 *
 * Pops are simulated the way a real browser performs them: the address bar is ALREADY at the
 * restored entry when popstate fires (replaceState + dispatch). Pages are stubbed — this
 * exercises App's wiring, not page internals (those have their own suites:
 * FormFillPage.r3.test.tsx, ChecklistItemRow.test.tsx).
 */
import { act, cleanup, fireEvent, render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { FormPrefill, FormReturnTo } from "../pages/FormFillPage";

vi.mock("../lib/auth", () => ({ useAuth: vi.fn() }));

vi.mock("../pages/HomePage", () => ({
  HomePage: ({ onNavigate }: { onNavigate: (v: string) => void }) => (
    <div>
      home-page
      <button onClick={() => onNavigate("fieldops-tasks")}>go-tasks</button>
      <button onClick={() => onNavigate("fill")}>go-fill</button>
    </div>
  ),
}));

vi.mock("../pages/FieldOpsMyTasks", () => ({
  FieldOpsMyTasks: ({ onOpenForm }: { onOpenForm?: (p: FormPrefill) => void }) => (
    <div>
      tasks-page
      <button onClick={() => onOpenForm?.({ jobId: "J1", parentCode: "daily-report", workDate: "2026-07-01" })}>
        open-form
      </button>
    </div>
  ),
}));

vi.mock("../pages/FormFillPage", () => ({
  FormFillPage: ({
    returnTo,
    onDirtyChange,
  }: {
    returnTo?: FormReturnTo;
    onDirtyChange?: (d: boolean) => void;
  }) => (
    <div>
      fill-page
      {returnTo ? <button onClick={returnTo.onReturn}>{returnTo.label}</button> : null}
      <button onClick={() => onDirtyChange?.(true)}>make-dirty</button>
    </div>
  ),
}));

import { App } from "../App";
import { useAuth } from "../lib/auth";

const FILL_URL = "/submit?job=J1&form=daily-report&date=2026-07-01";

afterEach(cleanup);
beforeEach(() => {
  vi.clearAllMocks();
  window.history.replaceState(null, "", "/"); // each test cold-starts at home
  vi.mocked(useAuth).mockReturnValue({
    user: { username: "sam", role: "submitter", capabilities: ["cap.tasks.own"] },
    loading: false,
    login: vi.fn(async () => {}),
    logout: vi.fn(async () => {}),
  } as never);
});

/** Simulate a browser back/forward landing on `url`: address bar moves FIRST, then popstate. */
function popTo(url: string) {
  act(() => {
    window.history.replaceState(null, "", url);
    window.dispatchEvent(new PopStateEvent("popstate", { state: null }));
  });
}

const currentUrl = () => window.location.pathname + window.location.search;

describe("App — R3 history semantics under the G2.5 router", () => {
  it("a top-level view change pushes a history entry with that view's URL", async () => {
    const { getByText } = render(<App />);
    fireEvent.click(getByText("go-tasks"));
    await waitFor(() => expect(getByText("tasks-page")).toBeTruthy());
    expect(currentUrl()).toBe("/tasks");
  });

  it("popstate restores the popped view (phone back stays inside the site)", async () => {
    const { getByText } = render(<App />);
    fireEvent.click(getByText("go-tasks"));
    await waitFor(() => expect(getByText("tasks-page")).toBeTruthy());
    popTo("/");
    await waitFor(() => expect(getByText("home-page")).toBeTruthy());
  });

  it("openForm captures the origin; the fill URL is shareable; return lands on My Tasks", async () => {
    const { getByText } = render(<App />);
    fireEvent.click(getByText("go-tasks"));
    await waitFor(() => expect(getByText("tasks-page")).toBeTruthy());
    fireEvent.click(getByText("open-form"));
    await waitFor(() => expect(getByText("fill-page")).toBeTruthy());
    // The deep-linked fill is now a real URL (the shareable projection of the prefill).
    expect(currentUrl()).toBe(FILL_URL);

    // The captured origin route drives the label + the return destination.
    fireEvent.click(getByText("Back to My Tasks"));
    await waitFor(() => expect(getByText("tasks-page")).toBeTruthy());
    expect(currentUrl()).toBe("/tasks");
  });

  it("a home-card fill gets NO returnTo (default flow unchanged)", async () => {
    const { getByText, queryByText } = render(<App />);
    fireEvent.click(getByText("go-fill"));
    await waitFor(() => expect(getByText("fill-page")).toBeTruthy());
    expect(currentUrl()).toBe("/submit");
    expect(queryByText("Back to My Tasks")).toBeNull();
    expect(queryByText("Back")).toBeNull();
  });

  it("popstate closes an open form view; a dirty form confirms first (decline stays + re-pushes)", async () => {
    const confirmSpy = vi.spyOn(window, "confirm");
    const { getByText, queryByText } = render(<App />);
    fireEvent.click(getByText("go-tasks"));
    await waitFor(() => expect(getByText("tasks-page")).toBeTruthy());
    fireEvent.click(getByText("open-form"));
    await waitFor(() => expect(getByText("fill-page")).toBeTruthy());
    fireEvent.click(getByText("make-dirty"));

    // Decline the discard: still on the form, and the consumed entry was re-pushed (URL restored).
    confirmSpy.mockReturnValueOnce(false);
    popTo("/tasks");
    expect(getByText("fill-page")).toBeTruthy();
    expect(queryByText("tasks-page")).toBeNull();
    expect(currentUrl()).toBe(FILL_URL);

    // Accept the discard: the popped view is restored.
    confirmSpy.mockReturnValueOnce(true);
    popTo("/tasks");
    await waitFor(() => expect(getByText("tasks-page")).toBeTruthy());
    expect(currentUrl()).toBe("/tasks");
    confirmSpy.mockRestore();
  });
});
