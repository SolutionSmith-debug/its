import { describe, it, expect } from "vitest";
import { checkParentGrouping } from "../editorValidation";
import type { CatalogParent } from "../registry";

// The client mirror of apply_publish / the Worker's validateParentGrouping — the guard
// that surfaces the "JHA test under jha" mistake inline before Publish.

const noVariant: CatalogParent = {
  parent_form_code: "jha", name: "Job Hazard Analysis", form_code: "jha-v1", variants: [],
};
const variantParent: CatalogParent = {
  parent_form_code: "toolbox-talk", name: "Toolbox Talk", form_code: null,
  variants: [{ variant_label: "PPE", form_code: "toolbox-talk-ppe-v1" }],
};
const catalog = [noVariant, variantParent];

describe("checkParentGrouping", () => {
  it("blocks adding ANY form to a standalone (no-variant) parent", () => {
    expect(checkParentGrouping(catalog, "jha", "Extra")).toMatch(/standalone form/i);
    expect(checkParentGrouping(catalog, "jha", null)).toMatch(/standalone form/i);
  });
  it("requires a variant label when adding to a variant parent", () => {
    expect(checkParentGrouping(catalog, "toolbox-talk", null)).toMatch(/variant label/i);
  });
  it("blocks a duplicate variant label", () => {
    expect(checkParentGrouping(catalog, "toolbox-talk", "PPE")).toMatch(/already has/i);
  });
  it("allows a new variant under a variant parent", () => {
    expect(checkParentGrouping(catalog, "toolbox-talk", "Ladders")).toBeNull();
  });
  it("allows a brand-new form type (no existing parent)", () => {
    expect(checkParentGrouping(catalog, "incident", null)).toBeNull();
  });
});
