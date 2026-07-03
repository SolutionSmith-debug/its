/**
 * G2.5 — deep-link routing through App (the router's integration surface):
 *
 *   • Cold-loading any route URL lands there after auth — including the flagship
 *     shared-link scenario: /jobs/JOB-000018 opens THAT job's detail.
 *   • While signed out, every route shows LoginPage and LEAVES THE URL ALONE, so the
 *     intended destination survives the login round-trip.
 *   • Unknown and unauthorized URLs render Home (never a blank page) and normalize the
 *     address bar; /login while signed in normalizes home too.
 *   • Within-page state reported up keeps the URL shareable: the Job Tracker's selected
 *     job (push on open, replace on in-page back) and the My Tasks tab (replace).
 *   • A popped /jobs/:id history entry revives that job's detail — the URL now encodes
 *     the job, superseding R7's "pop lands on the plain list".
 *
 * Pages are stubbed (App wiring under test, not page internals). The real
 * FieldOpsJobTracker's initialJobId consumption has its own suite
 * (FieldOpsJobTracker.test.tsx — "R7 deep link").
 */
import { act, cleanup, fireEvent, render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { FormPrefill } from "../pages/FormFillPage";

vi.mock("../lib/auth", () => ({ useAuth: vi.fn() }));

vi.mock("../pages/HomePage", () => ({
  HomePage: () => <div>home-page</div>,
}));
vi.mock("../pages/LoginPage", () => ({
  LoginPage: () => <div>login-page</div>,
}));
vi.mock("../pages/FieldOpsJobTracker", () => ({
  FieldOpsJobTracker: ({
    initialJobId,
    onJobViewChange,
  }: {
    initialJobId?: string | null;
    onJobViewChange?: (id: string | null) => void;
  }) => (
    <div>
      <span>tracker:{initialJobId ?? "list"}</span>
      <button onClick={() => onJobViewChange?.("JOB-7")}>tracker-open-7</button>
      <button onClick={() => onJobViewChange?.(null)}>tracker-back-list</button>
    </div>
  ),
}));
vi.mock("../pages/FieldOpsMyTasks", () => ({
  FieldOpsMyTasks: ({
    initialTab,
    onTabChange,
  }: {
    initialTab?: "assigned" | "daily";
    onTabChange?: (t: "assigned" | "daily") => void;
  }) => (
    <div>
      <span>tasks:{initialTab ?? "default"}</span>
      <button onClick={() => onTabChange?.("daily")}>tasks-pick-daily</button>
    </div>
  ),
}));
vi.mock("../pages/FormFillPage", () => ({
  FormFillPage: ({ prefill }: { prefill?: FormPrefill }) => (
    <div>
      fill:{prefill ? `${prefill.jobId ?? ""}|${prefill.parentCode ?? ""}|${prefill.workDate ?? ""}` : "blank"}
    </div>
  ),
}));
vi.mock("../pages/FieldOpsEquipment", () => ({
  FieldOpsEquipment: () => <div>equipment-page</div>,
}));

import { App } from "../App";
import { useAuth } from "../lib/auth";

const ALL_CAPS = [
  "cap.tasks.own",
  "cap.jobtracker.read",
  "cap.equipment.field",
  "cap.form.submit",
];

function authAs(caps: string[] | null) {
  vi.mocked(useAuth).mockReturnValue({
    user: caps ? { username: "sam", role: "submitter", capabilities: caps } : null,
    loading: false,
    login: vi.fn(async () => {}),
    logout: vi.fn(async () => {}),
  } as never);
}

function coldLoad(url: string) {
  window.history.replaceState(null, "", url);
  return render(<App />);
}

/** Browser back/forward simulation: address bar moves first, then popstate fires. */
function popTo(url: string) {
  act(() => {
    window.history.replaceState(null, "", url);
    window.dispatchEvent(new PopStateEvent("popstate", { state: null }));
  });
}

const currentUrl = () => window.location.pathname + window.location.search;

afterEach(cleanup);
beforeEach(() => {
  vi.clearAllMocks();
  window.history.replaceState(null, "", "/");
  authAs(ALL_CAPS);
});

describe("App router — cold-load deep links (authed)", () => {
  it("the shared-link scenario: /jobs/JOB-000018 opens THAT job's detail", async () => {
    const { getByText } = coldLoad("/jobs/JOB-000018");
    await waitFor(() => expect(getByText("tracker:JOB-000018")).toBeTruthy());
    expect(currentUrl()).toBe("/jobs/JOB-000018");
  });

  it("/tasks lands on My Tasks with the default (auto-switchable) tab", async () => {
    const { getByText } = coldLoad("/tasks");
    await waitFor(() => expect(getByText("tasks:default")).toBeTruthy());
  });

  it("/tasks/daily lands on My Tasks with the daily tab pinned", async () => {
    const { getByText } = coldLoad("/tasks/daily");
    await waitFor(() => expect(getByText("tasks:daily")).toBeTruthy());
  });

  it("/submit?… seeds the fill prefill from the URL (shareable form deep link)", async () => {
    const { getByText } = coldLoad("/submit?job=J1&form=daily-report&date=2026-07-01");
    await waitFor(() => expect(getByText("fill:J1|daily-report|2026-07-01")).toBeTruthy());
  });

  it("an unknown URL renders Home and normalizes the address bar (never a blank page)", async () => {
    const { getByText } = coldLoad("/no-such-page");
    await waitFor(() => expect(getByText("home-page")).toBeTruthy());
    expect(currentUrl()).toBe("/");
  });

  it("an unauthorized URL (missing capability) renders Home and normalizes", async () => {
    authAs(["cap.tasks.own"]); // no cap.equipment.field
    const { getByText, queryByText } = coldLoad("/equipment");
    await waitFor(() => expect(getByText("home-page")).toBeTruthy());
    expect(queryByText("equipment-page")).toBeNull();
    await waitFor(() => expect(currentUrl()).toBe("/"));
  });

  it("/login while signed in normalizes home", async () => {
    const { getByText } = coldLoad("/login");
    await waitFor(() => expect(getByText("home-page")).toBeTruthy());
    await waitFor(() => expect(currentUrl()).toBe("/"));
  });
});

describe("App router — login preserves the intended destination", () => {
  it("signed out: any deep link shows LoginPage with the URL untouched; login lands there", async () => {
    authAs(null);
    const { getByText, rerender } = coldLoad("/jobs/JOB-000018");
    expect(getByText("login-page")).toBeTruthy();
    expect(currentUrl()).toBe("/jobs/JOB-000018"); // destination preserved through the gate

    authAs(ALL_CAPS); // the login round-trip completes
    rerender(<App />);
    await waitFor(() => expect(getByText("tracker:JOB-000018")).toBeTruthy());
    expect(currentUrl()).toBe("/jobs/JOB-000018");
  });
});

describe("App router — within-page state keeps the URL shareable", () => {
  it("opening a job's detail pushes /jobs/<id>; in-page back replaces to /jobs", async () => {
    const { getByText } = coldLoad("/jobs");
    await waitFor(() => expect(getByText("tracker:list")).toBeTruthy());
    const before = window.history.length;

    fireEvent.click(getByText("tracker-open-7")); // deeper → push (texted-link moment)
    expect(currentUrl()).toBe("/jobs/JOB-7");
    expect(window.history.length).toBe(before + 1);
    expect(getByText("tracker:list")).toBeTruthy(); // NO remount — the page already shows it

    fireEvent.click(getByText("tracker-back-list")); // dismiss → replace
    expect(currentUrl()).toBe("/jobs");
    expect(window.history.length).toBe(before + 1);
  });

  it("a My Tasks tab flip replaces the URL (no history spam)", async () => {
    const { getByText } = coldLoad("/tasks");
    await waitFor(() => expect(getByText("tasks:default")).toBeTruthy());
    const before = window.history.length;

    fireEvent.click(getByText("tasks-pick-daily"));
    expect(currentUrl()).toBe("/tasks/daily");
    expect(window.history.length).toBe(before); // replace, not push
  });
});

describe("App router — history traversal is URL-authoritative", () => {
  it("a popped /jobs/:id entry revives THAT job's detail (fresh remount)", async () => {
    const { getByText } = coldLoad("/jobs");
    await waitFor(() => expect(getByText("tracker:list")).toBeTruthy());

    popTo("/jobs/JOB-9"); // e.g. browser FORWARD onto a previously-pushed detail entry
    await waitFor(() => expect(getByText("tracker:JOB-9")).toBeTruthy());
  });

  it("a pop onto an unrecognized URL homes + normalizes (never blank)", async () => {
    const { getByText } = coldLoad("/tasks");
    await waitFor(() => expect(getByText("tasks:default")).toBeTruthy());

    popTo("/derailed");
    await waitFor(() => expect(getByText("home-page")).toBeTruthy());
    expect(currentUrl()).toBe("/");
  });
});
