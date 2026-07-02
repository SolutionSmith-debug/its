/**
 * R4 — the ONE shared checklist-item form (replacing the two page-local ItemForm duplicates) + its
 * row helpers. Covers: human type labels with raw keys as option values; the per-type helper line;
 * the catalog-driven form_code select (names shown, codes submitted, orphan codes preserved);
 * target_count only for count; Cancel in edit mode; and the seq helpers (nextSeq / planRenumber /
 * itemInputFromRow / itemMetaLabel) that implement the "ordered by seq ASC, new items at max+10,
 * reorder = re-write seq via the existing edit route" convention.
 */
import { cleanup, fireEvent, render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useState } from "react";
import type * as checklist from "../../lib/fieldops_checklist";
import {
  ChecklistItemForm,
  EMPTY_ITEM,
  itemInputFromRow,
  itemMetaLabel,
  nextSeq,
  planRenumber,
} from "../ChecklistItemForm";

afterEach(cleanup);

// Controlled-state harness so fireEvent.change flows back into the draft like in the real pages.
function Harness({ initial, onSubmit, onCancel }: {
  initial: checklist.ItemInput;
  onSubmit?: (draft: checklist.ItemInput) => void;
  onCancel?: () => void;
}) {
  const [draft, setDraft] = useState<checklist.ItemInput>(initial);
  return (
    <ChecklistItemForm
      label="Test item"
      draft={draft}
      onChange={setDraft}
      onSubmit={(e) => {
        e.preventDefault();
        onSubmit?.(draft);
      }}
      busy={false}
      submitLabel="Save"
      onCancel={onCancel}
    />
  );
}

describe("ChecklistItemForm — type select + per-type helper", () => {
  it("shows human type labels while submitting the raw wire keys", () => {
    const { getByLabelText } = render(<Harness initial={EMPTY_ITEM} />);
    const sel = getByLabelText("Test item type") as HTMLSelectElement;
    const opts = Array.from(sel.options).map((o) => ({ v: o.value, t: o.textContent }));
    expect(opts).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ v: "manual_attest", t: "Check" }),
        expect.objectContaining({ v: "count", t: "Count" }),
        expect.objectContaining({ v: "form_linked", t: "Form" }),
        expect.objectContaining({ v: "inspection", t: "Inspection" }),
      ]),
    );
  });

  it("renders a one-line helper that follows the selected type", () => {
    const { container, getByLabelText } = render(<Harness initial={EMPTY_ITEM} />);
    expect(container.textContent ?? "").toContain("simple attest");
    fireEvent.change(getByLabelText("Test item type"), { target: { value: "form_linked" } });
    expect(container.textContent ?? "").toContain("checks itself off automatically when the named form is filed");
    fireEvent.change(getByLabelText("Test item type"), { target: { value: "count" } });
    expect(container.textContent ?? "").toContain("records a number");
    fireEvent.change(getByLabelText("Test item type"), { target: { value: "inspection" } });
    expect(container.textContent ?? "").toContain("that inspection form is filed");
  });
});

describe("ChecklistItemForm — form_code catalog select", () => {
  it("is hidden for non-form types and offers catalog parents (name shown, code submitted) for form types", () => {
    const onSubmit = vi.fn();
    const { getByLabelText, queryByLabelText } = render(<Harness initial={EMPTY_ITEM} onSubmit={onSubmit} />);
    expect(queryByLabelText("Test item form code")).toBeNull(); // manual_attest → no form field
    fireEvent.change(getByLabelText("Test item type"), { target: { value: "form_linked" } });
    const codeSel = getByLabelText("Test item form code") as HTMLSelectElement;
    const opts = Array.from(codeSel.options).map((o) => ({ v: o.value, t: o.textContent }));
    expect(opts).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ v: "jha", t: "Job Hazard Analysis" }),
        expect.objectContaining({ v: "daily-report", t: "Daily Field Report" }),
      ]),
    );
    fireEvent.change(getByLabelText("Test item label"), { target: { value: "Attach the JHA" } });
    fireEvent.change(codeSel, { target: { value: "jha" } });
    fireEvent.submit(codeSel.closest("form")!);
    expect(onSubmit).toHaveBeenCalledWith(expect.objectContaining({ form_code: "jha" }));
  });

  it("keeps a stored code that fell out of the catalog selectable + marked (edit prefill never silently swaps)", () => {
    const { getByLabelText } = render(
      <Harness initial={{ item_type: "form_linked", label: "Old item", seq: 10, form_code: "retired-form" }} />,
    );
    const codeSel = getByLabelText("Test item form code") as HTMLSelectElement;
    expect(codeSel.value).toBe("retired-form");
    const orphan = Array.from(codeSel.options).find((o) => o.value === "retired-form");
    expect(orphan?.textContent).toContain("not in catalog");
  });
});

describe("ChecklistItemForm — count target + cancel", () => {
  it("shows target_count only for count and submits it as a number", () => {
    const onSubmit = vi.fn();
    const { getByLabelText, queryByLabelText } = render(<Harness initial={EMPTY_ITEM} onSubmit={onSubmit} />);
    expect(queryByLabelText("Test item target count")).toBeNull();
    fireEvent.change(getByLabelText("Test item type"), { target: { value: "count" } });
    fireEvent.change(getByLabelText("Test item label"), { target: { value: "Extinguishers on site" } });
    fireEvent.change(getByLabelText("Test item target count"), { target: { value: "4" } });
    fireEvent.submit(getByLabelText("Test item target count").closest("form")!);
    expect(onSubmit).toHaveBeenCalledWith(expect.objectContaining({ item_type: "count", target_count: 4 }));
  });

  it("renders a Cancel button only when onCancel is given, and it fires without submitting", () => {
    const onSubmit = vi.fn();
    const onCancel = vi.fn();
    const { getByLabelText } = render(<Harness initial={EMPTY_ITEM} onSubmit={onSubmit} onCancel={onCancel} />);
    fireEvent.click(getByLabelText("Test item cancel"));
    expect(onCancel).toHaveBeenCalledTimes(1);
    expect(onSubmit).not.toHaveBeenCalled();
  });
});

describe("seq helpers", () => {
  it("nextSeq = max+10 (10 on an empty list)", () => {
    expect(nextSeq([])).toBe(10);
    expect(nextSeq([{ seq: 10 }, { seq: 25 }])).toBe(35);
  });

  it("planRenumber on a clean 10/20/30 list yields exactly the swapped pair", () => {
    const rows = [{ id: 1, seq: 10 }, { id: 2, seq: 20 }, { id: 3, seq: 30 }];
    const plan = planRenumber(rows, 0, 1); // move first down
    expect(plan).toEqual([
      { row: rows[1], seq: 10 },
      { row: rows[0], seq: 20 },
    ]);
  });

  it("planRenumber heals a messy (tied/zeroed) list to canonical spacing in the same pass", () => {
    const rows = [{ id: 1, seq: 0 }, { id: 2, seq: 0 }, { id: 3, seq: 40 }];
    const plan = planRenumber(rows, 2, -1); // move last up
    // New order: 1, 3, 2 → canonical 10/20/30; every row's seq changes.
    expect(plan).toEqual([
      { row: rows[0], seq: 10 },
      { row: rows[2], seq: 20 },
      { row: rows[1], seq: 30 },
    ]);
  });

  it("planRenumber is empty when the move falls off either end", () => {
    const rows = [{ seq: 10 }, { seq: 20 }];
    expect(planRenumber(rows, 0, -1)).toEqual([]);
    expect(planRenumber(rows, 1, 1)).toEqual([]);
  });

  it("itemInputFromRow rebuilds the FULL write payload (the edit route replaces every field)", () => {
    expect(
      itemInputFromRow({ seq: 20, item_type: "count", label: "Anchors", form_code: null, target_count: 4 }),
    ).toEqual({ item_type: "count", label: "Anchors", seq: 20, target_count: 4 });
    expect(
      itemInputFromRow({ seq: 10, item_type: "form_linked", label: "JHA", form_code: "jha", target_count: null }),
    ).toEqual({ item_type: "form_linked", label: "JHA", seq: 10, form_code: "jha" });
  });

  it("itemMetaLabel renders human copy: catalog names for form items, target for count", () => {
    expect(itemMetaLabel({ item_type: "form_linked", form_code: "jha", target_count: null })).toBe(
      "Form · Job Hazard Analysis",
    );
    expect(itemMetaLabel({ item_type: "inspection", form_code: "no-such-code", target_count: null })).toBe(
      "Inspection · no-such-code",
    );
    expect(itemMetaLabel({ item_type: "count", form_code: null, target_count: 4 })).toBe("Count · target 4");
    expect(itemMetaLabel({ item_type: "manual_attest", form_code: null, target_count: null })).toBe("Check");
  });
});
