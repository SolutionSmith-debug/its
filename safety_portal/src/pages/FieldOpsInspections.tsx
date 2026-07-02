import { useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";
import * as checklist from "../lib/fieldops_checklist";
import { fetchPersonnelList, type PersonnelRow } from "../lib/fieldops_personnel";
import { fetchJobList, type JobRow } from "../lib/fieldops_jobtracker";
import { PageShell } from "../components/PageShell";
import { ChecklistItemRow } from "../components/ChecklistItemRow";
import { ChecklistItemForm,
  EMPTY_ITEM,
  itemInputFromRow,
  itemMetaLabel,
  nextSeq,
  planRenumber, ConfirmDelete } from "../components/ChecklistItemForm";

// R4 — the consolidated admin "Checklists" area (same 'fieldops-inspections' view key / Home card).
// ONE surface owns both checklist kinds (spec Q4):
//   1. the company-wide DEFAULT daily checklist (full add/edit/reorder/delete — moved here from the
//      Job Tracker job detail, which keeps only the per-job tailoring: add-for-this-job / hide / unhide);
//   2. the generic_inspection LIBRARY (create / rename / deactivate / delete / per-template item
//      editing) + the assign control.
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

// ── Section 1 — the company-wide default daily checklist (full CRUD, moved from Job Tracker) ──────
function DefaultChecklistSection() {
  const [def, setDef] = useState<checklist.DefaultChecklist | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<Msg | null>(null);
  const [addDraft, setAddDraft] = useState<checklist.ItemInput>(EMPTY_ITEM);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [editDraft, setEditDraft] = useState<checklist.ItemInput>(EMPTY_ITEM);

  async function reload() {
    try {
      setDef(await checklist.fetchDefaultChecklist());
    } catch {
      setMsg({ ok: false, text: "Could not load the default checklist." });
    } finally {
      setLoading(false);
    }
  }
  useEffect(() => {
    void reload();
    // eslint-disable-next-line react-hooks/exhaustive-deps
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

  const items = def?.items ?? [];

  function submitAdd(e: FormEvent) {
    e.preventDefault();
    if (!addDraft.label.trim()) {
      setMsg({ ok: false, text: "Item label is required." });
      return;
    }
    void run(async () => {
      await checklist.addDefaultItem({ ...addDraft, seq: addDraft.seq ?? nextSeq(items) });
      setAddDraft(EMPTY_ITEM);
    }, "Default item added — every job's checklist picks it up tomorrow.");
  }

  function startEdit(it: checklist.DefaultItem) {
    setEditingId(it.id);
    setEditDraft(itemInputFromRow(it)); // carries the row's seq — the edit route replaces EVERY field
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
      await checklist.editDefaultItem(id, editDraft);
      setEditingId(null);
    }, "Default item updated.");
  }

  function move(index: number, dir: -1 | 1) {
    const plan = planRenumber(items, index, dir);
    if (plan.length === 0) return;
    void run(async () => {
      for (const p of plan) {
        await checklist.editDefaultItem(p.row.id, { ...itemInputFromRow(p.row), seq: p.seq });
      }
    }, "Order updated.");
  }

  return (
    <section className="card dash-section" aria-label="Default daily checklist">
      <h3 className="dash-detail__h2">Default daily checklist</h3>
      <p className="dash-card__sub muted">
        The company-wide checklist every placed manager gets each day. Changes here take effect
        tomorrow — today's already-generated checklists keep their snapshot. Per-job tailoring
        (add an item for one job, hide a shared item) lives in Job Tracker → job detail.
      </p>
      <MsgLine msg={msg} />

      {loading ? (
        <div className="muted">Loading default checklist…</div>
      ) : items.length === 0 ? (
        <div className="dash-unavail">No default items yet — add the first one below.</div>
      ) : (
        <ul className="dash-tasklist" aria-label="Default checklist items">
          {items.map((it, idx) => (
            <li key={it.id}>
              {editingId === it.id ? (
                <ChecklistItemForm
                  label="Edit default item"
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
                    actionLabel="Remove from all jobs"
                    ariaLabel={`Delete default ${it.label ?? `item ${it.id}`}`}
                    copy={`Delete “${it.label ?? `item ${it.id}`}”? This removes it from EVERY job's daily checklist starting tomorrow.`}
                    busy={busy}
                    onConfirm={() => run(() => checklist.deleteDefaultItem(it.id), "Default item deleted.")}
                  />
                </>
              )}
            </li>
          ))}
        </ul>
      )}

      {!loading && (
        <ChecklistItemForm
          label="Add default item"
          draft={addDraft}
          onChange={setAddDraft}
          onSubmit={submitAdd}
          busy={busy}
          submitLabel="Add to default"
        />
      )}
    </section>
  );
}

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

// ── Section 3 — the assign control (existing routes; the guarded flow + assignments list are R5) ──
function AssignForm({
  templates,
  onDone,
}: {
  templates: checklist.InspectionTemplate[];
  onDone?: () => void;
}) {
  const [people, setPeople] = useState<PersonnelRow[]>([]);
  const [jobs, setJobs] = useState<JobRow[]>([]);
  const [templateId, setTemplateId] = useState<string>("");
  const [assignee, setAssignee] = useState<string>("");
  const [jobId, setJobId] = useState<string>("");
  const [dueDate, setDueDate] = useState<string>("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<Msg | null>(null);

  useEffect(() => {
    // Only offer login-linked people: an assigned inspection surfaces via the session→personnel link,
    // so a non-login roster person could never see or complete one.
    fetchPersonnelList()
      .then((r) => setPeople(r.personnel.filter((p) => p.username !== null)))
      .catch(() => setPeople([]));
    fetchJobList("active")
      .then((r) => setJobs(r.jobs))
      .catch(() => setJobs([]));
  }, []);

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
    setBusy(true);
    setMsg(null);
    try {
      const input: checklist.AssignInput = { template_id: tid, assignee_personnel_id: aid };
      if (jobId) input.job_id = jobId;
      if (dueDate) input.due_date = dueDate;
      const res = await checklist.assignInspection(input);
      setMsg({ ok: true, text: `Assigned (${res.item_count} item${res.item_count === 1 ? "" : "s"}).` });
      setDueDate("");
      onDone?.();
    } catch (err) {
      // R1: user copy comes from errorCopy.ts via err.message — never duplicated in pages.
      setMsg({ ok: false, text: err instanceof Error ? err.message : "Assign failed." });
    } finally {
      setBusy(false);
    }
  }

  // Inactive templates are retired — not assignable, so not offered.
  const assignable = templates.filter((t) => t.active);

  return (
    <section className="card dash-section" aria-label="Assign an inspection">
      <h3 className="dash-detail__h2">Assign an inspection checklist</h3>
      <MsgLine msg={msg} />
      <form onSubmit={submit} className="dash-row" aria-label="Assign form">
        <label className="field">
          <span className="field__label">Checklist</span>
          <select aria-label="Checklist" value={templateId} onChange={(e) => setTemplateId(e.target.value)}>
            <option value="">— checklist —</option>
            {assignable.map((t) => (
              <option key={t.id} value={t.id}>{t.title}</option>
            ))}
          </select>
        </label>{" "}
        <label className="field">
          <span className="field__label">Assign to</span>
          <select aria-label="Assignee" value={assignee} onChange={(e) => setAssignee(e.target.value)}>
            <option value="">— person —</option>
            {people.map((p) => (
              <option key={p.id} value={p.id}>{p.name}{p.trade ? ` (${p.trade})` : ""}</option>
            ))}
          </select>
        </label>{" "}
        <label className="field">
          <span className="field__label">Job (optional)</span>
          <select aria-label="Job (optional)" value={jobId} onChange={(e) => setJobId(e.target.value)}>
            <option value="">— job (optional) —</option>
            {jobs.map((j) => (
              <option key={j.job_id} value={j.job_id}>{j.project_name ?? j.job_id}</option>
            ))}
          </select>
        </label>{" "}
        <label className="field">
          <span className="field__label">Due date (optional)</span>
          <input
            type="date"
            aria-label="Due date (optional)"
            value={dueDate}
            onChange={(e) => setDueDate(e.target.value)}
          />
        </label>{" "}
        <button type="submit" className="btn btn--primary" disabled={busy}>Assign</button>
      </form>
    </section>
  );
}

/**
 * R4 — the consolidated admin "Checklists" page (view key 'fieldops-inspections' unchanged).
 * Two clearly-headed areas: the company-wide Default daily checklist (full CRUD, extracted from the
 * Job Tracker job detail) and the Inspection-checklists library (author / rename / deactivate /
 * delete / per-template items) + the assign control. cap.checklist.manage gates the Home card +
 * every call (the Worker re-gates — Invariant 2).
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
        One place for both checklist kinds: the shared <strong>default daily checklist</strong> every
        placed manager gets, and the <strong>inspection checklists</strong> you author and assign to a
        manager or subcontractor (they appear in that person's My Tasks tab). Per-job tailoring of the
        daily checklist lives in Job Tracker → job detail.
      </p>

      <DefaultChecklistSection />

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

      <AssignForm templates={templates} />
    </PageShell>
  );
}
