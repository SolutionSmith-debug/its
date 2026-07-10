/**
 * PO Configuration admin page — the EDITABLE editor (slice 3). Confirms the read view still renders
 * the three config classes, that each editor POSTs the right send-free change-request body (incl. the
 * tax percent→basis-points conversion and the terms new-version body), that the status monitor renders
 * pills for each request — never silent on a FAILED row (its failure_reason prints verbatim) — and that
 * the provisioned Subcontracts card is present + disabled. Read-only accounts (no cap.po.manage) see no
 * editors or monitor. Mirrors the admin-page test idiom: mock the lib (keep the real pctToBp via
 * importActual) + auth, resetAllMocks, drive with fireEvent, expect-inside-waitFor.
 */
import { cleanup, fireEvent, render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../lib/po", async () => {
  const actual = await vi.importActual<typeof import("../../lib/po")>("../../lib/po");
  return {
    ...actual, // keep the real pctToBp (the tax %→bp conversion under test) + types
    fetchPoConfig: vi.fn(),
    fetchTerms: vi.fn(),
    submitConfigEdit: vi.fn(),
    fetchConfigStatus: vi.fn(),
  };
});
vi.mock("../../lib/auth", () => ({ useAuth: vi.fn() }));

import * as api from "../../lib/po";
import { useAuth } from "../../lib/auth";
import { PoConfigPage } from "../PoConfigPage";

const CONFIG: api.PoConfig = {
  purchaser: {
    entity: "Evergreen Renewables LLC",
    address_lines: ["100 Spectrum Center Dr. STE 570", "Irvine, CA. 92618"],
    phone: "888-303-6424",
    invoice_routing: { to: "invoices@evergreenrenewables.com", cc: ["tealap@evergreenrenewables.com"] },
  },
  tax: {
    rates_bp: { IL: 900, OR: 0 },
    state_names: { IL: "Illinois", OR: "Oregon" },
  },
};

const TERMS: api.TermsProfile[] = [
  {
    id: "standard_17",
    kind: "library",
    label: "Standard 17-clause",
    description: "The default purchase-order terms.",
    current_version: "v1",
    tokens: ["{{purchaser_entity}}", "{{seller_name}}"],
    render_line: null,
  },
  {
    id: "negotiated_gtc",
    kind: "attach",
    label: "Negotiated GTC",
    description: "Vendor-negotiated master terms, attached not generated.",
    current_version: null,
    tokens: [],
    render_line: "See attached negotiated GTC.",
  },
];

function authWith(capabilities: string[] = ["cap.po.manage"]) {
  return {
    user: { username: "admin", role: "admin" as const, capabilities },
    loading: false,
    login: vi.fn(),
    logout: vi.fn(),
  };
}

afterEach(cleanup);
beforeEach(() => {
  vi.resetAllMocks();
  vi.mocked(useAuth).mockReturnValue(authWith());
  vi.mocked(api.fetchPoConfig).mockResolvedValue(CONFIG);
  vi.mocked(api.fetchTerms).mockResolvedValue(TERMS);
  vi.mocked(api.fetchConfigStatus).mockResolvedValue([]);
  vi.mocked(api.submitConfigEdit).mockResolvedValue({ ok: true, id: 1, status: "queued" });
});

describe("PoConfigPage — read view", () => {
  it("renders the purchaser identity", async () => {
    const { getByText } = render(<PoConfigPage onBack={vi.fn()} />);
    await waitFor(() => expect(getByText("Evergreen Renewables LLC")).toBeTruthy());
    expect(getByText("100 Spectrum Center Dr. STE 570")).toBeTruthy();
    expect(getByText("To: invoices@evergreenrenewables.com")).toBeTruthy();
  });

  it("renders tax rates as percents (basis points → %)", async () => {
    const { getByText } = render(<PoConfigPage onBack={vi.fn()} />);
    await waitFor(() => expect(getByText("9.00%")).toBeTruthy()); // 900 bp
    expect(getByText("0.00%")).toBeTruthy(); // 0 bp (OR) — never dropped
    expect(getByText("Illinois")).toBeTruthy();
  });

  it("renders both terms profiles with kind and version", async () => {
    const { getByText } = render(<PoConfigPage onBack={vi.fn()} />);
    await waitFor(() => expect(getByText("Standard 17-clause")).toBeTruthy());
    expect(getByText("Negotiated GTC")).toBeTruthy();
    expect(getByText("v: v1")).toBeTruthy();
    expect(getByText("See attached negotiated GTC.")).toBeTruthy();
  });

  it("shows an error banner when the config load fails", async () => {
    vi.mocked(api.fetchPoConfig).mockRejectedValue(new Error("boom"));
    const { getByText } = render(<PoConfigPage onBack={vi.fn()} />);
    await waitFor(() => expect(getByText(/Could not load PO configuration/)).toBeTruthy());
  });
});

describe("PoConfigPage — editors (send-free enqueue)", () => {
  it("purchaser edit POSTs an op:edit request with the full purchaser payload", async () => {
    const { container, getByText } = render(<PoConfigPage onBack={vi.fn()} />);
    await waitFor(() => expect(getByText("Evergreen Renewables LLC")).toBeTruthy());
    fireEvent.click(getByText("Edit purchaser"));
    const editor = container.querySelector('[aria-label="Purchaser identity"] .accounts__editor') as HTMLElement;
    const inputs = editor.querySelectorAll("input.field__input"); // [entity, phone, to]
    fireEvent.change(inputs[0], { target: { value: "Evergreen Renewables Holdings LLC" } });
    fireEvent.click(getByText("Queue change"));
    await waitFor(() =>
      expect(api.submitConfigEdit).toHaveBeenCalledWith(
        expect.objectContaining({
          workstream: "po_materials",
          artifact_key: "purchaser",
          op: "edit",
          payload: expect.objectContaining({
            entity: "Evergreen Renewables Holdings LLC",
            address_lines: ["100 Spectrum Center Dr. STE 570", "Irvine, CA. 92618"],
            invoice_routing: expect.objectContaining({ to: "invoices@evergreenrenewables.com" }),
          }),
        }),
      ),
    );
    // Optimistic never-silent banner + a status re-poll.
    await waitFor(() => expect(getByText(/Queued — the purchaser change/)).toBeTruthy());
  });

  it("tax edit converts the percent to integer basis points in the payload", async () => {
    const { container, getByText } = render(<PoConfigPage onBack={vi.fn()} />);
    await waitFor(() => expect(getByText("9.00%")).toBeTruthy());
    fireEvent.click(getByText("Edit tax table"));
    const rows = container.querySelectorAll(".po-config__tax-edit-row");
    // rows are seeded in sorted state order (IL, OR); row 0 = IL, its 3rd input is the rate %.
    const ilRate = rows[0].querySelectorAll("input.field__input")[2];
    fireEvent.change(ilRate, { target: { value: "7.5" } }); // 7.5% → 750 bp
    fireEvent.click(getByText("Queue change"));
    await waitFor(() =>
      expect(api.submitConfigEdit).toHaveBeenCalledWith(
        expect.objectContaining({
          workstream: "po_materials",
          artifact_key: "tax",
          op: "edit",
          payload: {
            rates_bp: { IL: 750, OR: 0 },
            state_names: { IL: "Illinois", OR: "Oregon" },
          },
        }),
      ),
    );
  });

  it("rejects a bad rate client-side (never reaches the wire)", async () => {
    const { container, getByText } = render(<PoConfigPage onBack={vi.fn()} />);
    await waitFor(() => expect(getByText("9.00%")).toBeTruthy());
    fireEvent.click(getByText("Edit tax table"));
    const rows = container.querySelectorAll(".po-config__tax-edit-row");
    fireEvent.change(rows[0].querySelectorAll("input.field__input")[2], { target: { value: "9.005" } }); // >2dp → non-integer bp
    fireEvent.click(getByText("Queue change"));
    await waitFor(() => expect(getByText(/Enter the IL rate as a percent/)).toBeTruthy());
    expect(api.submitConfigEdit).not.toHaveBeenCalled();
  });

  it("terms edit POSTs an op:add_version request with target_version + {profile_id, text}", async () => {
    const { getByText, getByLabelText } = render(<PoConfigPage onBack={vi.fn()} />);
    await waitFor(() => expect(getByText("Standard 17-clause")).toBeTruthy());
    fireEvent.click(getByText("Add a terms version"));
    fireEvent.change(getByLabelText(/New version name/), { target: { value: "standard_17_v2" } });
    fireEvent.change(getByLabelText("Terms clause text"), { target: { value: "1. The revised clause text." } });
    fireEvent.click(getByText("Queue new version"));
    await waitFor(() =>
      expect(api.submitConfigEdit).toHaveBeenCalledWith({
        workstream: "po_materials",
        artifact_key: "terms",
        op: "add_version",
        payload: { profile_id: "standard_17", text: "1. The revised clause text." },
        target_version: "standard_17_v2",
      }),
    );
    // The legal-review gate is surfaced in the editor.
    expect(getByText(/legal_review: pending/i)).toBeTruthy();
  });

  it("surfaces a Worker rejection verbatim (never silent) — config_edit_in_progress", async () => {
    const { ApiError } = await vi.importActual<typeof import("../../lib/errorCopy")>("../../lib/errorCopy");
    vi.mocked(api.submitConfigEdit).mockRejectedValue(new ApiError("config_edit_in_progress", 409));
    const { getByText } = render(<PoConfigPage onBack={vi.fn()} />);
    await waitFor(() => expect(getByText("Evergreen Renewables LLC")).toBeTruthy());
    fireEvent.click(getByText("Edit purchaser"));
    fireEvent.click(getByText("Queue change"));
    await waitFor(() => expect(getByText(/already being processed/)).toBeTruthy());
  });
});

describe("PoConfigPage — status monitor", () => {
  const NOW = 1_700_000_000;
  it("renders a status pill per request and never silences a FAILED row", async () => {
    vi.mocked(api.fetchConfigStatus).mockResolvedValue([
      { id: 2, workstream: "po_materials", artifact_key: "tax", op: "edit", status: "live", failed_stage: null, failure_reason: null, created_at: NOW, updated_at: NOW },
      { id: 1, workstream: "po_materials", artifact_key: "terms", op: "add_version", status: "failed", failed_stage: "validate", failure_reason: "schema check failed: bad rate", created_at: NOW, updated_at: NOW },
    ] as api.ConfigRequest[]);
    const { getByText, container } = render(<PoConfigPage onBack={vi.fn()} />);
    await waitFor(() => expect(getByText("failed")).toBeTruthy());
    expect(getByText("live")).toBeTruthy();
    // failure_reason printed verbatim, in an alert.
    const failure = getByText(/schema check failed: bad rate/);
    expect(failure).toBeTruthy();
    expect(failure.getAttribute("role")).toBe("alert");
    // The failed row carries the failed styling hook.
    expect(container.querySelector(".form-editor__req--failed")).toBeTruthy();
  });
});

describe("PoConfigPage — capability gating", () => {
  it("a read-only account (no cap.po.manage) sees the read view but no editors or monitor", async () => {
    vi.mocked(useAuth).mockReturnValue(authWith(["cap.po.receive"]));
    const { getByText, queryByText } = render(<PoConfigPage onBack={vi.fn()} />);
    await waitFor(() => expect(getByText("Evergreen Renewables LLC")).toBeTruthy());
    expect(queryByText("Edit purchaser")).toBeNull();
    expect(queryByText("Edit tax table")).toBeNull();
    expect(queryByText("Add a terms version")).toBeNull();
    expect(queryByText("Config change status")).toBeNull();
    expect(api.fetchConfigStatus).not.toHaveBeenCalled();
  });

  it("shows the provisioned Subcontracts placeholder as present + disabled", async () => {
    const { getByText } = render(<PoConfigPage onBack={vi.fn()} />);
    await waitFor(() => expect(getByText("Subcontracts")).toBeTruthy());
    const btn = getByText("Edit subcontracts (coming soon)") as HTMLButtonElement;
    expect(btn.disabled).toBe(true);
  });
});
