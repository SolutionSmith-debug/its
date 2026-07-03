/**
 * job_requirements section rendering + capture (SOP daily form, slice D4).
 *
 * The section is a PLACEHOLDER in the definition (daily-report-v4); the content is the
 * `requirements` prop the HOST fetched (the job's D1 overlay). This file asserts the D4 render
 * contract:
 *   • no prop / zero items → the section renders NOTHING (title included) — every other form and
 *     the generic fill page are unaffected;
 *   • each kind renders its control: note = guidance-paragraph text; confirm = a checkbox;
 *     text = a text input; form_link = the existing deep-link affordance (adapter-wired open +
 *     filed indicator for DAILY_STATUS_FAMILIES codes ONLY — other codes get an honest
 *     "no live indicator" note);
 *   • answers are captured under values[<section key>] as the SELF-DESCRIBING array
 *     [{label, kind, response}] covering EVERY displayed item (notes ride along empty), rebuilt
 *     from the CURRENT item set on each change;
 *   • seedRequirementResponses builds the all-empty array the host seeds on load.
 */
import { cleanup, fireEvent, render } from "@testing-library/react";
import { useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  FormRenderer,
  initialValues,
  seedRequirementResponses,
  type FormValues,
  type JobRequirementResponse,
} from "../FormRenderer";
import { getDefinition } from "../registry";
import type { DailyRequirementItem } from "../../lib/fieldops_daily_form";
import type { FormDefinition } from "../types";

afterEach(cleanup);

const DEF = getDefinition("daily-report-v4") as FormDefinition;

const ITEMS: DailyRequirementItem[] = [
  { id: 1, seq: 10, kind: "note", label: "Client requires FR clothing on site", form_code: null },
  { id: 2, seq: 20, kind: "confirm", label: "Badge in at the client gate", form_code: null },
  { id: 3, seq: 30, kind: "text", label: "Client rep spoken to today", form_code: null },
  { id: 4, seq: 40, kind: "form_link", label: "File the client JHA", form_code: "jha" },
  { id: 5, seq: 50, kind: "form_link", label: "File a toolbox talk", form_code: "toolbox-talk" },
];

/** Controlled-render harness: values state lives here so capture assertions see updates. */
function Harness({
  items,
  onValues,
  formLinks,
}: {
  items?: DailyRequirementItem[];
  onValues?: (v: FormValues) => void;
  formLinks?: { open: (p: string) => void; filedLabel?: (p: string) => string | null };
}) {
  const [values, setValues] = useState<FormValues>(() => initialValues(DEF));
  onValues?.(values);
  return <FormRenderer def={DEF} values={values} setValues={setValues} requirements={items} formLinks={formLinks} />;
}

describe("daily-report-v4 carries the placeholder section", () => {
  it("is bundled with ONE job_requirements section keyed job_requirements, before the F guidance", () => {
    expect(DEF).not.toBeNull();
    const mounts = DEF.sections.filter((s) => s.type === "job_requirements");
    expect(mounts).toHaveLength(1);
    expect(mounts[0]).toMatchObject({ key: "job_requirements", title: "Job-specific requirements" });
    const idx = DEF.sections.findIndex((s) => s.type === "job_requirements");
    const last = DEF.sections[DEF.sections.length - 1];
    expect(idx).toBe(DEF.sections.length - 2); // near the end…
    expect(last.type === "guidance" && last.heading.startsWith("F. General Expectations")).toBe(true);
  });

  it("contributes NO initialValues key (the HOST seeds it when the items load)", () => {
    expect("job_requirements" in initialValues(DEF)).toBe(false);
  });
});

describe("empty states — the section renders NOTHING", () => {
  it("without the requirements prop (the generic fill page)", () => {
    const { container } = render(
      <FormRenderer def={DEF} values={initialValues(DEF)} setValues={() => {}} />,
    );
    expect(container.textContent ?? "").not.toContain("Job-specific requirements");
    expect(container.querySelector(".fr__job-reqs")).toBeNull();
  });

  it("with zero items", () => {
    const { container } = render(
      <FormRenderer def={DEF} values={initialValues(DEF)} setValues={() => {}} requirements={[]} />,
    );
    expect(container.textContent ?? "").not.toContain("Job-specific requirements");
  });
});

describe("each kind renders its control", () => {
  it("renders the section title + note as guidance-paragraph text (no input)", () => {
    const { container } = render(<Harness items={ITEMS} />);
    expect(container.textContent ?? "").toContain("Job-specific requirements");
    const note = container.querySelector(".fr__job-reqs .fr__guidance-p");
    expect(note?.textContent).toBe("Client requires FR clothing on site");
  });

  it("confirm renders a checkbox; text renders a text input", () => {
    const { getByLabelText } = render(<Harness items={ITEMS} />);
    const confirm = getByLabelText("Badge in at the client gate") as HTMLInputElement;
    expect(confirm.type).toBe("checkbox");
    const text = getByLabelText("Client rep spoken to today") as HTMLInputElement;
    expect(text.type).toBe("text");
  });

  it("form_link renders the deep-link button wired to the adapter, with the filed indicator for a STATUS-FAMILY code", () => {
    const open = vi.fn();
    const filedLabel = vi.fn((code: string) => (code === "jha" ? "Filed ✓ 2:14 PM" : null));
    const { getByText, container } = render(<Harness items={ITEMS} formLinks={{ open, filedLabel }} />);
    fireEvent.click(getByText("File the client JHA →"));
    expect(open).toHaveBeenCalledWith("jha");
    expect(container.textContent ?? "").toContain("Filed ✓ 2:14 PM");
  });

  it("a form_link OUTSIDE the status families renders the link with an honest no-indicator note (and never queries it)", () => {
    const filedLabel = vi.fn(() => "SHOULD NOT SHOW");
    const { getByText, container } = render(<Harness items={ITEMS} formLinks={{ open: vi.fn(), filedLabel }} />);
    expect(getByText("File a toolbox talk →")).not.toBeNull();
    expect(filedLabel).not.toHaveBeenCalledWith("toolbox-talk");
    expect(container.textContent ?? "").toContain("No live filed indicator for this form type");
  });

  it("with NO adapter the form_link button is disabled (the inert Submit-a-Form posture)", () => {
    const { getByText } = render(<Harness items={ITEMS} />);
    expect((getByText("File the client JHA →") as HTMLButtonElement).disabled).toBe(true);
  });
});

describe("capture — values[job_requirements] is the self-describing array", () => {
  it("checking the confirm writes the FULL array (notes ride along empty; the checked item = 'Confirmed')", () => {
    let latest: FormValues = {};
    const { getByLabelText } = render(<Harness items={ITEMS} onValues={(v) => (latest = v)} />);
    fireEvent.click(getByLabelText("Badge in at the client gate"));
    const arr = latest.job_requirements as JobRequirementResponse[];
    expect(arr).toHaveLength(ITEMS.length);
    expect(arr[0]).toEqual({ label: "Client requires FR clothing on site", kind: "note", response: "" });
    expect(arr[1]).toEqual({ label: "Badge in at the client gate", kind: "confirm", response: "Confirmed" });
    // Unchecking clears it back to "".
    fireEvent.click(getByLabelText("Badge in at the client gate"));
    expect((latest.job_requirements as JobRequirementResponse[])[1].response).toBe("");
  });

  it("typing into the text item captures the answer and PRESERVES other answers", () => {
    let latest: FormValues = {};
    const { getByLabelText } = render(<Harness items={ITEMS} onValues={(v) => (latest = v)} />);
    fireEvent.click(getByLabelText("Badge in at the client gate"));
    fireEvent.change(getByLabelText("Client rep spoken to today"), { target: { value: "Ana R." } });
    const arr = latest.job_requirements as JobRequirementResponse[];
    expect(arr[1].response).toBe("Confirmed"); // preserved across the second edit
    expect(arr[2]).toEqual({ label: "Client rep spoken to today", kind: "text", response: "Ana R." });
    // form_link entries ride along with an empty response (the linked form files separately).
    expect(arr[3]).toEqual({ label: "File the client JHA", kind: "form_link", response: "" });
  });

  it("seedRequirementResponses builds the all-empty array the host seeds on load", () => {
    expect(seedRequirementResponses(ITEMS)).toEqual(
      ITEMS.map((it) => ({ label: it.label, kind: it.kind, response: "" })),
    );
  });
});
