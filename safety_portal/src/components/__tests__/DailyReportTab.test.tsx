/**
 * DailyReportTab (SOP daily form, slice D2) — section-level detail tests: the Daily tab IS the
 * daily-report-v2 form now. Covers: date selector (Pacific today default, max today) + job line +
 * the v2 SOP form rendered inline through the REAL definition/renderer; the R2-carried explanatory
 * empty states (not-manager / unlinked / unplaced); never-silent placement load (error + working
 * Retry); best-effort crew/equipment/prepared_by prefill (+ the soft warn on failure); form_link
 * deep-links via the R3 openForm machinery + the live "Filed ✓" indicators; the "already filed"
 * banner + load-&-amend; the standard submit path (idempotent submission id, amends_uuid);
 * past-date filed-state-first collapse. Page-level integration (tabs, auto-switch, refresh) lives
 * in pages/__tests__/FieldOpsMyTasks.test.tsx.
 */
import { cleanup, fireEvent, render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../lib/auth", () => ({ useAuth: vi.fn() }));
vi.mock("../../lib/fieldops_jobtracker", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../lib/fieldops_jobtracker")>();
  return { ...actual, fetchJobList: vi.fn(), fetchJobDetail: vi.fn() };
});
vi.mock("../../lib/fieldops_daily_form", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../lib/fieldops_daily_form")>();
  return { ...actual, fetchDailyFormStatus: vi.fn() };
});
vi.mock("../../lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../lib/api")>();
  return { ...actual, fetchRecent: vi.fn(), submitForm: vi.fn() };
});

import * as api from "../../lib/api";
import * as jobs from "../../lib/fieldops_jobtracker";
import { fetchDailyFormStatus } from "../../lib/fieldops_daily_form";
import { ApiError } from "../../lib/errorCopy";
import { pacificToday } from "../myTasksShared";
import { DailyReportTab } from "../DailyReportTab";
import { useAuth } from "../../lib/auth";

function authAs(role: "submitter" | "manager" | "admin", capabilities: string[] = ["cap.tasks.own", "cap.jobtracker.read"]) {
  return {
    user: { username: "mgr.mo", role, capabilities },
    loading: false,
    login: vi.fn(async () => {}),
    logout: vi.fn(async () => {}),
  };
}

const TODAY = pacificToday();

const JOB_LIST: jobs.JobListResponse = {
  jobs: [{ job_id: "JOB-A", project_name: "Alpha", status: "active", progress: 0, client_name: null, crew: [], open_tasks: [] }],
  next_cursor: null,
  viewer_current_job: "JOB-A",
};
const UNPLACED_LIST: jobs.JobListResponse = { ...JOB_LIST, viewer_current_job: null };

const DETAIL: jobs.JobDetailResponse = {
  job: {
    job_id: "JOB-A",
    project_name: "Alpha",
    status: "active",
    progress: 0,
    client: null,
    crew: [
      { id: 1, name: "Sam", trade: "electrician", account_role: null },
      { id: 2, name: "Lee", trade: null, account_role: null },
    ],
    tasks: [],
    time_entries: [],
    equipment_on_site: [{ id: 9, name: "Excavator", kind: null, identifier: "EX-1", label: null, read_at: null }],
    inspections: [],
  },
  cursors: { tasks: null, time: null, insp: null },
  viewer_personnel: { id: 1, name: "Mo Manager" },
};

const EMPTY_STATUS = { filed: {}, daily_filed: null };

function inputValues(container: HTMLElement): string[] {
  return Array.from(container.querySelectorAll("input, textarea")).map((el) => (el as HTMLInputElement).value);
}
function dateInput(container: HTMLElement): HTMLInputElement {
  return container.querySelector('input[type="date"]') as HTMLInputElement;
}

afterEach(cleanup);
beforeEach(() => {
  vi.resetAllMocks();
  vi.mocked(useAuth).mockReturnValue(authAs("manager"));
  vi.mocked(jobs.fetchJobList).mockResolvedValue(JOB_LIST);
  vi.mocked(jobs.fetchJobDetail).mockResolvedValue(DETAIL);
  vi.mocked(fetchDailyFormStatus).mockResolvedValue(EMPTY_STATUS);
  vi.mocked(api.fetchRecent).mockResolvedValue(null);
  vi.mocked(api.submitForm).mockResolvedValue(undefined);
});

describe("DailyReportTab — the placed manager's inline SOP form", () => {
  it("renders the date selector (Pacific today, max today), the job line, and the v2 form inline", async () => {
    const { container, getByLabelText } = render(<DailyReportTab linked={true} onOpenForm={vi.fn()} />);
    await waitFor(() => expect(container.textContent ?? "").toContain("Alpha"));
    const dt = dateInput(container);
    expect(dt.value).toBe(TODAY);
    expect(dt.max).toBe(TODAY);
    expect(container.textContent ?? "").toContain("JOB-A");
    // The REAL daily-report-v2 definition rendered through the REAL FormRenderer: SOP guidance,
    // form_link buttons, and the submit control are all present INLINE (no picker anywhere).
    expect(container.textContent ?? "").toContain("SITE SUPERVISOR");
    expect(container.textContent ?? "").toContain("Create Job Hazard Analysis");
    // D3 (daily-report-v3): the manager's photo-upload place renders inline (the
    // site_photos header photo field → PhotoField), and the 50-photo minimum is gone.
    expect(container.textContent ?? "").toContain("Site photos");
    expect(container.querySelector(".photo-field")).not.toBeNull();
    expect(container.textContent ?? "").not.toContain("Minimum 50");
    expect(getByLabelText("Submit daily report")).not.toBeNull();
    expect(container.querySelector("select")).toBeNull(); // no job/form pickers — envelope is fixed
  });

  it("reports the placement up via onLoaded (drives the parent auto-switch + quick actions)", async () => {
    const onLoaded = vi.fn();
    render(<DailyReportTab linked={true} onLoaded={onLoaded} />);
    await waitFor(() =>
      expect(onLoaded).toHaveBeenCalledWith({ placement: { job_id: "JOB-A", project_name: "Alpha" } }),
    );
  });

  it("prefills crew_progress + equipment_on_site rows and prepared_by from the job detail", async () => {
    const { container } = render(<DailyReportTab linked={true} onOpenForm={vi.fn()} />);
    await waitFor(() => expect(inputValues(container)).toContain("Sam (electrician)"));
    const values = inputValues(container);
    expect(values).toContain("Lee"); // no trade → bare name
    expect(values).toContain("Excavator (EX-1)");
    expect(values).toContain("Mo Manager"); // prepared_by from viewer_personnel
  });

  it("a prefill failure is a soft warn, never a blocker: the form still renders and submits", async () => {
    vi.mocked(jobs.fetchJobDetail).mockRejectedValue(new ApiError(null, 500));
    const { container, getByLabelText } = render(<DailyReportTab linked={true} onOpenForm={vi.fn()} />);
    await waitFor(() => expect(container.textContent ?? "").toContain("Couldn't prefill crew and equipment"));
    expect(getByLabelText("Submit daily report")).not.toBeNull();
    expect((getByLabelText("Submit daily report") as HTMLButtonElement).disabled).toBe(false);
    expect(inputValues(container)).not.toContain("Sam (electrician)");
  });
});

describe("DailyReportTab — R2-carried explanatory empty states (Mandatory A)", () => {
  it("a non-manager gets the crew-lead copy and no placement fetch", async () => {
    vi.mocked(useAuth).mockReturnValue(authAs("submitter"));
    const onLoaded = vi.fn();
    const { container } = render(<DailyReportTab linked={true} onLoaded={onLoaded} />);
    await waitFor(() => expect(onLoaded).toHaveBeenCalledWith({ placement: null }));
    expect(container.textContent ?? "").toContain("crew-lead managers");
    expect(jobs.fetchJobList).not.toHaveBeenCalled();
    expect(container.querySelector('input[type="date"]')).toBeNull();
  });

  it("an UNLINKED manager (linked:false) gets the roster-link copy", async () => {
    vi.mocked(jobs.fetchJobList).mockResolvedValue(UNPLACED_LIST);
    const { container } = render(<DailyReportTab linked={false} />);
    await waitFor(() => expect(container.textContent ?? "").toContain("isn't linked to a roster person"));
    expect(container.textContent ?? "").not.toContain("not placed on a job yet");
  });

  it("a linked-but-unplaced manager gets the not-placed copy (+ onLoaded null)", async () => {
    vi.mocked(jobs.fetchJobList).mockResolvedValue(UNPLACED_LIST);
    const onLoaded = vi.fn();
    const { container } = render(<DailyReportTab linked={true} onLoaded={onLoaded} />);
    await waitFor(() => expect(container.textContent ?? "").toContain("not placed on a job yet"));
    expect(onLoaded).toHaveBeenCalledWith({ placement: null });
  });

  it("a placement load failure shows the error + a WORKING Retry (never a lying empty)", async () => {
    vi.mocked(jobs.fetchJobList)
      .mockRejectedValueOnce(new ApiError(null, 500))
      .mockResolvedValueOnce(JOB_LIST);
    const { container, getByLabelText } = render(<DailyReportTab linked={true} />);
    await waitFor(() => expect(container.textContent ?? "").toContain("Something went wrong on the server"));
    expect(container.textContent ?? "").not.toContain("not placed on a job yet");
    fireEvent.click(getByLabelText("Retry loading your daily report"));
    await waitFor(() => expect(container.textContent ?? "").toContain("Alpha"));
    expect(jobs.fetchJobList).toHaveBeenCalledTimes(2);
  });
});

describe("DailyReportTab — form_link deep-links + filed indicators", () => {
  it("a form_link button deep-links via openForm with the tab's job + date (returnTo rides App)", async () => {
    const onOpenForm = vi.fn();
    const { getByRole } = render(<DailyReportTab linked={true} onOpenForm={onOpenForm} />);
    const btn = await waitFor(() => getByRole("button", { name: /Create Job Hazard Analysis/ }));
    fireEvent.click(btn);
    expect(onOpenForm).toHaveBeenCalledWith(
      expect.objectContaining({ jobId: "JOB-A", parentCode: "jha", workDate: TODAY }),
    );
  });

  it("renders a live 'Filed ✓ <time> by <name>' indicator from the status endpoint", async () => {
    vi.mocked(fetchDailyFormStatus).mockResolvedValue({
      filed: { jha: { filed_at: 1_700_000_000, filed_by_name: "Sam Submitter" } },
      daily_filed: null,
    });
    const { container } = render(<DailyReportTab linked={true} onOpenForm={vi.fn()} />);
    await waitFor(() => expect(container.textContent ?? "").toContain("Filed ✓"));
    expect(container.textContent ?? "").toContain("by Sam Submitter");
  });

  it("a NULL filed_by_name drops the 'by …' clause (display-name-only, never a raw account)", async () => {
    vi.mocked(fetchDailyFormStatus).mockResolvedValue({
      filed: { jha: { filed_at: 1_700_000_000, filed_by_name: null } },
      daily_filed: null,
    });
    const { container } = render(<DailyReportTab linked={true} onOpenForm={vi.fn()} />);
    await waitFor(() => expect(container.textContent ?? "").toContain("Filed ✓"));
    expect(container.querySelector(".fr__form-link-filed")?.textContent ?? "").not.toContain(" by ");
  });

  it("a status read failure soft-warns with Retry — the form stays fillable (never silent)", async () => {
    vi.mocked(fetchDailyFormStatus)
      .mockRejectedValueOnce(new ApiError(null, 500))
      .mockResolvedValue({ filed: { jha: { filed_at: 1_700_000_000, filed_by_name: null } }, daily_filed: null });
    const { container, getByLabelText } = render(<DailyReportTab linked={true} onOpenForm={vi.fn()} />);
    // R1 convention: the ApiError's HUMAN copy surfaces (errMsg falls back only on non-Errors);
    // the `what`-scoped Retry disambiguates it from any sibling error.
    await waitFor(() => expect(getByLabelText("Retry checking filed forms")).not.toBeNull());
    expect(container.textContent ?? "").toContain("Something went wrong on the server");
    expect((getByLabelText("Submit daily report") as HTMLButtonElement).disabled).toBe(false);
    fireEvent.click(getByLabelText("Retry checking filed forms"));
    await waitFor(() => expect(container.textContent ?? "").toContain("Filed ✓"));
  });

  it("a date change refetches the status for the NEW date", async () => {
    const { container } = render(<DailyReportTab linked={true} onOpenForm={vi.fn()} />);
    await waitFor(() => expect(fetchDailyFormStatus).toHaveBeenCalledWith("JOB-A", TODAY));
    fireEvent.change(dateInput(container), { target: { value: "2026-01-15" } });
    await waitFor(() => expect(fetchDailyFormStatus).toHaveBeenCalledWith("JOB-A", "2026-01-15"));
  });
});

describe("DailyReportTab — filed banner + amend + submit", () => {
  const FILED = { filed_at: 1_700_000_000, filed_by_name: "Mo Manager" };

  it("shows the 'Daily report filed ✓' banner when daily_filed is set, with the form still open today", async () => {
    vi.mocked(fetchDailyFormStatus).mockResolvedValue({ filed: { "daily-report": FILED }, daily_filed: FILED });
    const { container, getByLabelText } = render(<DailyReportTab linked={true} onOpenForm={vi.fn()} />);
    await waitFor(() => expect(container.textContent ?? "").toContain("Daily report filed ✓"));
    expect(container.textContent ?? "").toContain("by Mo Manager");
    // Today: the form renders OPEN below the banner (file-another / amend stays one tap away).
    expect(container.querySelector("details")).toBeNull();
    expect(getByLabelText("Submit daily report")).not.toBeNull();
  });

  it("Load & amend seeds the prior values and submits WITH amends_uuid (the existing amend machinery)", async () => {
    vi.mocked(fetchDailyFormStatus).mockResolvedValue({ filed: { "daily-report": FILED }, daily_filed: FILED });
    vi.mocked(api.fetchRecent).mockResolvedValue({ submission_uuid: "prior-1", values: { weather: "Sunny" } });
    const { container, getByText, getByLabelText } = render(<DailyReportTab linked={true} onOpenForm={vi.fn()} />);
    const amendBtn = await waitFor(() => getByText("Load & amend it"));
    fireEvent.click(amendBtn);
    expect(inputValues(container)).toContain("Sunny");
    const submit = getByLabelText("Submit daily report") as HTMLButtonElement;
    expect(submit.textContent).toContain("Submit amendment");
    fireEvent.click(submit);
    await waitFor(() =>
      expect(api.submitForm).toHaveBeenCalledWith(expect.objectContaining({ amends_uuid: "prior-1" })),
    );
  });

  it("submit goes through the standard send-free path with the fixed envelope + idempotent id", async () => {
    const { container, getByLabelText } = render(<DailyReportTab linked={true} onOpenForm={vi.fn()} />);
    await waitFor(() => expect(getByLabelText("Submit daily report")).not.toBeNull());
    fireEvent.click(getByLabelText("Submit daily report"));
    await waitFor(() =>
      expect(api.submitForm).toHaveBeenCalledWith(
        expect.objectContaining({
          job_id: "JOB-A",
          form_code: "daily-report-v3",
          work_date: TODAY,
          amends_uuid: null,
          submission_uuid: expect.any(String),
        }),
      ),
    );
    // The D3 site-photos key rides the standard values payload (initialValues seeds
    // every header photo field to [] — the Worker skips empty photo arrays).
    const sent = vi.mocked(api.submitForm).mock.calls[0][0] as { values: Record<string, unknown> };
    expect(sent.values.site_photos).toEqual([]);
    // Success feedback + the status refetch that flips the indicators/banner.
    await waitFor(() => expect(container.textContent ?? "").toContain("Submitted ✓"));
    expect(vi.mocked(fetchDailyFormStatus).mock.calls.length).toBeGreaterThanOrEqual(2);
  });

  it("a submit failure shows the inline error and re-enables the button (never silent)", async () => {
    vi.mocked(api.submitForm).mockRejectedValue(new Error("Submission failed. Please try again."));
    const { container, getByLabelText } = render(<DailyReportTab linked={true} onOpenForm={vi.fn()} />);
    await waitFor(() => expect(getByLabelText("Submit daily report")).not.toBeNull());
    fireEvent.click(getByLabelText("Submit daily report"));
    await waitFor(() => expect(container.textContent ?? "").toContain("Submission failed."));
    expect((getByLabelText("Submit daily report") as HTMLButtonElement).disabled).toBe(false);
    expect(container.textContent ?? "").not.toContain("Submitted ✓");
  });

  it("a PAST date with a filing defaults to the filed state first: the form collapses behind a disclosure", async () => {
    vi.mocked(fetchDailyFormStatus).mockResolvedValue({ filed: { "daily-report": FILED }, daily_filed: FILED });
    const { container } = render(<DailyReportTab linked={true} onOpenForm={vi.fn()} />);
    await waitFor(() => expect(container.textContent ?? "").toContain("Daily report filed ✓"));
    fireEvent.change(dateInput(container), { target: { value: "2026-01-15" } });
    await waitFor(() => expect(container.querySelector("details")).not.toBeNull());
    const details = container.querySelector("details")!;
    expect(details.hasAttribute("open")).toBe(false); // filed state first; the form is one tap away
    expect(details.textContent ?? "").toContain("File another or amend for this date");
    expect(details.querySelector('[aria-label="Submit daily report"]')).not.toBeNull();
  });
});

describe("DailyReportTab — draft persistence (the deep-link data-loss BLOCK fix)", () => {
  beforeEach(() => sessionStorage.clear());

  it("typed values survive an unmount/remount (a form_link navigation can no longer destroy the day's work)", async () => {
    const first = render(<DailyReportTab linked={true} onOpenForm={vi.fn()} />);
    await waitFor(() => expect(first.container.textContent ?? "").toContain("SITE SUPERVISOR"));
    fireEvent.change(first.getByLabelText("Weather"), { target: { value: "Sunny, light wind" } });
    first.unmount(); // = the App page-node swap when a form_link deep-link fires
    const second = render(<DailyReportTab linked={true} onOpenForm={vi.fn()} />);
    await waitFor(() => {
      expect((second.getByLabelText("Weather") as HTMLInputElement).value).toBe("Sunny, light wind"); // the draft WON over the prefill
    });
  });

  it("a successful submit clears the draft — the next mount starts fresh", async () => {
    const first = render(<DailyReportTab linked={true} onOpenForm={vi.fn()} />);
    await waitFor(() => expect(first.container.textContent ?? "").toContain("SITE SUPERVISOR"));
    fireEvent.change(first.getByLabelText("Weather"), { target: { value: "Overcast" } });
    fireEvent.click(first.getByLabelText("Submit daily report"));
    await waitFor(() => expect(api.submitForm).toHaveBeenCalled());
    first.unmount();
    const second = render(<DailyReportTab linked={true} onOpenForm={vi.fn()} />);
    await waitFor(() => expect(second.container.textContent ?? "").toContain("SITE SUPERVISOR"));
    expect((second.getByLabelText("Weather") as HTMLInputElement).value).toBe(""); // draft cleared on filing
  });

  it("drafts are per-date: switching the date swaps to that date's draft (or the seed), and back", async () => {
    const view = render(<DailyReportTab linked={true} onOpenForm={vi.fn()} />);
    const { container } = view;
    await waitFor(() => expect(container.textContent ?? "").toContain("SITE SUPERVISOR"));
    const weather = () => view.getByLabelText("Weather") as HTMLInputElement;
    fireEvent.change(weather(), { target: { value: "Today's weather" } });
    fireEvent.change(dateInput(container), { target: { value: "2026-07-01" } });
    await waitFor(() => expect(weather().value).toBe("")); // yesterday starts from the seed
    fireEvent.change(weather(), { target: { value: "Yesterday's weather" } });
    fireEvent.change(dateInput(container), { target: { value: TODAY } });
    await waitFor(() => expect(weather().value).toBe("Today's weather")); // each day kept its own
  });
});
