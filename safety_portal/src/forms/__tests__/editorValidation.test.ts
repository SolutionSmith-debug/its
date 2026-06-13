import { describe, it, expect } from "vitest";
import { checkParentGrouping, validateDraft } from "../editorValidation";
import { FIELD_INPUTS } from "../editorModel";
import type { Input, Field, FormDefinition } from "../types";
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

// ── Input-type acceptance (the photo-publish-block regression + drift guard) ──────
// THE BUG (PR-1 #271): "photo" was added to editorModel.FIELD_INPUTS + the Worker's
// publishValidation INPUTS, but NOT to editorValidation's hand-copied INPUTS set — so the
// client validator rejected every photo header field with "has an invalid input type." and
// blocked Publish. The fix derives INPUTS from FIELD_INPUTS (the single source of truth).

const CTX = { identity: "probe", parentFormCode: "probe" };

/** A minimal valid draft whose header carries ONE field of the given input. select gets an
 *  option so the select-needs-options rule doesn't fire and mask an input-type error. */
function draftWithHeaderInput(input: Input): FormDefinition {
  const field: Field =
    input === "select"
      ? { key: "probe_field", label: "Probe", input, options: ["A"] }
      : { key: "probe_field", label: "Probe", input };
  return {
    form_code: "probe-v1",
    parent_form_code: "probe",
    form_name: "Probe",
    variant_label: null,
    version: 1,
    archetype: "sectioned_assessment",
    source_pdf: "",
    sections: [{ type: "header", fields: [field] }],
  };
}

describe("validateDraft — header input types", () => {
  it("accepts a header field with input 'photo' (PR-1 #271 regression)", () => {
    const errors = validateDraft(draftWithHeaderInput("photo"), CTX);
    expect(errors).not.toContain('Section 1: field "probe_field" has an invalid input type.');
    expect(errors).toEqual([]);
  });

  // Parity: every member of FIELD_INPUTS (the source of truth) is accepted by the editor
  // validator. This permanently kills the three-copies (editorModel / editorValidation /
  // publishValidation) drift class — adding an input to FIELD_INPUTS now auto-flows here.
  it.each(FIELD_INPUTS)("accepts every FIELD_INPUTS member (%s)", (input) => {
    const errors = validateDraft(draftWithHeaderInput(input), CTX);
    expect(errors).not.toContain('Section 1: field "probe_field" has an invalid input type.');
  });

  it("rejects an input genuinely outside FIELD_INPUTS", () => {
    const def = draftWithHeaderInput("text");
    (def.sections[0] as { fields: { input: string }[] }).fields[0].input = "bogus";
    expect(validateDraft(def, CTX)).toContain(
      'Section 1: field "probe_field" has an invalid input type.',
    );
  });

  // Preserve the existing rule: photo is HEADER-LEVEL ONLY — a photo column inside a
  // repeating_table / signature_table is still rejected (mirrors publishValidation +
  // form_pdf, which lays photos out as header-level figures, not table cells).
  it("still rejects a photo field as a repeating_table column (header-level only)", () => {
    const def = draftWithHeaderInput("text");
    def.sections.push({
      type: "repeating_table",
      key: "crew",
      columns: [{ key: "snap", label: "Snap", input: "photo" }],
    });
    expect(validateDraft(def, CTX)).toContain(
      "Section 2: photo fields are header-level only (not table columns).",
    );
  });
});
