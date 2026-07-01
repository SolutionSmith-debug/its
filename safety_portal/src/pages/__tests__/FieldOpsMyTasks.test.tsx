/**
 * Assigned-Tasks tab (P4 S1) — "My Tasks" page + its HomePage card gate.
 * Mirrors FieldOpsPersonnel.test.tsx: vi.mock the lib + useAuth, render, query.
 */
import { cleanup, fireEvent, render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../lib/fieldops_tasks", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../lib/fieldops_tasks")>();
  return { ...actual, fetchMyTasks: vi.fn(), setTaskStatus: vi.fn() };
});
vi.mock("../../lib/auth", () => ({ useAuth: vi.fn() }));

import * as api from "../../lib/fieldops_tasks";
import { FieldOpsMyTasks } from "../FieldOpsMyTasks";
import { HomePage } from "../HomePage";
import { useAuth } from "../../lib/auth";

function authWith(capabilities: string[]) {
  return {
    user: { username: "sam", role: "submitter" as const, capabilities },
    loading: false,
    login: vi.fn(async () => {}),
    logout: vi.fn(async () => {}),
  };
}

afterEach(cleanup);
beforeEach(() => {
  vi.resetAllMocks();
  vi.mocked(useAuth).mockReturnValue(authWith(["cap.tasks.own"]));
});

const TASKS: api.MyTask[] = [
  { id: 1, job_id: "JOB-A", project_name: "Alpha", description: "Dig footings", status: "open", created_at: 100 },
  { id: 2, job_id: "JOB-A", project_name: "Alpha", description: "Pour slab", status: "in_progress", created_at: 90 },
  { id: 3, job_id: "JOB-B", project_name: "Bravo", description: "Frame wall", status: "open", created_at: 80 },
];

describe("FieldOpsMyTasks", () => {
  it("renders my tasks grouped by job (project name)", async () => {
    vi.mocked(api.fetchMyTasks).mockResolvedValue({ tasks: TASKS });
    const { container } = render(<FieldOpsMyTasks onBack={() => {}} />);
    // Two job groups (Alpha with 2 tasks, Bravo with 1).
    await waitFor(() => expect(container.querySelectorAll(".dash-section")).toHaveLength(2));
    const headings = Array.from(container.querySelectorAll(".dash-detail__h2")).map((h) => h.textContent ?? "");
    expect(headings.some((h) => h.includes("Alpha") && h.includes("JOB-A"))).toBe(true);
    expect(headings.some((h) => h.includes("Bravo") && h.includes("JOB-B"))).toBe(true);
    const txt = container.textContent ?? "";
    expect(txt).toContain("Dig footings");
    expect(txt).toContain("Pour slab");
    expect(txt).toContain("Frame wall");
    // The Alpha group holds two task rows.
    const alphaSection = Array.from(container.querySelectorAll(".dash-section")).find((s) => (s.textContent ?? "").includes("Alpha"))!;
    expect(alphaSection.querySelectorAll(".dash-tasklist li")).toHaveLength(2);
  });

  it("a status change fires setTaskStatus(taskId, status)", async () => {
    vi.mocked(api.fetchMyTasks).mockResolvedValue({ tasks: TASKS });
    vi.mocked(api.setTaskStatus).mockResolvedValue(undefined);
    const { getByLabelText } = render(<FieldOpsMyTasks onBack={() => {}} />);
    const select = await waitFor(() => getByLabelText("Set status for task 1") as HTMLSelectElement);
    expect(select.value).toBe("open");
    fireEvent.change(select, { target: { value: "done" } });
    await waitFor(() => expect(api.setTaskStatus).toHaveBeenCalledWith(1, "done"));
  });

  it("shows an empty state for a user with no assigned tasks (e.g. no linked personnel)", async () => {
    vi.mocked(api.fetchMyTasks).mockResolvedValue({ tasks: [] });
    const { container } = render(<FieldOpsMyTasks onBack={() => {}} />);
    await waitFor(() => expect(container.querySelector(".dash-unavail")).not.toBeNull());
    expect(container.textContent ?? "").toContain("No tasks are assigned to you");
    expect(container.querySelector(".dash-tasklist")).toBeNull();
  });
});

describe("HomePage — My Tasks card gate", () => {
  it("renders the My Tasks card for a holder of cap.tasks.own", () => {
    vi.mocked(useAuth).mockReturnValue(authWith(["cap.tasks.own"]));
    const { container } = render(<HomePage onNavigate={() => {}} />);
    expect(container.textContent ?? "").toContain("My Tasks");
  });

  it("hides the My Tasks card without cap.tasks.own", () => {
    vi.mocked(useAuth).mockReturnValue(authWith(["cap.form.submit"]));
    const { container } = render(<HomePage onNavigate={() => {}} />);
    expect(container.textContent ?? "").not.toContain("My Tasks");
  });
});
