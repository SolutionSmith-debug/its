import { describe, it, expect } from "vitest";
import { roleLabel, type Role } from "../api";

// Slice T — the 'submitter' tier is DISPLAY-renamed "Subcontractor". The KEY stays 'submitter'
// (option values, the API, the security-load-bearing fail-safe default in worker/auth.ts) — ONLY
// the human label changes. roleLabel is the single place that maps key → label.
describe("roleLabel (Slice T subcontractor display rename)", () => {
  it("renders 'submitter' as 'Subcontractor' (label only — the KEY is unchanged)", () => {
    const key: Role = "submitter";
    expect(roleLabel(key)).toBe("Subcontractor");
    // The value the app carries around is still the literal 'submitter'.
    expect(key).toBe("submitter");
  });

  it("leaves manager + admin labels intact", () => {
    expect(roleLabel("manager")).toBe("Manager");
    expect(roleLabel("admin")).toBe("Admin");
  });
});
