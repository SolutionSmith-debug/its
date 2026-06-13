/**
 * PR-5 — Form Request page (browse an active job's filed forms → multi-select →
 * batch-request → per-row download poll).
 *
 * Asserts:
 *   1. picking a job loads its filed forms into a table (metadata columns);
 *   2. checkbox multi-select enables "Request selected (N)"; clicking it calls
 *      requestPdfs with the checked uuids, and each row enters the "Preparing…" →
 *      (mocked pdfStatus ready) → "Download" flow wired to the row's uuid;
 *   3. a row the server reports as already-requested starts in the download flow
 *      (no checkbox); an already-ready row shows "Download" immediately.
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

const FILED = [
  { submission_uuid: "uuid-aaa", form_code: "jha", work_date: "2026-06-08", filed_at: 1_780_000_000, requested: false, ready: false },
  { submission_uuid: "uuid-bbb", form_code: "toolbox", work_date: "2026-06-09", filed_at: 1_780_000_100, requested: false, ready: false },
];

async function pickJob(container: HTMLElement) {
  await waitFor(() => expect(container.querySelector('option[value="J1"]')).not.toBeNull());
  fireEvent.change(container.querySelector("select")!, { target: { value: "J1" } });
}

describe("FormRequestPage — browse + batch request + per-row download", () => {
  it("picking a job loads its filed forms into the table", async () => {
    vi.mocked(api.fetchFiled).mockResolvedValue(FILED);
    const { container } = render(<FormRequestPage onBack={() => {}} />);
    await pickJob(container as HTMLElement);

    await waitFor(() => expect(api.fetchFiled).toHaveBeenCalledWith("J1"));
    await waitFor(() => expect(container.querySelectorAll("tbody tr").length).toBe(2));
    const text = container.textContent ?? "";
    expect(text).toContain("jha");
    expect(text).toContain("2026-06-08");
    expect(text).toContain("toolbox");
  });

  it("an empty job shows the no-forms message", async () => {
    vi.mocked(api.fetchFiled).mockResolvedValue([]);
    const { container, getByText } = render(<FormRequestPage onBack={() => {}} />);
    await pickJob(container as HTMLElement);
    await waitFor(() => expect(getByText(/No filed forms for this job yet/)).toBeTruthy());
  });

  it("multi-select → Request selected → requestPdfs(uuids) → rows poll to Download", async () => {
    vi.mocked(api.fetchFiled).mockResolvedValue(FILED);
    const { container, getByText } = render(<FormRequestPage onBack={() => {}} />);
    await pickJob(container as HTMLElement);
    await waitFor(() => expect(container.querySelectorAll("tbody tr").length).toBe(2));

    // Check both rows.
    const boxes = Array.from(container.querySelectorAll('input[type="checkbox"]')) as HTMLInputElement[];
    expect(boxes).toHaveLength(2);
    fireEvent.click(boxes[0]);
    fireEvent.click(boxes[1]);

    // The action button reflects the count and is enabled.
    const btn = getByText(/Request selected \(2\)/);
    fireEvent.click(btn);
    await waitFor(() => expect(api.requestPdfs).toHaveBeenCalledWith(["uuid-aaa", "uuid-bbb"]));

    // Each row enters Preparing… then the poll's first tick (mocked ready) → Download.
    await waitFor(() => expect(container.querySelectorAll("tbody tr").length).toBe(2));
    await waitFor(() => {
      const downloads = Array.from(container.querySelectorAll("button")).filter((b) => /^Download/.test(b.textContent ?? ""));
      expect(downloads.length).toBe(2);
    });
    expect(api.pdfStatus).toHaveBeenCalledWith("uuid-aaa");
    expect(api.pdfStatus).toHaveBeenCalledWith("uuid-bbb");

    // Clicking a Download triggers the canonical download for that row's uuid.
    const dl = Array.from(container.querySelectorAll("button")).find((b) => /^Download/.test(b.textContent ?? ""))!;
    fireEvent.click(dl);
    expect(api.downloadPdf).toHaveBeenCalled();
  });

  it("a server-reported already-requested row starts in the download flow (no checkbox)", async () => {
    vi.mocked(api.fetchFiled).mockResolvedValue([
      { submission_uuid: "uuid-req", form_code: "jha", work_date: "2026-06-08", filed_at: 1_780_000_000, requested: true, ready: false },
    ]);
    const { container } = render(<FormRequestPage onBack={() => {}} />);
    await pickJob(container as HTMLElement);
    await waitFor(() => expect(container.querySelectorAll("tbody tr").length).toBe(1));
    // No checkbox for an already-requested row; it polls (Preparing…) then resolves to Download.
    expect(container.querySelector('input[type="checkbox"]')).toBeNull();
    await waitFor(() => {
      const dl = Array.from(container.querySelectorAll("button")).find((b) => /^Download/.test(b.textContent ?? ""));
      expect(dl).toBeTruthy();
    });
  });

  it("an already-ready row shows Download immediately (no poll needed)", async () => {
    vi.mocked(api.fetchFiled).mockResolvedValue([
      { submission_uuid: "uuid-rdy", form_code: "jha", work_date: "2026-06-08", filed_at: 1_780_000_000, requested: true, ready: true },
    ]);
    const { container } = render(<FormRequestPage onBack={() => {}} />);
    await pickJob(container as HTMLElement);
    await waitFor(() => {
      const dl = Array.from(container.querySelectorAll("button")).find((b) => /^Download/.test(b.textContent ?? ""));
      expect(dl).toBeTruthy();
    });
  });
});
