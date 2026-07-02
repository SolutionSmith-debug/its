import { useEffect, useState } from "react";
import * as checklist from "../lib/fieldops_checklist";
import { ChecklistItemRow } from "./ChecklistItemRow";
import { statusLabel } from "../lib/labels";
import { resolveFormTarget } from "../forms/registry";
import type { FormPrefill } from "../pages/FormFillPage";
import {
  CompletedDisclosure,
  InlineRowMsg,
  SectionError,
  SectionLoading,
  SectionRefreshWarn,
  errMsg,
  fmtDate,
  pacificToday,
  type RowFeedback,
} from "./myTasksShared";

/**
 * S6 — "Assigned inspections" for ANYONE with an assigned inspection (manager OR subcontractor;
 * extracted from FieldOpsMyTasks in R2 — lives on the Assigned-tasks tab). Fetches
 * GET /checklist/assigned and renders each assigned inspection instance + its items with the SAME
 * completion controls as the daily section (ChecklistItemRow). Completion reuses the SAME
 * ownership-scoped item-state routes (kind-agnostic server-side).
 *
 * R2 hardening (Mandatory B + render findings):
 *   • Load failure no longer collapses to an invisible section — error + working Retry.
 *   • Distinct loading state; a CONFIRMED-empty response (no error) still renders nothing, so the
 *     section stays invisible for users the feature doesn't touch. (`linked:false` is explained
 *     by the tasks list on the same tab — not duplicated here.)
 *   • R1 template_title is the heading; "#id" demoted to small text. Due dates via fmtDate; an
 *     open inspection past due gets an "Overdue" warn pill.
 *   • Per-row busy + inline per-row feedback; mutation/refetch try-split as in the daily section.
 *   • Completed items collapse under "Completed (N)" per inspection.
 */
export function AssignedInspectionsSection({
  onOpenForm,
  refreshToken = 0,
}: {
  onOpenForm?: (p: FormPrefill) => void;
  /** Bump to refetch (page Refresh / focus / visibilitychange — the parent owns the trigger). */
  refreshToken?: number;
}) {
  const [resp, setResp] = useState<checklist.AssignedInspectionsResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busyIds, setBusyIds] = useState<ReadonlySet<number>>(new Set());
  const [rowMsgs, setRowMsgs] = useState<Record<number, RowFeedback>>({});
  const [softWarn, setSoftWarn] = useState<string | null>(null);

  async function load() {
    setLoading(true);
    setError(null);
    try {
      setResp(await checklist.fetchAssignedInspections());
      setSoftWarn(null);
    } catch (err) {
      // Never silent (Mandatory B): previously a fetch error rendered NOTHING (silent-swallow site 2).
      setError(errMsg(err, "Could not load your assigned inspections."));
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

  /** Apply a CompleteResult locally: the item's row + ONLY its containing inspection's status. */
  function applyResult(
    r: checklist.AssignedInspectionsResponse | null,
    res: checklist.CompleteResult,
  ): checklist.AssignedInspectionsResponse | null {
    if (!r) return r;
    return {
      ...r,
      inspections: r.inspections.map((insp) => {
        if (!insp.items.some((it) => it.id === res.id)) return insp;
        return {
          instance: { ...insp.instance, status: res.instance_status },
          items: insp.items.map((it) =>
            it.id === res.id
              ? { ...it, status: res.status, value_num: res.value_num !== undefined ? res.value_num : it.value_num }
              : it,
          ),
        };
      }),
    };
  }

  /** Mutation/refetch try-split — see DailyChecklistSection.runItemAction (same Mandatory-B contract). */
  async function runItemAction(
    item: checklist.ChecklistItemState,
    call: () => Promise<checklist.CompleteResult>,
    okText: (res: checklist.CompleteResult) => string,
  ) {
    if (busyIds.has(item.id)) return;
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
    setResp((r) => applyResult(r, res));
    setRowMsg(item.id, { ok: true, text: okText(res) });
    try {
      setResp(await checklist.fetchAssignedInspections());
      setSoftWarn(null);
    } catch {
      setSoftWarn("Saved — but the list couldn't refresh; what you see may be slightly stale.");
    } finally {
      markBusy(item.id, false);
    }
  }

  function complete(item: checklist.ChecklistItemState, note?: string) {
    void runItemAction(
      item,
      () => checklist.completeChecklistItem(item.id, note ? { note } : undefined),
      (res) => (res.instance_status === "complete" ? "Inspection complete." : "Item updated."),
    );
  }

  function uncomplete(item: checklist.ChecklistItemState) {
    void runItemAction(
      item,
      () => checklist.uncompleteChecklistItem(item.id),
      () => "Item updated.",
    );
  }

  function recordCount(item: checklist.ChecklistItemState, value: number) {
    if (!Number.isFinite(value)) {
      setRowMsg(item.id, { ok: false, text: "Enter a number." });
      return;
    }
    void runItemAction(
      item,
      () => checklist.recordCountItem(item.id, value),
      (res) => (res.instance_status === "complete" ? "Inspection complete." : "Count recorded."),
    );
  }

  // A form_linked/inspection item in an assignment only auto-closes when the instance carries a
  // concrete (job, date) — otherwise there's no submission to match. Build the deep-link from those;
  // the row disables the button when canOpenForm is false.
  function openLinkedForm(inst: checklist.AssignedInstance, item: checklist.ChecklistItemState) {
    if (!onOpenForm || !item.form_code || !inst.job_id || !inst.instance_date) return;
    const { parentCode, variantCode } = resolveFormTarget(item.form_code);
    onOpenForm({ jobId: inst.job_id, parentCode, variantCode: variantCode || undefined, workDate: inst.instance_date });
  }

  // ── Render states ───────────────────────────────────────────────────────────────────────────────
  if (loading && !resp) return <SectionLoading label="Loading assigned inspections…" />;
  if (error && !resp) {
    return <SectionError message={error} onRetry={() => void load()} what="loading assigned inspections" />;
  }
  if (!resp) return null;
  // Confirmed-empty (no error): invisible for users with no assignments. The tasks list on this tab
  // explains linked:false, so no duplicate roster-link copy here.
  if (resp.inspections.length === 0 && !error) return null;

  const today = pacificToday();

  return (
    <section className="card dash-section" aria-label="Assigned inspections">
      <h3 className="dash-detail__h2">Assigned inspections</h3>
      {error && <SectionRefreshWarn message={error} onRetry={() => void load()} what="loading assigned inspections" />}
      {softWarn && <SectionRefreshWarn message={softWarn} onRetry={() => void load()} what="refreshing assigned inspections" />}
      {resp.inspections.map((insp) => {
        const overdue =
          insp.instance.status === "open" && !!insp.instance.instance_date && insp.instance.instance_date < today;
        const openItems = insp.items.filter((it) => it.status !== "done");
        const doneItems = insp.items.filter((it) => it.status === "done");
        const renderItem = (it: checklist.ChecklistItemState) => (
          <li key={it.id}>
            <ChecklistItemRow
              item={it}
              busy={busyIds.has(it.id)}
              canOpenForm={!!onOpenForm && !!insp.instance.job_id && !!insp.instance.instance_date}
              onComplete={complete}
              onUncomplete={uncomplete}
              onRecordCount={recordCount}
              onOpenForm={(item) => openLinkedForm(insp.instance, item)}
            />
            {it.filed_by ? <span className="dash-card__sub"> · filed by {it.filed_by}</span> : null}
            {rowMsgs[it.id] ? <InlineRowMsg msg={rowMsgs[it.id]} /> : null}
          </li>
        );
        return (
          <div key={insp.instance.id} className="dash-subsection">
            <h4 className="dash-detail__h2">
              {/* R1 template_title is the heading; the raw instance id is demoted to small text. */}
              {insp.instance.template_title ?? "Inspection"}
              <span className="dash-card__sub"> · #{insp.instance.id}</span>
              {insp.instance.project_name || insp.instance.job_id ? (
                <span className="dash-card__sub"> · {insp.instance.project_name ?? insp.instance.job_id}</span>
              ) : null}
              {insp.instance.instance_date ? (
                <span className="dash-card__sub"> · due {fmtDate(insp.instance.instance_date)}</span>
              ) : null}{" "}
              {overdue && <span className="dash-pill dash-pill--warn">Overdue</span>}{" "}
              <span className={insp.instance.status === "complete" ? "dash-pill dash-pill--ok" : "dash-pill dash-pill--warn"}>
                {statusLabel(insp.instance.status)}
              </span>
            </h4>
            {insp.items.length === 0 ? (
              <div className="muted">No items on this inspection.</div>
            ) : (
              <>
                {openItems.length > 0 && <ul className="dash-tasklist">{openItems.map(renderItem)}</ul>}
                <CompletedDisclosure count={doneItems.length}>
                  <ul className="dash-tasklist">{doneItems.map(renderItem)}</ul>
                </CompletedDisclosure>
              </>
            )}
          </div>
        );
      })}
    </section>
  );
}
