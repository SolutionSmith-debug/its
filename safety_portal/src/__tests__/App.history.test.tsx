/**
 * R3 — App-level returnTo capture + minimal history integration.
 *
 *   1. Every top-level view change pushes a { view } history entry.
 *   2. popstate restores the popped view (closing an open form view).
 *   3. openForm captures the originating view; FormFillPage gets returnTo { label, onReturn }
 *      and the return lands back on the captured view (My Tasks), with a fresh history entry.
 *   4. A non-deep-link fill (home card) gets NO returnTo — default flow unchanged.
 *   5. A dirty form (reported up via onDirtyChange) confirms before a popstate discard;
 *      declining re-pushes the fill entry and stays.
 *
 * Pages are stubbed — this exercises App's wiring, not page internals (those have their own
 * suites: FormFillPage.r3.test.tsx, ChecklistItemRow.test.tsx).
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

afterEach(cleanup);
beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(useAuth).mockReturnValue({
    user: { username: "sam", role: "submitter", capabilities: ["cap.tasks.own"] },
    loading: false,
    login: vi.fn(async () => {}),
    logout: vi.fn(async () => {}),
  } as never);
});

function popTo(view: string) {
  act(() => {
    window.dispatchEvent(new PopStateEvent("popstate", { state: { view } }));
  });
}

describe("App — R3 history integration", () => {
  it("a top-level view change pushes a history entry for that view", async () => {
    const { getByText } = render(<App />);
    fireEvent.click(getByText("go-tasks"));
    await waitFor(() => expect(getByText("tasks-page")).toBeTruthy());
    expect((window.history.state as { view?: string })?.view).toBe("fieldops-tasks");
  });

  it("popstate restores the popped view (phone back stays inside the site)", async () => {
    const { getByText } = render(<App />);
    fireEvent.click(getByText("go-tasks"));
    await waitFor(() => expect(getByText("tasks-page")).toBeTruthy());
    popTo("home");
    await waitFor(() => expect(getByText("home-page")).toBeTruthy());
  });

  it("openForm captures the origin; the fill's return button lands back on My Tasks", async () => {
    const { getByText } = render(<App />);
    fireEvent.click(getByText("go-tasks"));
    await waitFor(() => expect(getByText("tasks-page")).toBeTruthy());
    fireEvent.click(getByText("open-form"));
    await waitFor(() => expect(getByText("fill-page")).toBeTruthy());
    expect((window.history.state as { view?: string })?.view).toBe("fill");

    // The captured origin view drives the label + the return destination.
    fireEvent.click(getByText("Back to My Tasks"));
    await waitFor(() => expect(getByText("tasks-page")).toBeTruthy());
    expect((window.history.state as { view?: string })?.view).toBe("fieldops-tasks");
  });

  it("a home-card fill gets NO returnTo (default flow unchanged)", async () => {
    const { getByText, queryByText } = render(<App />);
    fireEvent.click(getByText("go-fill"));
    await waitFor(() => expect(getByText("fill-page")).toBeTruthy());
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

    // Decline the discard: still on the form, and the consumed history entry was re-pushed.
    confirmSpy.mockReturnValueOnce(false);
    popTo("fieldops-tasks");
    expect(getByText("fill-page")).toBeTruthy();
    expect(queryByText("tasks-page")).toBeNull();
    expect((window.history.state as { view?: string })?.view).toBe("fill");

    // Accept the discard: the popped view is restored.
    confirmSpy.mockReturnValueOnce(true);
    popTo("fieldops-tasks");
    await waitFor(() => expect(getByText("tasks-page")).toBeTruthy());
    confirmSpy.mockRestore();
  });
});
