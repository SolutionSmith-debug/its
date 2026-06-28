/**
 * Field Ops Equipment page (BRIEF B).
 * List renders dash-grid of dash-card--click with status pill; detail shows location/inspections/logs.
 * Mirrors FieldOpsPersonnel.test.tsx: vi.mock the lib, mock BOTH fetchers before render, query by
 * specific classes. resetAllMocks (not clearAllMocks) so mockResolvedValueOnce queues don't leak.
 */
import { cleanup, fireEvent, render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../lib/fieldops_equipment", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../lib/fieldops_equipment")>();
  return {
    ...actual,
    fetchEquipmentList: vi.fn(),
    fetchEquipmentDetail: vi.fn(),
    setEquipmentStatus: vi.fn(),
    logEquipmentMaintenance: vi.fn(),
  };
});

vi.mock("../../lib/auth", () => ({ useAuth: vi.fn() }));

import * as api from "../../lib/fieldops_equipment";
import { FieldOpsEquipment } from "../FieldOpsEquipment";
import { useAuth } from "../../lib/auth";

afterEach(cleanup);
beforeEach(() => {
  vi.resetAllMocks();
  vi.mocked(useAuth).mockReturnValue({
    user: { username: "submitter.jim", role: "submitter", capabilities: ["cap.equipment.field"] },
    loading: false,
    login: vi.fn(async () => {}),
    logout: vi.fn(async () => {}),
  });
});

const EQUIPMENT_LIST: api.EquipmentListResponse["equipment"] = [
  {
    id: 1,
    name: "Unit Alpha",
    kind: "skid-steer",
    identifier: "SK-001",
    status: "fmc",
    status_note: null,
    status_changed_at: null,
    status_actor: null,
    location: { equipment_id: 1, id: 10, label: "Site A", lat: 37.7749, lon: -122.4194, read_at: 1_686_000_000, recorded_at: 1_686_000_000, job_id: "JOB-A" },
    latest_inspection: { equipment_id: 1, uuid: "i-1", form_code: "skid-daily", version: 1, performed_at: 1_685_996_400, recorded_at: 1_685_996_400, job_id: "JOB-A" },
    recent_logs: [
      { equipment_id: 1, uuid: "l-1", log_type: "fuel", value_num: 25, detail: null, status_value: null, performed_at: 1_685_992_800, recorded_at: 1_685_992_800 },
    ],
  },
  {
    id: 2,
    name: "Unit Beta",
    kind: "telehandler",
    identifier: "TH-002",
    status: "degraded",
    status_note: "Brake pad wear detected",
    status_changed_at: null,
    status_actor: null,
    location: null,
    latest_inspection: null,
    recent_logs: [],
  },
];

const DETAIL_DATA: api.EquipmentDetail = {
  header: { id: 1, name: "Unit Alpha", kind: "skid-steer", identifier: "SK-001", status: "fmc", status_note: null, status_changed_at: null, status_actor: null },
  locations: [{ equipment_id: 1, id: 10, label: "Site A", lat: 37.7749, lon: -122.4194, read_at: 1_686_000_000, recorded_at: 1_686_000_000, job_id: "JOB-A" }],
  inspections: [{ uuid: "i-1", equipment_id: 1, form_code: "skid-daily", version: 1, performed_at: 1_685_996_400, recorded_at: 1_685_996_400, job_id: "JOB-A" }],
  logs: [{ uuid: "l-1", equipment_id: 1, log_type: "fuel", value_num: 25, detail: null, status_value: null, performed_at: 1_685_992_800, recorded_at: 1_685_992_800 }],
};

const NO_CURSORS = { loc: null, insp: null, log: null };

describe("FieldOpsEquipment — list view", () => {
  it("renders empty state when no equipment", async () => {
    vi.mocked(api.fetchEquipmentList).mockResolvedValue({ equipment: [], next_cursor: null });
    const { container } = render(<FieldOpsEquipment onBack={() => {}} />);

    await waitFor(() => expect(container.querySelector(".dash-unavail")).not.toBeNull());
    expect(container.textContent ?? "").toContain("No active equipment.");
  });

  it("renders dash-grid of cards with status pill", async () => {
    vi.mocked(api.fetchEquipmentList).mockResolvedValue({ equipment: EQUIPMENT_LIST, next_cursor: null });
    const { container } = render(<FieldOpsEquipment onBack={() => {}} />);

    await waitFor(() => expect(container.querySelectorAll(".dash-card--click")).toHaveLength(2));

    const pills = Array.from(container.querySelectorAll(".dash-pill"));
    expect(pills.some((p) => p.classList.contains("dash-pill--ok"))).toBe(true);
    expect(pills.some((p) => p.classList.contains("dash-pill--warn"))).toBe(true);

    expect(container.textContent ?? "").toContain("Unit Alpha");
    expect(container.textContent ?? "").toContain("Site A");
    expect(container.textContent ?? "").toContain("Unit Beta");
    expect(container.textContent ?? "").toContain("Unavailable");
  });

  it("row click opens detail", async () => {
    vi.mocked(api.fetchEquipmentList).mockResolvedValue({ equipment: EQUIPMENT_LIST, next_cursor: null });
    vi.mocked(api.fetchEquipmentDetail).mockResolvedValue({ equipment: DETAIL_DATA, cursors: NO_CURSORS });
    const { container } = render(<FieldOpsEquipment onBack={() => {}} />);

    await waitFor(() => expect(container.querySelectorAll(".dash-card--click")).toHaveLength(2));
    fireEvent.click(container.querySelector(".dash-card--click")!);

    await waitFor(() => {
      expect(container.querySelectorAll(".dash-card--click")).toHaveLength(0);
      expect(container.querySelector(".page__heading")?.textContent).toBe("Unit Alpha");
    });
  });

  it("Load more button fetches next page", async () => {
    vi.mocked(api.fetchEquipmentList)
      .mockResolvedValueOnce({ equipment: EQUIPMENT_LIST.slice(0, 1), next_cursor: "cursor-1" })
      .mockResolvedValueOnce({ equipment: EQUIPMENT_LIST.slice(1, 2), next_cursor: null });
    const { container } = render(<FieldOpsEquipment onBack={() => {}} />);

    await waitFor(() => expect(container.querySelector(".dash-load-more button")).not.toBeNull());
    fireEvent.click(container.querySelector(".dash-load-more button")!);

    await waitFor(() => expect(api.fetchEquipmentList).toHaveBeenLastCalledWith("cursor-1"));
  });
});

describe("FieldOpsEquipment — detail view", () => {
  async function openDetail(detail = DETAIL_DATA, cursors = NO_CURSORS) {
    vi.mocked(api.fetchEquipmentList).mockResolvedValue({ equipment: EQUIPMENT_LIST, next_cursor: null });
    vi.mocked(api.fetchEquipmentDetail).mockResolvedValue({ equipment: detail, cursors });
    const utils = render(<FieldOpsEquipment onBack={() => {}} />);
    await waitFor(() => expect(utils.container.querySelectorAll(".dash-card--click")).toHaveLength(2));
    fireEvent.click(utils.container.querySelector(".dash-card--click")!);
    await waitFor(() => expect(api.fetchEquipmentDetail).toHaveBeenCalledWith(1, undefined));
    return utils;
  }

  it("renders header with status pill and snapshot fields", async () => {
    const { container } = await openDetail();
    await waitFor(() => expect(container.querySelector(".page__heading")?.textContent).toBe("Unit Alpha"));
    const pills = Array.from(container.querySelectorAll(".dash-pill"));
    expect(pills.some((p) => p.classList.contains("dash-pill--ok"))).toBe(true);
    expect(container.textContent ?? "").toContain("Full Mission Capable");
  });

  it("location section renders the location row", async () => {
    // Detail has a location table AND an inspection table; assert the location data by its label
    // rather than a cross-section tbody count.
    const { container } = await openDetail();
    await waitFor(() => expect(container.textContent ?? "").toContain("Site A"));
    expect(container.querySelector("table.dash-table")).not.toBeNull();
  });

  it("inspections section renders the inspection row", async () => {
    const { container } = await openDetail();
    await waitFor(() => expect(container.textContent ?? "").toContain("skid-daily"));
  });

  it("logs section renders the log list", async () => {
    const { container } = await openDetail();
    await waitFor(() => expect(container.querySelectorAll("ul.dash-loglist li")).toHaveLength(1));
  });

  it("Load more buttons for each leg fetch independently", async () => {
    // First detail load has only the location leg paginated (loc cursor set) → exactly one
    // "Load more" button, in the location section. Clicking it re-fetches with { loc }.
    vi.mocked(api.fetchEquipmentList).mockResolvedValue({ equipment: EQUIPMENT_LIST, next_cursor: null });
    vi.mocked(api.fetchEquipmentDetail)
      .mockResolvedValueOnce({ equipment: DETAIL_DATA, cursors: { loc: "loc-cursor", insp: null, log: null } })
      .mockResolvedValueOnce({
        equipment: { ...DETAIL_DATA, locations: [{ ...DETAIL_DATA.locations[0], recorded_at: 1_685_999_900 }] },
        cursors: NO_CURSORS,
      });
    const { container } = render(<FieldOpsEquipment onBack={() => {}} />);

    await waitFor(() => expect(container.querySelectorAll(".dash-card--click")).toHaveLength(2));
    fireEvent.click(container.querySelector(".dash-card--click")!);
    await waitFor(() => expect(container.querySelector(".dash-load-more button")).not.toBeNull());

    fireEvent.click(container.querySelector(".dash-load-more button")!);
    await waitFor(() => expect(api.fetchEquipmentDetail).toHaveBeenCalledWith(1, { loc: "loc-cursor" }));
  });

  it("back button returns to list", async () => {
    const { container } = await openDetail();
    await waitFor(() => expect(container.querySelectorAll(".dash-card--click")).toHaveLength(0));

    fireEvent.click(container.querySelector(".dash-back-btn button")!);
    await waitFor(() => expect(container.querySelectorAll(".dash-card--click")).toHaveLength(2));
  });
});

describe("FieldOpsEquipment — field actions (write, cap.equipment.field)", () => {
  async function openDetail() {
    vi.mocked(api.fetchEquipmentList).mockResolvedValue({ equipment: EQUIPMENT_LIST, next_cursor: null });
    vi.mocked(api.fetchEquipmentDetail).mockResolvedValue({ equipment: DETAIL_DATA, cursors: NO_CURSORS });
    const utils = render(<FieldOpsEquipment onBack={() => {}} />);
    await waitFor(() => expect(utils.container.querySelectorAll(".dash-card--click")).toHaveLength(2));
    fireEvent.click(utils.container.querySelector(".dash-card--click")!);
    await waitFor(() => expect(api.fetchEquipmentDetail).toHaveBeenCalledWith(1, undefined));
    return utils;
  }

  it("renders the field-action forms when the user has cap.equipment.field", async () => {
    const { container } = await openDetail();
    await waitFor(() => expect(container.querySelector("form[aria-label='Update readiness status']")).not.toBeNull());
    expect(container.querySelector("form[aria-label='Add machine log']")).not.toBeNull();
  });

  it("hides the field-action forms when the user lacks the cap", async () => {
    vi.mocked(useAuth).mockReturnValue({
      user: { username: "x.y", role: "submitter", capabilities: [] },
      loading: false,
      login: vi.fn(async () => {}),
      logout: vi.fn(async () => {}),
    });
    const { container } = await openDetail();
    await waitFor(() => expect(container.querySelector(".page__heading")?.textContent).toBe("Unit Alpha"));
    expect(container.querySelector("form[aria-label='Update readiness status']")).toBeNull();
  });

  it("submitting the status form calls setEquipmentStatus + refetches the detail", async () => {
    vi.mocked(api.setEquipmentStatus).mockResolvedValue(undefined);
    const { container } = await openDetail();
    const form = (await waitFor(() => container.querySelector("form[aria-label='Update readiness status']")))!;
    fireEvent.change(form.querySelector("select")!, { target: { value: "degraded" } });
    fireEvent.submit(form);
    await waitFor(() => expect(api.setEquipmentStatus).toHaveBeenCalledWith(1, expect.objectContaining({ status: "degraded" })));
    await waitFor(() => expect(vi.mocked(api.fetchEquipmentDetail).mock.calls.length).toBeGreaterThanOrEqual(2)); // initial + reload
  });

  it("submitting the machine-log form calls logEquipmentMaintenance", async () => {
    vi.mocked(api.logEquipmentMaintenance).mockResolvedValue(undefined);
    const { container } = await openDetail();
    const form = (await waitFor(() => container.querySelector("form[aria-label='Add machine log']")))!;
    fireEvent.change(form.querySelector("select")!, { target: { value: "fuel" } });
    fireEvent.submit(form);
    await waitFor(() => expect(api.logEquipmentMaintenance).toHaveBeenCalledWith(1, expect.objectContaining({ log_type: "fuel" })));
  });
});
