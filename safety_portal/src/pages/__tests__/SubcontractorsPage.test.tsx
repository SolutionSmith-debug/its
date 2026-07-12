/**
 * Subcontractors page (SC-S5) — SPA render-smoke. Faithful mirror of PoVendorsPage.test.tsx:
 * list + create + edit + deactivate-never-delete, cap.subcontracts.manage gating the write
 * affordances (the Worker re-gates), §51 "Syncing to Smartsheet" badge on sync_state='pending'
 * rows. HEADLINE delta the vendor test does NOT have: the directory is GROUPED BY STATE, so this
 * suite adds a grouping-header assertion (per-state section head + the "Unassigned" bucket for a
 * blank state). Idiom mirrored: mock the lib fns + auth (importOriginal keeps the real
 * trades/state vocabulary), resetAllMocks, default read-only, expect-inside-waitFor, within().
 */
import { cleanup, fireEvent, render, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../lib/subcontracts", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../lib/subcontracts")>();
  return {
    ...actual,
    fetchSubcontractors: vi.fn(),
    createSubcontractor: vi.fn(),
    updateSubcontractor: vi.fn(),
    fetchSubTerms: vi.fn(),
  };
});
vi.mock("../../lib/auth", () => ({ useAuth: vi.fn() }));

import * as api from "../../lib/subcontracts";
import { useAuth } from "../../lib/auth";
import { SubcontractorsPage } from "../SubcontractorsPage";

function authWith(capabilities: string[]) {
  return {
    user: { username: "office", role: "admin" as const, capabilities },
    loading: false,
    login: vi.fn(),
    logout: vi.fn(),
  };
}

const TERMS: api.TermsProfile[] = [
  {
    id: "standard_subcontract",
    kind: "library",
    label: "Standard Subcontract",
    description: "Evergreen's standard subcontract terms.",
    current_version: "1",
    tokens: ["contractor_entity", "sub_name"],
    render_line: null,
  },
  {
    id: "negotiated_msa",
    kind: "attach",
    label: "Negotiated MSA (attach-not-generate)",
    description: "Reference-line-only profile.",
    current_version: null,
    tokens: [],
    render_line: "THIS SUBCONTRACT IS SUBJECT TO THE NEGOTIATED MSA.",
  },
];

function subcontractor(overrides: Partial<api.Subcontractor>): api.Subcontractor {
  return {
    sub_key: "SUB-000001",
    sub_name: "Apex Civil",
    address: "1 Steel Way, Portland, OR",
    contact_name: "Sam Foreman",
    contact_email: "office@apexcivil.com",
    contact_phone: "555-0101",
    state: "OR",
    trades: ["Civil"],
    default_terms_profile: "standard_subcontract",
    msa_reference: "",
    coi_reference: "",
    license_number: "",
    active: 1,
    notes: "",
    origin: "portal",
    sync_state: "synced",
    mirror_version: 1,
    ...overrides,
  };
}

const SUBS: api.Subcontractor[] = [
  subcontractor({}),
  subcontractor({
    sub_key: "SUB-000002",
    sub_name: "Chint Electrical",
    state: "CA",
    trades: ["Electrical"],
    sync_state: "pending", // §51 up-sync pending — drives the badge
  }),
  subcontractor({
    sub_key: "SUB-000003",
    sub_name: "Nomad Fencing",
    state: "", // blank — collates into the "Unassigned" bucket
    trades: ["Fencing"],
  }),
];

afterEach(cleanup);
beforeEach(() => {
  vi.resetAllMocks();
  vi.mocked(useAuth).mockReturnValue(authWith([]));
  vi.mocked(api.fetchSubcontractors).mockResolvedValue(SUBS);
  vi.mocked(api.fetchSubTerms).mockResolvedValue(TERMS);
});

describe("SubcontractorsPage", () => {
  it("renders the subcontractor list under cap.subcontracts.manage with the write controls", async () => {
    vi.mocked(useAuth).mockReturnValue(authWith(["cap.subcontracts.manage"]));
    const { getByText, getAllByText } = render(<SubcontractorsPage onBack={() => {}} />);
    await waitFor(() => expect(getByText("Apex Civil")).toBeTruthy());
    expect(getByText("+ Add a subcontractor")).toBeTruthy();
    expect(getAllByText("Edit").length).toBe(3);
    expect(getAllByText("Deactivate").length).toBe(3);
  });

  it("groups the directory by state — one section head per state, blank into an Unassigned bucket", async () => {
    vi.mocked(useAuth).mockReturnValue(authWith(["cap.subcontracts.manage"]));
    const { getByText } = render(<SubcontractorsPage onBack={() => {}} />);
    await waitFor(() => expect(getByText("Apex Civil")).toBeTruthy());
    // full state names as headers (CA→California, OR→Oregon), blank → "Unassigned"
    expect(getByText("California")).toBeTruthy();
    expect(getByText("Oregon")).toBeTruthy();
    expect(getByText("Unassigned")).toBeTruthy();
    // the OR-grouped card lives under the Oregon section, not the California one
    const orSection = getByText("Oregon").closest(".dash-section") as HTMLElement;
    expect(within(orSection).getByText("Apex Civil")).toBeTruthy();
    expect(within(orSection).queryByText("Chint Electrical")).toBeNull();
  });

  it("hides every write control without cap.subcontracts.manage (UI affordance; the router gates the view)", async () => {
    vi.mocked(useAuth).mockReturnValue(authWith([]));
    const { queryByText } = render(<SubcontractorsPage onBack={() => {}} />);
    await waitFor(() => expect(queryByText("Apex Civil")).not.toBeNull());
    expect(queryByText("+ Add a subcontractor")).toBeNull();
    expect(queryByText("Edit")).toBeNull();
    expect(queryByText("Deactivate")).toBeNull();
  });

  it("creates a subcontractor with state, trades, and the terms profile; reloads on success", async () => {
    vi.mocked(useAuth).mockReturnValue(authWith(["cap.subcontracts.manage"]));
    vi.mocked(api.createSubcontractor).mockResolvedValue({ ok: true, sub_key: "SUB-000004" });
    const { getByText, getByLabelText, getByRole } = render(<SubcontractorsPage onBack={() => {}} />);
    await waitFor(() => expect(api.fetchSubcontractors).toHaveBeenCalledTimes(1));

    fireEvent.click(getByText("+ Add a subcontractor"));
    fireEvent.change(getByLabelText("Subcontractor name (required)"), { target: { value: "Vantage Mechanical" } });
    fireEvent.change(getByLabelText("Contact email (the send-time recipient)"), {
      target: { value: "ops@vantage.example" },
    });
    fireEvent.change(getByLabelText("State"), { target: { value: "OR" } });
    fireEvent.click(getByRole("button", { name: "Civil" })); // trade chip toggle
    fireEvent.change(getByLabelText("Default terms profile"), { target: { value: "standard_subcontract" } });
    fireEvent.click(getByText("Add subcontractor"));

    await waitFor(() =>
      expect(api.createSubcontractor).toHaveBeenCalledWith(
        expect.objectContaining({
          sub_name: "Vantage Mechanical",
          contact_email: "ops@vantage.example",
          state: "OR",
          trades: ["Civil"],
          default_terms_profile: "standard_subcontract",
          active: 1,
        }),
      ),
    );
    await waitFor(() => expect(api.fetchSubcontractors).toHaveBeenCalledTimes(2)); // reload-after-write
  });

  it("shows the 'Syncing to Smartsheet' badge only on sync_state='pending' rows (§51 up-sync)", async () => {
    vi.mocked(useAuth).mockReturnValue(authWith(["cap.subcontracts.manage"]));
    const { getByText } = render(<SubcontractorsPage onBack={() => {}} />);
    await waitFor(() => expect(getByText("Chint Electrical")).toBeTruthy());
    const pendingCard = getByText("Chint Electrical").closest(".card") as HTMLElement;
    const syncedCard = getByText("Apex Civil").closest(".card") as HTMLElement;
    expect(within(pendingCard).getByText("Syncing to Smartsheet")).toBeTruthy();
    expect(within(syncedCard).queryByText("Syncing to Smartsheet")).toBeNull();
  });

  it("deactivates through the two-step armed control — an update with active:0, never a delete", async () => {
    vi.mocked(useAuth).mockReturnValue(authWith(["cap.subcontracts.manage"]));
    vi.mocked(api.updateSubcontractor).mockResolvedValue(undefined);
    const { getByText } = render(<SubcontractorsPage onBack={() => {}} />);
    await waitFor(() => expect(getByText("Apex Civil")).toBeTruthy());
    const card = getByText("Apex Civil").closest(".card") as HTMLElement;

    fireEvent.click(within(card).getByText("Deactivate")); // arm
    expect(api.updateSubcontractor).not.toHaveBeenCalled(); // first tap never fires the write
    fireEvent.click(within(card).getByText("Confirm deactivate"));

    await waitFor(() =>
      expect(api.updateSubcontractor).toHaveBeenCalledWith(
        "SUB-000001",
        expect.objectContaining({ sub_name: "Apex Civil", active: 0 }),
      ),
    );
  });

  it("edits a subcontractor in place, preserving its active flag", async () => {
    vi.mocked(useAuth).mockReturnValue(authWith(["cap.subcontracts.manage"]));
    vi.mocked(api.updateSubcontractor).mockResolvedValue(undefined);
    const { getByText, getByLabelText } = render(<SubcontractorsPage onBack={() => {}} />);
    await waitFor(() => expect(getByText("Apex Civil")).toBeTruthy());
    const card = getByText("Apex Civil").closest(".card") as HTMLElement;

    fireEvent.click(within(card).getByText("Edit"));
    fireEvent.change(getByLabelText("Contact phone"), { target: { value: "555-9999" } });
    fireEvent.click(within(card).getByText("Save"));

    await waitFor(() =>
      expect(api.updateSubcontractor).toHaveBeenCalledWith(
        "SUB-000001",
        expect.objectContaining({ contact_phone: "555-9999", active: 1 }),
      ),
    );
  });
});
