/**
 * PO Configuration admin page — a READ-ONLY view of purchaser identity (D5), the ship-to tax
 * table (D8), and the terms profiles (D6/S3). No writes, so the test just confirms the three
 * config classes render from their fetch helpers and that basis-points render as a percent.
 * Mirrors the admin-page test idiom: mock the lib + auth (PageShell pulls useAuth), resetAllMocks,
 * expect-inside-waitFor.
 */
import { cleanup, render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../lib/po", () => ({
  fetchPoConfig: vi.fn(),
  fetchTerms: vi.fn(),
}));
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

function authWith() {
  return {
    user: { username: "admin", role: "admin" as const, capabilities: ["cap.po.manage"] },
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
});

describe("PoConfigPage", () => {
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
