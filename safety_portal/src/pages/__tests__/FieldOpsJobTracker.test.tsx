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
    reassignTask: vi.fn(),
    logTime: vi.fn(),
  };
});
vi.mock("../../lib/auth", () => ({ useAuth: vi.fn() }));
// Unified job-create flow: the detail-view assign controls call these two libs (P2.6 crew.assign +
// the equipment move). Mock the four fns the component imports; types are erased so the type-only
// PersonnelRow import needs no runtime stub.
vi.mock("../../lib/fieldops_personnel", () => ({ fetchPersonnelList: vi.fn(), assignPersonnel: vi.fn(), fetchMyCrew: vi.fn() }));
vi.mock("../../lib/fieldops_equipment", () => ({ fetchEquipmentList: vi.fn(), moveEquipment: vi.fn() }));

import * as api from "../../lib/fieldops_jobtracker";
import { fetchPersonnelList, assignPersonnel, fetchMyCrew, type PersonnelRow } from "../../lib/fieldops_personnel";
import { fetchEquipmentList, moveEquipment } from "../../lib/fieldops_equipment";
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
  // Safe empty defaults so the detail-view picker-load effect never rejects; assign tests override.
  vi.mocked(fetchPersonnelList).mockResolvedValue({ personnel: [], latest_entries: [], next_cursor: null });
  vi.mocked(fetchEquipmentList).mockResolvedValue({ equipment: [], next_cursor: null });
  // Slice T: a subcontractor's log-time picker fetches its own loggable crew; default empty.
  vi.mocked(fetchMyCrew).mockResolvedValue([]);
});

// Picker fixtures for the assign controls. Pat is unplaced; "Al Already" is already on JOB-A (so the
// crew-assign <select> should exclude him). The equipment item carries the full EquipmentHeader shape.
const PERSONNEL_OPTS: PersonnelRow[] = [
  { id: 10, name: "Pat Placed", trade: "operator", username: null, current_job: null },
  { id: 11, name: "Al Already", trade: "laborer", username: null, current_job: "JOB-A" },
];
const EQUIP_LIST = {
  equipment: [
    {
      id: 20, name: "Skid 1", kind: "skid-steer", identifier: "S1",
      status: "fmc" as const, status_note: null, status_changed_at: null, status_actor: null,
      location: null, latest_inspection: null, recent_logs: [],
    },
  ],
  next_cursor: null,
};

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
  crew: [{ id: 1, name: "Alice Chen", trade: "operator", account_role: "submitter" }],
  tasks: [{ id: 1, description: "Dig footings", status: "open", created_at: 100, personnel_id: 1, personnel_name: "Alice Chen" }],
  time_entries: [
    {
      uuid: "te-1", hours: 8, work_started_at: 1, work_ended_at: 2, recorded_at: 200, notes: "note",
      personnel_name: "Alice Chen", task_id: 1, task_description: "Dig footings", recorded_by_name: "Boss Bob",
    },
  ],
  equipment_on_site: [{ id: 5, name: "here-unit", kind: "skid-steer", identifier: "H1", label: "Site", read_at: 200 }],
  inspections: [{ uuid: "in-1", form_code: "skid-daily", version: 1, performed_at: 150, recorded_at: 150, equipment_name: "here-unit" }],
};

// R7 — the viewer's own linked roster row (worker `viewer_personnel`): id 1 = Alice Chen, so the
// own-only task tests and the "Me (Alice Chen)" log-time default resolve against the fixture task.
const VIEWER: api.ViewerPersonnel = { id: 1, name: "Alice Chen" };

const NO_CURSORS: { tasks: string | null; time: string | null; insp: string | null } = { tasks: null, time: null, insp: null };

describe("FieldOpsJobTracker — list view", () => {
  it("renders empty state when no jobs", async () => {
    vi.mocked(api.fetchJobList).mockResolvedValue({ jobs: [], next_cursor: null });
    const { container } = render(<FieldOpsJobTracker onBack={() => {}} />);
    await waitFor(() => expect(container.querySelector(".dash-unavail")).not.toBeNull());
    expect(container.textContent ?? "").toContain("No jobs for this status.");
  });

  it("renders job cards with pill, crew chips, open tasks (no progress bar)", async () => {
    vi.mocked(api.fetchJobList).mockResolvedValue({ jobs: JOBS, next_cursor: null });
    const { container } = render(<FieldOpsJobTracker onBack={() => {}} />);

    await waitFor(() => expect(container.querySelectorAll(".dash-card--click")).toHaveLength(2));
    const pills = Array.from(container.querySelectorAll(".dash-pill"));
    expect(pills.some((p) => p.classList.contains("dash-pill--ok"))).toBe(true); // active
    expect(pills.some((p) => p.classList.contains("dash-pill--warn"))).toBe(true); // on_hold
    expect(container.querySelector(".dash-progress")).toBeNull(); // progress % removed
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
    vi.mocked(api.fetchJobDetail).mockResolvedValue({ job: DETAIL, cursors: NO_CURSORS, viewer_personnel: VIEWER });
    const { container } = render(<FieldOpsJobTracker onBack={() => {}} />);

    await waitFor(() => expect(container.querySelectorAll(".dash-card--click")).toHaveLength(2));
    fireEvent.click(container.querySelector(".dash-card--click")!);
    await waitFor(() => expect(container.querySelector(".page__heading")?.textContent).toBe("Alpha"));
  });
});

describe("FieldOpsJobTracker — detail view", () => {
  async function openDetail(cursors = NO_CURSORS) {
    vi.mocked(api.fetchJobList).mockResolvedValue({ jobs: JOBS, next_cursor: null });
    vi.mocked(api.fetchJobDetail).mockResolvedValue({ job: DETAIL, cursors, viewer_personnel: VIEWER });
    const utils = render(<FieldOpsJobTracker onBack={() => {}} />);
    await waitFor(() => expect(utils.container.querySelectorAll(".dash-card--click")).toHaveLength(2));
    fireEvent.click(utils.container.querySelector(".dash-card--click")!);
    await waitFor(() => expect(api.fetchJobDetail).toHaveBeenCalledWith("JOB-A"));
    return utils;
  }

  it("renders header, client, crew, tasks, time, equipment, inspections (no progress bar)", async () => {
    const { container } = await openDetail();
    await waitFor(() => expect(container.querySelector(".page__heading")?.textContent).toBe("Alpha"));
    const txt = container.textContent ?? "";
    expect(txt).toContain("Acme Co"); // client
    expect(txt).toContain("Alice Chen"); // crew + time
    expect(txt).toContain("Dig footings"); // task
    expect(txt).toContain("here-unit"); // equipment on site
    expect(txt).toContain("skid-daily"); // inspection
    expect(container.querySelector(".dash-progress")).toBeNull(); // progress % removed
    expect(txt).not.toContain("Progress —");
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

  it("D2 retirement: the per-job Daily-checklist editor is GONE from the detail (even for the admin cap)", async () => {
    // Full-cap admin view — the strongest case: if the editor were still mounted anywhere, this
    // cap set would render it. The daily content lives in the daily-report-v2 form definition now.
    vi.mocked(useAuth).mockReturnValue(authWith(["cap.jobtracker.manage", "cap.checklist.manage", "cap.tasks.own", "cap.time.log"]));
    const { container } = await openDetail();
    await waitFor(() => expect(container.querySelector(".page__heading")?.textContent).toBe("Alpha"));
    expect(container.querySelector('[aria-label="Daily checklist"]')).toBeNull();
    expect(container.textContent ?? "").not.toContain("Daily checklist");
    expect(container.textContent ?? "").not.toContain("Add checklist item");
  });
});

describe("FieldOpsJobTracker — write UI", () => {
  async function openManagedDetail(caps: string[], detail: api.JobDetail = DETAIL) {
    vi.mocked(useAuth).mockReturnValue(authWith(caps));
    vi.mocked(api.fetchJobList).mockResolvedValue({ jobs: JOBS, next_cursor: null });
    vi.mocked(api.fetchJobDetail).mockResolvedValue({ job: detail, cursors: NO_CURSORS, viewer_personnel: VIEWER });
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
    // Slice 3: create routes into the new job's detail — mock the follow-up detail fetch.
    vi.mocked(api.fetchJobDetail).mockResolvedValue({
      job: { ...DETAIL, job_id: "JOB-C", project_name: "Charlie", crew: [], equipment_on_site: [] },
      cursors: NO_CURSORS,
    });
    const { container, getByText, getByPlaceholderText } = render(<FieldOpsJobTracker onBack={() => {}} />);
    await waitFor(() => expect(container.querySelectorAll(".dash-card--click")).toHaveLength(2));

    fireEvent.click(getByText("+ New job"));
    // Slice 6: no Job ID input — the office employee types only the Project Name; the portal assigns the id.
    fireEvent.change(getByPlaceholderText("Project name"), { target: { value: "Charlie" } });
    fireEvent.change(getByPlaceholderText("Client name (optional)"), { target: { value: "Globex" } });
    fireEvent.submit(container.querySelector('[aria-label="Create job"]')!);

    await waitFor(() =>
      expect(api.createJob).toHaveBeenCalledWith({
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

  it("manager sees add-task / lifecycle + routing controls; the progress % is fully removed", async () => {
    const { container } = await openManagedDetail(["cap.jobtracker.manage"]);
    expect(container.querySelector('[aria-label="Add a task"]')).not.toBeNull();
    // The bare "Close job" button is gone — replaced by the lifecycle selector + routing editor.
    expect((container.textContent ?? "").includes("Close job")).toBe(false);
    expect(container.querySelector('[aria-label="Set job lifecycle"]')).not.toBeNull();
    expect(container.querySelector('[aria-label="Job lifecycle"]')).not.toBeNull();
    expect((container.textContent ?? "").includes("Edit routing / contacts")).toBe(true);
    // Progress % is removed everywhere: no set-progress control, no progress bar, no "Progress —" label.
    expect(container.querySelector('[aria-label="Update job progress"]')).toBeNull();
    expect(container.querySelector(".dash-progress")).toBeNull();
    expect(container.textContent ?? "").not.toContain("Progress —");
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

  it("a manager (cap.tasks.assign, no jobtracker.manage) sees add-task + per-task assign but NOT job-create/lifecycle", async () => {
    // Assigned-Tasks S1: task authority widened to cap.tasks.assign, but job create / lifecycle /
    // routing stay cap.jobtracker.manage (admin).
    vi.mocked(useAuth).mockReturnValue(authWith(["cap.tasks.assign"]));
    vi.mocked(api.fetchJobList).mockResolvedValue({ jobs: JOBS, next_cursor: null });
    vi.mocked(api.fetchJobDetail).mockResolvedValue({ job: DETAIL, cursors: NO_CURSORS, viewer_personnel: VIEWER });
    const { container } = render(<FieldOpsJobTracker onBack={() => {}} />);
    await waitFor(() => expect(container.querySelectorAll(".dash-card--click")).toHaveLength(2));
    // List view: no "+ New job" affordance (job create stays admin-only).
    expect(container.textContent ?? "").not.toContain("+ New job");
    expect(container.querySelector('[aria-label="Create job"]')).toBeNull();
    // Open the detail.
    fireEvent.click(container.querySelector(".dash-card--click")!);
    await waitFor(() => expect(api.fetchJobDetail).toHaveBeenCalledWith("JOB-A"));
    // Add-task + per-task assign ARE present…
    expect(container.querySelector('[aria-label="Add a task"]')).not.toBeNull();
    expect(container.querySelector('[aria-label="Assign task 1"]')).not.toBeNull();
    // …but lifecycle + routing are withheld (admin-only).
    expect(container.querySelector('[aria-label="Set job lifecycle"]')).toBeNull();
    expect(container.querySelector('[aria-label="Job lifecycle"]')).toBeNull();
    expect((container.textContent ?? "").includes("Edit routing / contacts")).toBe(false);
  });

  it("cap.time.log renders the Log time form and posts hours + task against the open job", async () => {
    vi.mocked(api.logTime).mockResolvedValue({ uuid: "u-1" });
    const { getByLabelText } = await openManagedDetail(["cap.time.log"]);
    const form = getByLabelText("Log time") as HTMLFormElement;
    fireEvent.change(form.querySelector('input[placeholder="Hours"]')!, { target: { value: "6.5" } });
    fireEvent.change(getByLabelText("Log time task"), { target: { value: "1" } }); // task #1 (form now has 2 selects)
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

  // Slice T — a SUBCONTRACTOR (cap.time.log, NOT cap.personnel.manage) is offered self + the crew THEY
  // created (fetchMyCrew), not the job's full placed crew (which the Worker would 403 anyway).
  // R7: the self row (viewer id 1) renders as the explicit "Me (Alice Chen)" default, and crew
  // placed on a DIFFERENT job carry an "on <job>" annotation.
  it("subcontractor time-log picker offers Me + created crew (fetchMyCrew), not job.crew", async () => {
    vi.mocked(fetchMyCrew).mockResolvedValue([
      { id: 1, name: "Alice Chen", trade: "operator", current_job: "JOB-A" }, // self (viewer)
      { id: 77, name: "Helper Hank", trade: "laborer", current_job: "JOB-A" },
      { id: 78, name: "Rover Ray", trade: "laborer", current_job: "JOB-B" }, // placed elsewhere
    ]);
    const { getByLabelText } = await openManagedDetail(["cap.jobtracker.read", "cap.time.log"]);
    await waitFor(() => expect(fetchMyCrew).toHaveBeenCalled());
    const select = getByLabelText("Log time for") as HTMLSelectElement;
    const opts = Array.from(select.options).map((o) => o.textContent ?? "");
    expect(select.value).toBe("1"); // Me is the DEFAULT (resolves the viewer's personnel id)
    expect(opts).toContain("Me (Alice Chen)");
    expect(opts).toContain("Job-level (no person)");
    expect(opts).toContain("Helper Hank"); // created crew on THIS job — no annotation
    expect(opts).toContain("Rover Ray — on JOB-B"); // placed on another job — annotated
    expect(opts).not.toContain("Alice Chen"); // self is only offered as "Me (…)", never twice
  });

  // A MANAGER/admin (holds cap.personnel.manage) keeps the job's placed-crew picker (no fetchMyCrew).
  // R7: their own row (viewer) surfaces as the "Me (Alice Chen)" default too.
  it("manager/admin time-log picker keeps the job's placed crew with the Me default", async () => {
    const { getByLabelText } = await openManagedDetail(["cap.time.log", "cap.personnel.manage"]);
    const select = getByLabelText("Log time for") as HTMLSelectElement;
    const opts = Array.from(select.options).map((o) => o.textContent ?? "");
    expect(select.value).toBe("1"); // the viewer's own personnel id — the Me default
    expect(opts).toContain("Me (Alice Chen)");
    expect(opts).toContain("Job-level (no person)");
    expect(fetchMyCrew).not.toHaveBeenCalled();
  });

  it("add-task can assign the new task to a crew member (personnel_id)", async () => {
    vi.mocked(api.addTask).mockResolvedValue({ id: 100 });
    const { getByLabelText } = await openManagedDetail(["cap.jobtracker.manage"]);
    fireEvent.change(getByLabelText("Add a task").querySelector("input")!, { target: { value: "Pour slab" } });
    fireEvent.change(getByLabelText("Assign new task to"), { target: { value: "1" } }); // Alice Chen (crew id 1)
    fireEvent.submit(getByLabelText("Add a task"));
    await waitFor(() =>
      expect(api.addTask).toHaveBeenCalledWith("JOB-A", { description: "Pour slab", personnel_id: 1 }),
    );
  });

  it("the per-task assignee select reassigns to another crew member", async () => {
    vi.mocked(api.reassignTask).mockResolvedValue(undefined);
    const twoCrew: api.JobDetail = {
      ...DETAIL,
      crew: [
        { id: 1, name: "Alice Chen", trade: "operator", account_role: "submitter" },
        { id: 2, name: "Bob Vance", trade: "laborer", account_role: "submitter" },
      ],
      tasks: [{ id: 1, description: "Dig footings", status: "open", created_at: 100, personnel_id: 1, personnel_name: "Alice Chen" }],
    };
    const { getByLabelText } = await openManagedDetail(["cap.jobtracker.manage"], twoCrew);
    const sel = getByLabelText("Assign task 1") as HTMLSelectElement;
    expect(sel.value).toBe("1"); // currently Alice
    fireEvent.change(sel, { target: { value: "2" } }); // reassign to Bob
    await waitFor(() => expect(api.reassignTask).toHaveBeenCalledWith(1, 2));
  });

  it("the per-task assignee select can unassign (personnel_id null)", async () => {
    vi.mocked(api.reassignTask).mockResolvedValue(undefined);
    const { getByLabelText } = await openManagedDetail(["cap.jobtracker.manage"]);
    fireEvent.change(getByLabelText("Assign task 1"), { target: { value: "" } });
    await waitFor(() => expect(api.reassignTask).toHaveBeenCalledWith(1, null));
  });

  it("log time can be attributed to a specific person (personnel_id)", async () => {
    vi.mocked(api.logTime).mockResolvedValue({ uuid: "u-3" });
    // Attributing time to a member of the JOB's placed crew is a manager/admin power (Slice T: a
    // subcontractor is scoped to self + crew they created). cap.personnel.manage → the job.crew picker.
    const { getByLabelText } = await openManagedDetail(["cap.time.log", "cap.personnel.manage"]);
    const form = getByLabelText("Log time") as HTMLFormElement;
    fireEvent.change(form.querySelector('input[placeholder="Hours"]')!, { target: { value: "4" } });
    fireEvent.change(getByLabelText("Log time for"), { target: { value: "1" } }); // Alice (crew id 1)
    fireEvent.submit(form);
    await waitFor(() =>
      expect(api.logTime).toHaveBeenCalledWith(expect.objectContaining({ job_id: "JOB-A", hours: 4, personnel_id: 1 })),
    );
  });
});

describe("FieldOpsJobTracker — unified job-create flow (assign crew / equipment + create nudge)", () => {
  async function openDetailWith(caps: string[], detail: api.JobDetail = DETAIL) {
    vi.mocked(useAuth).mockReturnValue(authWith(caps));
    vi.mocked(api.fetchJobList).mockResolvedValue({ jobs: JOBS, next_cursor: null });
    vi.mocked(api.fetchJobDetail).mockResolvedValue({ job: detail, cursors: NO_CURSORS, viewer_personnel: VIEWER });
    const utils = render(<FieldOpsJobTracker onBack={() => {}} />);
    await waitFor(() => expect(utils.container.querySelectorAll(".dash-card--click")).toHaveLength(2));
    fireEvent.click(utils.container.querySelector(".dash-card--click")!);
    await waitFor(() => expect(api.fetchJobDetail).toHaveBeenCalledWith("JOB-A"));
    return utils;
  }

  it("a manager (crew.assign + equipment.field, no jobtracker.manage) sees assign controls but NOT add-task", async () => {
    const { container } = await openDetailWith(["cap.crew.assign", "cap.equipment.field"]);
    await waitFor(() => expect(container.querySelector('[aria-label="Assign crew to job"]')).not.toBeNull());
    expect(container.querySelector('[aria-label="Assign equipment to job"]')).not.toBeNull();
    // No jobtracker.manage → the whole "Manage job" section (incl. Add a task + progress) is withheld.
    expect(container.querySelector('[aria-label="Add a task"]')).toBeNull();
    expect(container.querySelector('[aria-label="Update job progress"]')).toBeNull();
  });

  it("assign-crew posts assignPersonnel(personId, job_id); excludes an already-placed person", async () => {
    vi.mocked(fetchPersonnelList).mockResolvedValue({ personnel: PERSONNEL_OPTS, latest_entries: [], next_cursor: null });
    vi.mocked(assignPersonnel).mockResolvedValue(undefined);
    const { getByLabelText } = await openDetailWith(["cap.crew.assign"]);
    // Wait for the async picker load to populate the <select> (assert INSIDE waitFor so it retries).
    await waitFor(() => {
      const s = getByLabelText("Crew member to place") as HTMLSelectElement;
      const values = Array.from(s.options).map((o) => o.value);
      expect(values).toContain("10"); // Pat Placed (unplaced) offered
      expect(values).not.toContain("11"); // Al Already (already on JOB-A) excluded
    });
    fireEvent.change(getByLabelText("Crew member to place"), { target: { value: "10" } });
    fireEvent.submit(getByLabelText("Assign crew to job"));
    await waitFor(() => expect(assignPersonnel).toHaveBeenCalledWith(10, "JOB-A"));
  });

  it("remove-crew is TWO-STEP (R7 ChipX): first tap arms 'Remove?', second posts assignPersonnel(personId, null)", async () => {
    vi.mocked(assignPersonnel).mockResolvedValue(undefined);
    const { getByLabelText, queryByLabelText } = await openDetailWith(["cap.crew.assign"]);
    // DETAIL.crew has Alice Chen (id 1); the ✕ remove button carries her aria-label.
    const removeBtn = await waitFor(() => getByLabelText("Remove Alice Chen from crew"));
    fireEvent.click(removeBtn); // arm — nothing posted yet
    expect(assignPersonnel).not.toHaveBeenCalled();
    const confirmBtn = getByLabelText("Confirm Remove Alice Chen from crew");
    expect(confirmBtn.textContent).toBe("Remove?");
    fireEvent.click(confirmBtn); // confirm
    await waitFor(() => expect(assignPersonnel).toHaveBeenCalledWith(1, null));
    void queryByLabelText;
  });

  it("assign-equipment posts moveEquipment(equipId, { job_id })", async () => {
    vi.mocked(fetchEquipmentList).mockResolvedValue(EQUIP_LIST);
    vi.mocked(moveEquipment).mockResolvedValue(undefined);
    const { getByLabelText } = await openDetailWith(["cap.equipment.field"]);
    await waitFor(() => {
      const s = getByLabelText("Equipment to move here") as HTMLSelectElement;
      expect(Array.from(s.options).map((o) => o.value)).toContain("20");
    });
    fireEvent.change(getByLabelText("Equipment to move here"), { target: { value: "20" } });
    fireEvent.submit(getByLabelText("Assign equipment to job"));
    await waitFor(() => expect(moveEquipment).toHaveBeenCalledWith(20, { job_id: "JOB-A" }));
  });

  it("hides the assign controls (and crew remove) without their caps", async () => {
    const { container } = await openDetailWith(["cap.jobtracker.manage"]); // manage, but not crew/equipment.field
    expect(container.querySelector('[aria-label="Assign crew to job"]')).toBeNull();
    expect(container.querySelector('[aria-label="Assign equipment to job"]')).toBeNull();
    expect(container.querySelector('[aria-label^="Remove "]')).toBeNull();
  });

  it("Slice 3: creating a job opens its detail with a dismissible 'finish setting up' nudge", async () => {
    vi.mocked(useAuth).mockReturnValue(authWith(["cap.jobtracker.manage"]));
    vi.mocked(api.fetchJobList).mockResolvedValue({ jobs: JOBS, next_cursor: null });
    vi.mocked(api.createJob).mockResolvedValue({ job_id: "JOB-C" });
    vi.mocked(api.fetchJobDetail).mockResolvedValue({
      job: { ...DETAIL, job_id: "JOB-C", project_name: "Charlie", crew: [], equipment_on_site: [] },
      cursors: NO_CURSORS,
    });
    const { container, getByText, getByPlaceholderText } = render(<FieldOpsJobTracker onBack={() => {}} />);
    await waitFor(() => expect(container.querySelectorAll(".dash-card--click")).toHaveLength(2));

    fireEvent.click(getByText("+ New job"));
    fireEvent.change(getByPlaceholderText("Project name"), { target: { value: "Charlie" } });
    fireEvent.submit(container.querySelector('[aria-label="Create job"]')!);

    await waitFor(() => expect(container.querySelector('[aria-label="Finish setting up job"]')).not.toBeNull());
    expect(container.textContent ?? "").toContain("Finish setting up JOB-C");
    expect(container.querySelector(".page__heading")?.textContent).toBe("Charlie");
    // The nudge highlights the empty crew/equipment sections.
    expect(container.textContent ?? "").toContain("needs crew");
    expect(container.textContent ?? "").toContain("needs equipment");
    // Dismissible.
    fireEvent.click(getByText("Done"));
    await waitFor(() => expect(container.querySelector('[aria-label="Finish setting up job"]')).toBeNull());
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// R7 — attribution + gating + never-silent polish.
// ─────────────────────────────────────────────────────────────────────────────
describe("FieldOpsJobTracker — R7 time attribution", () => {
  async function openDetail(caps: string[], detail: api.JobDetail = DETAIL, viewer: api.ViewerPersonnel | null = VIEWER) {
    vi.mocked(useAuth).mockReturnValue(authWith(caps));
    vi.mocked(api.fetchJobList).mockResolvedValue({ jobs: JOBS, next_cursor: null });
    vi.mocked(api.fetchJobDetail).mockResolvedValue({ job: detail, cursors: NO_CURSORS, viewer_personnel: viewer });
    const utils = render(<FieldOpsJobTracker onBack={() => {}} />);
    await waitFor(() => expect(utils.container.querySelectorAll(".dash-card--click")).toHaveLength(2));
    fireEvent.click(utils.container.querySelector(".dash-card--click")!);
    await waitFor(() => expect(api.fetchJobDetail).toHaveBeenCalledWith("JOB-A"));
    return utils;
  }

  it("with NO linked personnel the Me option is absent, Job-level is the explicit default, and the gap is said out loud", async () => {
    const { getByLabelText, container } = await openDetail(["cap.time.log", "cap.personnel.manage"], DETAIL, null);
    const select = getByLabelText("Log time for") as HTMLSelectElement;
    const opts = Array.from(select.options).map((o) => o.textContent ?? "");
    expect(select.value).toBe(""); // job-level — never a phantom "me"
    expect(opts.some((o) => o.startsWith("Me ("))).toBe(false);
    expect(opts).toContain("Job-level (no person)");
    expect(container.textContent ?? "").toContain("isn't linked to a roster person");
  });

  it("submitting with empty hours is blocked client-side with inline copy (no API call)", async () => {
    vi.mocked(api.logTime).mockResolvedValue({ uuid: "u-x" });
    const { getByLabelText, container } = await openDetail(["cap.time.log", "cap.personnel.manage"]);
    fireEvent.submit(getByLabelText("Log time"));
    expect(api.logTime).not.toHaveBeenCalled();
    const alert = container.querySelector('[role="alert"]');
    expect(alert?.textContent ?? "").toContain("more than 0, at most 24");
  });

  it("hours out of bounds (25) is blocked client-side; a valid entry clears the error and posts", async () => {
    vi.mocked(api.logTime).mockResolvedValue({ uuid: "u-y" });
    const { getByLabelText, container } = await openDetail(["cap.time.log", "cap.personnel.manage"]);
    const form = getByLabelText("Log time") as HTMLFormElement;
    const hours = form.querySelector('input[placeholder="Hours"]')!;
    fireEvent.change(hours, { target: { value: "25" } });
    fireEvent.submit(form);
    expect(api.logTime).not.toHaveBeenCalled();
    expect(container.querySelector('[role="alert"]')).not.toBeNull();
    fireEvent.change(hours, { target: { value: "7.5" } });
    expect(container.querySelector('[role="alert"]')).toBeNull(); // typing clears the inline error
    fireEvent.submit(form);
    await waitFor(() =>
      expect(api.logTime).toHaveBeenCalledWith(expect.objectContaining({ hours: 7.5, personnel_id: 1 })),
    );
  });

  it("the task picker suffixes done tasks with (done)", async () => {
    const withDone: api.JobDetail = {
      ...DETAIL,
      tasks: [
        ...DETAIL.tasks,
        { id: 2, description: "Old chore", status: "done", created_at: 90, personnel_id: null, personnel_name: null },
      ],
    };
    const { getByLabelText } = await openDetail(["cap.time.log", "cap.personnel.manage"], withDone);
    const opts = Array.from((getByLabelText("Log time task") as HTMLSelectElement).options).map((o) => o.textContent);
    expect(opts).toContain("Old chore (done)");
    expect(opts).toContain("Dig footings"); // open task — unsuffixed
  });

  it("a CLOSED job shows 'time can't be logged' instead of the form", async () => {
    const closed: api.JobDetail = { ...DETAIL, status: "closed" };
    const { container } = await openDetail(["cap.time.log", "cap.personnel.manage"], closed);
    expect(container.querySelector('[aria-label="Log time"]')).toBeNull();
    expect(container.textContent ?? "").toContain("This job is closed — time can't be logged.");
  });

  it("the time table renders Task + By columns from the worker joins", async () => {
    const { container } = await openDetail(["cap.jobtracker.read"]);
    const headers = Array.from(container.querySelectorAll(".dash-table--stack th")).map((th) => th.textContent);
    expect(headers).toEqual(["Who", "Hours", "Task", "By", "Recorded", "Notes"]);
    const cells = Array.from(container.querySelectorAll(".dash-table--stack td")).map((td) => td.textContent);
    expect(cells).toContain("Dig footings"); // task_description
    expect(cells).toContain("Boss Bob"); // recorded_by_name
  });

  it("the By column renders an em-dash (NEVER a raw username) when no roster name resolves", async () => {
    // (R7 review BLOCK fix) display-name-only posture: an unresolved recorder shows "—".
    const noName: api.JobDetail = {
      ...DETAIL,
      time_entries: [{ ...DETAIL.time_entries[0], recorded_by_name: null, personnel_name: null }],
    };
    const { container } = await openDetail(["cap.jobtracker.read"], noName);
    const cells = Array.from(container.querySelectorAll(".dash-table--stack td")).map((td) => td.textContent);
    expect(cells).not.toContain("boss.bob"); // the raw stamp must never surface
    expect(cells).toContain("—");
    expect(cells).toContain("Job-level"); // explicit no-subject wording
  });
});

describe("FieldOpsJobTracker — R7 task-control gating + optimistic status", () => {
  const MIXED_CREW: api.JobDetail = {
    ...DETAIL,
    crew: [
      { id: 1, name: "Alice Chen", trade: "operator", account_role: "submitter" },
      { id: 2, name: "Mo Manager", trade: "foreman", account_role: "manager" },
      { id: 3, name: "No Login Ned", trade: "laborer", account_role: null },
    ],
    tasks: [
      { id: 1, description: "Dig footings", status: "open", created_at: 100, personnel_id: 1, personnel_name: "Alice Chen" },
      { id: 2, description: "Manager's chore", status: "open", created_at: 90, personnel_id: 2, personnel_name: "Mo Manager" },
    ],
  };

  async function openDetail(caps: string[], detail: api.JobDetail = MIXED_CREW, viewer: api.ViewerPersonnel | null = VIEWER) {
    vi.mocked(useAuth).mockReturnValue(authWith(caps));
    vi.mocked(api.fetchJobList).mockResolvedValue({ jobs: JOBS, next_cursor: null });
    vi.mocked(api.fetchJobDetail).mockResolvedValue({ job: detail, cursors: NO_CURSORS, viewer_personnel: viewer });
    const utils = render(<FieldOpsJobTracker onBack={() => {}} />);
    await waitFor(() => expect(utils.container.querySelectorAll(".dash-card--click")).toHaveLength(2));
    fireEvent.click(utils.container.querySelector(".dash-card--click")!);
    await waitFor(() => expect(api.fetchJobDetail).toHaveBeenCalledWith("JOB-A"));
    return utils;
  }

  it("an own-only actor gets the status control ONLY on their own task (server rule mirrored)", async () => {
    const { container } = await openDetail(["cap.tasks.own"]);
    // Task 1 is assigned to the viewer's personnel (id 1) → control renders.
    expect(container.querySelector('[aria-label="Set status for task 1"]')).not.toBeNull();
    // Task 2 belongs to someone else → NO control (the worker would 403 forbidden_task anyway).
    expect(container.querySelector('[aria-label="Set status for task 2"]')).toBeNull();
  });

  it("an own-only actor with NO linked personnel gets no status controls at all", async () => {
    const { container } = await openDetail(["cap.tasks.own"], MIXED_CREW, null);
    expect(container.querySelector('[aria-label^="Set status for task"]')).toBeNull();
  });

  it("a manager/admin (task authority) keeps status controls on every task", async () => {
    const { container } = await openDetail(["cap.tasks.own", "cap.tasks.assign"]);
    expect(container.querySelector('[aria-label="Set status for task 1"]')).not.toBeNull();
    expect(container.querySelector('[aria-label="Set status for task 2"]')).not.toBeNull();
  });

  it("an assign-only manager sees non-subcontractor options DISABLED with hints, and the whole select locked on a manager-held task", async () => {
    vi.mocked(useAuth).mockReturnValue(authWith(["cap.tasks.assign"]));
    vi.mocked(api.fetchJobList).mockResolvedValue({ jobs: JOBS, next_cursor: null });
    vi.mocked(api.fetchJobDetail).mockResolvedValue({ job: MIXED_CREW, cursors: NO_CURSORS, viewer_personnel: null });
    const { container, getByLabelText } = render(<FieldOpsJobTracker onBack={() => {}} />);
    await waitFor(() => expect(container.querySelectorAll(".dash-card--click")).toHaveLength(2));
    fireEvent.click(container.querySelector(".dash-card--click")!);
    await waitFor(() => expect(api.fetchJobDetail).toHaveBeenCalledWith("JOB-A"));

    // Task 1 (held by a submitter): select live; manager + no-login options disabled with hints.
    const sel1 = getByLabelText("Assign task 1") as HTMLSelectElement;
    expect(sel1.disabled).toBe(false);
    const byText = new Map(Array.from(sel1.options).map((o) => [o.textContent ?? "", o.disabled]));
    expect(byText.get("Alice Chen")).toBe(false); // submitter-linked — assignable
    expect(byText.get("Mo Manager (manager)")).toBe(true);
    expect(byText.get("No Login Ned (no login)")).toBe(true);

    // Task 2 is currently HELD by a manager-linked person → the entire select locks (W1 mirror).
    expect((getByLabelText("Assign task 2") as HTMLSelectElement).disabled).toBe(true);
  });

  it("an ADMIN sees every assign option enabled (unrestricted server-side)", async () => {
    const { getByLabelText } = await openDetail(["cap.jobtracker.manage"]);
    const sel = getByLabelText("Assign task 1") as HTMLSelectElement;
    for (const o of Array.from(sel.options)) expect(o.disabled).toBe(false);
    expect((getByLabelText("Assign task 2") as HTMLSelectElement).disabled).toBe(false);
  });

  it("status change is OPTIMISTIC per-row: applied locally, no detail refetch, inline 'Updated.'", async () => {
    vi.mocked(api.setTaskStatus).mockResolvedValue(undefined);
    const { getByLabelText, container } = await openDetail(["cap.tasks.own"]);
    const callsBefore = vi.mocked(api.fetchJobDetail).mock.calls.length;
    const sel = getByLabelText("Set status for task 1") as HTMLSelectElement;
    fireEvent.change(sel, { target: { value: "done" } });
    await waitFor(() => expect(api.setTaskStatus).toHaveBeenCalledWith(1, "done"));
    await waitFor(() => expect(container.textContent ?? "").toContain("Updated."));
    expect(sel.value).toBe("done"); // applied locally
    expect(vi.mocked(api.fetchJobDetail).mock.calls.length).toBe(callsBefore); // no refetch
  });

  it("a FAILED status change reverts ONLY that row and shows the row-scoped error", async () => {
    vi.mocked(api.setTaskStatus).mockRejectedValue(new Error("You can only update tasks assigned to you."));
    const { getByLabelText, container } = await openDetail(["cap.tasks.own"]);
    const sel = getByLabelText("Set status for task 1") as HTMLSelectElement;
    fireEvent.change(sel, { target: { value: "done" } });
    await waitFor(() => expect(container.textContent ?? "").toContain("You can only update tasks assigned to you."));
    expect(sel.value).toBe("open"); // reverted
  });

  it("task pills and status options render humanized labels (no raw snake_case)", async () => {
    const inProgress: api.JobDetail = {
      ...MIXED_CREW,
      tasks: [{ id: 1, description: "Dig footings", status: "in_progress", created_at: 100, personnel_id: 1, personnel_name: "Alice Chen" }],
    };
    const { container } = await openDetail(["cap.tasks.own"], inProgress);
    expect(container.textContent ?? "").toContain("In progress");
    expect(container.textContent ?? "").not.toContain("in_progress");
  });
});

describe("FieldOpsJobTracker — R7 never-silent (swallow sites 3–6)", () => {
  it("initial list-load failure renders an error with a WORKING Retry (never the empty state)", async () => {
    vi.mocked(api.fetchJobList)
      .mockRejectedValueOnce(new Error("boom"))
      .mockResolvedValueOnce({ jobs: JOBS, next_cursor: null });
    const { container, getByLabelText } = render(<FieldOpsJobTracker onBack={() => {}} />);
    await waitFor(() => expect(container.textContent ?? "").toContain("Failed to load jobs."));
    expect(container.textContent ?? "").not.toContain("No jobs for this status."); // mutually exclusive
    fireEvent.click(getByLabelText("Retry loading jobs"));
    await waitFor(() => expect(container.querySelectorAll(".dash-card--click")).toHaveLength(2));
  });

  it("a failed detail open returns to the list with an error + Retry that re-opens the job", async () => {
    vi.mocked(api.fetchJobList).mockResolvedValue({ jobs: JOBS, next_cursor: null });
    vi.mocked(api.fetchJobDetail)
      .mockRejectedValueOnce(new Error("boom"))
      .mockResolvedValueOnce({ job: DETAIL, cursors: NO_CURSORS, viewer_personnel: VIEWER });
    const { container, getByLabelText } = render(<FieldOpsJobTracker onBack={() => {}} />);
    await waitFor(() => expect(container.querySelectorAll(".dash-card--click")).toHaveLength(2));
    fireEvent.click(container.querySelector(".dash-card--click")!);
    await waitFor(() => expect(container.textContent ?? "").toContain("Failed to load job details."));
    fireEvent.click(getByLabelText("Retry loading jobs"));
    await waitFor(() => expect(container.querySelector(".page__heading")?.textContent).toBe("Alpha"));
  });

  it("a leg Load-more failure surfaces IN THE DETAIL with Retry (was list-only, invisible)", async () => {
    vi.mocked(api.fetchJobList).mockResolvedValue({ jobs: JOBS, next_cursor: null });
    vi.mocked(api.fetchJobDetail)
      .mockResolvedValueOnce({ job: DETAIL, cursors: { tasks: "more", time: null, insp: null }, viewer_personnel: VIEWER })
      .mockRejectedValueOnce(new Error("boom"))
      .mockResolvedValueOnce({ job: DETAIL, cursors: NO_CURSORS, viewer_personnel: VIEWER });
    const { container, getByLabelText } = render(<FieldOpsJobTracker onBack={() => {}} />);
    await waitFor(() => expect(container.querySelectorAll(".dash-card--click")).toHaveLength(2));
    fireEvent.click(container.querySelector(".dash-card--click")!);
    await waitFor(() => expect(container.querySelector(".dash-load-more button")).not.toBeNull());
    fireEvent.click(container.querySelector(".dash-load-more button")!);
    await waitFor(() => expect(container.textContent ?? "").toContain("Failed to load more."));
    // Still on the detail (heading intact) and the Retry re-runs the leg fetch.
    expect(container.querySelector(".page__heading")?.textContent).toBe("Alpha");
    fireEvent.click(getByLabelText("Retry refreshing this job"));
    await waitFor(() => expect(vi.mocked(api.fetchJobDetail).mock.calls.length).toBe(3));
  });

  it("picker load failure (site 3) shows a visible error whose Retry re-fetches the options", async () => {
    vi.mocked(useAuth).mockReturnValue(authWith(["cap.crew.assign"]));
    vi.mocked(api.fetchJobList).mockResolvedValue({ jobs: JOBS, next_cursor: null });
    vi.mocked(api.fetchJobDetail).mockResolvedValue({ job: DETAIL, cursors: NO_CURSORS, viewer_personnel: VIEWER });
    vi.mocked(fetchPersonnelList)
      .mockRejectedValueOnce(new Error("boom"))
      .mockResolvedValueOnce({ personnel: PERSONNEL_OPTS, latest_entries: [], next_cursor: null });
    const { container, getByLabelText } = render(<FieldOpsJobTracker onBack={() => {}} />);
    await waitFor(() => expect(container.querySelectorAll(".dash-card--click")).toHaveLength(2));
    fireEvent.click(container.querySelector(".dash-card--click")!);
    await waitFor(() => expect(container.textContent ?? "").toContain("Couldn't load the crew picker options."));
    fireEvent.click(getByLabelText("Retry loading picker options"));
    await waitFor(() => {
      const s = getByLabelText("Crew member to place") as HTMLSelectElement;
      expect(Array.from(s.options).map((o) => o.value)).toContain("10");
    });
    expect(container.textContent ?? "").not.toContain("Couldn't load the crew picker options.");
  });

  it("fetchMyCrew failure (site 4) shows a visible error with Retry next to the log-time form", async () => {
    vi.mocked(useAuth).mockReturnValue(authWith(["cap.time.log"]));
    vi.mocked(api.fetchJobList).mockResolvedValue({ jobs: JOBS, next_cursor: null });
    vi.mocked(api.fetchJobDetail).mockResolvedValue({ job: DETAIL, cursors: NO_CURSORS, viewer_personnel: VIEWER });
    vi.mocked(fetchMyCrew)
      .mockRejectedValueOnce(new Error("boom"))
      .mockResolvedValueOnce([{ id: 77, name: "Helper Hank", trade: "laborer", current_job: "JOB-A" }]);
    const { container, getByLabelText } = render(<FieldOpsJobTracker onBack={() => {}} />);
    await waitFor(() => expect(container.querySelectorAll(".dash-card--click")).toHaveLength(2));
    fireEvent.click(container.querySelector(".dash-card--click")!);
    await waitFor(() => expect(container.textContent ?? "").toContain("Couldn't load your crew for the time log."));
    fireEvent.click(getByLabelText("Retry loading your crew"));
    await waitFor(() => {
      const opts = Array.from((getByLabelText("Log time for") as HTMLSelectElement).options).map((o) => o.textContent);
      expect(opts).toContain("Helper Hank");
    });
  });

  it("post-create detail-open failure (site 5) says the job WAS created and offers Retry", async () => {
    vi.mocked(useAuth).mockReturnValue(authWith(["cap.jobtracker.manage"]));
    vi.mocked(api.fetchJobList).mockResolvedValue({ jobs: JOBS, next_cursor: null });
    vi.mocked(api.createJob).mockResolvedValue({ job_id: "JOB-C" });
    vi.mocked(api.fetchJobDetail)
      .mockRejectedValueOnce(new Error("boom"))
      .mockResolvedValueOnce({
        job: { ...DETAIL, job_id: "JOB-C", project_name: "Charlie" },
        cursors: NO_CURSORS,
        viewer_personnel: VIEWER,
      });
    const { container, getByText, getByPlaceholderText, getByLabelText } = render(<FieldOpsJobTracker onBack={() => {}} />);
    await waitFor(() => expect(container.querySelectorAll(".dash-card--click")).toHaveLength(2));
    fireEvent.click(getByText("+ New job"));
    fireEvent.change(getByPlaceholderText("Project name"), { target: { value: "Charlie" } });
    fireEvent.submit(container.querySelector('[aria-label="Create job"]')!);
    await waitFor(() => expect(container.textContent ?? "").toContain("Job JOB-C was created, but opening it failed."));
    expect(container.textContent ?? "").toContain("Job JOB-C created."); // the success is never retracted
    fireEvent.click(getByLabelText("Retry loading jobs"));
    await waitFor(() => expect(container.querySelector(".page__heading")?.textContent).toBe("Charlie"));
  });

  it("mutation success + refetch failure: success message stands, stale-view warn appears with Retry", async () => {
    vi.mocked(useAuth).mockReturnValue(authWith(["cap.jobtracker.manage"]));
    vi.mocked(api.fetchJobList).mockResolvedValue({ jobs: JOBS, next_cursor: null });
    vi.mocked(api.addTask).mockResolvedValue({ id: 99 });
    vi.mocked(api.fetchJobDetail)
      .mockResolvedValueOnce({ job: DETAIL, cursors: NO_CURSORS, viewer_personnel: VIEWER }) // open
      .mockRejectedValueOnce(new Error("boom")) // post-mutation refresh fails
      .mockResolvedValueOnce({ job: DETAIL, cursors: NO_CURSORS, viewer_personnel: VIEWER }); // retry
    const { container, getByLabelText } = render(<FieldOpsJobTracker onBack={() => {}} />);
    await waitFor(() => expect(container.querySelectorAll(".dash-card--click")).toHaveLength(2));
    fireEvent.click(container.querySelector(".dash-card--click")!);
    await waitFor(() => expect(api.fetchJobDetail).toHaveBeenCalledWith("JOB-A"));
    const form = getByLabelText("Add a task");
    fireEvent.change(form.querySelector("input")!, { target: { value: "Pour slab" } });
    fireEvent.submit(form);
    await waitFor(() => expect(container.textContent ?? "").toContain("Task added.")); // NOT "failed"
    await waitFor(() => expect(container.textContent ?? "").toContain("Saved, but refreshing the job failed"));
    fireEvent.click(getByLabelText("Retry refreshing this job"));
    await waitFor(() => expect(container.textContent ?? "").not.toContain("Saved, but refreshing the job failed"));
  });
});

describe("FieldOpsJobTracker — R7 'Your job' badge", () => {
  it("badges the list card matching viewer_current_job (and only that one)", async () => {
    vi.mocked(api.fetchJobList).mockResolvedValue({ jobs: JOBS, next_cursor: null, viewer_current_job: "JOB-A" });
    const { container } = render(<FieldOpsJobTracker onBack={() => {}} />);
    await waitFor(() => expect(container.querySelectorAll(".dash-card--click")).toHaveLength(2));
    const cards = Array.from(container.querySelectorAll(".dash-card--click"));
    const alpha = cards.find((c) => c.textContent?.includes("Alpha"))!;
    const bravo = cards.find((c) => c.textContent?.includes("Bravo"))!;
    expect(alpha.textContent).toContain("Your job");
    expect(bravo.textContent).not.toContain("Your job");
  });

  it("no badge when the viewer is unlinked/unplaced (viewer_current_job null)", async () => {
    vi.mocked(api.fetchJobList).mockResolvedValue({ jobs: JOBS, next_cursor: null, viewer_current_job: null });
    const { container } = render(<FieldOpsJobTracker onBack={() => {}} />);
    await waitFor(() => expect(container.querySelectorAll(".dash-card--click")).toHaveLength(2));
    expect(container.textContent ?? "").not.toContain("Your job");
  });
});

describe("FieldOpsJobTracker — R7 deep link (initialJobId)", () => {
  it("mounting with initialJobId opens that job's detail directly", async () => {
    vi.mocked(api.fetchJobList).mockResolvedValue({ jobs: JOBS, next_cursor: null });
    vi.mocked(api.fetchJobDetail).mockResolvedValue({ job: DETAIL, cursors: NO_CURSORS, viewer_personnel: VIEWER });
    const { container } = render(<FieldOpsJobTracker onBack={() => {}} initialJobId="JOB-A" />);
    await waitFor(() => expect(api.fetchJobDetail).toHaveBeenCalledWith("JOB-A"));
    await waitFor(() => expect(container.querySelector(".page__heading")?.textContent).toBe("Alpha"));
    // Back returns to the normal list.
    fireEvent.click(container.querySelector(".dash-back-btn button")!);
    await waitFor(() => expect(container.querySelectorAll(".dash-card--click")).toHaveLength(2));
  });
});
