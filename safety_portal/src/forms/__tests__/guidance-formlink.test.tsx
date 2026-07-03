/**
 * guidance + form_link section rendering (SOP daily form, slice D1).
 *
 * The render-smoke net (render-smoke.test.tsx) already holds daily-report-v2 to the
 * structural count/needle contract; THIS file asserts the D1-specific behaviors that
 * counts/needles can't express:
 *   • guidance renders readable rich text — heading, paragraphs, bullet lists, and
 *     VISUALLY-DISTINCT callouts (the fr__callout--critical/quality/note classes);
 *   • form_link renders a btn--primary "Create <form> →" button that is DISABLED with
 *     the "available from the Daily tab" helper when NO FormLinkAdapter is supplied
 *     (the generic Submit-a-Form fill page), and ENABLED + wired to the adapter's
 *     open()/filedLabel() when one is (the Daily tab, slice D2);
 *   • neither type contributes a fill-state key: initialValues(daily-report-v2)
 *     carries ONLY the definition's field/table/checklist/freeform keys, so the
 *     existing values → /api/submit path is unchanged by their presence.
 */
import { cleanup, fireEvent, render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { FormRenderer, initialValues } from "../FormRenderer";
import { getDefinition } from "../registry";
import type { FormDefinition, Section } from "../types";

afterEach(cleanup);

const DEF = getDefinition("daily-report-v2") as FormDefinition;

function renderV2(formLinks?: { open: (p: string) => void; filedLabel?: (p: string) => string | null }) {
  return render(
    <FormRenderer def={DEF} values={initialValues(DEF)} setValues={() => {}} formLinks={formLinks} />,
  );
}

describe("daily-report-v2 definition resolves", () => {
  it("is bundled and carries guidance + form_link sections", () => {
    expect(DEF).not.toBeNull();
    expect(DEF.sections.some((s) => s.type === "guidance")).toBe(true);
    expect(DEF.sections.some((s) => s.type === "form_link")).toBe(true);
  });
});

describe("guidance sections render readable rich text", () => {
  it("renders every guidance heading as a section title", () => {
    const { container } = renderV2();
    const titles = [...container.querySelectorAll(".fr__guidance .fr__section-title")].map(
      (el) => el.textContent,
    );
    const headings = DEF.sections.filter((s) => s.type === "guidance").map((s) => s.heading);
    expect(headings.length).toBeGreaterThan(0);
    for (const h of headings) expect(titles).toContain(h);
  });

  it("renders paragraphs and bullet lists verbatim", () => {
    const { container } = renderV2();
    const text = container.textContent ?? "";
    // A known SOP paragraph and a known bullet (verbatim from the docx).
    expect(text).toContain(
      "Show up before the crew. Your presence before workers arrive communicates accountability and sets the standard for the entire day.",
    );
    const firstBullets = DEF.sections
      .flatMap((s) => (s.type === "guidance" ? s.blocks : []))
      .find((b) => b.type === "bullets");
    expect(firstBullets).toBeDefined();
    const li = [...container.querySelectorAll(".fr__guidance-bullets li")].map((el) => el.textContent);
    for (const item of (firstBullets as Extract<typeof firstBullets, { type: "bullets" }>)!.items) {
      expect(li).toContain(item);
    }
  });

  it("renders visually-distinct callout styles (critical / quality / note)", () => {
    const { container } = renderV2();
    for (const style of ["critical", "quality", "note"] as const) {
      const nodes = container.querySelectorAll(`.fr__callout--${style}`);
      expect(nodes.length, `expected a fr__callout--${style} callout`).toBeGreaterThan(0);
    }
    // The life-safety rule renders inside the critical callout, verbatim.
    const critical = container.querySelector(".fr__callout--critical");
    expect(critical?.textContent).toContain("CRITICAL RULE: Never allow workers in an unprotected trench.");
    // The FINAL STATEMENT renders as a callout too.
    expect(container.textContent).toContain("Hold the line.");
  });
});

describe("form_link sections — button + optional adapter", () => {
  it("with NO adapter: disabled btn--primary + 'available from the Daily tab' helper", () => {
    const { container } = renderV2();
    const sections = [...container.querySelectorAll(".fr__form-link")];
    const links = DEF.sections.filter((s) => s.type === "form_link");
    expect(sections.length).toBe(links.length);
    for (const sec of sections) {
      const btn = sec.querySelector("button.btn--primary") as HTMLButtonElement;
      expect(btn).not.toBeNull();
      expect(btn.disabled).toBe(true);
      expect(sec.textContent).toContain("available from the Daily tab");
    }
    // Button text is the definition label + the arrow chrome.
    const labels = sections.map((s) => s.querySelector("button")?.textContent?.trim());
    for (const l of links) expect(labels).toContain(`${(l as { label: string }).label} →`);
  });

  it("with an adapter: enabled, open() fires with the parent code, filed label shows", () => {
    const open = vi.fn();
    const filedLabel = vi.fn((p: string) => (p === "jha" ? "Filed ✓ 2:14 PM" : null));
    const { container } = renderV2({ open, filedLabel });
    const buttons = [...container.querySelectorAll(".fr__form-link button.btn--primary")] as HTMLButtonElement[];
    expect(buttons.length).toBeGreaterThan(0);
    for (const b of buttons) expect(b.disabled).toBe(false);

    const jhaBtn = buttons.find((b) => b.textContent?.includes("Create Job Hazard Analysis"));
    expect(jhaBtn).toBeDefined();
    fireEvent.click(jhaBtn!);
    expect(open).toHaveBeenCalledWith("jha");

    // The filed indicator renders for the parent the adapter reports as filed…
    expect(container.querySelector(".fr__form-link-filed")?.textContent).toBe("Filed ✓ 2:14 PM");
    // …and the definition helper (not the disabled-state hint) is shown.
    expect(container.textContent).not.toContain("available from the Daily tab");
    expect(container.textContent).toContain("File today's JHA for this job before work begins.");
  });
});

describe("daily-report-v1 still renders (append-only regression)", () => {
  // The render-smoke net only exercises ACTIVE forms (now v2); the superseded v1
  // definition must stay bundled AND renderable — filed/in-flight v1 submissions
  // resolve it forever (append-only, design C1/C9).
  it("renders the full v1 structure with no D1 chrome", () => {
    const v1 = getDefinition("daily-report-v1") as FormDefinition;
    expect(v1).not.toBeNull();
    const { container } = render(
      <FormRenderer def={v1} values={initialValues(v1)} setValues={() => {}} />,
    );
    // v1 = header + 4 repeating tables + 2 freeforms = 7 section containers.
    expect(container.querySelectorAll(".fr__section").length).toBe(7);
    expect(container.textContent).toContain("Crew / Subcontractor Progress");
    expect(container.textContent).toContain("Tomorrow's Progress Goals");
    // No guidance/form_link chrome may appear on a v1 render.
    expect(container.querySelector(".fr__guidance")).toBeNull();
    expect(container.querySelector(".fr__form-link")).toBeNull();
  });
});

describe("guidance/form_link contribute NO fill-state keys (submit path unchanged)", () => {
  it("initialValues carries exactly the field/table/checklist/freeform keys", () => {
    const expected = new Set<string>();
    for (const s of DEF.sections as Section[]) {
      if (s.type === "header") for (const f of s.fields) expected.add(f.key);
      else if (s.type === "repeating_table" || s.type === "signature_table") expected.add(s.key);
      else if (s.type === "checklist" || s.type === "freeform") expected.add(s.key);
      // guidance / form_link / static_text: keyless by design
    }
    expect(new Set(Object.keys(initialValues(DEF)))).toEqual(expected);
  });
});
