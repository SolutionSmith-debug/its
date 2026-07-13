/**
 * PO/SC Configuration admin page — the EDITABLE editor. Confirms the read view still renders the PO
 * config classes, that each editor POSTs the right send-free change-request body (incl. the tax
 * percent→basis-points conversion and the terms new-version body via the shared TermsProfilesEditor),
 * that the status monitor renders pills for each request — never silent on a FAILED row — and that the
 * SUBCONTRACT editors (Contractor + the same TermsProfilesEditor under workstream=subcontracts) POST
 * under the subcontracts workstream. Read-only accounts see no editors or monitor. Mirrors the
 * admin-page test idiom: mock the libs (keep the real pctToBp via importActual) + auth, resetAllMocks,
 * drive with fireEvent, expect-inside-waitFor.
 */
import { cleanup, fireEvent, render, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../lib/po", async () => {
  const actual = await vi.importActual<typeof import("../../lib/po")>("../../lib/po");
  return {
    ...actual, // keep the real pctToBp (the tax %→bp conversion under test) + types
    fetchPoConfig: vi.fn(),
    fetchTerms: vi.fn(),
    fetchTermsText: vi.fn(),
    fetchTermsVersions: vi.fn(),
    submitConfigEdit: vi.fn(),
    fetchConfigStatus: vi.fn(),
  };
});
vi.mock("../../lib/subcontracts", () => ({
  fetchSubcontractConfig: vi.fn(),
  fetchTerms: vi.fn(),
  fetchTermsText: vi.fn(),
  fetchTermsVersions: vi.fn(),
  fetchExhibitTemplateKeys: vi.fn(),
  fetchExhibitKeyText: vi.fn(),
  fetchExhibitKeyVersions: vi.fn(),
}));
vi.mock("../../lib/auth", () => ({ useAuth: vi.fn() }));

import * as api from "../../lib/po";
import * as sub from "../../lib/subcontracts";
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

const SUB_CONFIG: sub.SubcontractConfig = {
  contractor: {
    entity: "Evergreen Renewables LLC",
    address_lines: ["100 Spectrum Center Dr. STE 570", "Irvine, CA. 92618"],
    phone: "888-303-6424",
    signature_entity: "Evergreen Renewables LLC",
    prime_contractor_default: "Evergreen Renewables of Virginia LLC",
  },
  payment_terms: { retainage_bp: 1000, retainage_reduced_bp: 500, retainage_reduction_at_pct: 50 },
  governing_law_states: ["VA", "OR"],
};

const SUB_TERMS: sub.TermsProfile[] = [
  {
    id: "standard_subcontract",
    kind: "library",
    label: "Standard 27-article subcontract",
    description: "The default subcontract terms.",
    current_version: "v1",
    tokens: ["{{contract_price_clause}}"],
    render_line: null,
  },
  {
    id: "negotiated_msa",
    kind: "attach",
    label: "Negotiated MSA",
    description: "Attach-not-generate.",
    current_version: null,
    tokens: [],
    render_line: "THE WORK IS UNDER, AND SUBJECT TO, THE NEGOTIATED MSA.",
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
  vi.mocked(api.fetchTermsText).mockResolvedValue({
    profile_id: "standard_17",
    version: "v1",
    text: "1. The current clause text.",
  });
  vi.mocked(api.fetchTermsVersions).mockResolvedValue({
    profile_id: "standard_17",
    current_version: "1",
    versions: [
      { version: "1", legal_review: "cleared" },
      { version: "standard_17_v2", legal_review: "pending" },
    ],
  });
  vi.mocked(api.fetchConfigStatus).mockResolvedValue([]);
  vi.mocked(api.submitConfigEdit).mockResolvedValue({ ok: true, id: 1, status: "queued" });
  // Subcontract config loads independently (best-effort in load()); seed it so the subcontract group
  // renders for a cap.subcontracts.manage account.
  vi.mocked(sub.fetchSubcontractConfig).mockResolvedValue(SUB_CONFIG);
  vi.mocked(sub.fetchTerms).mockResolvedValue(SUB_TERMS);
  vi.mocked(sub.fetchTermsText).mockResolvedValue({ profile_id: "standard_subcontract", version: "v1", text: "1. Current subcontract clause." });
  vi.mocked(sub.fetchTermsVersions).mockResolvedValue({
    profile_id: "standard_subcontract",
    current_version: "v1",
    versions: [
      { version: "v1", legal_review: "cleared" },
      { version: "standard_v2", legal_review: "pending" },
    ],
  });
  vi.mocked(sub.fetchExhibitTemplateKeys).mockResolvedValue([
    { template_key: "civil", current_version: "v1", trades: ["Civil"], versions: [{ version: "v1", legal_review: "cleared" }] },
    {
      template_key: "electrical",
      current_version: "v1",
      trades: ["AC Electrical", "MV Electrical", "DC Electrical"],
      versions: [{ version: "v1", legal_review: "cleared" }],
    },
  ]);
  vi.mocked(sub.fetchExhibitKeyText).mockResolvedValue({ template_key: "civil", version: "v1", article_ii: "Civil scope text." });
  vi.mocked(sub.fetchExhibitKeyVersions).mockResolvedValue({
    template_key: "civil",
    current_version: "v1",
    versions: [
      { version: "v1", legal_review: "cleared" },
      { version: "v2", legal_review: "pending" },
    ],
  });
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

  it("pre-fills the terms textarea with the current version's text (edit-from-live)", async () => {
    vi.mocked(api.fetchTermsText).mockResolvedValue({
      profile_id: "standard_17",
      version: "v1",
      text: "1. The pre-filled current clause.",
    });
    const { getByText, getByLabelText } = render(<PoConfigPage onBack={vi.fn()} />);
    await waitFor(() => expect(getByText("Standard 17-clause")).toBeTruthy());
    fireEvent.click(getByText("Add a terms version"));
    await waitFor(() =>
      expect((getByLabelText("Terms clause text") as HTMLTextAreaElement).value).toBe(
        "1. The pre-filled current clause.",
      ),
    );
    expect(api.fetchTermsText).toHaveBeenCalledWith("standard_17");
  });

  it("make-current: submits op:set_current for the chosen version, gated on the confirm checkbox", async () => {
    const { getByText, getByLabelText } = render(<PoConfigPage onBack={vi.fn()} />);
    await waitFor(() => expect(getByText("Standard 17-clause")).toBeTruthy());
    fireEvent.click(getByText("Make a version current"));
    // versions load; the picker pre-selects the first non-current version (standard_17_v2).
    await waitFor(() => expect(getByLabelText("Version to make current")).toBeTruthy());
    // The submit is DISABLED until the operator confirms review — explicit, not a silent toggle.
    expect((getByText("Make it live") as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(getByLabelText(/I have reviewed this version/i));
    fireEvent.click(getByText("Make it live"));
    await waitFor(() =>
      expect(api.submitConfigEdit).toHaveBeenCalledWith({
        workstream: "po_materials",
        artifact_key: "terms",
        op: "set_current",
        payload: { profile_id: "standard_17" },
        target_version: "standard_17_v2",
      }),
    );
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

  it("a PO-only admin (no cap.subcontracts.manage) does NOT see the subcontract group", async () => {
    // Default authWith is cap.po.manage only.
    const { getByText, queryByText } = render(<PoConfigPage onBack={vi.fn()} />);
    await waitFor(() => expect(getByText("Evergreen Renewables LLC")).toBeTruthy());
    expect(queryByText("Contractor (subcontracts)")).toBeNull();
    expect(queryByText("Subcontract terms profiles")).toBeNull();
  });
});

describe("PoConfigPage — subcontract config (workstream=subcontracts)", () => {
  const bothCaps = () => vi.mocked(useAuth).mockReturnValue(authWith(["cap.po.manage", "cap.subcontracts.manage"]));

  it("an admin with cap.subcontracts.manage sees the Contractor + subcontract terms editors", async () => {
    bothCaps();
    const { getByText, getByLabelText } = render(<PoConfigPage onBack={vi.fn()} />);
    await waitFor(() => expect(getByText("Contractor (subcontracts)")).toBeTruthy());
    expect(getByText("Subcontract terms profiles")).toBeTruthy();
    expect(getByText("Standard 27-article subcontract")).toBeTruthy();
    // The contractor editor opens seeded from the served subcontract config.
    fireEvent.click(getByText("Edit contractor"));
    expect((getByLabelText("Entity (required)") as HTMLInputElement).value).toBe("Evergreen Renewables LLC");
  });

  it("a subcontract contractor edit POSTs op:edit under workstream=subcontracts with the full payload", async () => {
    bothCaps();
    const { getByText, getByLabelText } = render(<PoConfigPage onBack={vi.fn()} />);
    await waitFor(() => expect(getByText("Contractor (subcontracts)")).toBeTruthy());
    fireEvent.click(getByText("Edit contractor"));
    fireEvent.change(getByLabelText("Entity (required)"), { target: { value: "Evergreen Renewables Holdings LLC" } });
    fireEvent.click(getByText("Queue change"));
    await waitFor(() =>
      expect(api.submitConfigEdit).toHaveBeenCalledWith(
        expect.objectContaining({
          workstream: "subcontracts",
          artifact_key: "contractor",
          op: "edit",
          payload: expect.objectContaining({
            entity: "Evergreen Renewables Holdings LLC",
            address_lines: ["100 Spectrum Center Dr. STE 570", "Irvine, CA. 92618"],
            signature_entity: "Evergreen Renewables LLC",
            prime_contractor_default: "Evergreen Renewables of Virginia LLC",
          }),
        }),
      ),
    );
  });

  it("a subcontract terms make-current POSTs op:set_current under workstream=subcontracts", async () => {
    bothCaps();
    const { container, getByText } = render(<PoConfigPage onBack={vi.fn()} />);
    await waitFor(() => expect(getByText("Subcontract terms profiles")).toBeTruthy());
    // Two TermsProfilesEditor instances render (PO + subcontract) — scope to the subcontract section.
    const subSection = container.querySelector('[aria-label="Subcontract terms profiles"]') as HTMLElement;
    fireEvent.click(within(subSection).getByText("Make a version current"));
    await waitFor(() => expect(within(subSection).getByLabelText("Version to make current")).toBeTruthy());
    fireEvent.click(within(subSection).getByLabelText(/I have reviewed this version/i));
    fireEvent.click(within(subSection).getByText("Make it live"));
    await waitFor(() =>
      expect(api.submitConfigEdit).toHaveBeenCalledWith({
        workstream: "subcontracts",
        artifact_key: "terms",
        op: "set_current",
        payload: { profile_id: "standard_subcontract" },
        target_version: "standard_v2",
      }),
    );
  });

  it("an exhibit add_version POSTs op:add_version under workstream=subcontracts, artifact=exhibit", async () => {
    bothCaps();
    const { container, getByText } = render(<PoConfigPage onBack={vi.fn()} />);
    await waitFor(() => expect(getByText("Exhibit A — Article II templates")).toBeTruthy());
    const exSection = container.querySelector('[aria-label="Exhibit A trade templates"]') as HTMLElement;
    await waitFor(() => expect(within(exSection).getByText("civil")).toBeTruthy()); // templates loaded
    fireEvent.click(within(exSection).getByText("Add an Article II version"));
    // The scope textarea pre-fills from the current version's text.
    await waitFor(() =>
      expect((within(exSection).getByLabelText("Article II scope text") as HTMLTextAreaElement).value).toBe(
        "Civil scope text.",
      ),
    );
    fireEvent.change(within(exSection).getByLabelText("New version name (lowercase, e.g. v2)"), {
      target: { value: "v2" },
    });
    fireEvent.change(within(exSection).getByLabelText("Article II scope text"), {
      target: { value: "Civil v2 scope." },
    });
    fireEvent.click(within(exSection).getByText("Queue new version"));
    await waitFor(() =>
      expect(api.submitConfigEdit).toHaveBeenCalledWith({
        workstream: "subcontracts",
        artifact_key: "exhibit",
        op: "add_version",
        payload: { template_key: "civil", text: "Civil v2 scope." },
        target_version: "v2",
      }),
    );
  });

  it("an exhibit make-current POSTs op:set_current under workstream=subcontracts, artifact=exhibit", async () => {
    bothCaps();
    const { container, getByText } = render(<PoConfigPage onBack={vi.fn()} />);
    await waitFor(() => expect(getByText("Exhibit A — Article II templates")).toBeTruthy());
    const exSection = container.querySelector('[aria-label="Exhibit A trade templates"]') as HTMLElement;
    await waitFor(() => expect(within(exSection).getByText("civil")).toBeTruthy()); // templates loaded
    fireEvent.click(within(exSection).getByText("Make a version current"));
    await waitFor(() => expect(within(exSection).getByLabelText("Version to make current")).toBeTruthy());
    fireEvent.click(within(exSection).getByLabelText(/I have reviewed this version/i));
    fireEvent.click(within(exSection).getByText("Make it live"));
    await waitFor(() =>
      expect(api.submitConfigEdit).toHaveBeenCalledWith({
        workstream: "subcontracts",
        artifact_key: "exhibit",
        op: "set_current",
        payload: { template_key: "civil" },
        target_version: "v2",
      }),
    );
  });
});
