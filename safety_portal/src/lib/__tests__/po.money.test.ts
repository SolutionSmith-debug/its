/**
 * PO money helpers (S6) — the display mirror of the Worker's integer-cents math
 * (worker/po.ts lineExtendedCents / computeTotals). These pins matter: the SPA shows the
 * office admin the SAME cents the Worker will recompute and sign, so a green mirror here is
 * what keeps the generate-time totals assert from 409ing in normal use. String-math parsers
 * are pinned against the classic float trap (19.99 × 100 ≠ 1999 in doubles).
 */
import { describe, expect, it } from "vitest";
import {
  computeDisplayTotals,
  formatCents,
  lineExtendedCents,
  parseDollarsToCents,
  parseDollarsToMicrocents,
} from "../po";

describe("formatCents", () => {
  it("renders integer cents as $1,234.56", () => {
    expect(formatCents(0)).toBe("$0.00");
    expect(formatCents(5)).toBe("$0.05");
    expect(formatCents(4046)).toBe("$40.46");
    expect(formatCents(123456)).toBe("$1,234.56");
    expect(formatCents(1_000_000_00)).toBe("$1,000,000.00");
    expect(formatCents(-4046)).toBe("-$40.46");
  });
});

describe("parseDollarsToCents", () => {
  it("parses dollars strings by string math (no float drift)", () => {
    expect(parseDollarsToCents("19.99")).toBe(1999); // the float-trap case
    expect(parseDollarsToCents("12")).toBe(1200);
    expect(parseDollarsToCents("12.3")).toBe(1230);
    expect(parseDollarsToCents("$1,234.56")).toBe(123456);
    expect(parseDollarsToCents("0.05")).toBe(5);
  });
  it("rejects malformed / >2dp input", () => {
    expect(parseDollarsToCents("")).toBeNull();
    expect(parseDollarsToCents("abc")).toBeNull();
    expect(parseDollarsToCents("12.345")).toBeNull();
    expect(parseDollarsToCents("-5")).toBeNull();
  });
});

describe("parseDollarsToMicrocents", () => {
  it("parses $/W to microcents (dollars × 1e8)", () => {
    expect(parseDollarsToMicrocents("0.35")).toBe(35_000_000);
    expect(parseDollarsToMicrocents("1")).toBe(100_000_000);
    expect(parseDollarsToMicrocents("0.00000001")).toBe(1);
  });
  it("rejects malformed / >8dp input", () => {
    expect(parseDollarsToMicrocents("0.000000001")).toBeNull();
    expect(parseDollarsToMicrocents("x")).toBeNull();
  });
});

describe("lineExtendedCents (Worker mirror)", () => {
  it("default lines: round(qty × unit_cost_cents)", () => {
    expect(lineExtendedCents({ qty: 3, unit_cost_cents: 1234, watts: null, price_per_watt_microcents: null })).toBe(3702);
    // fractional qty rounds like the Worker
    expect(lineExtendedCents({ qty: 2.5, unit_cost_cents: 101, watts: null, price_per_watt_microcents: null })).toBe(253);
  });
  it("per-watt lines: round(watts × ppw_microcents / 1e6)", () => {
    expect(lineExtendedCents({ qty: 1, unit_cost_cents: null, watts: 1000, price_per_watt_microcents: 350_000 })).toBe(350);
    expect(
      lineExtendedCents({ qty: 1, unit_cost_cents: null, watts: 400_000, price_per_watt_microcents: 35_000_000 }),
    ).toBe(14_000_000); // 400 kW at $0.35/W = $140,000.00 = 14,000,000 cents
  });
});

describe("computeDisplayTotals (Worker mirror)", () => {
  const LINES = [
    { qty: 3, unit_cost_cents: 1234, watts: null, price_per_watt_microcents: null }, // 3702
    { qty: 2, unit_cost_cents: 5, watts: null, price_per_watt_microcents: null }, // 10
  ];
  const RATES = { IL: 900, OR: 0 };

  it("auto resolves the state rate: subtotal 3712 → tax 334 → total 4046", () => {
    expect(computeDisplayTotals(LINES, "auto", 0, 0, "IL", RATES)).toEqual({
      subtotal_cents: 3712,
      tax_rate_bp: 900,
      tax_cents: 334,
      total_cents: 4046,
    });
  });
  it("auto FAILS CLOSED (null) on a state missing from the table", () => {
    expect(computeDisplayTotals(LINES, "auto", 0, 0, "CA", RATES)).toBeNull();
  });
  it("exempt/included zero the tax; override uses the given bp; shipping rides the total", () => {
    expect(computeDisplayTotals(LINES, "exempt", 0, 250, "IL", RATES)).toEqual({
      subtotal_cents: 3712,
      tax_rate_bp: 0,
      tax_cents: 0,
      total_cents: 3962,
    });
    expect(computeDisplayTotals(LINES, "override", 500, 0, "", RATES)).toEqual({
      subtotal_cents: 3712,
      tax_rate_bp: 500,
      tax_cents: 186, // round(3712 × 500 / 10000) = round(185.6)
      total_cents: 3898,
    });
  });
});
