import { useState } from "react";
import type * as checklist from "../lib/fieldops_checklist";
import { recordCountItem } from "../lib/fieldops_checklist";
import { ApiError } from "../lib/errorCopy";
import { itemTypeLabel } from "../lib/labels";
import { formCatalog, resolveFormTarget } from "../forms/registry";

// Assigned-Tasks tab (P4 field-ops) — the shared per-item completion controls, used by BOTH the S3/S4
// daily checklist section AND the S6 assigned-inspection section (spec: the assigned section uses "the
// SAME completion controls as the daily section"). Renders the inner content of one checklist item <li>
// (the parent owns the <li> wrapper + list). Local note/count input state lives here; the parent owns
// busy-gating, the network call, and the post-mutation refresh via the callbacks. Per item type:
//   • manual_attest — a check (+ optional note) with an Undo + edit-note (idempotent re-complete) when
//     done; photo evidence renders when photo_ref is present (see the R3 photo note below).
//   • count         — a numeric input + Record; done → controls frozen, recorded value + Undo. When the
//     parent opts in via `onCountRecorded` (R3), the ROW owns the record call so a server 'below_target'
//     400 can drive the inline acknowledge-with-note flow (the R1 acknowledge path); without the opt-in
//     the legacy parent-owned `onRecordCount` path is byte-identical to the pre-R3 behavior.
//   • form_linked / inspection — a deep-link button (NOT manually checkable); auto-closes on a matching
//     submission (server loop-closure). A dead deep-link (unknown/retired form_code, or an assigned
//     instance without job+date) renders explanatory text instead of a silently disabled button; a done
//     item shows a static "Filed ✓" pill + a small "File another" link.
//
// PROP-COMPAT CONTRACT (R3, parallel-R2): the seven original props are FROZEN. `onComplete` gained an
// OPTIONAL third `photoRef` parameter (a narrower callback stays assignable, so existing callers
// typecheck and simply ignore it); `onCountRecorded` is NEW and OPTIONAL with a safe default (absent →
// the exact legacy count flow). Existing callers keep working unchanged.
//
// R3 PHOTO NOTE (Q2a — honest gap): `photo_ref` is a BOUNDED reference string (worker MAX_PHOTO_REF =
// 256 chars, migration 0026) — it cannot carry image bytes. The existing photo pipeline (PhotoField →
// base64 PhotoValue inside a form submission's payload_json → worker validatePhotoValues → Mac §34
// photo_screen) is SUBMISSION-BOUND: there is no worker route that stores a standalone photo for a
// checklist item state and returns a ref, and none that serves one back. Photo CAPTURE here therefore
// requires a worker change (e.g. POST /checklist/item-state/:id/photo → ≤256-char ref + a GET to serve
// it) and is NOT faked; what ships is the evidence-rendering half (a photo_ref that is a renderable
// data URI gets a thumbnail, anything else a "photo attached" marker) plus the photoRef pass-through
// plumbing on onComplete, so a future capture path needs zero prop changes.

// Status → pill class for a checklist item state (done = ok, else neutral).
function itemPill(status: string): string {
  return status === "done" ? "dash-pill dash-pill--ok" : "dash-pill";
}

/** Copy for a form_linked/inspection item whose deep-link cannot open (R3 dead-end explanations). */
export const DEADEND_FORM_UNAVAILABLE = "This item points at a form that isn't available — tell the office.";
export const DEADEND_NO_JOB_DATE = "This item needs a job and date before its form can be opened — tell the office.";

export function ChecklistItemRow({
  item,
  busy,
  canOpenForm,
  onComplete,
  onUncomplete,
  onRecordCount,
  onOpenForm,
  onCountRecorded,
}: {
  item: checklist.ChecklistItemState;
  busy: boolean;
  canOpenForm: boolean;
  /** photoRef (R3, optional 3rd arg) threads an existing photo_ref through an idempotent re-complete
   * (edit-note) so it isn't clobbered; legacy 2-param callers remain assignable and just drop it. */
  onComplete: (item: checklist.ChecklistItemState, note?: string, photoRef?: string) => void;
  onUncomplete: (item: checklist.ChecklistItemState) => void;
  onRecordCount: (item: checklist.ChecklistItemState, value: number) => void;
  onOpenForm: (item: checklist.ChecklistItemState) => void;
  /** R3 opt-in: when present, the ROW owns the count record call (recordCountItem) so a server
   * 'below_target' drives the inline acknowledge-with-note flow; called after each successful
   * record/acknowledge so the parent can refetch. Absent → the legacy onRecordCount path, unchanged. */
  onCountRecorded?: () => void | Promise<void>;
}) {
  const [note, setNote] = useState("");
  const [count, setCount] = useState(item.value_num !== null ? String(item.value_num) : "");
  // R3 — done-manual edit-note affordance (idempotent re-complete).
  const [editingNote, setEditingNote] = useState(false);
  const [editNote, setEditNote] = useState("");
  // R3 — row-owned count flow (only used when onCountRecorded is provided).
  const [countBusy, setCountBusy] = useState(false);
  const [countErr, setCountErr] = useState<string | null>(null);
  const [belowTarget, setBelowTarget] = useState<{ value: number } | null>(null);
  const [ackNote, setAckNote] = useState("");

  const done = item.status === "done";
  const isManual = item.item_type === "manual_attest";
  const isCount = item.item_type === "count";
  const isLinked = item.item_type === "form_linked" || item.item_type === "inspection";

  // Row-owned count record (R3): server-driven below-target detection → inline acknowledge prompt.
  async function recordOwned(value: number) {
    if (!Number.isFinite(value) || value < 0) {
      setCountErr("Enter a number.");
      return;
    }
    setCountBusy(true);
    setCountErr(null);
    try {
      await recordCountItem(item.id, value);
      setBelowTarget(null);
      setAckNote("");
      await onCountRecorded?.();
    } catch (err) {
      if (err instanceof ApiError && err.code === "below_target") {
        setBelowTarget({ value }); // the value IS recorded server-side; offer the acknowledge flow
      } else {
        setCountErr(err instanceof Error ? err.message : "Update failed.");
      }
    } finally {
      setCountBusy(false);
    }
  }

  // The R1 acknowledge path: complete BELOW target with a required explanatory note.
  async function acknowledgeShortfall() {
    if (!belowTarget) return;
    const trimmed = ackNote.trim();
    if (trimmed.length === 0) return; // button is disabled without a note; belt-and-suspenders
    setCountBusy(true);
    setCountErr(null);
    try {
      await recordCountItem(item.id, belowTarget.value, { acknowledgeBelowTarget: true, note: trimmed });
      setBelowTarget(null);
      setAckNote("");
      await onCountRecorded?.();
    } catch (err) {
      setCountErr(err instanceof Error ? err.message : "Update failed.");
    } finally {
      setCountBusy(false);
    }
  }

  // R3 photo evidence (render-only half; see the module note for the capture gap).
  const photoEvidence = item.photo_ref ? (
    item.photo_ref.startsWith("data:image/") ? (
      <img
        src={item.photo_ref}
        alt={`Photo for item ${item.id}`}
        style={{ maxWidth: 96, maxHeight: 96, verticalAlign: "middle", borderRadius: 4 }}
      />
    ) : (
      <span className="dash-card__sub" title={item.photo_ref}> · photo attached</span>
    )
  ) : null;

  // R3 — an acknowledged shortfall stays flagged after completion (value below target, but done).
  const doneBelowTarget =
    done && item.value_num !== null && item.target_count !== null && item.value_num < item.target_count;

  // R3 — deep-link openability (dead-end explanations instead of silently disabled buttons). The
  // catalog check mirrors resolveFormTarget's lookup: an unknown/retired form_code resolves to a
  // parentCode that isn't in the catalog.
  let linkedControls = null;
  if (isLinked) {
    const target = item.form_code ? resolveFormTarget(item.form_code) : null;
    const formAvailable = target !== null && formCatalog().some((p) => p.parent_form_code === target.parentCode);
    const canOpen = canOpenForm && formAvailable;
    if (done) {
      linkedControls = (
        <>
          {" "}
          <span className="dash-pill dash-pill--ok">Filed ✓</span>
          {item.filed_by ? <span className="dash-card__sub"> · filed by {item.filed_by}</span> : null}
          {canOpen ? (
            <>
              {" "}
              <button
                type="button"
                className="btn btn--ghost"
                aria-label={`File another ${item.label ?? `form for item ${item.id}`}`}
                disabled={busy}
                onClick={() => onOpenForm(item)}
              >
                File another
              </button>
            </>
          ) : null}
        </>
      );
    } else if (!formAvailable) {
      linkedControls = (
        <span className="dash-card__sub" role="note">
          {" "}{DEADEND_FORM_UNAVAILABLE}
        </span>
      );
    } else if (!canOpenForm) {
      linkedControls = (
        <span className="dash-card__sub" role="note">
          {" "}{DEADEND_NO_JOB_DATE}
        </span>
      );
    } else {
      linkedControls = (
        <>
          {" "}
          <button
            type="button"
            className="btn btn--primary"
            aria-label={`Complete ${item.label ?? `item ${item.id}`}`}
            disabled={busy}
            onClick={() => onOpenForm(item)}
          >
            {`Complete ${item.label ?? "form"}`}
          </button>
        </>
      );
    }
  }

  return (
    <>
      <span className={itemPill(item.status)}>{done ? "done" : "pending"}</span> {item.label}
      <span className="dash-card__sub"> · {itemTypeLabel(item.item_type)}</span>
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
            className={done ? "btn btn--secondary" : "btn btn--primary"}
            aria-label={done ? `Undo item ${item.id}` : `Complete item ${item.id}`}
            disabled={busy}
            onClick={() => (done ? onUncomplete(item) : onComplete(item, note || undefined))}
          >
            {done ? "Undo" : "Mark done"}
          </button>
          {done && item.note && !editingNote ? <span className="dash-card__sub"> · {item.note}</span> : null}
          {done ? photoEvidence : null}
          {done && !editingNote ? (
            <>
              {" "}
              <button
                type="button"
                className="btn btn--ghost"
                aria-label={`Edit note for item ${item.id}`}
                disabled={busy}
                onClick={() => {
                  setEditNote(item.note ?? "");
                  setEditingNote(true);
                }}
              >
                {item.note ? "Edit note" : "Add note"}
              </button>
            </>
          ) : null}
          {done && editingNote ? (
            <>
              {" "}
              <input
                type="text"
                aria-label={`New note for item ${item.id}`}
                placeholder="note"
                value={editNote}
                onChange={(e) => setEditNote(e.target.value)}
                disabled={busy}
              />{" "}
              <button
                type="button"
                className="btn btn--secondary"
                aria-label={`Save note for item ${item.id}`}
                disabled={busy}
                onClick={() => {
                  // Idempotent re-complete: the server overwrites note (and photo_ref — thread the
                  // existing one through so a note edit doesn't clobber photo evidence).
                  onComplete(item, editNote.trim() || undefined, item.photo_ref ?? undefined);
                  setEditingNote(false);
                }}
              >
                Save note
              </button>
            </>
          ) : null}
        </>
      ) : isCount ? (
        <>
          {item.target_count !== null ? (
            <span className="dash-card__sub"> · target {item.target_count}</span>
          ) : null}
          {done ? (
            // R3 — frozen when done: recorded value + Undo, no live controls.
            <>
              <span className="dash-card__sub"> · recorded {item.value_num ?? "—"}</span>
              {doneBelowTarget ? (
                <>
                  {" "}
                  <span className="dash-pill dash-pill--warn">below target</span>
                </>
              ) : null}
              {item.note ? <span className="dash-card__sub"> · {item.note}</span> : null}{" "}
              <button
                type="button"
                className="btn btn--secondary"
                aria-label={`Undo item ${item.id}`}
                disabled={busy || countBusy}
                onClick={() => onUncomplete(item)}
              >
                Undo
              </button>
            </>
          ) : (
            <>
              {" "}
              <input
                type="number"
                inputMode="numeric"
                min={0}
                aria-label={`Count for item ${item.id}`}
                placeholder="count"
                value={count}
                onChange={(e) => setCount(e.target.value)}
                disabled={busy || countBusy}
              />{" "}
              <button
                type="button"
                className="btn btn--primary"
                aria-label={`Record item ${item.id}`}
                disabled={busy || countBusy}
                onClick={() => {
                  const value = count === "" ? NaN : Number(count);
                  if (onCountRecorded) void recordOwned(value);
                  else onRecordCount(item, value); // legacy parent-owned path (pre-R3, unchanged)
                }}
              >
                Record
              </button>
              {item.value_num !== null ? (
                // R3 — the recorded value stays visible while the item is open (a below-target
                // record persists server-side even though the item didn't complete).
                <span className="dash-card__sub"> · recorded {item.value_num}</span>
              ) : null}
              {belowTarget ? (
                // R3 — inline acknowledge-with-note prompt after a server 'below_target' 400 (the
                // R1 acknowledge path; the note is REQUIRED server-side).
                <span role="alert">
                  {" "}
                  <span className="dash-pill dash-pill--warn">below target</span>{" "}
                  <span className="dash-card__sub">
                    Recorded {belowTarget.value}
                    {item.target_count !== null ? ` of target ${item.target_count}` : ""} — the item stays
                    open unless you record it anyway with a note.
                  </span>{" "}
                  <input
                    type="text"
                    aria-label={`Shortfall note for item ${item.id}`}
                    placeholder="why is it below target? (required)"
                    value={ackNote}
                    onChange={(e) => setAckNote(e.target.value)}
                    disabled={busy || countBusy}
                  />{" "}
                  <button
                    type="button"
                    className="btn btn--secondary"
                    aria-label={`Record item ${item.id} anyway`}
                    disabled={busy || countBusy || ackNote.trim().length === 0}
                    onClick={() => void acknowledgeShortfall()}
                  >
                    Record anyway
                  </button>{" "}
                  <button
                    type="button"
                    className="btn btn--ghost"
                    aria-label={`Dismiss shortfall prompt for item ${item.id}`}
                    disabled={busy || countBusy}
                    onClick={() => {
                      setBelowTarget(null);
                      setAckNote("");
                    }}
                  >
                    Cancel
                  </button>
                </span>
              ) : null}
              {countErr ? (
                <span className="login__error" role="alert">
                  {" "}{countErr}
                </span>
              ) : null}
            </>
          )}
        </>
      ) : isLinked ? (
        linkedControls
      ) : null}
    </>
  );
}

// Type-level guard for the R3 prop-compat contract: a pre-R3 caller's callbacks (2-param onComplete,
// no onCountRecorded) must remain assignable forever. This compiles — or the props broke.
type _LegacyOnComplete = (item: checklist.ChecklistItemState, note?: string) => void;
type _AssertCompat = _LegacyOnComplete extends Parameters<typeof ChecklistItemRow>[0]["onComplete"]
  ? true
  : never;
const _compatCheck: _AssertCompat = true;
void _compatCheck;
