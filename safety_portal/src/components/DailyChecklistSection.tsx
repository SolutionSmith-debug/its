import { useEffect, useRef, useState } from "react";
import * as checklist from "../lib/fieldops_checklist";
import { ChecklistItemRow } from "./ChecklistItemRow";
import { statusLabel } from "../lib/labels";
import { resolveFormTarget } from "../forms/registry";
import type { FormPrefill } from "../pages/FormFillPage";
import {
  CompletedDisclosure,
  InlineRowMsg,
  ROSTER_LINK_COPY,
  SectionError,
  SectionLoading,
  SectionRefreshWarn,
  errMsg,
  fmtDate,
  pacificToday,
  type RowFeedback,
} from "./myTasksShared";

/**
 * S3/S4 — "Today's checklist" for a placed manager (extracted from FieldOpsMyTasks in R2; the
 * Daily-checklist tab's content). Self-contained: fetches GET /checklist/mine, which runs
 * Worker-on-read generation + S4 loop-closure reconcile. Per item type (S4):
 *   • manual_attest — a check with an optional note (+ undo).
 *   • count         — a number input + "Record" (done when value ≥ target_count).
 *   • form_linked / inspection — a deep-link ("Complete <label>") into FormFillPage pre-filled with
 *     the instance's job + date + the item's form. NOT manually checkable — auto-closes on the next
 *     load once a matching form is filed (server loop-closure).
 *
 * R2 hardening (Mandatory A + B):
 *   • `instance: null` no longer renders NOTHING — the R1 `reason` code drives an explanatory
 *     empty state (not_manager / no_personnel_link / not_placed).
 *   • Distinct loading state; load failure → error + working Retry (error and empty exclusive).
 *   • Mutation/refetch try-split: the route's CompleteResult is applied locally on success; a
 *     failed FOLLOW-UP refetch keeps the (locally-updated) data + shows a soft warn — a successful
 *     mutation is never reported as failed.
 *   • Per-row busy + per-row inline feedback (one in-flight item no longer freezes the section).
 *   • Day rollover: when the loaded instance_date ≠ today (Pacific) a "new day" banner replaces
 *     interactivity, so completions can't land on yesterday's checklist.
 *   • Completed items collapse under "Completed (N)"; open items render by default.
 */
export function DailyChecklistSection({
  onOpenForm,
  refreshToken = 0,
  onLoaded,
}: {
  onOpenForm?: (p: FormPrefill) => void;
  /** Bump to refetch (page Refresh / focus / visibilitychange — the parent owns the trigger). */
  refreshToken?: number;
  /** Reports each successful load up (drives the parent's auto-tab-switch + Add-crew placement hint). */
  onLoaded?: (info: { instance: checklist.DailyInstance | null; reason: checklist.DailyEmptyReason | null }) => void;
}) {
  const [data, setData] = useState<checklist.MyChecklist | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busyIds, setBusyIds] = useState<ReadonlySet<number>>(new Set());
  const [rowMsgs, setRowMsgs] = useState<Record<number, RowFeedback>>({});
  const [softWarn, setSoftWarn] = useState<string | null>(null);
  const [rollupBusy, setRollupBusy] = useState(false);
  const [rollupMsg, setRollupMsg] = useState<RowFeedback | null>(null);

  // Ref'd so the load effect doesn't re-fire when the parent re-renders with a new inline callback.
  const onLoadedRef = useRef(onLoaded);
  onLoadedRef.current = onLoaded;

  async function load() {
    setLoading(true);
    setError(null);
    try {
      const d = await checklist.fetchMyChecklist();
      setData(d);
      setSoftWarn(null);
      onLoadedRef.current?.({ instance: d.instance, reason: d.reason });
    } catch (err) {
      // Never silent (Mandatory B): with no data yet this renders the error+Retry block; with data
      // already on screen it renders the refresh warn and the previous data stays up.
      setError(errMsg(err, "Could not load today's checklist."));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [refreshToken]);

  function markBusy(id: number, on: boolean) {
    setBusyIds((s) => {
      const next = new Set(s);
      if (on) next.add(id);
      else next.delete(id);
      return next;
    });
  }

  function setRowMsg(id: number, msg: RowFeedback | null) {
    setRowMsgs((m) => {
      const next = { ...m };
      if (msg) next[id] = msg;
      else delete next[id];
      return next;
    });
  }

  /** Apply a mutation's CompleteResult to the local snapshot (the try-split's success half). */
  function applyResult(d: checklist.MyChecklist | null, res: checklist.CompleteResult): checklist.MyChecklist | null {
    if (!d || !d.instance) return d;
    return {
      ...d,
      instance: { ...d.instance, status: res.instance_status },
      items: d.items.map((it) =>
        it.id === res.id
          ? { ...it, status: res.status, value_num: res.value_num !== undefined ? res.value_num : it.value_num }
          : it,
      ),
    };
  }

  /**
   * The mutation/refetch try-split (Mandatory B). The mutation and the follow-up refetch fail
   * INDEPENDENTLY: a failed mutation shows the row error; a failed refetch after a successful
   * mutation keeps the locally-applied data + shows a soft warn — never "Update failed." for a
   * write that landed.
   */
  async function runItemAction(
    item: checklist.ChecklistItemState,
    call: () => Promise<checklist.CompleteResult>,
    okText: (res: checklist.CompleteResult) => string,
  ) {
    if (busyIds.has(item.id)) return;
    // Action-time day-rollover guard (review WARN): the render-time `stale` disable covers wake/focus,
    // but a desktop tab left open+focused across midnight never re-renders — without this check a tap
    // would write the completion onto YESTERDAY's item-state. Refresh instead of mutating.
    if (data?.instance && data.instance.instance_date !== pacificToday()) {
      setRowMsg(item.id, { ok: false, text: "A new day has started — refreshing your checklist." });
      void load();
      return;
    }
    markBusy(item.id, true);
    setRowMsg(item.id, null);
    let res: checklist.CompleteResult;
    try {
      res = await call();
    } catch (err) {
      // R1: user copy comes from errorCopy.ts via err.message — never duplicated in pages.
      setRowMsg(item.id, { ok: false, text: errMsg(err, "Update failed.") });
      markBusy(item.id, false);
      return;
    }
    setData((d) => applyResult(d, res));
    setRowMsg(item.id, { ok: true, text: okText(res) });
    try {
      const fresh = await checklist.fetchMyChecklist();
      setData(fresh);
      setSoftWarn(null);
      onLoadedRef.current?.({ instance: fresh.instance, reason: fresh.reason });
    } catch {
      setSoftWarn("Saved — but the checklist couldn't refresh; what you see may be slightly stale.");
    } finally {
      markBusy(item.id, false);
    }
  }

  // manual_attest complete (optional note).
  function completeItem(item: checklist.ChecklistItemState, note?: string) {
    void runItemAction(
      item,
      () => checklist.completeChecklistItem(item.id, note ? { note } : undefined),
      (res) => (res.instance_status === "complete" ? "Checklist complete." : "Item updated."),
    );
  }

  // manual_attest / count undo.
  function uncompleteItem(item: checklist.ChecklistItemState) {
    void runItemAction(
      item,
      () => checklist.uncompleteChecklistItem(item.id),
      (res) => (res.instance_status === "complete" ? "Checklist complete." : "Item updated."),
    );
  }

  // count — record a value (server completes iff value ≥ target_count, else 'below_target' error).
  function recordCount(item: checklist.ChecklistItemState, value: number) {
    if (!Number.isFinite(value)) {
      setRowMsg(item.id, { ok: false, text: "Enter a number." });
      return;
    }
    void runItemAction(
      item,
      () => checklist.recordCountItem(item.id, value),
      (res) => (res.instance_status === "complete" ? "Checklist complete." : "Count recorded."),
    );
  }

  // form_linked / inspection — deep-link into the fill flow, pre-filled from the instance + item.
  function openLinkedForm(item: checklist.ChecklistItemState) {
    if (!onOpenForm || !data?.instance || !item.form_code) return;
    const { parentCode, variantCode } = resolveFormTarget(item.form_code);
    onOpenForm({
      jobId: data.instance.job_id,
      parentCode,
      variantCode: variantCode || undefined,
      workDate: data.instance.instance_date,
    });
  }

  // S5 — assemble the Daily Report draft from the completed checklist, then deep-link into the
  // prefilled Daily Report form. The manager reviews/edits/submits via the normal form-submit.
  async function reviewAndFileDailyReport() {
    if (rollupBusy || !onOpenForm) return;
    setRollupBusy(true);
    setRollupMsg(null);
    try {
      const draft = await checklist.fetchRollupDraft();
      const { parentCode, variantCode } = resolveFormTarget(draft.form_code);
      onOpenForm({
        jobId: draft.job_id,
        parentCode,
        variantCode: variantCode || undefined,
        workDate: draft.work_date,
        values: draft.values,
      });
    } catch (err) {
      setRollupMsg({ ok: false, text: errMsg(err, "Could not assemble the Daily Report.") });
    } finally {
      setRollupBusy(false);
    }
  }

  // ── Render states (loading / error / reason-coded empty / content — mutually exclusive) ────────
  if (loading && !data) return <SectionLoading label="Loading today's checklist…" />;
  if (error && !data) return <SectionError message={error} onRetry={() => void load()} what="loading today's checklist" />;
  if (!data) return null;

  if (data.instance === null) {
    // Mandatory A: the R1 reason code explains the empty Daily tab instead of a lying blank.
    const copy =
      data.reason === "not_manager"
        ? "The daily checklist is for crew-lead managers who are placed on a job — it doesn't apply to this account."
        : data.reason === "no_personnel_link"
          ? ROSTER_LINK_COPY
          : data.reason === "not_placed"
            ? "You're not placed on a job yet — ask the office to place you. Your daily checklist appears once you're placed."
            : "There's no daily checklist for you today.";
    return (
      <section className="card dash-section" aria-label="Daily checklist status">
        <h3 className="dash-detail__h2">Daily checklist</h3>
        <div className="dash-unavail">{copy}</div>
      </section>
    );
  }

  const rolledUp = data.instance.rolled_up_submission_uuid !== null;
  const complete = data.instance.status === "complete";
  // Day rollover: a tab left open overnight still shows YESTERDAY's instance. Completions must not
  // land on it — controls are disabled (via the row `busy` prop) until a refresh loads today.
  const stale = data.instance.instance_date !== pacificToday();
  const openItems = data.items.filter((it) => it.status !== "done");
  const doneItems = data.items.filter((it) => it.status === "done");

  const renderItem = (it: checklist.ChecklistItemState) => (
    <li key={it.id}>
      <ChecklistItemRow
        item={it}
        busy={stale || busyIds.has(it.id)}
        canOpenForm={!!onOpenForm}
        onComplete={completeItem}
        onUncomplete={uncompleteItem}
        onRecordCount={recordCount}
        onOpenForm={openLinkedForm}
      />
      {/* R1 filed_by: who filed the submission that auto-closed this item (render half). */}
      {it.filed_by ? <span className="dash-card__sub"> · filed by {it.filed_by}</span> : null}
      {rowMsgs[it.id] ? <InlineRowMsg msg={rowMsgs[it.id]} /> : null}
    </li>
  );

  return (
    <section className="card dash-section" aria-label="Today's checklist">
      <h3 className="dash-detail__h2">
        Today&apos;s checklist
        <span className="dash-card__sub">
          {" "}
          · {data.instance.project_name ?? data.instance.job_id} · {fmtDate(data.instance.instance_date)}
        </span>{" "}
        <span className={complete ? "dash-pill dash-pill--ok" : "dash-pill dash-pill--warn"}>
          {statusLabel(data.instance.status)}
        </span>
      </h3>

      {stale && (
        <div className="banner banner--err" role="alert">
          A new day has started — refresh to load today&apos;s checklist.{" "}
          <button type="button" className="btn btn--secondary" aria-label="Refresh today's checklist" onClick={() => void load()}>
            Refresh
          </button>
        </div>
      )}
      {error && <SectionRefreshWarn message={error} onRetry={() => void load()} what="loading today's checklist" />}
      {softWarn && <SectionRefreshWarn message={softWarn} onRetry={() => void load()} what="refreshing today's checklist" />}

      {data.items.length === 0 ? (
        <div className="muted">No checklist items for today.</div>
      ) : (
        <>
          {openItems.length > 0 && <ul className="dash-tasklist">{openItems.map(renderItem)}</ul>}
          <CompletedDisclosure count={doneItems.length}>
            <ul className="dash-tasklist">{doneItems.map(renderItem)}</ul>
          </CompletedDisclosure>
        </>
      )}

      {rollupMsg && <div className={`banner ${rollupMsg.ok ? "banner--ok" : "banner--err"}`}>{rollupMsg.text}</div>}

      {/* S5 auto-rollup → Daily Report. Once every item is done, assemble + review/file the Daily
          Report. After it's filed, the reconcile stamps rolled_up_submission_uuid → the filed state. */}
      {rolledUp ? (
        <div className="dash-rollup" aria-label="Daily Report filed">
          <span className="dash-pill dash-pill--ok">Daily Report filed ✓</span>
          {data.instance.rolled_up_by ? <span className="dash-card__sub">by {data.instance.rolled_up_by}</span> : null}
        </div>
      ) : complete && !stale ? (
        <div className="dash-rollup">
          <button
            type="button"
            className="btn btn--primary"
            aria-label="Review and file Daily Report"
            disabled={rollupBusy || !onOpenForm}
            onClick={() => void reviewAndFileDailyReport()}
          >
            {rollupBusy ? "Assembling…" : "Review & file Daily Report"}
          </button>
          <span className="dash-card__sub"> · pre-filled from today&apos;s checklist; you confirm before filing</span>
        </div>
      ) : null}
    </section>
  );
}
