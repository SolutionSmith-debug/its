/**
 * PR-5/PR-6 — Form Request page. PR-6 adds the Job → Month-Year → (optional) Form-type
 * cascade in front of the PR-5 browse → multi-select → batch-request → per-row download flow.
 *
 * Asserts:
 *   1. picking a job loads its Month + Form-type dropdowns (fetchFiledMonths), no table yet;
 *   2. picking a month fetches THAT work-month's filed forms into the table;
 *   3. changing the form-type filter refetches with the form_code;
 *   4. a job with no filed forms shows the no-forms message (no month dropdown);
 *   5. multi-select → "Request selected (N)" → requestPdfs(uuids) → each row enters the
 *      Preparing… → (mocked pdfStatus ready) → Download flow wired to the row's uuid;
 *   6. an already-ready row shows Download immediately.
 *
 * Mirrors FormFillPage.pdf.test.tsx: vi.mock the api module, fireEvent + waitFor.
 * FormRequestPage doesn't use useAuth, so no auth mock is needed.
 */
import { cleanup, fireEvent, render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../lib/api")>();
  return {
    ...actual,
    fetchJobs: vi.fn().mockResolvedValue([
      { job_id: "J1", project_name: "North Ridge" },
      { job_id: "J2", project_name: "South Mesa" },
    ]),
    fetchFiledMonths: vi.fn(),
    fetchFiled: vi.fn(),
    requestPdfs: vi.fn().mockResolvedValue(2),
    pdfStatus: vi.fn().mockResolvedValue({ requested: true, ready: true, expires_at: 1_900_000_000 }),
    downloadPdf: vi.fn(),
  };
});

import * as api from "../../lib/api";
import { FormRequestPage } from "../FormRequestPage";

afterEach(cleanup);
beforeEach(() => vi.clearAllMocks());

const MONTHS = {
  months: [
    { month: "2026-06", count: 2 },
    { month: "2026-05", count: 1 },
  ],
  form_codes: ["jha", "toolbox"],
};

const FILED_JUN = [
  { submission_uuid: "uuid-aaa", form_code: "jha", work_date: "2026-06-08", filed_at: 1_780_000_000, requested: false, ready: false },
  { submission_uuid: "uuid-bbb", form_code: "toolbox", work_date: "2026-06-09", filed_at: 1_780_000_100, requested: false, ready: false },
];

async function pickJob(container: HTMLElement) {
  await waitFor(() => expect(container.querySelector('option[value="J1"]')).not.toBeNull());
  fireEvent.change(container.querySelector('select[aria-label="Job"]')!, { target: { value: "J1" } });
}

async function pickMonth(container: HTMLElement, month = "2026-06") {
  await waitFor(() => expect(container.querySelector('select[aria-label="Month"]')).not.toBeNull());
  fireEvent.change(container.querySelector('select[aria-label="Month"]')!, { target: { value: month } });
}

describe("FormRequestPage — month/form cascade + browse + request + download", () => {
  it("picking a job loads its Month + Form-type options (no table until a month is picked)", async () => {
    vi.mocked(api.fetchFiledMonths).mockResolvedValue(MONTHS);
    const { container } = render(<FormRequestPage onBack={() => {}} />);
    await pickJob(container as HTMLElement);

    await waitFor(() => expect(api.fetchFiledMonths).toHaveBeenCalledWith("J1"));
    await waitFor(() => expect(container.querySelector('select[aria-label="Month"]')).not.toBeNull());
    // Both months appear (value = "YYYY-MM"), with the count rendered.
    expect(container.querySelector('option[value="2026-06"]')).not.toBeNull();
    expect(container.querySelector('option[value="2026-05"]')).not.toBeNull();
    expect(container.textContent ?? "").toContain("(2)");
    // Form-type dropdown is populated.
    expect(container.querySelector('select[aria-label="Form type"]')).not.toBeNull();
    expect(container.querySelector('option[value="jha"]')).not.toBeNull();
    expect(container.querySelector('option[value="toolbox"]')).not.toBeNull();
    // No documents table yet — the cascade requires a month first.
    expect(api.fetchFiled).not.toHaveBeenCalled();
    expect(container.querySelector("tbody")).toBeNull();
  });

  it("picking a month fetches that work-month's filed forms into the table", async () => {
    vi.mocked(api.fetchFiledMonths).mockResolvedValue(MONTHS);
    vi.mocked(api.fetchFiled).mockResolvedValue(FILED_JUN);
    const { container } = render(<FormRequestPage onBack={() => {}} />);
    await pickJob(container as HTMLElement);
    await pickMonth(container as HTMLElement, "2026-06");

    await waitFor(() => expect(api.fetchFiled).toHaveBeenCalledWith("J1", expect.objectContaining({ month: "2026-06" })));
    await waitFor(() => expect(container.querySelectorAll("tbody tr").length).toBe(2));
    expect(container.textContent ?? "").toContain("jha");
    expect(container.textContent ?? "").toContain("toolbox");
  });

  it("changing the form-type filter refetches with the form_code", async () => {
    vi.mocked(api.fetchFiledMonths).mockResolvedValue(MONTHS);
    vi.mocked(api.fetchFiled).mockResolvedValue(FILED_JUN);
    const { container } = render(<FormRequestPage onBack={() => {}} />);
    await pickJob(container as HTMLElement);
    await pickMonth(container as HTMLElement, "2026-06");
    await waitFor(() => expect(api.fetchFiled).toHaveBeenCalledWith("J1", expect.objectContaining({ month: "2026-06" })));

    fireEvent.change(container.querySelector('select[aria-label="Form type"]')!, { target: { value: "jha" } });
    await waitFor(() =>
      expect(api.fetchFiled).toHaveBeenCalledWith("J1", expect.objectContaining({ month: "2026-06", form_code: "jha" })),
    );
  });

  it("a job with no filed forms shows the no-forms message (no month dropdown)", async () => {
    vi.mocked(api.fetchFiledMonths).mockResolvedValue({ months: [], form_codes: [] });
    const { container, getByText } = render(<FormRequestPage onBack={() => {}} />);
    await pickJob(container as HTMLElement);
    await waitFor(() => expect(getByText(/No filed forms for this job yet/)).toBeTruthy());
    expect(container.querySelector('select[aria-label="Month"]')).toBeNull();
    expect(api.fetchFiled).not.toHaveBeenCalled();
  });

  it("multi-select → Request selected → requestPdfs(uuids) → rows poll to Download", async () => {
    vi.mocked(api.fetchFiledMonths).mockResolvedValue(MONTHS);
    vi.mocked(api.fetchFiled).mockResolvedValue(FILED_JUN);
    const { container, getByText } = render(<FormRequestPage onBack={() => {}} />);
    await pickJob(container as HTMLElement);
    await pickMonth(container as HTMLElement, "2026-06");
    await waitFor(() => expect(container.querySelectorAll("tbody tr").length).toBe(2));

    const boxes = Array.from(container.querySelectorAll('input[type="checkbox"]')) as HTMLInputElement[];
    expect(boxes).toHaveLength(2);
    fireEvent.click(boxes[0]);
    fireEvent.click(boxes[1]);

    const btn = getByText(/Request selected \(2\)/);
    fireEvent.click(btn);
    await waitFor(() => expect(api.requestPdfs).toHaveBeenCalledWith(["uuid-aaa", "uuid-bbb"]));

    await waitFor(() => {
      const downloads = Array.from(container.querySelectorAll("button")).filter((b) => /^Download/.test(b.textContent ?? ""));
      expect(downloads.length).toBe(2);
    });
    expect(api.pdfStatus).toHaveBeenCalledWith("uuid-aaa");
    expect(api.pdfStatus).toHaveBeenCalledWith("uuid-bbb");

    const dl = Array.from(container.querySelectorAll("button")).find((b) => /^Download/.test(b.textContent ?? ""))!;
    fireEvent.click(dl);
    expect(api.downloadPdf).toHaveBeenCalled();
  });

  it("an already-ready row shows Download immediately (no poll needed)", async () => {
    vi.mocked(api.fetchFiledMonths).mockResolvedValue(MONTHS);
    vi.mocked(api.fetchFiled).mockResolvedValue([
      { submission_uuid: "uuid-rdy", form_code: "jha", work_date: "2026-06-08", filed_at: 1_780_000_000, requested: true, ready: true },
    ]);
    const { container } = render(<FormRequestPage onBack={() => {}} />);
    await pickJob(container as HTMLElement);
    await pickMonth(container as HTMLElement, "2026-06");
    await waitFor(() => {
      const dl = Array.from(container.querySelectorAll("button")).find((b) => /^Download/.test(b.textContent ?? ""));
      expect(dl).toBeTruthy();
    });
  });
});
