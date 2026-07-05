import { useState } from "react";
import type { FormEvent } from "react";
import type * as checklist from "../lib/fieldops_checklist";
import { itemTypeLabel } from "../lib/labels";
import { formCatalog } from "../forms/registry";

// R4 — the ONE shared add/edit checklist-item form, replacing the two near-verbatim ItemForm
// duplicates that lived in FieldOpsJobTracker (daily editor) and FieldOpsInspections (library
// editor). Consumed by BOTH pages plus every inline per-row edit. Design points:
//   • item_type renders HUMAN labels (labels.ts) with a one-line helper per type — the raw enum
//     keys stay the option VALUES (wire contract unchanged).
//   • form_code is a SELECT over the real catalog parents (names shown, codes submitted), only for
//     the form-bearing types — free-text form codes were the #1 authoring foot-gun (R1 422s them
//     server-side; this removes the typo path client-side). An edited item whose stored code fell
//     out of the catalog still shows it (marked), so prefill never silently swaps a value.
//   • seq is carried IN the draft but not rendered: the pages seed it (nextSeq = max+10 on add,
//     the row's own seq on edit — the Worker's parseItem defaults an ABSENT seq to 0, so an edit
//     that dropped seq would silently reorder the item to the top).
// Row-level helpers (nextSeq / planRenumber / itemInputFromRow / itemMetaLabel) live here too so
// both pages share one implementation of the seq convention (items ordered by seq ASC).

export const ITEM_TYPES: checklist.ChecklistItemType[] = ["manual_attest", "count", "form_linked", "inspection"];

export const EMPTY_ITEM: checklist.ItemInput = { item_type: "manual_attest", label: "" };

// One-line "what does this type do" helper, shown under the type select.
const TYPE_HELP: Record<checklist.ChecklistItemType, string> = {
  manual_attest: "Check — a simple attest; the person taps “Mark done”.",
  count: "Count — the person records a number, checked against the target.",
  form_linked: "Form — checks itself off automatically when the named form is filed.",
  inspection: "Inspection — checks itself off automatically when that inspection form is filed.",
};

/** The two item types that carry a form_code. */
export function isFormBearing(itemType: string): boolean {
  return itemType === "form_linked" || itemType === "inspection";
}

/** Catalog parent display name for a stored form_code, or null when the code isn't in the catalog. */
export function formNameForCode(code: string | null): string | null {
  if (!code) return null;
  return formCatalog().find((p) => p.parent_form_code === code)?.name ?? null;
}

// Short HUMAN summary of an item's type-specific payload for list rows (was the duplicated
// itemMeta in both pages, which leaked raw item_type keys + raw form codes).
export function itemMetaLabel(item: { item_type: string; form_code: string | null; target_count: number | null }): string {
  if (isFormBearing(item.item_type)) {
    return `${itemTypeLabel(item.item_type)} · ${formNameForCode(item.form_code) ?? item.form_code ?? "—"}`;
  }
  if (item.item_type === "count") return `${itemTypeLabel(item.item_type)} · target ${item.target_count ?? "?"}`;
  return itemTypeLabel(item.item_type);
}

/** Auto-suggested seq for a NEW item: max existing + 10 (10 for an empty list). */
export function nextSeq(rows: { seq: number }[]): number {
  return rows.reduce((m, r) => Math.max(m, r.seq), 0) + 10;
}

/** Parse the `requires_photo` flag out of an item's stored config_json (tolerant of null/garbage —
 * a clobbered or legacy config_json reads as "not required" and never throws). */
export function parseRequiresPhoto(configJson: string | null | undefined): boolean {
  if (!configJson) return false;
  try {
    return (JSON.parse(configJson) as { requires_photo?: unknown }).requires_photo === true;
  } catch {
    return false;
  }
}

/** Rebuild the full ItemInput write payload from a stored row (edit prefill / reorder re-writes).
 * The edit routes REPLACE every field, so a partial payload would clobber — always send it all. */
export function itemInputFromRow(row: {
  seq: number;
  item_type: string;
  label: string | null;
  form_code: string | null;
  target_count: number | null;
  config_json?: string | null;
}): checklist.ItemInput {
  const input: checklist.ItemInput = {
    item_type: row.item_type as checklist.ChecklistItemType,
    label: row.label ?? "",
    seq: row.seq,
  };
  if (row.form_code !== null) input.form_code = row.form_code;
  if (row.target_count !== null) input.target_count = row.target_count;
  if (parseRequiresPhoto(row.config_json)) input.requires_photo = true;
  return input;
}

/** Plan an up/down reorder as seq re-writes via the existing edit route. Swaps positions
 * `index` ↔ `index+dir`, renumbers the list to the canonical 10/20/30… spacing, and returns ONLY
 * the rows whose seq actually changes (a clean 10/20/30 list yields exactly the swapped pair;
 * a legacy list with ties/zeros gets healed in the same pass). Empty when the move falls off
 * either end. */
export function planRenumber<T extends { seq: number }>(
  rows: T[],
  index: number,
  dir: -1 | 1,
): { row: T; seq: number }[] {
  const j = index + dir;
  if (index < 0 || index >= rows.length || j < 0 || j >= rows.length) return [];
  const order = [...rows];
  [order[index], order[j]] = [order[j], order[index]];
  const changes: { row: T; seq: number }[] = [];
  order.forEach((row, i) => {
    const want = (i + 1) * 10;
    if (row.seq !== want) changes.push({ row, seq: want });
  });
  return changes;
}

/**
 * The shared checklist-item form. `label` prefixes every field's aria-label (keep it UNIQUE per
 * mounted instance — e.g. "Add default item" vs "Edit default item" — so tests and screen readers
 * can tell concurrent forms apart). `onCancel`, when given, renders a Cancel button (edit mode).
 */
export function ChecklistItemForm({
  label,
  draft,
  onChange,
  onSubmit,
  busy,
  submitLabel,
  onCancel,
}: {
  label: string;
  draft: checklist.ItemInput;
  onChange: (next: checklist.ItemInput) => void;
  onSubmit: (e: FormEvent) => void;
  busy: boolean;
  submitLabel: string;
  onCancel?: () => void;
}) {
  const set = (patch: Partial<checklist.ItemInput>) => onChange({ ...draft, ...patch });
  // Tab-launched parents (launch:"daily-tab" — the SOP daily form) are excluded from the picker: an
  // inspection item deep-linking there would land on a blank one-way fill page (D2 regression review).
  // A stored value pointing at one still round-trips via the orphan path below (marked, never swapped).
  const catalog = formCatalog().filter((p) => p.launch !== "daily-tab");
  // Prefilled code that fell out of the catalog (retired / legacy): keep it selectable + marked,
  // never silently swap the stored value on open.
  const orphanCode =
    draft.form_code && !catalog.some((p) => p.parent_form_code === draft.form_code) ? draft.form_code : null;
  return (
    <form onSubmit={onSubmit} className="dash-row" aria-label={label}>
      <input
        aria-label={`${label} label`}
        value={draft.label}
        onChange={(e) => set({ label: e.target.value })}
        placeholder="Item label"
        maxLength={256}
      />{" "}
      <select
        aria-label={`${label} type`}
        value={draft.item_type}
        onChange={(e) => set({ item_type: e.target.value as checklist.ChecklistItemType })}
      >
        {ITEM_TYPES.map((t) => (
          <option key={t} value={t}>{itemTypeLabel(t)}</option>
        ))}
      </select>{" "}
      {isFormBearing(draft.item_type) && (
        <select
          aria-label={`${label} form code`}
          value={draft.form_code ?? ""}
          onChange={(e) => set({ form_code: e.target.value || undefined })}
        >
          <option value="">— pick a form —</option>
          {orphanCode !== null && (
            <option value={orphanCode}>{orphanCode} (not in catalog)</option>
          )}
          {catalog.map((p) => (
            <option key={p.parent_form_code} value={p.parent_form_code}>{p.name}</option>
          ))}
        </select>
      )}
      {draft.item_type === "count" && (
        <input
          aria-label={`${label} target count`}
          value={draft.target_count ?? ""}
          onChange={(e) => set({ target_count: e.target.value === "" ? undefined : Number(e.target.value) })}
          placeholder="Target N"
          inputMode="numeric"
          size={5}
        />
      )}{" "}
      {!isFormBearing(draft.item_type) && (
        <label className="checklist-req-photo">
          <input
            type="checkbox"
            aria-label={`${label} requires photo`}
            checked={draft.requires_photo ?? false}
            onChange={(e) => set({ requires_photo: e.target.checked || undefined })}
          />{" "}
          Requires photo
        </label>
      )}{" "}
      <button type="submit" disabled={busy} className="btn btn--primary">{submitLabel}</button>
      {onCancel && (
        <>
          {" "}
          <button type="button" className="btn btn--secondary" aria-label={`${label} cancel`} onClick={onCancel}>
            Cancel
          </button>
        </>
      )}
      <span className="dash-card__sub muted" style={{ display: "block", width: "100%" }}>
        {TYPE_HELP[draft.item_type]}
      </span>
    </form>
  );
}

// Two-step destructive confirm: first tap swaps the button for blast-radius copy + Confirm/Cancel.
// The cancel path leaves everything untouched — no lib call fires until the explicit Confirm.
// Shared by the Checklists admin page AND the Job Tracker per-job editor (R4 review WARN: every
// destructive checklist action must be confirm-gated with its scope named).
export function ConfirmDelete({
  actionLabel,
  ariaLabel,
  copy,
  busy,
  onConfirm,
}: {
  actionLabel: string;
  ariaLabel: string;
  copy: string;
  busy: boolean;
  onConfirm: () => void;
}) {
  const [open, setOpen] = useState(false);
  if (!open) {
    return (
      <button type="button" className="btn btn--danger" aria-label={ariaLabel} disabled={busy} onClick={() => setOpen(true)}>
        {actionLabel}
      </button>
    );
  }
  return (
    <span role="group" aria-label={`${ariaLabel} confirmation`}>
      <span className="dash-card__sub">{copy}</span>{" "}
      <button
        type="button"
        className="btn btn--danger"
        aria-label={`Confirm ${ariaLabel}`}
        disabled={busy}
        onClick={() => {
          setOpen(false);
          onConfirm();
        }}
      >
        Confirm
      </button>{" "}
      <button type="button" className="btn btn--secondary" aria-label={`Cancel ${ariaLabel}`} onClick={() => setOpen(false)}>
        Cancel
      </button>
    </span>
  );
}
