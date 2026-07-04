/**
 * Checklist scale buttons are a TRUE TOGGLE (operator directive 2026-07-03, #2 — the daily field
 * report's "Confirmed" buttons could be set but never un-set): clicking the SELECTED option clears
 * the item's response back to "" (unanswered); clicking a different option still switches to it.
 *
 * The contract locked here:
 *   • toggle round-trip — confirmed → click → unconfirmed, on BOTH the group-scale and the
 *     item-scale ("Confirmed" / "N/A today") variants;
 *   • the filed value SHAPE is unchanged — a toggled-off item carries `{ response: "" }` (a
 *     string; the established unanswered value the PDF renderer prints as a blank cell, distinct
 *     from N/A) — never a dropped key, undefined, or a new sentinel;
 *   • the visual states revert — `fr__scale-opt--on` + aria-pressed derive from the response;
 *   • multi-option scales keep radio-style switching (selecting another option replaces, only a
 *     re-click clears).
 *
 * The Daily-tab-level round-trip (draft persistence + the real daily-report-v5 definition +
 * submit payload) lives in src/components/__tests__/DailyReportTab.test.tsx; the D4
 * job-requirements confirm checkbox toggle lives in job-requirements.test.tsx (same renderer,
 * different control). A SYNTHETIC definition here keeps this matrix version-proof.
 */
import { cleanup, fireEvent, render, within } from "@testing-library/react";
import { useState } from "react";
import { afterEach, describe, expect, it } from "vitest";

import { FormRenderer, initialValues, type FormValues } from "../FormRenderer";
import type { FormDefinition } from "../types";

afterEach(cleanup);

const DEF: FormDefinition = {
  form_code: "toggle-test-v1",
  parent_form_code: "toggle-test",
  form_name: "Toggle test",
  variant_label: null,
  version: 1,
  archetype: "checklist",
  source_pdf: "n/a",
  sections: [
    {
      type: "checklist",
      key: "arrival",
      groups: [
        {
          key: "arrival_duties",
          label: "Arrival & walkthrough",
          scale: ["Confirmed"], // the daily form's one-option confirm — group-level scale
          comment_per_item: true,
          items: [{ key: "arrived_walkthrough", label: "Arrived before the crew" }],
        },
        {
          key: "trenching_duties",
          label: "Trenching & excavation",
          scale: ["Confirmed", "N/A today"],
          items: [
            // item-level scale override (the daily form's trenching/electrical shape)
            { key: "trenching_inspected", label: "Trenching protections inspected", scale: ["Confirmed", "N/A today"] },
          ],
        },
      ],
    },
  ],
};

/** Controlled harness — values state lives here so payload-shape assertions see live updates. */
function Harness({ onValues }: { onValues?: (v: FormValues) => void }) {
  const [values, setValues] = useState<FormValues>(() => initialValues(DEF));
  onValues?.(values);
  return <FormRenderer def={DEF} values={values} setValues={setValues} />;
}

function scaleBtn(container: HTMLElement, itemLabel: string, opt: string): HTMLButtonElement {
  const rg = within(container).getByRole("radiogroup", { name: itemLabel });
  return within(rg).getByRole("button", { name: opt }) as HTMLButtonElement;
}

type ChecklistState = Record<string, { response?: string; comment?: string }>;

describe("scale buttons — true toggle (directive 2026-07-03)", () => {
  it("one-option 'Confirmed': click selects, second click CLEARS back to unanswered ('')", () => {
    let latest: FormValues = {};
    const { container } = render(<Harness onValues={(v) => (latest = v)} />);
    const btn = () => scaleBtn(container, "Arrived before the crew", "Confirmed");

    expect(btn().getAttribute("aria-pressed")).toBe("false");
    fireEvent.click(btn());
    expect(btn().getAttribute("aria-pressed")).toBe("true");
    expect(btn().className).toContain("fr__scale-opt--on");
    expect((latest.arrival as ChecklistState).arrived_walkthrough).toEqual({ response: "Confirmed" });

    fireEvent.click(btn()); // the un-confirm
    expect(btn().getAttribute("aria-pressed")).toBe("false");
    expect(btn().className).not.toContain("fr__scale-opt--on");
    // The SHAPE contract: the key stays, response is the STRING "" (the unanswered value the
    // whole pipeline already understands — initialValues, drafts, and the PDF's blank cell).
    expect((latest.arrival as ChecklistState).arrived_walkthrough).toEqual({ response: "" });
  });

  it("multi-option scale: selecting another option still SWITCHES; only re-clicking the selected one clears", () => {
    let latest: FormValues = {};
    const { container } = render(<Harness onValues={(v) => (latest = v)} />);
    const confirmed = () => scaleBtn(container, "Trenching protections inspected", "Confirmed");
    const na = () => scaleBtn(container, "Trenching protections inspected", "N/A today");

    fireEvent.click(na());
    expect(na().getAttribute("aria-pressed")).toBe("true");
    fireEvent.click(confirmed()); // radio-style switch — NOT a clear
    expect(confirmed().getAttribute("aria-pressed")).toBe("true");
    expect(na().getAttribute("aria-pressed")).toBe("false");
    expect((latest.arrival as ChecklistState).trenching_inspected).toEqual({ response: "Confirmed" });

    fireEvent.click(confirmed()); // re-click the selected option → cleared
    expect(confirmed().getAttribute("aria-pressed")).toBe("false");
    expect(na().getAttribute("aria-pressed")).toBe("false");
    expect((latest.arrival as ChecklistState).trenching_inspected).toEqual({ response: "" });
  });

  it("a toggle-off preserves a typed comment (only the response clears — the setChecklist patch-merge)", () => {
    let latest: FormValues = {};
    const { container } = render(<Harness onValues={(v) => (latest = v)} />);
    const btn = () => scaleBtn(container, "Arrived before the crew", "Confirmed");
    const comment = container.querySelector("input.fr__item-comment") as HTMLInputElement;
    fireEvent.click(btn());
    fireEvent.change(comment, { target: { value: "gate was iced over" } });
    fireEvent.click(btn()); // unconfirm — the comment must ride through untouched
    expect((latest.arrival as ChecklistState).arrived_walkthrough).toEqual({
      response: "",
      comment: "gate was iced over",
    });
  });
});
