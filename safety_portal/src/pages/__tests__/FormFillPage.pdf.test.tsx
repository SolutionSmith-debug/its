/**
 * PR-4 Part A — the submitted-page receipt + request-driven canonical PDF download.
 *
 * Drives a real submission through FormFillPage (mocked api/auth) to reach the
 * `if (submitted)` confirmation block, then asserts:
 *   1. the RECEIPT renders the filed facts (form name, job, work date, submission id);
 *   2. the "Make available for download" button runs requestPdf → "Preparing…" →
 *      (mocked pdfStatus ready) → an enabled "Download (available until …)" button that,
 *      when clicked, calls downloadPdf with the submission's uuid.
 *
 * Mirrors the existing src/ page-test harness (FormsPage.editing.test.tsx): vi.mock the
 * auth + api modules, fireEvent the selects/buttons, waitFor the async transitions. No
 * jest-dom (this repo wires none) — plain DOM queries + native vitest matchers.
 */
import { cleanup, fireEvent, render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// A submitter session, no AuthProvider / network round-trip.
vi.mock("../../lib/auth", () => ({
  useAuth: () => ({
    user: { username: "pm.test", role: "submitter" },
    loading: false,
    login: vi.fn(),
    logout: vi.fn(),
  }),
}));

// Stub the whole api surface this page touches. fetchJobs/fetchRecent get the page to a
// renderable form; submitForm resolves so we land on the submitted page; the three PDF
// helpers are the unit under test.
vi.mock("../../lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../lib/api")>();
  return {
    ...actual,
    fetchJobs: vi.fn().mockResolvedValue([{ job_id: "J1", project_name: "North Ridge" }]),
    fetchRecent: vi.fn().mockResolvedValue(null),
    submitForm: vi.fn().mockResolvedValue(undefined),
    requestPdf: vi.fn().mockResolvedValue({ ok: true, ready: false }),
    pdfStatus: vi.fn().mockResolvedValue({ requested: true, ready: true, expires_at: 1_900_000_000 }),
    downloadPdf: vi.fn(),
  };
});

import * as api from "../../lib/api";
import { FormFillPage } from "../FormFillPage";

afterEach(cleanup);
beforeEach(() => vi.clearAllMocks());

// Pick a no-variant parent form so a single select drives a renderable definition.
async function submitOnce(container: HTMLElement, getByText: (t: string) => HTMLElement) {
  // Jobs load async on mount — wait for the option to appear.
  await waitFor(() => expect(container.querySelector('option[value="J1"]')).not.toBeNull());

  const selects = () => Array.from(container.querySelectorAll("select"));
  // [0] = Job, [1] = Form (no variant select for a no-variant parent like JHA).
  fireEvent.change(selects()[0], { target: { value: "J1" } });
  fireEvent.change(selects()[1], { target: { value: "jha" } });

  // The form definition resolves → the Submit button appears.
  await waitFor(() => expect(getByText("Submit")).toBeTruthy());
  fireEvent.click(getByText("Submit"));

  // Land on the submitted confirmation page.
  await waitFor(() => expect(getByText("Submitted ✓")).toBeTruthy());
}

describe("FormFillPage — submitted receipt + request-driven PDF download", () => {
  it("renders the receipt with the filed facts", async () => {
    const { container, getByText } = render(<FormFillPage onBack={() => {}} />);
    await submitOnce(container as HTMLElement, getByText);

    const text = container.textContent ?? "";
    expect(text).toContain("Job Hazard Analysis"); // form name (def.form_name)
    expect(text).toContain("North Ridge"); // job project_name
    // The submission id is rendered in a <code> inside the receipt.
    const idCell = container.querySelector(".receipt code");
    expect(idCell?.textContent).toBeTruthy();
    expect((idCell?.textContent ?? "").length).toBeGreaterThan(10);
  });

  it("request → preparing → ready → download wired to the submission uuid", async () => {
    const { container, getByText } = render(<FormFillPage onBack={() => {}} />);
    await submitOnce(container as HTMLElement, getByText);

    // The uuid shown in the receipt is the one the download flow must use.
    const uuid = container.querySelector(".receipt code")?.textContent ?? "";
    expect(uuid.length).toBeGreaterThan(10);

    // 1. Click "Make available for download" → requestPdf fires with the uuid.
    fireEvent.click(getByText("Make available for download"));
    expect(api.requestPdf).toHaveBeenCalledWith(uuid);

    // 2. "Preparing…" shows while the poll runs.
    await waitFor(() => expect(getByText(/Preparing…/)).toBeTruthy());

    // 3. The poll's first tick reads pdfStatus (mocked ready) → the Download button
    //    appears with the expiry, and pdfStatus was polled with the uuid.
    await waitFor(() => expect(getByText(/^Download/)).toBeTruthy());
    expect(api.pdfStatus).toHaveBeenCalledWith(uuid);
    expect(getByText(/^Download/).textContent ?? "").toContain("available until");

    // 4. Clicking Download triggers the canonical download for this uuid.
    fireEvent.click(getByText(/^Download/));
    expect(api.downloadPdf).toHaveBeenCalledWith(uuid);
  });
});
