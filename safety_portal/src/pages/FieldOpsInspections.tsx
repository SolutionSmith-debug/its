import { useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";
import * as checklist from "../lib/fieldops_checklist";
import type { PersonnelRow } from "../lib/fieldops_personnel";
import { fetchJobList, type JobRow } from "../lib/fieldops_jobtracker";
import { statusLabel } from "../lib/labels";
import { PageShell } from "../components/PageShell";
import { ChecklistItemRow } from "../components/ChecklistItemRow";
import { ChecklistItemForm,
  EMPTY_ITEM,
  isFormBearing,
  itemInputFromRow,
  itemMetaLabel,
  nextSeq,
  planRenumber, ConfirmDelete } from "../components/ChecklistItemForm";

// R4 — the admin "Checklists" area (same 'fieldops-inspections' view key / Home card), now
// INSPECTIONS-ONLY after the D2 retirement: the generic_inspection LIBRARY (create / rename /
// deactivate / delete / per-template item editing) + the assign control + the outstanding
// assignments list. The company-wide "Default daily checklist" editor that used to live here is
// RETIRED (D2, SOP daily form): the daily content moved into the daily-report-v2 FORM DEFINITION
// (edited via the form builder / publish pipeline), so there is no daily checklist template to
// tailor anymore. The checklist ENGINE (and these library surfaces) stays — inspections use it.
// Gated cap.checklist.manage (admin). Every call is re-gated server-side (Invariant 2); caps here
// drive UI affordances only. Send-free (D1 reads/writes). Feedback is per-section inline (no single
// top-of-page banner); loading is rendered distinct from empty everywhere.

type Msg = { ok: boolean; text: string };

function MsgLine({ msg }: { msg: Msg | null }) {
  if (!msg) return null;
  return <p className={`banner ${msg.ok ? "banner--ok" : "banner--err"}`}>{msg.text}</p>;
}

// Synthesize the assignee-side item-state shape from an authoring row so the read-only preview can
// render through the REAL assignee component (ChecklistItemRow) without touching it: open status,
// no completion data, disabled controls via busy=true.
function previewState(it: checklist.DefaultItem): checklist.ChecklistItemState {
  return {
    id: it.id,
    source_item_id: it.id,
    item_type: it.item_type,
    label: it.label,
    form_code: it.form_code,
    target_count: it.target_count,
    status: "open",
    note: null,
    photo_ref: null,
    completed_by: null,
    completed_at: null,
    value_num: null,
    filed_by: null,
  };
}

const NOOP = () => {};

// ── Section 2 (per-template) — one library template's item editor + read-only assignee preview ────
function TemplateItemsEditor({
  template,
  onTemplatesChanged,
}: {
  template: checklist.InspectionTemplate;
  onTemplatesChanged: () => void;
}) {
  const [detail, setDetail] = useState<checklist.InspectionDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<Msg | null>(null);
  const [addDraft, setAddDraft] = useState<checklist.ItemInput>(EMPTY_ITEM);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [editDraft, setEditDraft] = useState<checklist.ItemInput>(EMPTY_ITEM);
  const [preview, setPreview] = useState(false);
  const sectionRef = useRef<HTMLDivElement | null>(null);

  const templateId = template.id;

  async function reload() {
    try {
      setDetail(await checklist.fetchInspectionTemplate(templateId));
    } catch {
      setMsg({ ok: false, text: "Could not load the checklist." });
    } finally {
      setLoading(false);
    }
  }
  useEffect(() => {
    setLoading(true);
    setDetail(null);
    setEditingId(null);
    setPreview(false);
    void reload();
    // Bring the editor into view on select/create — the library list sits above the fold and a
    // just-created empty template would otherwise "do nothing" visually.
    const el = sectionRef.current;
    if (el && typeof el.scrollIntoView === "function") el.scrollIntoView({ behavior: "smooth", block: "start" });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [templateId]);

  async function run(fn: () => Promise<unknown>, okText: string, alsoTemplates = false) {
    if (busy) return;
    setBusy(true);
    setMsg(null);
    try {
      await fn();
      await reload();
      if (alsoTemplates) onTemplatesChanged(); // item counts on the library rows
      setMsg({ ok: true, text: okText });
    } catch (err) {
      setMsg({ ok: false, text: err instanceof Error ? err.message : "Update failed." });
    } finally {
      setBusy(false);
    }
  }

  const items = detail?.items ?? [];

  function submitAdd(e: FormEvent) {
    e.preventDefault();
    if (!addDraft.label.trim()) {
      setMsg({ ok: false, text: "Item label is required." });
      return;
    }
    void run(async () => {
      await checklist.addInspectionItem(templateId, { ...addDraft, seq: addDraft.seq ?? nextSeq(items) });
      setAddDraft(EMPTY_ITEM);
    }, "Item added.", true);
  }

  function startEdit(it: checklist.DefaultItem) {
    setEditingId(it.id);
    setEditDraft(itemInputFromRow(it));
  }

  function submitEdit(e: FormEvent) {
    e.preventDefault();
    if (editingId === null) return;
    if (!editDraft.label.trim()) {
      setMsg({ ok: false, text: "Item label is required." });
      return;
    }
    const id = editingId;
    void run(async () => {
      await checklist.editInspectionItem(templateId, id, editDraft);
      setEditingId(null);
    }, "Item updated.");
  }

  function move(index: number, dir: -1 | 1) {
    const plan = planRenumber(items, index, dir);
    if (plan.length === 0) return;
    void run(async () => {
      for (const p of plan) {
        await checklist.editInspectionItem(templateId, p.row.id, { ...itemInputFromRow(p.row), seq: p.seq });
      }
    }, "Order updated.");
  }

  return (
    <div className="dash-subsection" ref={sectionRef}>
      <h4 className="dash-detail__h2">Items · {template.title ?? `checklist ${templateId}`}</h4>
      <MsgLine msg={msg} />
      {loading ? (
        <div className="muted">Loading items…</div>
      ) : (
        <>
          <div className="dash-row">
            <button
              type="button"
              className="btn btn--secondary"
              aria-label="Preview as assignee"
              onClick={() => setPreview((v) => !v)}
            >
              {preview ? "Back to editing" : "Preview as assignee"}
            </button>
          </div>

          {preview ? (
            <>
              <p className="dash-card__sub muted">
                Read-only preview — this is how the checklist renders for the assignee. Controls are disabled.
              </p>
              {items.length === 0 ? (
                <div className="dash-unavail">Nothing to preview — this checklist has no items yet.</div>
              ) : (
                <ul className="dash-tasklist" aria-label="Assignee preview">
                  {items.map((it) => (
                    <li key={it.id}>
                      <ChecklistItemRow
                        item={previewState(it)}
                        busy={true}
                        canOpenForm={false}
                        onComplete={NOOP}
                        onUncomplete={NOOP}
                        onRecordCount={NOOP}
                        onOpenForm={NOOP}
                      />
                    </li>
                  ))}
                </ul>
              )}
            </>
          ) : (
            <>
              {items.length === 0 ? (
                <div className="dash-unavail">This checklist has no items yet — now add its items below.</div>
              ) : (
                <ul className="dash-tasklist" aria-label="Checklist items">
                  {items.map((it, idx) => (
                    <li key={it.id}>
                      {editingId === it.id ? (
                        <ChecklistItemForm
                          label="Edit item"
                          draft={editDraft}
                          onChange={setEditDraft}
                          onSubmit={submitEdit}
                          busy={busy}
                          submitLabel="Save"
                          onCancel={() => setEditingId(null)}
                        />
                      ) : (
                        <>
                          {it.label} <span className="dash-card__sub"> · {itemMetaLabel(it)}</span>{" "}
                          <button
                            type="button"
                            className="btn btn--secondary"
                            aria-label={`Move ${it.label ?? `item ${it.id}`} up`}
                            disabled={busy || idx === 0}
                            onClick={() => move(idx, -1)}
                          >
                            ↑
                          </button>{" "}
                          <button
                            type="button"
                            className="btn btn--secondary"
                            aria-label={`Move ${it.label ?? `item ${it.id}`} down`}
                            disabled={busy || idx === items.length - 1}
                            onClick={() => move(idx, 1)}
                          >
                            ↓
                          </button>{" "}
                          <button
                            type="button"
                            className="btn btn--edit"
                            aria-label={`Edit ${it.label ?? `item ${it.id}`}`}
                            disabled={busy}
                            onClick={() => startEdit(it)}
                          >
                            Edit
                          </button>{" "}
                          <ConfirmDelete
                            actionLabel="Remove"
                            ariaLabel={`Remove ${it.label ?? `item ${it.id}`}`}
                            copy={`Remove “${it.label ?? `item ${it.id}`}” from this checklist? Already-assigned copies keep their snapshot.`}
                            busy={busy}
                            onConfirm={() =>
                              run(() => checklist.deleteInspectionItem(templateId, it.id), "Item removed.", true)
                            }
                          />
                        </>
                      )}
                    </li>
                  ))}
                </ul>
              )}
              <ChecklistItemForm
                label="Add item"
                draft={addDraft}
                onChange={setAddDraft}
                onSubmit={submitAdd}
                busy={busy}
                submitLabel="Add item"
              />
            </>
          )}
        </>
      )}
    </div>
  );
}

// The device's local calendar date as 'YYYY-MM-DD' (the same shape instance_date carries) — used
// for the overdue check on the assignments list. Local, not UTC: an admin looks at this in Pacific
// working hours and "overdue" must not flip at 5pm because UTC rolled over.
function localTodayISO(): string {
  const d = new Date();
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}

// The persistent post-assign confirmation (replaces the old transient banner — an admin who looks
// up from their phone mid-task must still be able to see WHAT they just assigned and to WHOM).
interface AssignConfirmation {
  assignee: string;
  title: string;
  job: string | null;
  due: string | null;
  itemCount: number;
}

// ── Section 3 — the GUARDED assign control (R5 — the client halves of the R1 assign-time 422s) ────
// The Worker independently rejects all four stuck-assignment classes (R1); this form makes them
// unreachable from the UI in the first place:
//   • empty template        → its picker option is disabled "(no items yet)";
//   • form-linked w/o job+date → job + due date flip to REQUIRED (with inline copy) the moment a
//     selected template's detail shows form-bearing items; submit blocks client-side;
//   • unknown form code     → not producible here (the item editors use a catalog select, R4);
//   • duplicate double-tap  → busy-guard during submit + a FULL form reset after success (a repeat
//     assign requires deliberate re-selection), plus the persistent confirmation card.
function AssignForm({
  templates,
  onAssigned,
}: {
  templates: checklist.InspectionTemplate[];
  onAssigned?: () => void;
}) {
  const [people, setPeople] = useState<PersonnelRow[]>([]);
  const [jobs, setJobs] = useState<JobRow[]>([]);
  const [pickersError, setPickersError] = useState(false);
  const [templateId, setTemplateId] = useState<string>("");
  const [assignee, setAssignee] = useState<string>("");
  const [jobId, setJobId] = useState<string>("");
  const [dueDate, setDueDate] = useState<string>("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<Msg | null>(null);
  const [detail, setDetail] = useState<checklist.InspectionDetail | null>(null);
  const [detailWarn, setDetailWarn] = useState(false);
  const [confirmation, setConfirmation] = useState<AssignConfirmation | null>(null);

  // VERIFIED (R5): POST /checklist/assign requires an ACTIVE personnel row only — a portal login is
  // NOT required (worker/fieldops_checklist.ts: `SELECT id FROM personnel WHERE id=? AND active=1`).
  // So the FULL active roster is offered (fetchFullRoster pages the cursor to exhaustion — the old
  // single-page fetch silently dropped everyone past #50) with no login filter and no "can't be
  // assigned" hint. Each option carries the person's current placement for context. Never silent:
  // a load failure renders an error with Retry (A4 silent-swallow site #3), not an empty picker.
  async function loadPickers() {
    setPickersError(false);
    try {
      const [roster, jobsRes] = await Promise.all([checklist.fetchFullRoster(), fetchJobList("active")]);
      setPeople(roster);
      setJobs(jobsRes.jobs);
    } catch {
      setPickersError(true);
    }
  }
  useEffect(() => {
    void loadPickers();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // On template selection, fetch its detail: form-bearing items make job + due date REQUIRED (the
  // client half of the R1 'job_and_date_required' 422). Best-effort — a failed detail fetch warns
  // (with Retry) but never blocks the form; the Worker re-validates on assign regardless.
  function loadDetail(tid: number) {
    setDetailWarn(false);
    checklist
      .fetchInspectionTemplate(tid)
      .then((d) => setDetail((cur) => (String(tid) === templateIdRef.current ? d : cur)))
      .catch(() => setDetailWarn(true));
  }
  // Ref mirror so a slow detail response for a PREVIOUS selection can't clobber the current one.
  const templateIdRef = useRef(templateId);
  useEffect(() => {
    templateIdRef.current = templateId;
    setDetail(null);
    setDetailWarn(false);
    const tid = Number(templateId);
    if (Number.isInteger(tid) && tid > 0) loadDetail(tid);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [templateId]);

  const needsJobDate = (detail?.items ?? []).some((i) => isFormBearing(i.item_type));

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (busy) return;
    const tid = Number(templateId);
    const aid = Number(assignee);
    if (!Number.isInteger(tid) || tid <= 0) {
      setMsg({ ok: false, text: "Pick a checklist." });
      return;
    }
    if (!Number.isInteger(aid) || aid <= 0) {
      setMsg({ ok: false, text: "Pick a person." });
      return;
    }
    // Client half of the R1 server 422 — same rule, caught BEFORE the request with inline copy.
    if (needsJobDate && (jobId === "" || dueDate === "")) {
      setMsg({
        ok: false,
        text: "Pick a job and a due date — this checklist auto-checks from filed forms, so it needs both.",
      });
      return;
    }
    setBusy(true);
    setMsg(null);
    try {
      const input: checklist.AssignInput = { template_id: tid, assignee_personnel_id: aid };
      if (jobId) input.job_id = jobId;
      if (dueDate) input.due_date = dueDate;
      const res = await checklist.assignInspection(input);
      const person = people.find((p) => p.id === aid);
      const tpl = templates.find((t) => t.id === tid);
      const job = jobId ? jobs.find((j) => j.job_id === jobId) : undefined;
      setConfirmation({
        assignee: person?.name ?? `person #${aid}`,
        title: tpl?.title ?? `checklist ${tid}`,
        job: jobId ? (job?.project_name ?? jobId) : null,
        due: dueDate || null,
        itemCount: res.item_count,
      });
      // FULL reset (not just the date) — a repeat assign must be a deliberate fresh selection, so a
      // double-tap after success can't silently create a duplicate no-job/no-date instance.
      setTemplateId("");
      setAssignee("");
      setJobId("");
      setDueDate("");
      setDetail(null);
      onAssigned?.();
    } catch (err) {
      // R1: user copy comes from errorCopy.ts via err.message — never duplicated in pages.
      setMsg({ ok: false, text: err instanceof Error ? err.message : "Assign failed." });
    } finally {
      setBusy(false);
    }
  }

  // Inactive templates are retired — not assignable, so not offered. Empty (0-item) templates stay
  // VISIBLE but disabled "(no items yet)" — hiding them entirely would read as a missing checklist.
  const assignable = templates.filter((t) => t.active);

  return (
    <section className="card dash-section" aria-label="Assign an inspection">
      <h3 className="dash-detail__h2">Assign an inspection checklist</h3>
      <MsgLine msg={msg} />
      {pickersError && (
        <p className="banner banner--err">
          Couldn't load the people and jobs to assign to.{" "}
          <button type="button" className="btn btn--secondary" aria-label="Retry loading assign pickers" onClick={() => void loadPickers()}>
            Retry
          </button>
        </p>
      )}
      {confirmation && (
        <div className="dash-subsection" role="status" aria-label="Assignment confirmation">
          <strong>Assigned to {confirmation.assignee} ✓</strong>
          <span className="dash-card__sub" style={{ display: "block" }}>
            “{confirmation.title}” ({confirmation.itemCount} item{confirmation.itemCount === 1 ? "" : "s"})
            {confirmation.job !== null ? <> · {confirmation.job}</> : null}
            {confirmation.due !== null ? <> · due {confirmation.due}</> : null}
          </span>
        </div>
      )}
      <form onSubmit={submit} className="dash-row" aria-label="Assign form">
        <label className="field">
          <span className="field__label">Checklist</span>
          <select aria-label="Checklist" value={templateId} onChange={(e) => setTemplateId(e.target.value)}>
            <option value="">— checklist —</option>
            {assignable.map((t) => (
              <option key={t.id} value={t.id} disabled={t.item_count === 0}>
                {t.title}
                {t.item_count === 0 ? " (no items yet)" : ""}
              </option>
            ))}
          </select>
        </label>{" "}
        <label className="field">
          <span className="field__label">Assign to</span>
          <select aria-label="Assignee" value={assignee} onChange={(e) => setAssignee(e.target.value)}>
            <option value="">— person —</option>
            {people.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
                {p.trade ? ` (${p.trade})` : ""}
                {p.current_job ? ` — on ${p.current_job_name ?? p.current_job}` : ""}
              </option>
            ))}
          </select>
        </label>{" "}
        <label className="field">
          <span className="field__label">{needsJobDate ? "Job (required)" : "Job (optional)"}</span>
          <select aria-label="Job" value={jobId} onChange={(e) => setJobId(e.target.value)}>
            <option value="">{needsJobDate ? "— pick a job —" : "— job (optional) —"}</option>
            {jobs.map((j) => (
              <option key={j.job_id} value={j.job_id}>{j.project_name ?? j.job_id}</option>
            ))}
          </select>
        </label>{" "}
        <label className="field">
          <span className="field__label">{needsJobDate ? "Due date (required)" : "Due date (optional)"}</span>
          <input
            type="date"
            aria-label="Due date"
            value={dueDate}
            onChange={(e) => setDueDate(e.target.value)}
          />
        </label>{" "}
        <button type="submit" className="btn btn--primary" disabled={busy}>Assign</button>
        {needsJobDate && (
          <span className="dash-card__sub" style={{ display: "block", width: "100%" }}>
            This checklist auto-checks from filed forms — it needs a job and a date so filings can be
            matched to it. The due date is the date the work must be filed by.
          </span>
        )}
        {detailWarn && (
          <span className="dash-card__sub" style={{ display: "block", width: "100%" }}>
            Couldn't check this checklist's items — you can still assign; the server verifies.{" "}
            <button
              type="button"
              className="btn btn--secondary"
              aria-label="Retry loading checklist detail"
              onClick={() => {
                const tid = Number(templateId);
                if (Number.isInteger(tid) && tid > 0) loadDetail(tid);
              }}
            >
              Retry
            </button>
          </span>
        )}
      </form>
    </section>
  );
}

// ── Section 4 — outstanding assignments (R5 — the admin list + cancel; GET /checklist/instances) ──
// Every outstanding inspection assignment is listable, its progress visible, and cancellable — the
// close of the old fire-and-forget loop where a mistaken assignment was invisible and irrevocable.
// Loading ≠ empty; a fetch failure renders an error with Retry (never a lying blank).
function AssignmentsSection({ refreshKey }: { refreshKey: number }) {
  const [rows, setRows] = useState<checklist.AdminInstanceRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState(false);
  const [filter, setFilter] = useState<"open" | "all">("open");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<Msg | null>(null);

  async function load(f: "open" | "all") {
    setLoading(true);
    setLoadError(false);
    try {
      const res = await checklist.fetchChecklistInstances(f);
      setRows(res.instances);
    } catch {
      setLoadError(true);
    } finally {
      setLoading(false);
    }
  }
  useEffect(() => {
    void load(filter);
    // refreshKey: bumped by AssignForm after a successful assign so the new row appears immediately.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filter, refreshKey]);

  function cancel(row: checklist.AdminInstanceRow) {
    if (busy) return;
    setBusy(true);
    setMsg(null);
    void (async () => {
      try {
        await checklist.cancelChecklistInstance(row.id);
        await load(filter);
        setMsg({
          ok: true,
          text: `Cancelled “${row.template_title ?? `Inspection #${row.id}`}” for ${row.assignee_name ?? "the assignee"}.`,
        });
      } catch (err) {
        setMsg({ ok: false, text: err instanceof Error ? err.message : "Cancel failed." });
      } finally {
        setBusy(false);
      }
    })();
  }

  const today = localTodayISO();

  return (
    <section className="card dash-section" aria-label="Outstanding assignments">
      <h3 className="dash-detail__h2">Outstanding assignments</h3>
      <p className="dash-card__sub muted">
        Every assigned inspection checklist — who has it, which job, when it's due, and how far along
        it is. Cancel removes it from the person's Assigned inspections.
      </p>
      <div className="dash-row">
        <button
          type="button"
          className={filter === "open" ? "btn btn--primary" : "btn btn--secondary"}
          aria-label="Show open assignments"
          onClick={() => setFilter("open")}
        >
          Open
        </button>{" "}
        <button
          type="button"
          className={filter === "all" ? "btn btn--primary" : "btn btn--secondary"}
          aria-label="Show all assignments"
          onClick={() => setFilter("all")}
        >
          All
        </button>
      </div>
      <MsgLine msg={msg} />
      {loading ? (
        <div className="muted">Loading assignments…</div>
      ) : loadError ? (
        <p className="banner banner--err">
          Couldn't load the assignments.{" "}
          <button type="button" className="btn btn--secondary" aria-label="Retry loading assignments" onClick={() => void load(filter)}>
            Retry
          </button>
        </p>
      ) : rows.length === 0 ? (
        <div className="dash-unavail">
          {filter === "open"
            ? "No open assignments — everything assigned has been completed (or nothing is assigned yet)."
            : "No assignments yet."}
        </div>
      ) : (
        <ul className="dash-tasklist" aria-label="Assignment rows">
          {rows.map((r) => {
            const title = r.template_title ?? `Inspection #${r.id}`;
            const who = r.assignee_name ?? "(unknown assignee)";
            const overdue = r.status === "open" && r.instance_date !== null && r.instance_date < today;
            return (
              <li key={r.id}>
                <strong>{title}</strong>
                <span className="dash-card__sub"> · {who}</span>
                {r.job_id !== null && (
                  <span className="dash-card__sub"> · {r.project_name ?? r.job_id}</span>
                )}
                {r.instance_date !== null && <span className="dash-card__sub"> · due {r.instance_date}</span>}{" "}
                {overdue && <span className="dash-pill dash-pill--warn">overdue</span>}{" "}
                <span className={r.status === "complete" ? "dash-pill dash-pill--ok" : "dash-pill"}>
                  {statusLabel(r.status)}
                </span>
                <span className="dash-card__sub">
                  {" "}· {r.items_done}/{r.items_total} item{r.items_total === 1 ? "" : "s"} done
                </span>{" "}
                <ConfirmDelete
                  actionLabel="Cancel"
                  ariaLabel={`Cancel assignment ${title} for ${who}`}
                  copy={`Cancel “${title}” for ${who}? This removes it from ${who}'s Assigned inspections — any completed items are discarded.`}
                  busy={busy}
                  onConfirm={() => cancel(r)}
                />
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}

/**
 * R4 — the admin "Checklists" page (view key 'fieldops-inspections' unchanged), inspections-only
 * since D2: the Inspection-checklists library (author / rename / deactivate / delete / per-template
 * items) + the assign control + outstanding assignments. The Default-daily-checklist editor is
 * RETIRED (the daily content lives in the daily-report-v2 form definition — edit it via the form
 * builder). cap.checklist.manage gates the Home card + every call (the Worker re-gates —
 * Invariant 2).
 */
export function FieldOpsInspections({ onBack }: { onBack: () => void }) {
  const [templates, setTemplates] = useState<checklist.InspectionTemplate[]>([]);
  const [loading, setLoading] = useState(true);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [newTitle, setNewTitle] = useState("");
  const [filter, setFilter] = useState("");
  const [renamingId, setRenamingId] = useState<number | null>(null);
  const [renameTitle, setRenameTitle] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<Msg | null>(null);
  // R5: bumped after each successful assign so the Outstanding-assignments section refetches.
  const [assignmentsRefresh, setAssignmentsRefresh] = useState(0);

  async function reload() {
    try {
      const { templates: t } = await checklist.fetchInspectionTemplates();
      setTemplates(t);
    } catch {
      setMsg({ ok: false, text: "Could not load the inspection library." });
    } finally {
      setLoading(false);
    }
  }
  useEffect(() => {
    void reload();
  }, []);

  async function run(fn: () => Promise<unknown>, okText: string) {
    if (busy) return;
    setBusy(true);
    setMsg(null);
    try {
      await fn();
      await reload();
      setMsg({ ok: true, text: okText });
    } catch (err) {
      setMsg({ ok: false, text: err instanceof Error ? err.message : "Update failed." });
    } finally {
      setBusy(false);
    }
  }

  function create(e: FormEvent) {
    e.preventDefault();
    if (newTitle.trim() === "") {
      setMsg({ ok: false, text: "A title is required." });
      return;
    }
    void run(async () => {
      const res = await checklist.createInspectionTemplate(newTitle.trim());
      setNewTitle("");
      if (res.id) setSelectedId(res.id); // opens the item editor below (it scrolls itself into view)
    }, "Checklist created — now add its items below.");
  }

  function startRename(t: checklist.InspectionTemplate) {
    setRenamingId(t.id);
    setRenameTitle(t.title ?? "");
  }

  function submitRename(e: FormEvent) {
    e.preventDefault();
    if (renamingId === null) return;
    if (renameTitle.trim() === "") {
      setMsg({ ok: false, text: "A title is required." });
      return;
    }
    const id = renamingId;
    void run(async () => {
      await checklist.editInspectionTemplate(id, { title: renameTitle.trim() });
      setRenamingId(null);
    }, "Checklist renamed.");
  }

  function setActive(t: checklist.InspectionTemplate, active: boolean) {
    void run(
      () => checklist.editInspectionTemplate(t.id, { title: t.title ?? "", active }),
      active ? "Checklist reactivated — it can be assigned again." : "Checklist deactivated — it can no longer be assigned.",
    );
  }

  function remove(t: checklist.InspectionTemplate) {
    void run(async () => {
      await checklist.deleteInspectionTemplate(t.id);
      if (selectedId === t.id) setSelectedId(null);
    }, "Checklist deleted.");
  }

  const visible = filter.trim() === ""
    ? templates
    : templates.filter((t) => (t.title ?? "").toLowerCase().includes(filter.trim().toLowerCase()));
  const selected = selectedId !== null ? templates.find((t) => t.id === selectedId) ?? null : null;

  return (
    <PageShell onHome={onBack}>
      <h2 className="page__heading">Checklists</h2>
      <p className="dash__intro">
        The <strong>inspection checklists</strong> you author and assign to a manager or subcontractor
        (they appear in that person's My Tasks tab). The daily report's content is no longer a
        checklist edited here — it lives in the Daily Field Report <strong>form definition</strong>{" "}
        (edit it in Forms, the form builder).
      </p>

      <section className="card dash-section" aria-label="Inspection library">
        <h3 className="dash-detail__h2">Inspection checklists</h3>
        <MsgLine msg={msg} />
        {loading ? (
          <div className="muted">Loading inspection checklists…</div>
        ) : templates.length === 0 ? (
          <div className="dash-unavail">No inspection checklists yet — create one below.</div>
        ) : (
          <>
            {templates.length > 3 && (
              <div className="dash-row">
                <input
                  aria-label="Filter checklists"
                  value={filter}
                  onChange={(e) => setFilter(e.target.value)}
                  placeholder="Filter by title"
                />
              </div>
            )}
            {visible.length === 0 ? (
              <div className="muted">No checklist titles match “{filter.trim()}”.</div>
            ) : (
              <ul className="dash-tasklist">
                {visible.map((t) => (
                  <li key={t.id}>
                    {renamingId === t.id ? (
                      <form onSubmit={submitRename} className="dash-row" aria-label={`Rename ${t.title}`} style={{ display: "inline" }}>
                        <input
                          aria-label={`Rename ${t.title} title`}
                          value={renameTitle}
                          onChange={(e) => setRenameTitle(e.target.value)}
                          maxLength={256}
                        />{" "}
                        <button type="submit" className="btn btn--primary" disabled={busy}>Save</button>{" "}
                        <button type="button" className="btn btn--secondary" onClick={() => setRenamingId(null)}>
                          Cancel
                        </button>
                      </form>
                    ) : (
                      <>
                        <button
                          type="button"
                          className={selectedId === t.id ? "btn btn--primary" : "btn btn--secondary"}
                          aria-label={`Edit ${t.title}`}
                          onClick={() => setSelectedId(selectedId === t.id ? null : t.id)}
                        >
                          {t.title}
                        </button>
                        {!t.active && <span className="dash-pill dash-pill--warn"> inactive — not assignable</span>}
                        <span className="dash-card__sub">
                          {" "}· {t.item_count} item{t.item_count === 1 ? "" : "s"} · created{" "}
                          {new Date(t.created_at * 1000).toLocaleDateString()}
                        </span>{" "}
                        <button
                          type="button"
                          className="btn btn--edit"
                          aria-label={`Rename ${t.title}`}
                          disabled={busy}
                          onClick={() => startRename(t)}
                        >
                          Rename
                        </button>{" "}
                        {t.active ? (
                          <button
                            type="button"
                            className="btn btn--secondary"
                            aria-label={`Deactivate ${t.title}`}
                            disabled={busy}
                            onClick={() => setActive(t, false)}
                          >
                            Deactivate
                          </button>
                        ) : (
                          <button
                            type="button"
                            className="btn btn--secondary"
                            aria-label={`Reactivate ${t.title}`}
                            disabled={busy}
                            onClick={() => setActive(t, true)}
                          >
                            Reactivate
                          </button>
                        )}{" "}
                        <ConfirmDelete
                          actionLabel="Delete"
                          ariaLabel={`Delete ${t.title}`}
                          copy={`Delete “${t.title}” and its ${t.item_count} item${t.item_count === 1 ? "" : "s"}? Any outstanding assigned copies keep their snapshot. Prefer Deactivate to retire it without deleting.`}
                          busy={busy}
                          onConfirm={() => remove(t)}
                        />
                      </>
                    )}
                  </li>
                ))}
              </ul>
            )}
          </>
        )}
        {!loading && (
          <form onSubmit={create} className="dash-row" aria-label="Create checklist">
            <input
              aria-label="New checklist title"
              value={newTitle}
              onChange={(e) => setNewTitle(e.target.value)}
              placeholder="New checklist title"
              maxLength={256}
            />{" "}
            <button type="submit" className="btn btn--primary" disabled={busy}>Create</button>
          </form>
        )}
      </section>

      {selected !== null && (
        <section className="card dash-section" aria-label="Edit checklist items">
          <TemplateItemsEditor template={selected} onTemplatesChanged={() => void reload()} />
        </section>
      )}

      <AssignForm templates={templates} onAssigned={() => setAssignmentsRefresh((k) => k + 1)} />

      <AssignmentsSection refreshKey={assignmentsRefresh} />
    </PageShell>
  );
}
