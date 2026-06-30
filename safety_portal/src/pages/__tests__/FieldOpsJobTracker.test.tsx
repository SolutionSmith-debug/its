/**
 * Field Ops Job Tracker page (BRIEF C).
 * List: status filter + dash-grid of dash-card--click jobs (pill, progress bar, crew chips, open
 * tasks). Detail: header + progress + client + crew + tasks + time + equipment + inspections, with
 * per-leg Load more. Mirrors FieldOpsEquipment.test.tsx: mock both fetchers before render,
 * resetAllMocks, query by specific classes.
 */
import { cleanup, fireEvent, render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../lib/fieldops_jobtracker", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../lib/fieldops_jobtracker")>();
  return {
    ...actual,
    fetchJobList: vi.fn(),
    fetchJobDetail: vi.fn(),
    createJob: vi.fn(),
    closeJob: vi.fn(),
    setLifecycle: vi.fn(),
    editContacts: vi.fn(),
    setJobProgress: vi.fn(),
    addTask: vi.fn(),
    setTaskStatus: vi.fn(),
    logTime: vi.fn(),
  };
});
vi.mock("../../lib/auth", () => ({ useAuth: vi.fn() }));

import * as api from "../../lib/fieldops_jobtracker";
import { useAuth } from "../../lib/auth";
import { FieldOpsJobTracker } from "../FieldOpsJobTracker";

// Build a useAuth() return for an account holding the given capability keys (none → logged-out shell).
function authWith(capabilities: string[]) {
  return {
    user: capabilities.length ? { username: "u", role: "admin" as const, capabilities } : null,
    loading: false,
    login: vi.fn(),
    logout: vi.fn(),
  };
}

afterEach(cleanup);
beforeEach(() => {
  vi.resetAllMocks();
  // Default: no write caps → read-only shell (existing read tests behave exactly as before).
  vi.mocked(useAuth).mockReturnValue(authWith([]));
});

const JOBS: api.JobRow[] = [
  {
    job_id: "JOB-A",
    project_name: "Alpha",
    status: "active",
    progress: 40,
    client_name: "Acme Co",
    crew: [{ id: 1, name: "Alice Chen", trade: "operator" }],
    open_tasks: [{ id: 1, description: "Dig footings", status: "open", personnel_name: "Alice Chen" }],
  },
  {
    job_id: "JOB-B",
    project_name: "Bravo",
    status: "on_hold",
    progress: 0,
    client_name: null,
    crew: [],
    open_tasks: [],
  },
];

const DETAIL: api.JobDetail = {
  job_id: "JOB-A",
  project_name: "Alpha",
  status: "active",
  progress: 60,
  client: { name: "Acme Co", contact: "Pat", phone: "555-0100", email: "pat@example.com" },
  crew: [{ id: 1, name: "Alice Chen", trade: "operator" }],
  tasks: [{ id: 1, description: "Dig footings", status: "open", created_at: 100, personnel_name: "Alice Chen" }],
  time_entries: [{ uuid: "te-1", hours: 8, work_started_at: 1, work_ended_at: 2, recorded_at: 200, notes: "note", personnel_name: "Alice Chen" }],
  equipment_on_site: [{ id: 5, name: "here-unit", kind: "skid-steer", identifier: "H1", label: "Site", read_at: 200 }],
  inspections: [{ uuid: "in-1", form_code: "skid-daily", version: 1, performed_at: 150, recorded_at: 150, equipment_name: "here-unit" }],
};

const NO_CURSORS: { tasks: string | null; time: string | null; insp: string | null } = { tasks: null, time: null, insp: null };

describe("FieldOpsJobTracker — list view", () => {
  it("renders empty state when no jobs", async () => {
    vi.mocked(api.fetchJobList).mockResolvedValue({ jobs: [], next_cursor: null });
    const { container } = render(<FieldOpsJobTracker onBack={() => {}} />);
    await waitFor(() => expect(container.querySelector(".dash-unavail")).not.toBeNull());
    expect(container.textContent ?? "").toContain("No jobs for this status.");
  });

  it("renders job cards with pill, progress bar, crew chips, open tasks", async () => {
    vi.mocked(api.fetchJobList).mockResolvedValue({ jobs: JOBS, next_cursor: null });
    const { container } = render(<FieldOpsJobTracker onBack={() => {}} />);

    await waitFor(() => expect(container.querySelectorAll(".dash-card--click")).toHaveLength(2));
    const pills = Array.from(container.querySelectorAll(".dash-pill"));
    expect(pills.some((p) => p.classList.contains("dash-pill--ok"))).toBe(true); // active
    expect(pills.some((p) => p.classList.contains("dash-pill--warn"))).toBe(true); // on_hold
    const fill = container.querySelector(".dash-progress__fill") as HTMLElement | null;
    expect(fill?.style.width).toBe("40%");
    expect(container.querySelector(".dash-chip")?.textContent).toContain("Alice Chen");
    expect(container.querySelector(".dash-tasklist")?.textContent).toContain("Dig footings");
    expect(container.textContent ?? "").toContain("Acme Co");
  });

  it("changing the status filter refetches with the new status", async () => {
    vi.mocked(api.fetchJobList).mockResolvedValue({ jobs: JOBS, next_cursor: null });
    const { container } = render(<FieldOpsJobTracker onBack={() => {}} />);
    await waitFor(() => expect(api.fetchJobList).toHaveBeenCalledWith("active"));

    fireEvent.change(container.querySelector("select")!, { target: { value: "all" } });
    await waitFor(() => expect(api.fetchJobList).toHaveBeenCalledWith("all"));
  });

  it("Load more fetches the next page with the cursor", async () => {
    vi.mocked(api.fetchJobList)
      .mockResolvedValueOnce({ jobs: JOBS.slice(0, 1), next_cursor: "cursor-1" })
      .mockResolvedValueOnce({ jobs: JOBS.slice(1, 2), next_cursor: null });
    const { container } = render(<FieldOpsJobTracker onBack={() => {}} />);

    await waitFor(() => expect(container.querySelector(".dash-load-more button")).not.toBeNull());
    fireEvent.click(container.querySelector(".dash-load-more button")!);
    await waitFor(() => expect(api.fetchJobList).toHaveBeenLastCalledWith("active", "cursor-1"));
  });

  it("row click opens detail", async () => {
    vi.mocked(api.fetchJobList).mockResolvedValue({ jobs: JOBS, next_cursor: null });
    vi.mocked(api.fetchJobDetail).mockResolvedValue({ job: DETAIL, cursors: NO_CURSORS });
    const { container } = render(<FieldOpsJobTracker onBack={() => {}} />);

    await waitFor(() => expect(container.querySelectorAll(".dash-card--click")).toHaveLength(2));
    fireEvent.click(container.querySelector(".dash-card--click")!);
    await waitFor(() => expect(container.querySelector(".page__heading")?.textContent).toBe("Alpha"));
  });
});

describe("FieldOpsJobTracker — detail view", () => {
  async function openDetail(cursors = NO_CURSORS) {
    vi.mocked(api.fetchJobList).mockResolvedValue({ jobs: JOBS, next_cursor: null });
    vi.mocked(api.fetchJobDetail).mockResolvedValue({ job: DETAIL, cursors });
    const utils = render(<FieldOpsJobTracker onBack={() => {}} />);
    await waitFor(() => expect(utils.container.querySelectorAll(".dash-card--click")).toHaveLength(2));
    fireEvent.click(utils.container.querySelector(".dash-card--click")!);
    await waitFor(() => expect(api.fetchJobDetail).toHaveBeenCalledWith("JOB-A"));
    return utils;
  }

  it("renders header, progress, client, crew, tasks, time, equipment, inspections", async () => {
    const { container } = await openDetail();
    await waitFor(() => expect(container.querySelector(".page__heading")?.textContent).toBe("Alpha"));
    const txt = container.textContent ?? "";
    expect(txt).toContain("Acme Co"); // client
    expect(txt).toContain("Alice Chen"); // crew + time
    expect(txt).toContain("Dig footings"); // task
    expect(txt).toContain("here-unit"); // equipment on site
    expect(txt).toContain("skid-daily"); // inspection
    const fill = container.querySelector(".dash-progress__fill") as HTMLElement | null;
    expect(fill?.style.width).toBe("60%");
  });

  it("progress fill clamps out-of-range values to 0–100", async () => {
    vi.mocked(api.fetchJobList).mockResolvedValue({ jobs: JOBS, next_cursor: null });
    vi.mocked(api.fetchJobDetail).mockResolvedValue({ job: { ...DETAIL, progress: 150 }, cursors: NO_CURSORS });
    const { container } = render(<FieldOpsJobTracker onBack={() => {}} />);
    await waitFor(() => expect(container.querySelectorAll(".dash-card--click")).toHaveLength(2));
    fireEvent.click(container.querySelector(".dash-card--click")!);
    await waitFor(() => {
      const fill = container.querySelector(".dash-progress__fill") as HTMLElement | null;
      expect(fill?.style.width).toBe("100%");
    });
  });

  it("per-leg Load more re-fetches that leg's cursor", async () => {
    const { container } = await openDetail({ tasks: "task-cursor", time: null, insp: null });
    await waitFor(() => expect(container.querySelector(".dash-load-more button")).not.toBeNull());
    fireEvent.click(container.querySelector(".dash-load-more button")!);
    await waitFor(() => expect(api.fetchJobDetail).toHaveBeenCalledWith("JOB-A", { task: "task-cursor" }));
  });

  it("back button returns to list", async () => {
    const { container } = await openDetail();
    await waitFor(() => expect(container.querySelectorAll(".dash-card--click")).toHaveLength(0));
    fireEvent.click(container.querySelector(".dash-back-btn button")!);
    await waitFor(() => expect(container.querySelectorAll(".dash-card--click")).toHaveLength(2));
  });
});

describe("FieldOpsJobTracker — write UI", () => {
  async function openManagedDetail(caps: string[]) {
    vi.mocked(useAuth).mockReturnValue(authWith(caps));
    vi.mocked(api.fetchJobList).mockResolvedValue({ jobs: JOBS, next_cursor: null });
    vi.mocked(api.fetchJobDetail).mockResolvedValue({ job: DETAIL, cursors: NO_CURSORS });
    const utils = render(<FieldOpsJobTracker onBack={() => {}} />);
    await waitFor(() => expect(utils.container.querySelectorAll(".dash-card--click")).toHaveLength(2));
    fireEvent.click(utils.container.querySelector(".dash-card--click")!);
    await waitFor(() => expect(api.fetchJobDetail).toHaveBeenCalledWith("JOB-A"));
    return utils;
  }

  it("hides all write controls for a read-only user", async () => {
    vi.mocked(api.fetchJobList).mockResolvedValue({ jobs: JOBS, next_cursor: null });
    const { container } = render(<FieldOpsJobTracker onBack={() => {}} />);
    await waitFor(() => expect(container.querySelectorAll(".dash-card--click")).toHaveLength(2));
    // No "+ New job" affordance in the read-only list.
    expect(container.textContent ?? "").not.toContain("+ New job");
    expect(container.querySelector('[aria-label="Create job"]')).toBeNull();
  });

  it("manager can create a job; reloads the list on success", async () => {
    vi.mocked(useAuth).mockReturnValue(authWith(["cap.jobtracker.manage"]));
    vi.mocked(api.fetchJobList).mockResolvedValue({ jobs: JOBS, next_cursor: null });
    vi.mocked(api.createJob).mockResolvedValue({ job_id: "JOB-C" });
    const { container, getByText, getByPlaceholderText } = render(<FieldOpsJobTracker onBack={() => {}} />);
    await waitFor(() => expect(container.querySelectorAll(".dash-card--click")).toHaveLength(2));

    fireEvent.click(getByText("+ New job"));
    fireEvent.change(getByPlaceholderText("Job ID (e.g. JOB-1042)"), { target: { value: "job-c" } });
    fireEvent.change(getByPlaceholderText("Project name"), { target: { value: "Charlie" } });
    fireEvent.change(getByPlaceholderText("Client name (optional)"), { target: { value: "Globex" } });
    fireEvent.submit(container.querySelector('[aria-label="Create job"]')!);

    await waitFor(() =>
      expect(api.createJob).toHaveBeenCalledWith({
        job_id: "JOB-C", // trimmed + upper-cased client-side
        project_name: "Charlie",
        new_client: { name: "Globex" },
      }),
    );
    // Re-fetched the list after the create (initial mount + reload).
    await waitFor(() => expect(vi.mocked(api.fetchJobList).mock.calls.length).toBeGreaterThanOrEqual(2));
  });

  it("submits the full routing SoR (address, stakeholder, safety + progress contacts + CC) on create", async () => {
    vi.mocked(useAuth).mockReturnValue(authWith(["cap.jobtracker.manage"]));
    vi.mocked(api.fetchJobList).mockResolvedValue({ jobs: JOBS, next_cursor: null });
    vi.mocked(api.createJob).mockResolvedValue({ job_id: "JOB-C" });
    const { container, getByText, getAllByText, getByLabelText, getByPlaceholderText } = render(
      <FieldOpsJobTracker onBack={() => {}} />,
    );
    await waitFor(() => expect(container.querySelectorAll(".dash-card--click")).toHaveLength(2));

    fireEvent.click(getByText("+ New job"));
    fireEvent.change(getByPlaceholderText("Job ID (e.g. JOB-1042)"), { target: { value: "job-c" } });
    fireEvent.change(getByPlaceholderText("Project name"), { target: { value: "Charlie" } });
    fireEvent.change(getByPlaceholderText("Job address (optional)"), { target: { value: "1 Main St" } });
    fireEvent.change(getByPlaceholderText("Stakeholder name"), { target: { value: "Dana Owner" } });
    fireEvent.change(getByPlaceholderText("Stakeholder email"), { target: { value: "dana@ex.com" } });
    fireEvent.change(getByPlaceholderText("Stakeholder phone"), { target: { value: "555-0101" } });
    fireEvent.change(getByPlaceholderText("Safety contact name"), { target: { value: "Sam Safety" } });
    fireEvent.change(getByPlaceholderText("Safety contact email"), { target: { value: "sam@ex.com" } });
    fireEvent.click(getAllByText("+ Add CC")[0]); // Safety CC
    fireEvent.change(getByLabelText("Safety CC 1"), { target: { value: "scc@ex.com" } });
    fireEvent.change(getByPlaceholderText("Progress contact name"), { target: { value: "Pat Progress" } });
    fireEvent.change(getByPlaceholderText("Progress contact email"), { target: { value: "pat@ex.com" } });
    fireEvent.click(getAllByText("+ Add CC")[1]); // Progress CC
    fireEvent.change(getByLabelText("Progress CC 1"), { target: { value: "pcc@ex.com" } });

    fireEvent.submit(container.querySelector('[aria-label="Create job"]')!);

    await waitFor(() =>
      expect(api.createJob).toHaveBeenCalledWith(
        expect.objectContaining({
          job_id: "JOB-C",
          project_name: "Charlie",
          address: "1 Main St",
          stakeholder_name: "Dana Owner",
          stakeholder_email: "dana@ex.com",
          stakeholder_phone: "555-0101",
          safety_contact_name: "Sam Safety",
          safety_contact_email: "sam@ex.com",
          safety_cc: ["scc@ex.com"],
          progress_contact_name: "Pat Progress",
          progress_contact_email: "pat@ex.com",
          progress_cc: ["pcc@ex.com"],
        }),
      ),
    );
  });

  it("'Same as safety' copies the safety contact + CC into progress, which then stays independently editable", async () => {
    vi.mocked(useAuth).mockReturnValue(authWith(["cap.jobtracker.manage"]));
    vi.mocked(api.fetchJobList).mockResolvedValue({ jobs: JOBS, next_cursor: null });
    const { container, getByText, getAllByText, getByLabelText, getByPlaceholderText } = render(
      <FieldOpsJobTracker onBack={() => {}} />,
    );
    await waitFor(() => expect(container.querySelectorAll(".dash-card--click")).toHaveLength(2));

    fireEvent.click(getByText("+ New job"));
    fireEvent.change(getByPlaceholderText("Safety contact name"), { target: { value: "Sam Safety" } });
    fireEvent.change(getByPlaceholderText("Safety contact email"), { target: { value: "sam@ex.com" } });
    fireEvent.click(getAllByText("+ Add CC")[0]); // Safety CC
    fireEvent.change(getByLabelText("Safety CC 1"), { target: { value: "scc@ex.com" } });

    fireEvent.click(getByText("Same as safety"));

    expect((getByPlaceholderText("Progress contact name") as HTMLInputElement).value).toBe("Sam Safety");
    expect((getByPlaceholderText("Progress contact email") as HTMLInputElement).value).toBe("sam@ex.com");
    expect((getByLabelText("Progress CC 1") as HTMLInputElement).value).toBe("scc@ex.com");

    // After the copy the progress block is independently editable; safety is unchanged.
    fireEvent.change(getByPlaceholderText("Progress contact name"), { target: { value: "Pat Progress" } });
    expect((getByPlaceholderText("Progress contact name") as HTMLInputElement).value).toBe("Pat Progress");
    expect((getByPlaceholderText("Safety contact name") as HTMLInputElement).value).toBe("Sam Safety");
  });

  it("manager sees progress / add-task / lifecycle + routing controls and can set progress", async () => {
    vi.mocked(api.setJobProgress).mockResolvedValue({ progress: 75 });
    const { container, getByLabelText } = await openManagedDetail(["cap.jobtracker.manage"]);
    expect(container.querySelector('[aria-label="Update job progress"]')).not.toBeNull();
    expect(container.querySelector('[aria-label="Add a task"]')).not.toBeNull();
    // The bare "Close job" button is gone — replaced by the lifecycle selector + routing editor.
    expect((container.textContent ?? "").includes("Close job")).toBe(false);
    expect(container.querySelector('[aria-label="Set job lifecycle"]')).not.toBeNull();
    expect(container.querySelector('[aria-label="Job lifecycle"]')).not.toBeNull();
    expect((container.textContent ?? "").includes("Edit routing / contacts")).toBe(true);

    const form = getByLabelText("Update job progress");
    fireEvent.change(form.querySelector("input")!, { target: { value: "75" } });
    fireEvent.submit(form);
    await waitFor(() => expect(api.setJobProgress).toHaveBeenCalledWith("JOB-A", 75));
  });

  it("manager can add a task to the open job", async () => {
    vi.mocked(api.addTask).mockResolvedValue({ id: 99 });
    const { container, getByLabelText } = await openManagedDetail(["cap.jobtracker.manage"]);
    const form = getByLabelText("Add a task");
    fireEvent.change(form.querySelector("input")!, { target: { value: "Pour slab" } });
    fireEvent.submit(form);
    await waitFor(() => expect(api.addTask).toHaveBeenCalledWith("JOB-A", { description: "Pour slab" }));
    void container;
  });

  it("the lifecycle selector calls setLifecycle with the chosen value", async () => {
    vi.mocked(api.setLifecycle).mockResolvedValue({ lifecycle: "archived" });
    const { getByLabelText } = await openManagedDetail(["cap.jobtracker.manage"]);
    const select = getByLabelText("Job lifecycle") as HTMLSelectElement;
    expect(select.value).toBe("active"); // seeded from the active job's status
    fireEvent.change(select, { target: { value: "archived" } });
    await waitFor(() => expect(api.setLifecycle).toHaveBeenCalledWith("JOB-A", "archived"));
  });

  it("manager can edit routing / contacts on the open job", async () => {
    vi.mocked(api.editContacts).mockResolvedValue({ job_id: "JOB-A" });
    const { container, getByText, getByPlaceholderText } = await openManagedDetail(["cap.jobtracker.manage"]);
    fireEvent.click(getByText("Edit routing / contacts"));
    fireEvent.change(getByPlaceholderText("Safety contact email"), { target: { value: "new@ex.com" } });
    fireEvent.submit(container.querySelector('[aria-label="Edit routing and contacts"]')!);
    await waitFor(() =>
      expect(api.editContacts).toHaveBeenCalledWith(
        "JOB-A",
        expect.objectContaining({ safety_contact_email: "new@ex.com" }),
      ),
    );
  });

  it("cap.tasks.own renders a per-task status select and dispatches a change", async () => {
    vi.mocked(api.setTaskStatus).mockResolvedValue(undefined);
    const { getByLabelText } = await openManagedDetail(["cap.tasks.own"]);
    const select = getByLabelText("Set status for task 1") as HTMLSelectElement;
    expect(select.value).toBe("open");
    fireEvent.change(select, { target: { value: "done" } });
    await waitFor(() => expect(api.setTaskStatus).toHaveBeenCalledWith(1, "done"));
  });

  it("a tasks-only user gets the status select but no manage section", async () => {
    const { container } = await openManagedDetail(["cap.tasks.own"]);
    expect(container.querySelector('[aria-label="Set status for task 1"]')).not.toBeNull();
    expect(container.querySelector('[aria-label="Update job progress"]')).toBeNull();
    expect(container.querySelector('[aria-label="Add a task"]')).toBeNull();
    // The lifecycle selector + routing editor are manage-only, gated on cap.jobtracker.manage.
    expect(container.querySelector('[aria-label="Set job lifecycle"]')).toBeNull();
    expect(container.querySelector('[aria-label="Job lifecycle"]')).toBeNull();
    expect((container.textContent ?? "").includes("Edit routing / contacts")).toBe(false);
  });

  it("cap.time.log renders the Log time form and posts hours + task against the open job", async () => {
    vi.mocked(api.logTime).mockResolvedValue({ uuid: "u-1" });
    const { getByLabelText } = await openManagedDetail(["cap.time.log"]);
    const form = getByLabelText("Log time") as HTMLFormElement;
    fireEvent.change(form.querySelector('input[placeholder="Hours"]')!, { target: { value: "6.5" } });
    fireEvent.change(form.querySelector("select")!, { target: { value: "1" } }); // task #1
    fireEvent.change(form.querySelector('input[placeholder="Notes (optional)"]')!, { target: { value: "framing" } });
    fireEvent.submit(form);
    await waitFor(() =>
      expect(api.logTime).toHaveBeenCalledWith(
        expect.objectContaining({ job_id: "JOB-A", hours: 6.5, task_id: 1, notes: "framing" }),
      ),
    );
    // uuid is a client-generated idempotency key (integrity-bar).
    expect(vi.mocked(api.logTime).mock.calls[0][0].uuid).toBeTruthy();
  });

  it("Log time omits task_id for a job-level entry", async () => {
    vi.mocked(api.logTime).mockResolvedValue({ uuid: "u-2" });
    const { getByLabelText } = await openManagedDetail(["cap.time.log"]);
    const form = getByLabelText("Log time") as HTMLFormElement;
    fireEvent.change(form.querySelector('input[placeholder="Hours"]')!, { target: { value: "2" } });
    fireEvent.submit(form);
    await waitFor(() => expect(api.logTime).toHaveBeenCalled());
    const arg = vi.mocked(api.logTime).mock.calls[0][0];
    expect(arg).toMatchObject({ job_id: "JOB-A", hours: 2 });
    expect(arg.task_id).toBeUndefined();
  });

  it("hides the Log time form without cap.time.log", async () => {
    const { container } = await openManagedDetail(["cap.jobtracker.manage"]);
    expect(container.querySelector('[aria-label="Log time"]')).toBeNull();
  });
});
