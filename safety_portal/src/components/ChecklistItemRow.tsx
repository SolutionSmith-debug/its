import { useState } from "react";
import type * as checklist from "../lib/fieldops_checklist";

// Assigned-Tasks tab (P4 field-ops) — the shared per-item completion controls, used by BOTH the S3/S4
// daily checklist section AND the S6 assigned-inspection section (spec: the assigned section uses "the
// SAME completion controls as the daily section"). Renders the inner content of one checklist item <li>
// (the parent owns the <li> wrapper + list). Local note/count input state lives here; the parent owns
// busy-gating, the network call, and the post-mutation refresh via the callbacks. Per item type:
//   • manual_attest — a check (+ optional note) with an Undo when done.
//   • count         — a number input + Record (done iff value ≥ target_count; parent enforces).
//   • form_linked / inspection — a deep-link button (NOT manually checkable); auto-closes on a matching
//     submission (server loop-closure). A done badge reflects the auto-close.

// Status → pill class for a checklist item state (done = ok, else neutral).
function itemPill(status: string): string {
  return status === "done" ? "dash-pill dash-pill--ok" : "dash-pill";
}

export function ChecklistItemRow({
  item,
  busy,
  canOpenForm,
  onComplete,
  onUncomplete,
  onRecordCount,
  onOpenForm,
}: {
  item: checklist.ChecklistItemState;
  busy: boolean;
  canOpenForm: boolean;
  onComplete: (item: checklist.ChecklistItemState, note?: string) => void;
  onUncomplete: (item: checklist.ChecklistItemState) => void;
  onRecordCount: (item: checklist.ChecklistItemState, value: number) => void;
  onOpenForm: (item: checklist.ChecklistItemState) => void;
}) {
  const [note, setNote] = useState("");
  const [count, setCount] = useState(item.value_num !== null ? String(item.value_num) : "");

  const done = item.status === "done";
  const isManual = item.item_type === "manual_attest";
  const isCount = item.item_type === "count";
  const isLinked = item.item_type === "form_linked" || item.item_type === "inspection";

  return (
    <>
      <span className={itemPill(item.status)}>{done ? "done" : "pending"}</span> {item.label}
      <span className="dash-card__sub"> · {item.item_type}</span>
      {isManual ? (
        <>
          {!done && (
            <>
              {" "}
              <input
                type="text"
                aria-label={`Note for item ${item.id}`}
                placeholder="note (optional)"
                value={note}
                onChange={(e) => setNote(e.target.value)}
                disabled={busy}
              />
            </>
          )}{" "}
          <button
            type="button"
            className={done ? "btn btn--ghost" : "btn btn--primary"}
            aria-label={done ? `Undo item ${item.id}` : `Complete item ${item.id}`}
            disabled={busy}
            onClick={() => (done ? onUncomplete(item) : onComplete(item, note || undefined))}
          >
            {done ? "Undo" : "Mark done"}
          </button>
          {done && item.note ? <span className="dash-card__sub"> · {item.note}</span> : null}
        </>
      ) : isCount ? (
        <>
          {item.target_count !== null ? (
            <span className="dash-card__sub"> · target {item.target_count}</span>
          ) : null}{" "}
          <input
            type="number"
            min={0}
            aria-label={`Count for item ${item.id}`}
            placeholder="count"
            value={count}
            onChange={(e) => setCount(e.target.value)}
            disabled={busy}
          />{" "}
          <button
            type="button"
            className="btn btn--primary"
            aria-label={`Record item ${item.id}`}
            disabled={busy}
            onClick={() => onRecordCount(item, count === "" ? NaN : Number(count))}
          >
            Record
          </button>
          {done && item.value_num !== null ? (
            <span className="dash-card__sub"> · recorded {item.value_num}</span>
          ) : null}
        </>
      ) : isLinked ? (
        <>
          {" "}
          <button
            type="button"
            className={done ? "btn btn--ghost" : "btn btn--primary"}
            aria-label={`Complete ${item.label ?? `item ${item.id}`}`}
            disabled={busy || !canOpenForm}
            onClick={() => onOpenForm(item)}
          >
            {done ? "Filed ✓ — file again" : `Complete ${item.label ?? "form"}`}
          </button>
        </>
      ) : null}
    </>
  );
}
