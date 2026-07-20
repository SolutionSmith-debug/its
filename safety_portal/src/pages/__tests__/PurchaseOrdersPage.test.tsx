/**
 * Purchase-Orders HUB (2026-07 fold) — the tab strip, the keep-alive panels, and the two
 * cross-tab handoffs that are the fold's point:
 *   • Orders "New PO from a vendor estimate" → the DISPOSITION screen on the Estimates tab
 *     (the ADR-0004 decision-3 fidelity gate stays the only estimate→PO path), and
 *   • a disposition import → back on the Orders tab with the minted draft OPEN in the
 *     builder, still editable.
 * The REAL panel components mount inside the hub (integration — the handoff wiring is what
 * this file locks); every network read/write is mocked at the lib layer.
 */
import { cleanup, fireEvent, render, waitFor, within } from "@testing-library/react";
import { useState } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../lib/po", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../lib/po")>();
  return {
    ...actual, // keeps the REAL money math + catalogLineFields
    fetchVendors: vi.fn(),
    fetchTerms: vi.fn(),
    fetchPoConfig: vi.fn(),
    fetchPoMaterials: vi.fn(),
    fetchPos: vi.fn(),
    fetchPo: vi.fn(),
    fetchJobShipTo: vi.fn(),
    createDraft: vi.fn(),
    updateDraft: vi.fn(),
    generateDraft: vi.fn(),
    supersedePo: vi.fn(),
    cancelPo: vi.fn(),
    deletePoDraft: vi.fn(),
    fetchPoAttachments: vi.fn(),
    uploadPoAttachment: vi.fn(),
    deletePoAttachment: vi.fn(),
  };
});
vi.mock("../../lib/estimates", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../lib/estimates")>();
  return {
    ...actual, // keeps ESTIMATE_STATUS_LABEL + the upload constants
    fetchEstimates: vi.fn(),
    fetchEstimate: vi.fn(),
    uploadEstimate: vi.fn(),
    disposeEstimate: vi.fn(),
  };
});
vi.mock("../../lib/rfq", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../lib/rfq")>();
  return {
    ...actual,
    fetchRfqs: vi.fn(),
    fetchRfq: vi.fn(),
    createRfqDraft: vi.fn(),
    updateRfqDraft: vi.fn(),
    generateRfq: vi.fn(),
    cancelRfq: vi.fn(),
  };
});
vi.mock("../../lib/api", () => ({ fetchJobs: vi.fn() }));
vi.mock("../../lib/auth", () => ({ useAuth: vi.fn() }));

import * as api from "../../lib/po";
import * as est from "../../lib/estimates";
import * as rfq from "../../lib/rfq";
import { fetchJobs } from "../../lib/api";
import { useAuth } from "../../lib/auth";
import { PurchaseOrdersPage, type PoTab } from "../PurchaseOrdersPage";

function authWith(capabilities: string[]) {
  return {
    user: { username: "office", role: "admin" as const, capabilities },
    loading: false,
    login: vi.fn(),
    logout: vi.fn(),
  };
}

const VENDORS: api.Vendor[] = [
  {
    vendor_key: "VEN-000001",
    vendor_name: "Apex Racking",
    address: "1 Steel Way",
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
  },
];

/** One reviewable (manual-entry) estimate — no extraction, no previews, so the disposition's
 *  preview gate is legitimately skipped (hasExtractionLines false) and Tier-3 manual entry
 *  is the import path. */
const ESTIMATE_ROW: est.EstimateRow = {
  id: 5,
  est_uuid: "e-5",
  job_no: "2023.126",
  job_name: "Kendall Solar",
  vendor_key: null,
  filename: "apex-quote.pdf",
  declared_mime: "application/pdf",
  size_bytes: 1000,
  sha256: "abc",
  status: "needs_review",
  doc_type: "quote",
  detail: null,
  uploaded_by: "office",
  box_file_id: null,
  family_key: null,
  supersedes_estimate_id: null,
  po_id: null,
  rfq_id: null,
  rfq_vendor_key: null,
  created_at: 1,
  screened_at: 1,
  extracted_at: null,
  disposed_at: null,
};

const PO_DETAIL = {
  po: {
    id: 77,
    po_number: null,
    job_no: "2023.126",
    site_phase: 0,
    supersede_seq: 0,
    revision: null,
    job_id: "",
    job_name: "Kendall Solar",
    vendor_key: "VEN-000001",
    status: "draft",
    supersedes_po_id: null,
    total_cents: 3702,
    updated_at: 1,
    created_at: 1,
    ship_to_name: "Kendall Solar",
    ship_to_address: "",
    ship_to_city: "",
    ship_to_state: "IL",
    ship_to_zip: "",
    delivery_contact_name: "",
    delivery_contact_phone: "",
    delivery_contact_email: "",
    sow_text: "",
    delivery_instructions: "",
    payment_terms_text: "",
    terms_profile_id: "",
    terms_version: "",
    subtotal_cents: 3702,
    tax_mode: "auto",
    tax_rate_bp: 900,
    tax_cents: 333,
    shipping_cents: 0,
    line_column_variant: "default",
    approver_name: "",
    approver_title: "",
  } as api.PoDetail,
  line_items: [
    {
      position: 1,
      part_number: "",
      description: "Rail 208in",
      qty: 3,
      unit: "EA",
      unit_cost_cents: 1234,
      extended_cents: 3702,
      watts: null,
      panels: null,
      pallets: null,
      price_per_watt_microcents: null,
    },
  ],
};

/** Route-shaped harness: the hub's `tab` prop is App-owned state in production. */
function Harness({ initialTab = "orders" as PoTab }) {
  const [tab, setTab] = useState<PoTab>(initialTab);
  return <PurchaseOrdersPage tab={tab} onTabChange={setTab} onBack={() => {}} />;
}

const panel = (container: HTMLElement, label: string) =>
  container.querySelector(`[role="tabpanel"][aria-label="${label}"]`) as HTMLElement | null;

afterEach(cleanup);
beforeEach(() => {
  vi.resetAllMocks();
  vi.mocked(useAuth).mockReturnValue(authWith(["cap.po.manage"]));
  vi.mocked(api.fetchPos).mockResolvedValue([]);
  vi.mocked(api.fetchVendors).mockResolvedValue(VENDORS);
  vi.mocked(api.fetchTerms).mockResolvedValue([]);
  vi.mocked(api.fetchPoConfig).mockResolvedValue(null as never);
  vi.mocked(api.fetchPoMaterials).mockResolvedValue([]);
  vi.mocked(fetchJobs).mockResolvedValue([]);
  vi.mocked(rfq.fetchRfqs).mockResolvedValue([]);
  vi.mocked(est.fetchEstimates).mockResolvedValue([]);
});

describe("PurchaseOrdersPage — tab strip + keep-alive panels", () => {
  it("renders the three tabs; panels mount on first visit and STAY mounted across flips", async () => {
    const { container, getByRole } = render(<Harness />);
    await waitFor(() => expect(api.fetchPos).toHaveBeenCalledTimes(1));

    // Orders is the active tab; the other panels haven't mounted (no fetch yet).
    expect(getByRole("tab", { name: "Purchase Orders" }).getAttribute("aria-selected")).toBe("true");
    expect(panel(container, "Purchase Orders")!.hasAttribute("hidden")).toBe(false);
    expect(panel(container, "RFQs")).toBeNull();
    expect(rfq.fetchRfqs).not.toHaveBeenCalled();

    // Flip to RFQs: its panel mounts (single fetch); Orders hides but STAYS mounted.
    fireEvent.click(getByRole("tab", { name: "RFQs" }));
    await waitFor(() => expect(rfq.fetchRfqs).toHaveBeenCalledTimes(1));
    expect(panel(container, "Purchase Orders")!.hasAttribute("hidden")).toBe(true);
    expect(panel(container, "RFQs")!.hasAttribute("hidden")).toBe(false);

    // Flip back: no re-mount, no re-fetch (keep-alive is the wizard-state guarantee).
    fireEvent.click(getByRole("tab", { name: "Purchase Orders" }));
    expect(panel(container, "Purchase Orders")!.hasAttribute("hidden")).toBe(false);
    expect(api.fetchPos).toHaveBeenCalledTimes(1);
    expect(rfq.fetchRfqs).toHaveBeenCalledTimes(1);
  });

  it("cold-loading the estimates tab (deep link) renders it active with the upload form", async () => {
    const { container, getByRole } = render(<Harness initialTab="estimates" />);
    await waitFor(() => expect(est.fetchEstimates).toHaveBeenCalled());
    expect(getByRole("tab", { name: "Vendor Estimates" }).getAttribute("aria-selected")).toBe("true");
    expect(within(panel(container, "Vendor Estimates")!).getByText("Upload an estimate")).toBeTruthy();
    expect(panel(container, "Purchase Orders")).toBeNull(); // unvisited ⇒ unmounted
  });
});

describe("PurchaseOrdersPage — the estimate→PO fold", () => {
  it("walks the whole lane: pick on Orders → disposition on Estimates → import → draft OPEN in the builder", async () => {
    vi.mocked(est.fetchEstimates).mockResolvedValue([ESTIMATE_ROW]);
    vi.mocked(est.fetchEstimate).mockResolvedValue({
      estimate: ESTIMATE_ROW,
      extraction: null,
      lines: [],
      preview_count: 0,
    });
    vi.mocked(api.createDraft).mockResolvedValue({
      id: 77,
      totals: { subtotal_cents: 3702, tax_rate_bp: 900, tax_cents: 333, total_cents: 4035 },
    });
    vi.mocked(est.disposeEstimate).mockResolvedValue({ ok: true, status: "imported" });
    vi.mocked(api.fetchPo).mockResolvedValue(PO_DETAIL);
    vi.mocked(api.fetchPoAttachments).mockResolvedValue([]);

    const { container, getByRole, getByText, getByLabelText } = render(<Harness />);
    await waitFor(() => expect(api.fetchPos).toHaveBeenCalled());

    // 1 — the Orders tracker offers "New PO from a vendor estimate"; opening it lists the
    //     reviewable rows (fetched on demand).
    fireEvent.click(getByText("New PO from a vendor estimate"));
    await waitFor(() => expect(getByText("apex-quote.pdf")).toBeTruthy());

    // 2 — picking one flips to the Estimates tab and opens the DISPOSITION screen (the
    //     fidelity gate — never a direct import).
    fireEvent.click(getByText("Review & import"));
    await waitFor(() => expect(est.fetchEstimate).toHaveBeenCalledWith(5));
    expect(getByRole("tab", { name: "Vendor Estimates" }).getAttribute("aria-selected")).toBe("true");
    await waitFor(() => expect(getByText("Confirm & import")).toBeTruthy());

    // 3 — Tier-3 manual entry (this doc has no extraction lines, so the preview gate is
    //     legitimately inapplicable): one line + vendor + state; job_no pre-filled.
    fireEvent.change(getByLabelText("Manual line 1 description"), { target: { value: "Rail 208in" } });
    fireEvent.change(getByLabelText("Manual line 1 quantity"), { target: { value: "3" } });
    fireEvent.change(getByLabelText("Manual line 1 unit cost"), { target: { value: "12.34" } });
    fireEvent.change(getByLabelText("Vendor"), { target: { value: "VEN-000001" } });
    fireEvent.change(getByLabelText("Ship-to state (2 letters — drives tax)"), { target: { value: "IL" } });

    // 4 — import: the draft is created through the EXISTING createDraft route with the
    //     estimate_id provenance, then the estimate is disposed.
    fireEvent.click(getByText("Create draft PO"));
    await waitFor(() => expect(api.createDraft).toHaveBeenCalledTimes(1));
    expect(vi.mocked(api.createDraft).mock.calls[0][0]).toMatchObject({
      vendor_key: "VEN-000001",
      job_no: "2023.126",
      estimate_id: 5,
    });
    await waitFor(() => expect(est.disposeEstimate).toHaveBeenCalledWith(5, expect.objectContaining({ action: "imported", po_id: 77 })));

    // 5 — the hub hands the minted draft back to the Orders tab, OPEN in the builder and
    //     fully editable (the user can add/modify lines before Generate).
    await waitFor(() =>
      expect(getByRole("tab", { name: "Purchase Orders" }).getAttribute("aria-selected")).toBe("true"),
    );
    await waitFor(() => expect(api.fetchPo).toHaveBeenCalledWith(77));
    await waitFor(() => expect(getByText(/Estimate imported into draft #77/)).toBeTruthy());
    expect(getByText("Editing draft #77")).toBeTruthy();
    // The imported line is sitting in the editable grid, not a read-only view.
    expect((getByLabelText("Line 1 description") as HTMLInputElement).value).toBe("Rail 208in");
  });

  it("with nothing reviewable, the picker points at the Vendor Estimates tab", async () => {
    vi.mocked(est.fetchEstimates).mockResolvedValue([{ ...ESTIMATE_ROW, status: "imported", po_id: 42 }]);
    const { getByRole, getByText } = render(<Harness />);
    await waitFor(() => expect(api.fetchPos).toHaveBeenCalled());

    fireEvent.click(getByText("New PO from a vendor estimate"));
    await waitFor(() => expect(getByText(/No estimates are ready to import/)).toBeTruthy());
    fireEvent.click(getByText("Open Vendor Estimates"));
    await waitFor(() =>
      expect(getByRole("tab", { name: "Vendor Estimates" }).getAttribute("aria-selected")).toBe("true"),
    );
  });
});
