/**
 * PO Vendors page (S6) — SPA render-smoke. List + create + edit + deactivate-never-delete,
 * cap.po.manage gating the write affordances (the Worker re-gates), §51 "Syncing to
 * Smartsheet" badge on sync_state='pending' rows. Mirrors the MaterialsCatalogPage test
 * idiom: mock the lib fns + auth (importOriginal keeps the real vocabulary/constants),
 * resetAllMocks, default read-only, expect-inside-waitFor, row queries scoped via within().
 */
import { cleanup, fireEvent, render, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../lib/po", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../lib/po")>();
  return {
    ...actual,
    fetchVendors: vi.fn(),
    createVendor: vi.fn(),
    updateVendor: vi.fn(),
    fetchTerms: vi.fn(),
  };
});
vi.mock("../../lib/auth", () => ({ useAuth: vi.fn() }));

import * as api from "../../lib/po";
import { useAuth } from "../../lib/auth";
import { PoVendorsPage } from "../PoVendorsPage";

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
    id: "standard_17",
    kind: "library",
    label: "Standard 17-clause (Purchase Order 2019)",
    description: "Evergreen's standard domestic terms.",
    current_version: "1",
    tokens: ["purchaser_entity", "seller_name"],
    render_line: null,
  },
  {
    id: "negotiated_gtc",
    kind: "attach",
    label: "Negotiated GTC (attach-not-generate)",
    description: "Reference-line-only profile.",
    current_version: null,
    tokens: [],
    render_line: "THIS PURCHASE ORDER IS SUBJECT TO THE NEGOTIATED GTC.",
  },
];

function vendor(overrides: Partial<api.Vendor>): api.Vendor {
  return {
    vendor_key: "VEN-000001",
    vendor_name: "Apex Racking",
    address: "1 Steel Way, Portland, OR",
    contact_name: "Sam Orders",
    contact_email: "orders@apexracking.com",
    contact_phone: "555-0101",
    region: "West",
    supply_categories: ["racking"],
    default_terms_profile: "standard_17",
    gtc_reference: "",
    active: 1,
    notes: "",
    origin: "portal",
    sync_state: "synced",
    mirror_version: 1,
    ...overrides,
  };
}

const VENDORS: api.Vendor[] = [
  vendor({}),
  vendor({
    vendor_key: "VEN-000002",
    vendor_name: "Chint Power",
    region: "Midwest",
    supply_categories: ["inverters"],
    default_terms_profile: "chint_vendor",
    sync_state: "pending", // §51 up-sync pending — drives the badge
  }),
];

afterEach(cleanup);
beforeEach(() => {
  vi.resetAllMocks();
  vi.mocked(useAuth).mockReturnValue(authWith([]));
  vi.mocked(api.fetchVendors).mockResolvedValue(VENDORS);
  vi.mocked(api.fetchTerms).mockResolvedValue(TERMS);
});

describe("PoVendorsPage", () => {
  it("renders the vendor list under cap.po.manage with the write controls", async () => {
    vi.mocked(useAuth).mockReturnValue(authWith(["cap.po.manage"]));
    const { getByText, getAllByText } = render(<PoVendorsPage onBack={() => {}} />);
    await waitFor(() => expect(getByText("Apex Racking")).toBeTruthy());
    expect(getByText("+ Add a vendor")).toBeTruthy();
    expect(getAllByText("Edit").length).toBe(2);
    expect(getAllByText("Deactivate").length).toBe(2);
  });

  it("hides every write control without cap.po.manage (UI affordance; the router gates the view)", async () => {
    vi.mocked(useAuth).mockReturnValue(authWith([]));
    const { queryByText } = render(<PoVendorsPage onBack={() => {}} />);
    await waitFor(() => expect(queryByText("Apex Racking")).not.toBeNull());
    expect(queryByText("+ Add a vendor")).toBeNull();
    expect(queryByText("Edit")).toBeNull();
    expect(queryByText("Deactivate")).toBeNull();
  });

  it("creates a vendor with region, categories, and the terms profile; reloads on success", async () => {
    vi.mocked(useAuth).mockReturnValue(authWith(["cap.po.manage"]));
    vi.mocked(api.createVendor).mockResolvedValue({ vendor_key: "VEN-000003" });
    const { getByText, getByLabelText, getByRole } = render(<PoVendorsPage onBack={() => {}} />);
    await waitFor(() => expect(api.fetchVendors).toHaveBeenCalledTimes(1));

    fireEvent.click(getByText("+ Add a vendor"));
    fireEvent.change(getByLabelText("Vendor name (required)"), { target: { value: "VSUN Modules" } });
    fireEvent.change(getByLabelText("Contact email (the send-time recipient)"), {
      target: { value: "po@vsun.example" },
    });
    fireEvent.change(getByLabelText("Region"), { target: { value: "National" } });
    fireEvent.click(getByRole("button", { name: "Modules" })); // supply-category chip toggle
    fireEvent.change(getByLabelText("Default terms profile"), { target: { value: "standard_17" } });
    fireEvent.click(getByText("Add vendor"));

    await waitFor(() =>
      expect(api.createVendor).toHaveBeenCalledWith(
        expect.objectContaining({
          vendor_name: "VSUN Modules",
          contact_email: "po@vsun.example",
          region: "National",
          supply_categories: ["modules"],
          default_terms_profile: "standard_17",
          active: 1,
        }),
      ),
    );
    await waitFor(() => expect(api.fetchVendors).toHaveBeenCalledTimes(2)); // reload-after-write
  });

  it("shows the 'Syncing to Smartsheet' badge only on sync_state='pending' rows (§51 up-sync)", async () => {
    vi.mocked(useAuth).mockReturnValue(authWith(["cap.po.manage"]));
    const { getByText } = render(<PoVendorsPage onBack={() => {}} />);
    await waitFor(() => expect(getByText("Chint Power")).toBeTruthy());
    const pendingCard = getByText("Chint Power").closest(".card") as HTMLElement;
    const syncedCard = getByText("Apex Racking").closest(".card") as HTMLElement;
    expect(within(pendingCard).getByText("Syncing to Smartsheet")).toBeTruthy();
    expect(within(syncedCard).queryByText("Syncing to Smartsheet")).toBeNull();
  });

  it("deactivates through the two-step armed control — an update with active:0, never a delete", async () => {
    vi.mocked(useAuth).mockReturnValue(authWith(["cap.po.manage"]));
    vi.mocked(api.updateVendor).mockResolvedValue(undefined);
    const { getByText } = render(<PoVendorsPage onBack={() => {}} />);
    await waitFor(() => expect(getByText("Apex Racking")).toBeTruthy());
    const card = getByText("Apex Racking").closest(".card") as HTMLElement;

    fireEvent.click(within(card).getByText("Deactivate")); // arm
    expect(api.updateVendor).not.toHaveBeenCalled(); // first tap never fires the write
    fireEvent.click(within(card).getByText("Confirm deactivate"));

    await waitFor(() =>
      expect(api.updateVendor).toHaveBeenCalledWith(
        "VEN-000001",
        expect.objectContaining({ vendor_name: "Apex Racking", active: 0 }),
      ),
    );
  });

  it("edits a vendor in place, preserving its active flag", async () => {
    vi.mocked(useAuth).mockReturnValue(authWith(["cap.po.manage"]));
    vi.mocked(api.updateVendor).mockResolvedValue(undefined);
    const { getByText, getByLabelText } = render(<PoVendorsPage onBack={() => {}} />);
    await waitFor(() => expect(getByText("Apex Racking")).toBeTruthy());
    const card = getByText("Apex Racking").closest(".card") as HTMLElement;

    fireEvent.click(within(card).getByText("Edit"));
    fireEvent.change(getByLabelText("Contact phone"), { target: { value: "555-9999" } });
    fireEvent.click(within(card).getByText("Save"));

    await waitFor(() =>
      expect(api.updateVendor).toHaveBeenCalledWith(
        "VEN-000001",
        expect.objectContaining({ contact_phone: "555-9999", active: 1 }),
      ),
    );
  });
});
