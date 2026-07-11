// Cross-language HMAC canonical agreement (SC-S3c). The Worker (TS canonicalSubJson) signs a generated
// subcontract; the Mac daemon (Python shared/portal_hmac.sub_canonical_json) RECOMPUTES it byte-for-byte
// to verify before rendering/filing. If the two serializations ever diverge — key order, a numeric
// format, non-ASCII escaping, null handling — every signature fails closed and no subcontract can ever
// file. This test pins the EXACT bytes both sides must produce for one fully-populated fixture (all 31
// header keys incl. a null, a float qty, and non-ASCII text), and the exact HMAC over the sub:v1 string
// prefix. The identical fixture + expected values are asserted on the Python side in
// tests/test_portal_hmac_sub.py's companion vector — edit BOTH or neither. Generated + verified against
// shared/portal_hmac.sub_canonical_json + sign_sub("test-secret-xyz", sc_id=42).
import { describe, it, expect } from "vitest";
import { canonicalSubJson, subCanonicalString } from "../worker/subcontract";
import { hmacHex } from "../worker/hmac";

const SUB = {
  sc_number: "2026.001.A.0.1", job_no: "2026.001", site_phase: 0, supersede_seq: 0, revision: 1,
  sub_key: "SUB-000042", trade: "AC Electrical", job_id: "JOB-1", job_name: "Kendall Solar",
  project_name: "Kendall Solar Project", owner_entity: "Kendall Solar, LLC",
  prime_contractor: "Evergreen Renewables of Virginia LLC", site_name: "Kendall Site",
  site_address: "123 Solar Rd, Süd", governing_law_state: "OR", exhibit_a_template_id: "electrical",
  exhibit_a_template_version: "v1", exhibit_a_work_text: "The Work: AC électrical", scope_summary: "AC scope",
  price_basis: "fixed", contract_price_cents: 27401850, retainage_bp: 1000, subtotal_cents: 27401850,
  start_date: "2026-08-01", completion_date: "2026-12-31", terms_profile_id: "standard_subcontract",
  terms_version: "v1", template_family: "long_form", supersedes_sc_id: null, approver_name: "Jane Doe",
  approver_title: "PM",
} as unknown as Parameters<typeof canonicalSubJson>[0];

const SOV = [
  { position: 1, item_number: "1", description: "AC électrical", qty: 1, unit: "LS", unit_price_cents: 27401850, extended_cents: 27401850 },
  { position: 2, item_number: "2", description: "extra", qty: 2.5, unit: "EA", unit_price_cents: 400, extended_cents: 1000 },
] as unknown as Parameters<typeof canonicalSubJson>[1];

// Byte-exact output of shared/portal_hmac.sub_canonical_json(SUB, SOV) — the Python half of the contract.
const EXPECTED_CANONICAL =
  '{"sc_number":"2026.001.A.0.1","job_no":"2026.001","site_phase":0,"supersede_seq":0,"revision":1,' +
  '"sub_key":"SUB-000042","trade":"AC Electrical","job_id":"JOB-1","job_name":"Kendall Solar",' +
  '"project_name":"Kendall Solar Project","owner_entity":"Kendall Solar, LLC",' +
  '"prime_contractor":"Evergreen Renewables of Virginia LLC","site_name":"Kendall Site",' +
  '"site_address":"123 Solar Rd, Süd","governing_law_state":"OR","exhibit_a_template_id":"electrical",' +
  '"exhibit_a_template_version":"v1","exhibit_a_work_text":"The Work: AC électrical","scope_summary":"AC scope",' +
  '"price_basis":"fixed","contract_price_cents":27401850,"retainage_bp":1000,"subtotal_cents":27401850,' +
  '"start_date":"2026-08-01","completion_date":"2026-12-31","terms_profile_id":"standard_subcontract",' +
  '"terms_version":"v1","template_family":"long_form","supersedes_sc_id":null,"approver_name":"Jane Doe",' +
  '"approver_title":"PM","sov_lines":[{"position":1,"item_number":"1","description":"AC électrical","qty":1,' +
  '"unit":"LS","unit_price_cents":27401850,"extended_cents":27401850},{"position":2,"item_number":"2",' +
  '"description":"extra","qty":2.5,"unit":"EA","unit_price_cents":400,"extended_cents":1000}]}';

// HMAC-SHA256("test-secret-xyz", "sub:v1\n42\n2026.001.A.0.1\n" + EXPECTED_CANONICAL) from sign_sub.
const EXPECTED_HMAC = "3587af83478a542ef770da4c1661bf10dd2a96c3112550b217fa21e4e20bd87d";

describe("sub:v1 cross-language HMAC canonical agreement", () => {
  it("TS canonicalSubJson byte-matches the Python sub_canonical_json vector", () => {
    expect(canonicalSubJson(SUB, SOV)).toBe(EXPECTED_CANONICAL);
  });

  it("TS hmacHex over subCanonicalString matches the Python sign_sub vector", async () => {
    const msg = subCanonicalString(42, "2026.001.A.0.1", canonicalSubJson(SUB, SOV));
    expect(await hmacHex("test-secret-xyz", msg)).toBe(EXPECTED_HMAC);
  });
});
