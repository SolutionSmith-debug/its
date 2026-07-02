import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import * as api from "../lib/fieldops_tasks";
import * as personnel from "../lib/fieldops_personnel";
import * as checklist from "../lib/fieldops_checklist";
import { PageShell } from "../components/PageShell";
import { ChecklistItemRow } from "../components/ChecklistItemRow";
import { useAuth } from "../lib/auth";
import { resolveFormTarget } from "../forms/registry";
import type { FormPrefill } from "./FormFillPage";

// Status → pill class (mirrors the Job Tracker task-list styling): done = ok, in_progress = warn.
function statusPill(status: string): string {
  if (status === "done") return "dash-pill dash-pill--ok";
  if (status === "in_progress") return "dash-pill dash-pill--warn";
  return "dash-pill";
}

interface JobGroup {
  job_id: string;
  project_name: string | null;
  tasks: api.MyTask[];
}

// Group a flat task list by job, preserving the server order within each group and the order in
// which each job first appears.
function groupByJob(tasks: api.MyTask[]): JobGroup[] {
  const order: string[] = [];
  const byJob = new Map<string, JobGroup>();
  for (const t of tasks) {
    let g = byJob.get(t.job_id);
    if (!g) {
      g = { job_id: t.job_id, project_name: t.project_name, tasks: [] };
      byJob.set(t.job_id, g);
      order.push(t.job_id);
    }
    g.tasks.push(t);
  }
  return order.map((id) => byJob.get(id)!);
}

/**
 * S3/S4 — "Today's checklist" for a placed manager. Self-contained: fetches GET /checklist/mine, which
 * runs Worker-on-read generation + S4 loop-closure reconcile, and returns `instance: null` for anyone
 * who isn't a placed manager (a subcontractor, or a manager with no current_job) — in which case this
 * renders NOTHING (the My-Tasks list stays exactly as S1). Per item type (S4):
 *   • manual_attest — a check with an optional note (+ undo).
 *   • count         — a number input + "Record" (done when value ≥ target_count).
 *   • form_linked / inspection — a deep-link ("Complete <label>") into FormFillPage pre-filled with the
 *     instance's job + date + the item's form. NOT manually checkable — the item auto-closes on the next
 *     load once a matching form is filed (server loop-closure). A done/pending badge reflects that.
 */
function DailyChecklistSection({ onOpenForm }: { onOpenForm?: (p: FormPrefill) => void }) {
  const [data, setData] = useState<checklist.MyChecklist | null>(null);
  const [busyId, setBusyId] = useState<number | null>(null);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [rollupBusy, setRollupBusy] = useState(false);

  useEffect(() => {
    checklist
      .fetchMyChecklist()
      .then(setData)
      .catch(() => setData({ instance: null, items: [], reason: null }));
  }, []);

  // manual_attest complete (optional note).
  async function completeItem(item: checklist.ChecklistItemState, note?: string) {
    if (busyId !== null) return;
    setBusyId(item.id);
    setMsg(null);
    try {
      const res = await checklist.completeChecklistItem(item.id, note ? { note } : undefined);
      setData(await checklist.fetchMyChecklist());
      setMsg({ ok: true, text: res.instance_status === "complete" ? "Checklist complete." : "Item updated." });
    } catch (err) {
      setMsg({ ok: false, text: err instanceof Error ? err.message : "Update failed." });
    } finally {
      setBusyId(null);
    }
  }

  // manual_attest / count undo.
  async function uncompleteItem(item: checklist.ChecklistItemState) {
    if (busyId !== null) return;
    setBusyId(item.id);
    setMsg(null);
    try {
      const res = await checklist.uncompleteChecklistItem(item.id);
      setData(await checklist.fetchMyChecklist());
      setMsg({ ok: true, text: res.instance_status === "complete" ? "Checklist complete." : "Item updated." });
    } catch (err) {
      setMsg({ ok: false, text: err instanceof Error ? err.message : "Update failed." });
    } finally {
      setBusyId(null);
    }
  }

  // count — record a value (server completes iff value ≥ target_count, else 'below_target' error).
  async function recordCount(item: checklist.ChecklistItemState, value: number) {
    if (busyId !== null) return;
    if (!Number.isFinite(value)) {
      setMsg({ ok: false, text: "Enter a number." });
      return;
    }
    setBusyId(item.id);
    setMsg(null);
    try {
      const res = await checklist.recordCountItem(item.id, value);
      setData(await checklist.fetchMyChecklist());
      setMsg({ ok: true, text: res.instance_status === "complete" ? "Checklist complete." : "Count recorded." });
    } catch (err) {
      // R1: user copy comes from errorCopy.ts via err.message — never duplicated in pages.
      setMsg({ ok: false, text: err instanceof Error ? err.message : "Update failed." });
    } finally {
      setBusyId(null);
    }
  }

  // form_linked / inspection — deep-link into the fill flow, pre-filled from the instance + the item's form.
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

  // S5 — assemble the Daily Report draft from the completed checklist + day's data, then deep-link into
  // the prefilled Daily Report form. The manager reviews/edits/submits via the normal form-submit; the
  // instance shows "filed ✓" on the next load once the reconcile stamps rolled_up_submission_uuid.
  async function reviewAndFileDailyReport() {
    if (rollupBusy || !onOpenForm) return;
    setRollupBusy(true);
    setMsg(null);
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
      setMsg({ ok: false, text: err instanceof Error ? err.message : "Could not assemble the Daily Report." });
    } finally {
      setRollupBusy(false);
    }
  }

  // Not a placed manager → no daily section at all.
  if (!data || data.instance === null) return null;

  const rolledUp = data.instance.rolled_up_submission_uuid !== null;
  const complete = data.instance.status === "complete";

  return (
    <section className="card dash-section" aria-label="Today's checklist">
      <h3 className="dash-detail__h2">
        Today&apos;s checklist
        <span className="dash-card__sub"> · {data.instance.job_id} · {data.instance.instance_date}</span>{" "}
        <span className={data.instance.status === "complete" ? "dash-pill dash-pill--ok" : "dash-pill dash-pill--warn"}>
          {data.instance.status}
        </span>
      </h3>
      {msg && <div className={`banner ${msg.ok ? "banner--ok" : "banner--err"}`}>{msg.text}</div>}
      {data.items.length === 0 ? (
        <div className="muted">No checklist items for today.</div>
      ) : (
        <ul className="dash-tasklist">
          {data.items.map((it) => (
            <li key={it.id}>
              <ChecklistItemRow
                item={it}
                busy={busyId !== null}
                canOpenForm={!!onOpenForm}
                onComplete={completeItem}
                onUncomplete={uncompleteItem}
                onRecordCount={recordCount}
                onOpenForm={openLinkedForm}
              />
            </li>
          ))}
        </ul>
      )}

      {/* S5 auto-rollup → Daily Report. Once every item is done, assemble + review/file the Daily
          Report. After it's filed, the reconcile stamps rolled_up_submission_uuid → the filed state. */}
      {rolledUp ? (
        <div className="dash-rollup" aria-label="Daily Report filed">
          <span className="dash-pill dash-pill--ok">Daily Report filed ✓</span>
        </div>
      ) : complete ? (
        <div className="dash-rollup">
          <button
            type="button"
            className="btn btn--primary"
            aria-label="Review and file Daily Report"
            disabled={rollupBusy || !onOpenForm}
            onClick={reviewAndFileDailyReport}
          >
            {rollupBusy ? "Assembling…" : "Review & file Daily Report"}
          </button>
          <span className="dash-card__sub"> · pre-filled from today&apos;s checklist; you confirm before filing</span>
        </div>
      ) : null}
    </section>
  );
}

/**
 * S6 — "Assigned inspections" for ANYONE with an assigned inspection (manager OR subcontractor). Fetches
 * GET /checklist/assigned and renders each assigned inspection instance + its items with the SAME
 * completion controls as the daily section (via ChecklistItemRow — manual_attest check, count input,
 * form_linked/inspection deep-link + auto-close). Renders NOTHING when the actor has no assigned
 * inspections (or on any fetch error), so it's invisible for users the feature doesn't touch. Completion
 * reuses the SAME ownership-scoped item-state routes as the daily checklist (kind-agnostic server-side).
 */
function AssignedInspectionsSection({ onOpenForm }: { onOpenForm?: (p: FormPrefill) => void }) {
  const [inspections, setInspections] = useState<checklist.AssignedInspection[]>([]);
  const [busyId, setBusyId] = useState<number | null>(null);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);

  async function reload() {
    try {
      const { inspections: ins } = await checklist.fetchAssignedInspections();
      setInspections(ins);
    } catch {
      setInspections([]);
    }
  }
  useEffect(() => {
    void reload();
  }, []);

  async function complete(item: checklist.ChecklistItemState, note?: string) {
    if (busyId !== null) return;
    setBusyId(item.id);
    setMsg(null);
    try {
      await checklist.completeChecklistItem(item.id, note ? { note } : undefined);
      await reload();
      setMsg({ ok: true, text: "Item updated." });
    } catch (err) {
      setMsg({ ok: false, text: err instanceof Error ? err.message : "Update failed." });
    } finally {
      setBusyId(null);
    }
  }

  async function uncomplete(item: checklist.ChecklistItemState) {
    if (busyId !== null) return;
    setBusyId(item.id);
    setMsg(null);
    try {
      await checklist.uncompleteChecklistItem(item.id);
      await reload();
      setMsg({ ok: true, text: "Item updated." });
    } catch (err) {
      setMsg({ ok: false, text: err instanceof Error ? err.message : "Update failed." });
    } finally {
      setBusyId(null);
    }
  }

  async function recordCount(item: checklist.ChecklistItemState, value: number) {
    if (busyId !== null) return;
    if (!Number.isFinite(value)) {
      setMsg({ ok: false, text: "Enter a number." });
      return;
    }
    setBusyId(item.id);
    setMsg(null);
    try {
      await checklist.recordCountItem(item.id, value);
      await reload();
      setMsg({ ok: true, text: "Count recorded." });
    } catch (err) {
      // R1: user copy comes from errorCopy.ts via err.message — never duplicated in pages.
      setMsg({ ok: false, text: err instanceof Error ? err.message : "Update failed." });
    } finally {
      setBusyId(null);
    }
  }

  // A form_linked/inspection item in an assignment only auto-closes when the instance carries a concrete
  // (job, date) — otherwise there's no submission to match. Build the deep-link from those; the row
  // disables the button when canOpenForm is false.
  function openLinkedForm(inst: checklist.AssignedInstance, item: checklist.ChecklistItemState) {
    if (!onOpenForm || !item.form_code || !inst.job_id || !inst.instance_date) return;
    const { parentCode, variantCode } = resolveFormTarget(item.form_code);
    onOpenForm({ jobId: inst.job_id, parentCode, variantCode: variantCode || undefined, workDate: inst.instance_date });
  }

  if (inspections.length === 0) return null;

  return (
    <section className="card dash-section" aria-label="Assigned inspections">
      <h3 className="dash-detail__h2">Assigned inspections</h3>
      {msg && <div className={`banner ${msg.ok ? "banner--ok" : "banner--err"}`}>{msg.text}</div>}
      {inspections.map((insp) => (
        <div key={insp.instance.id} className="dash-subsection">
          <h4 className="dash-detail__h2">
            Inspection #{insp.instance.id}
            {insp.instance.project_name || insp.instance.job_id ? (
              <span className="dash-card__sub"> · {insp.instance.project_name ?? insp.instance.job_id}</span>
            ) : null}
            {insp.instance.instance_date ? (
              <span className="dash-card__sub"> · due {insp.instance.instance_date}</span>
            ) : null}{" "}
            <span className={insp.instance.status === "complete" ? "dash-pill dash-pill--ok" : "dash-pill dash-pill--warn"}>
              {insp.instance.status}
            </span>
          </h4>
          {insp.items.length === 0 ? (
            <div className="muted">No items on this inspection.</div>
          ) : (
            <ul className="dash-tasklist">
              {insp.items.map((it) => (
                <li key={it.id}>
                  <ChecklistItemRow
                    item={it}
                    busy={busyId !== null}
                    canOpenForm={!!onOpenForm && !!insp.instance.job_id && !!insp.instance.instance_date}
                    onComplete={complete}
                    onUncomplete={uncomplete}
                    onRecordCount={recordCount}
                    onOpenForm={(item) => openLinkedForm(insp.instance, item)}
                  />
                </li>
              ))}
            </ul>
          )}
        </div>
      ))}
    </section>
  );
}

/**
 * Slice T — "Add crew" for a SUBCONTRACTOR (cap.crew.create). Creates a NON-LOGIN roster person
 * auto-placed on the subcontractor's OWN current job (POST /api/fieldops/crew). The Worker resolves
 * the job from the actor's placement and refuses (422 not_placed) if the subcontractor isn't placed —
 * surfaced here as a clear "must be placed on a job" message. The cap gate is a CONVENIENCE; the
 * Worker re-gates + enforces the non-login + auto-place rules (Invariant 2).
 */
function AddCrewSection() {
  const [name, setName] = useState("");
  const [trade, setTrade] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (busy) return;
    const n = name.trim();
    if (n.length < 1) {
      setMsg({ ok: false, text: "Enter a name." });
      return;
    }
    setBusy(true);
    setMsg(null);
    try {
      const res = await personnel.createCrew({ name: n, trade: trade.trim() || undefined });
      setName("");
      setTrade("");
      setMsg({ ok: true, text: `Added ${n} to your crew on ${res.current_job}.` });
    } catch (err) {
      // R1: user copy comes from errorCopy.ts via err.message — never duplicated in pages.
      setMsg({ ok: false, text: err instanceof Error ? err.message : "Could not add crew." });
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="card dash-section" aria-label="Add crew">
      <h3 className="dash-detail__h2">Add crew</h3>
      <p className="dash-card__sub">
        Add a field-only crew member — they&apos;re placed on your current job automatically.
      </p>
      {msg && <div className={`banner ${msg.ok ? "banner--ok" : "banner--err"}`}>{msg.text}</div>}
      <form onSubmit={submit} className="dash-row" aria-label="Add crew form">
        <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Name" maxLength={128} />{" "}
        <input value={trade} onChange={(e) => setTrade(e.target.value)} placeholder="Trade (optional)" maxLength={64} />{" "}
        <button type="submit" disabled={busy} className="btn btn--primary">
          {busy ? "Adding…" : "Add crew"}
        </button>
      </form>
    </section>
  );
}

/**
 * Assigned-Tasks tab (P4 S1) — "My Tasks". A subcontractor (submitter) or manager sees the one-off
 * tasks assigned to THEM, grouped by job, and can advance each task's status (cap.tasks.own, reusing
 * the existing setTaskStatus route). "Assigned to me" is resolved server-side via the personnel↔account
 * link; an account with no linked personnel row simply has no tasks (empty state). The cap gate here is
 * a CONVENIENCE — the Worker re-gates every read + status write (Invariant 2).
 */
export function FieldOpsMyTasks({
  onBack,
  onOpenForm,
}: {
  onBack: () => void;
  onOpenForm?: (p: FormPrefill) => void;
}) {
  const { user } = useAuth();
  const caps = user?.capabilities ?? [];
  const canOwn = caps.includes("cap.tasks.own");
  const canCreateCrew = caps.includes("cap.crew.create");

  const [tasks, setTasks] = useState<api.MyTask[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<number | null>(null);
  const [actionMsg, setActionMsg] = useState<{ ok: boolean; text: string } | null>(null);

  useEffect(() => {
    load();
  }, []);

  async function load() {
    setLoading(true);
    setError(null);
    try {
      const data = await api.fetchMyTasks();
      setTasks(data.tasks);
    } catch {
      setError("Failed to load your tasks.");
    } finally {
      setLoading(false);
    }
  }

  async function changeStatus(taskId: number, status: api.TaskStatus) {
    if (busyId !== null) return;
    setBusyId(taskId);
    setActionMsg(null);
    // Optimistic update; revert on failure.
    const prev = tasks;
    setTasks((ts) => ts.map((t) => (t.id === taskId ? { ...t, status } : t)));
    try {
      await api.setTaskStatus(taskId, status);
      setActionMsg({ ok: true, text: "Task updated." });
    } catch (err) {
      setTasks(prev);
      setActionMsg({ ok: false, text: err instanceof Error ? err.message : "Update failed." });
    } finally {
      setBusyId(null);
    }
  }

  const groups = groupByJob(tasks);

  return (
    <PageShell onHome={onBack}>
      <h2 className="page__heading">My Tasks</h2>
      <p className="dash__intro">
        The one-off tasks assigned to you, grouped by job. Update a task's status as you work it.
      </p>

      {actionMsg && (
        <div className={`banner ${actionMsg.ok ? "banner--ok" : "banner--err"}`}>{actionMsg.text}</div>
      )}
      {error && <div className="banner banner--err">{error}</div>}

      {/* S3/S4 — the placed manager's daily checklist (renders nothing for anyone else). */}
      <DailyChecklistSection onOpenForm={onOpenForm} />

      {/* S6 — admin-assigned inspection checklists (renders nothing when none are assigned). */}
      <AssignedInspectionsSection onOpenForm={onOpenForm} />

      {/* Slice T — a subcontractor adds field-only crew, auto-placed on their current job. */}
      {canCreateCrew && <AddCrewSection />}

      {loading && tasks.length === 0 ? (
        <div className="muted">Loading your tasks…</div>
      ) : groups.length === 0 ? (
        <div className="dash-unavail">
          No tasks are assigned to you. Tasks your crew lead or the office assigns to you will appear here.
        </div>
      ) : (
        <div className="dash-grid">
          {groups.map((g) => (
            <section key={g.job_id} className="card dash-section">
              <h3 className="dash-detail__h2">
                {g.project_name ?? g.job_id}
                <span className="dash-card__sub"> · {g.job_id}</span>
              </h3>
              <ul className="dash-tasklist">
                {g.tasks.map((t) => (
                  <li key={t.id}>
                    <span className={statusPill(t.status)}>{t.status}</span> {t.description}
                    {canOwn && (
                      <>
                        {" "}
                        <select
                          aria-label={`Set status for task ${t.id}`}
                          value={t.status}
                          disabled={busyId !== null}
                          onChange={(e) => changeStatus(t.id, e.target.value as api.TaskStatus)}
                        >
                          <option value="open">open</option>
                          <option value="in_progress">in_progress</option>
                          <option value="done">done</option>
                        </select>
                      </>
                    )}
                  </li>
                ))}
              </ul>
            </section>
          ))}
        </div>
      )}
    </PageShell>
  );
}
