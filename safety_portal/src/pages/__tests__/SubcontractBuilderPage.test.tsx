/**
 * Subcontract Builder page (SC-S5) — SPA render-smoke for the wizard + tracker. Covers: cap-gated
 * affordances, the live integer-cents SOV-sums-to-price gate (a 2-line fixture whose extendeds sum
 * to the contract price → "balances" badge; a mismatch → warn badge), the job_no suggestion parse,
 * the generate flow (sends {contract_price_cents}; a sov_mismatch 409 re-renders the gate from the
 * Worker's `recomputed`; a success returns to the tracker with the sc_number), and the tracker state
 * machine (sent AND executed both offer Supersede — the dual-source delta from PO; queued offers
 * Cancel). Lib network fns + auth are mocked (importOriginal keeps the REAL money math — formatCents
 * / sovExtendedCents / computeSubtotal / US_STATES / stateName are the code under test here).
 */
import { cleanup, fireEvent, render, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../lib/subcontracts", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../lib/subcontracts")>();
  return {
    ...actual,
    fetchSubcontractors: vi.fn(),
    fetchSubConfig: vi.fn(),
    fetchSubTerms: vi.fn(),
    fetchSubDrafts: vi.fn(),
    fetchSubDraft: vi.fn(),
    createSubDraft: vi.fn(),
    updateSubDraft: vi.fn(),
    generateSubcontract: vi.fn(),
    supersedeSubcontract: vi.fn(),
    cancelSubcontract: vi.fn(),
  };
});
vi.mock("../../lib/api", () => ({ fetchJobs: vi.fn() }));
vi.mock("../../lib/auth", () => ({ useAuth: vi.fn() }));

import * as api from "../../lib/subcontracts";
import { fetchJobs } from "../../lib/api";
import { useAuth } from "../../lib/auth";
import { SubcontractBuilderPage } from "../SubcontractBuilderPage";

function authWith(capabilities: string[]) {
  return {
    user: { username: "office", role: "admin" as const, capabilities },
    loading: false,
    login: vi.fn(),
    logout: vi.fn(),
  };
}

const CONFIG: api.SubcontractConfig = {
  contractor: {
    entity: "Evergreen Renewables LLC",
    address_lines: ["100 Spectrum Center Dr. STE 570", "Irvine, CA. 92618"],
    phone: "888-303-6424",
    signature_entity: "Evergreen Renewables LLC",
    prime_contractor_default: "Evergreen Renewables LLC",
  },
  payment_terms: { retainage_bp: 1000, retainage_reduced_bp: 500, retainage_reduction_at_pct: 50 },
  // Small subset for the picker; must include CA (the fixture's governing-law state).
  governing_law_states: ["CA", "IL", "TX"],
};

const TERMS: api.TermsProfile[] = [
  {
    id: "standard_subcontract",
    kind: "library",
    label: "Standard Subcontract",
    description: "Evergreen's standard subcontract terms.",
    current_version: "1",
    tokens: [],
    render_line: null,
  },
];

const SUBS: api.Subcontractor[] = [
  {
    sub_key: "SUB-000001",
    sub_name: "Apex Electrical",
    address: "1 Volt Way",
    contact_name: "Sam Sparks",
    contact_email: "sam@apex.example",
    contact_phone: "555-0101",
    state: "CA",
    trades: ["Electrical"],
    default_terms_profile: "standard_subcontract",
    msa_reference: "",
    coi_reference: "",
    license_number: "C-10 12345",
    active: 1,
    notes: "",
    origin: "portal",
    sync_state: "synced",
    mirror_version: 1,
  },
];

const JOBS = [{ job_id: "JOB-000001", project_name: "2023.126 Kendall Solar" }];

function scRow(overrides: Partial<api.SubcontractListRow>): api.SubcontractListRow {
  return {
    id: 1,
    sc_number: null,
    job_no: "2023.126",
    site_phase: 0,
    supersede_seq: 0,
    revision: null,
    sub_key: "SUB-000001",
    job_id: "JOB-000001",
    job_name: "2023.126 Kendall Solar",
    project_name: "2023.126 Kendall Solar",
    owner_entity: "Kendall LLC",
    status: "draft",
    contract_price_cents: 3712,
    supersedes_sc_id: null,
    box_file_id: null,
    created_by: "office",
    created_at: 1_780_000_000,
    updated_at: 1_780_000_000,
    ...overrides,
  };
}

afterEach(cleanup);
beforeEach(() => {
  vi.resetAllMocks();
  vi.mocked(useAuth).mockReturnValue(authWith(["cap.subcontracts.manage"]));
  vi.mocked(api.fetchSubDrafts).mockResolvedValue([]);
  vi.mocked(api.fetchSubcontractors).mockResolvedValue(SUBS);
  vi.mocked(api.fetchSubTerms).mockResolvedValue(TERMS);
  vi.mocked(api.fetchSubConfig).mockResolvedValue(CONFIG);
  vi.mocked(fetchJobs).mockResolvedValue(JOBS);
});

/** Open the builder and fill a valid 2-line fixture: 3 × $12.34 + 2 × $0.05 = $37.12, job CA,
 *  subcontractor Apex Electrical, contract price $37.12 (balances). */
async function openBuilderWithFixture(r: ReturnType<typeof render>) {
  const { getByText, getByLabelText, getByRole } = r;
  await waitFor(() => expect(api.fetchSubDrafts).toHaveBeenCalled());
  fireEvent.click(getByText("+ New subcontract"));

  fireEvent.change(getByLabelText("Job"), { target: { value: "JOB-000001" } });
  // Subcontractor pick (the picker row button carries the subcontractor name).
  fireEvent.click(getByRole("button", { name: /Apex Electrical/ }));
  fireEvent.change(getByLabelText("Governing-law state"), { target: { value: "CA" } });

  // Two lines: 3 × $12.34 = $37.02 and 2 × $0.05 = $0.10 → subtotal $37.12.
  fireEvent.change(getByLabelText("Line 1 description"), { target: { value: "Mobilization" } });
  fireEvent.change(getByLabelText("Line 1 quantity"), { target: { value: "3" } });
  fireEvent.change(getByLabelText("Line 1 unit price"), { target: { value: "12.34" } });
  fireEvent.click(getByText("+ Add a line"));
  fireEvent.change(getByLabelText("Line 2 description"), { target: { value: "Closeout" } });
  fireEvent.change(getByLabelText("Line 2 quantity"), { target: { value: "2" } });
  fireEvent.change(getByLabelText("Line 2 unit price"), { target: { value: "0.05" } });

  fireEvent.change(getByLabelText("Contract price dollars"), { target: { value: "37.12" } });
}

describe("SubcontractBuilderPage", () => {
  it("renders the tracker under cap.subcontracts.manage; write affordances hidden without the cap", async () => {
    vi.mocked(api.fetchSubDrafts).mockResolvedValue([scRow({})]);
    const withCap = render(<SubcontractBuilderPage onBack={() => {}} />);
    await waitFor(() => expect(withCap.getByText("Draft #1")).toBeTruthy());
    expect(withCap.getByText("+ New subcontract")).toBeTruthy();
    expect(withCap.getByText("Open")).toBeTruthy();
    withCap.unmount();

    vi.mocked(useAuth).mockReturnValue(authWith([]));
    const withoutCap = render(<SubcontractBuilderPage onBack={() => {}} />);
    await waitFor(() => expect(withoutCap.getByText("Draft #1")).toBeTruthy());
    expect(withoutCap.queryByText("+ New subcontract")).toBeNull();
    expect(withoutCap.queryByText("Open")).toBeNull();
    expect(withoutCap.queryByText("Cancel SC")).toBeNull();
  });

  it("mirrors the Worker's integer-cents SOV math and shows the sums-to-price gate", async () => {
    const r = render(<SubcontractBuilderPage onBack={() => {}} />);
    await openBuilderWithFixture(r);

    // Per-row extended: round(3 × 1234) = 3702, round(2 × 5) = 10.
    expect(r.getByText("$37.02")).toBeTruthy();
    expect(r.getByText("$0.10")).toBeTruthy();

    // The gate panel: subtotal $37.12 == contract price $37.12 → balances.
    const panel = r.getByLabelText("Contract price panel") as HTMLElement;
    expect(within(panel).getByText("SOV balances to the contract price")).toBeTruthy();
  });

  it("warns (and blocks generate) when the SOV subtotal ≠ the contract price", async () => {
    const r = render(<SubcontractBuilderPage onBack={() => {}} />);
    await openBuilderWithFixture(r);

    // Break the balance: contract price $50.00 ≠ SOV subtotal $37.12.
    fireEvent.change(r.getByLabelText("Contract price dollars"), { target: { value: "50.00" } });
    const panel = r.getByLabelText("Contract price panel") as HTMLElement;
    expect(within(panel).getByText(/adjust a line\s+or the price/)).toBeTruthy();

    // Generate is blocked client-side — the draft is never saved on a mismatch.
    fireEvent.click(r.getByText("Generate subcontract"));
    await waitFor(() => expect(r.getByText(/must add up to the contract price/)).toBeTruthy());
    expect(api.createSubDraft).not.toHaveBeenCalled();
    expect(api.generateSubcontract).not.toHaveBeenCalled();
  });

  it("suggests the job number from the YYYY.NNN project-name prefix (editable)", async () => {
    const r = render(<SubcontractBuilderPage onBack={() => {}} />);
    await waitFor(() => expect(api.fetchSubDrafts).toHaveBeenCalled());
    fireEvent.click(r.getByText("+ New subcontract"));
    fireEvent.change(r.getByLabelText("Job"), { target: { value: "JOB-000001" } });
    const jobNo = r.getByLabelText("Job number (YYYY.NNN)") as HTMLInputElement;
    expect(jobNo.value).toBe("2023.126");
    fireEvent.change(jobNo, { target: { value: "bogus" } });
    expect(r.getByText(/must look like 2023\.126/)).toBeTruthy();
  });

  it("generate sends {contract_price_cents}; a sov_mismatch 409 re-renders the gate from `recomputed`", async () => {
    vi.mocked(api.createSubDraft).mockResolvedValue({ id: 7, subtotal_cents: 3712 });
    vi.mocked(api.generateSubcontract).mockResolvedValue({
      ok: false,
      error: "sov_mismatch",
      recomputed: { subtotal_cents: 9999, contract_price_cents: 3712 },
    });
    const r = render(<SubcontractBuilderPage onBack={() => {}} />);
    await openBuilderWithFixture(r);

    fireEvent.click(r.getByText("Generate subcontract"));

    // Save-then-generate: the draft persists first, then generate carries the DISPLAYED contract price.
    await waitFor(() => expect(api.createSubDraft).toHaveBeenCalledTimes(1));
    await waitFor(() =>
      expect(api.generateSubcontract).toHaveBeenCalledWith(7, { contract_price_cents: 3712 }),
    );

    // The refusal banner + the gate re-rendered from the server's recomputed subtotal.
    await waitFor(() => expect(r.getByText(/doesn't add up to the contract price/)).toBeTruthy());
    const panel = r.getByLabelText("Contract price panel") as HTMLElement;
    expect(within(panel).getByText("$99.99")).toBeTruthy(); // recomputed subtotal
  });

  it("a successful generate returns to the tracker with the subcontract number", async () => {
    vi.mocked(api.createSubDraft).mockResolvedValue({ id: 7, subtotal_cents: 3712 });
    vi.mocked(api.generateSubcontract).mockResolvedValue({
      ok: true,
      id: 7,
      sc_number: "2023.126.0.0",
      revision: 0,
      subtotal_cents: 3712,
    });
    const r = render(<SubcontractBuilderPage onBack={() => {}} />);
    await openBuilderWithFixture(r);

    fireEvent.click(r.getByText("Generate subcontract"));
    await waitFor(() => expect(r.getByText(/Subcontract 2023\.126\.0\.0 generated/)).toBeTruthy());
    expect(r.getByText("+ New subcontract")).toBeTruthy(); // back on the tracker
  });

  it("tracker state machine: sent AND executed both offer Supersede; queued offers Cancel", async () => {
    vi.mocked(api.fetchSubDrafts).mockResolvedValue([
      scRow({ id: 3, status: "sent", sc_number: "2023.126.0.0" }),
      scRow({ id: 5, status: "executed", sc_number: "2023.126.0.1" }),
      scRow({ id: 4, status: "queued" }),
    ]);
    const r = render(<SubcontractBuilderPage onBack={() => {}} />);
    await waitFor(() => expect(r.getByText("2023.126.0.0")).toBeTruthy());

    const sentCard = r.getByText("2023.126.0.0").closest(".card") as HTMLElement;
    expect(within(sentCard).getByText("Supersede")).toBeTruthy();
    expect(within(sentCard).queryByText("Cancel SC")).toBeNull();

    // The dual-source delta from PO: an EXECUTED subcontract is also a supersede source.
    const executedCard = r.getByText("2023.126.0.1").closest(".card") as HTMLElement;
    expect(within(executedCard).getByText("Supersede")).toBeTruthy();

    const queuedCard = r.getByText("Draft #4").closest(".card") as HTMLElement;
    expect(within(queuedCard).getByText("Cancel SC")).toBeTruthy();
    expect(within(queuedCard).queryByText("Supersede")).toBeNull();
  });
});
