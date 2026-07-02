import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import * as checklist from "../lib/fieldops_checklist";
import { fetchPersonnelList, type PersonnelRow } from "../lib/fieldops_personnel";
import { fetchJobList, type JobRow } from "../lib/fieldops_jobtracker";
import { PageShell } from "../components/PageShell";

// Assigned-Tasks tab (P4 field-ops feature) S6 — the admin Inspection-checklists LIBRARY + assign UI.
// Gated cap.checklist.manage (admin). The FOURTH consumer of the one checklist engine (spec Q6/Q8):
// admins author generic_inspection templates (title + items) and ASSIGN one to a manager/subcontractor
// ad-hoc → an inspection instance that surfaces in the assignee's My-Tasks tab. Every call is re-gated
// server-side (Invariant 2); the cap here drives UI affordances only. Send-free (D1 reads/writes).

const ITEM_TYPES: checklist.ChecklistItemType[] = ["form_linked", "manual_attest", "count", "inspection"];
const EMPTY_ITEM: checklist.ItemInput = { item_type: "manual_attest", label: "" };

// Short human summary of an item's type-specific payload (mirrors the Job Tracker editor).
function itemMeta(item: { item_type: string; form_code: string | null; target_count: number | null }): string {
  if (item.item_type === "form_linked" || item.item_type === "inspection") return `${item.item_type} · ${item.form_code ?? "—"}`;
  if (item.item_type === "count") return `count ≥ ${item.target_count ?? "?"}`;
  return item.item_type;
}

// A reusable add/edit checklist-item form (label + type, plus form_code or target_count per type).
// Self-contained so this page doesn't couple to the Job Tracker module.
function ItemForm({
  label,
  draft,
  onChange,
  onSubmit,
  busy,
  submitLabel,
}: {
  label: string;
  draft: checklist.ItemInput;
  onChange: (next: checklist.ItemInput) => void;
  onSubmit: (e: FormEvent) => void;
  busy: boolean;
  submitLabel: string;
}) {
  const set = (patch: Partial<checklist.ItemInput>) => onChange({ ...draft, ...patch });
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
          <option key={t} value={t}>{t}</option>
        ))}
      </select>{" "}
      {(draft.item_type === "form_linked" || draft.item_type === "inspection") && (
        <input
          aria-label={`${label} form code`}
          value={draft.form_code ?? ""}
          onChange={(e) => set({ form_code: e.target.value })}
          placeholder="Form code"
          maxLength={64}
        />
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
      <button type="submit" disabled={busy} className="btn btn--primary">{submitLabel}</button>
    </form>
  );
}

// The item editor for one selected library template (add / delete items on it).
function TemplateItemsEditor({
  templateId,
  onMessage,
}: {
  templateId: number;
  onMessage: (m: { ok: boolean; text: string }) => void;
}) {
  const [detail, setDetail] = useState<checklist.InspectionDetail | null>(null);
  const [draft, setDraft] = useState<checklist.ItemInput>(EMPTY_ITEM);
  const [busy, setBusy] = useState(false);

  async function reload() {
    try {
      setDetail(await checklist.fetchInspectionTemplate(templateId));
    } catch {
      onMessage({ ok: false, text: "Could not load the template." });
    }
  }
  useEffect(() => {
    void reload();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [templateId]);

  async function addItem(e: FormEvent) {
    e.preventDefault();
    if (busy) return;
    if (draft.label.trim() === "") {
      onMessage({ ok: false, text: "Item label is required." });
      return;
    }
    setBusy(true);
    try {
      await checklist.addInspectionItem(templateId, draft);
      setDraft(EMPTY_ITEM);
      await reload();
      onMessage({ ok: true, text: "Item added." });
    } catch (err) {
      onMessage({ ok: false, text: err instanceof Error ? err.message : "Add failed." });
    } finally {
      setBusy(false);
    }
  }

  async function removeItem(itemId: number) {
    if (busy) return;
    setBusy(true);
    try {
      await checklist.deleteInspectionItem(templateId, itemId);
      await reload();
      onMessage({ ok: true, text: "Item removed." });
    } catch (err) {
      onMessage({ ok: false, text: err instanceof Error ? err.message : "Remove failed." });
    } finally {
      setBusy(false);
    }
  }

  if (!detail) return <div className="muted">Loading items…</div>;
  return (
    <div className="dash-subsection">
      <h4 className="dash-detail__h2">Items · {detail.template.title}</h4>
      {detail.items.length === 0 ? (
        <div className="muted">No items yet — add one below.</div>
      ) : (
        <ul className="dash-tasklist">
          {detail.items.map((it) => (
            <li key={it.id}>
              {it.label} <span className="dash-card__sub"> · {itemMeta(it)}</span>{" "}
              <button
                type="button"
                className="btn btn--danger"
                aria-label={`Remove item ${it.id}`}
                disabled={busy}
                onClick={() => removeItem(it.id)}
              >
                Remove
              </button>
            </li>
          ))}
        </ul>
      )}
      <ItemForm label="Add item" draft={draft} onChange={setDraft} onSubmit={addItem} busy={busy} submitLabel="Add item" />
    </div>
  );
}

// The assign control: pick a template + a person (+ optional job + due date) → POST /assign.
function AssignForm({
  templates,
  onMessage,
}: {
  templates: checklist.InspectionTemplate[];
  onMessage: (m: { ok: boolean; text: string }) => void;
}) {
  const [people, setPeople] = useState<PersonnelRow[]>([]);
  const [jobs, setJobs] = useState<JobRow[]>([]);
  const [templateId, setTemplateId] = useState<string>("");
  const [assignee, setAssignee] = useState<string>("");
  const [jobId, setJobId] = useState<string>("");
  const [dueDate, setDueDate] = useState<string>("");
  const [busy, setBusy] = useState(false);

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
      onMessage({ ok: false, text: "Pick a checklist." });
      return;
    }
    if (!Number.isInteger(aid) || aid <= 0) {
      onMessage({ ok: false, text: "Pick a person." });
      return;
    }
    setBusy(true);
    try {
      const input: checklist.AssignInput = { template_id: tid, assignee_personnel_id: aid };
      if (jobId) input.job_id = jobId;
      if (dueDate) input.due_date = dueDate;
      const res = await checklist.assignInspection(input);
      onMessage({ ok: true, text: `Assigned (${res.item_count} item${res.item_count === 1 ? "" : "s"}).` });
      setDueDate("");
    } catch (err) {
      const text = err instanceof Error ? err.message : "Assign failed.";
      onMessage({ ok: false, text: text === "already_assigned" ? "That checklist is already assigned for this job + date." : text });
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="card dash-section" aria-label="Assign an inspection">
      <h3 className="dash-detail__h2">Assign an inspection checklist</h3>
      <form onSubmit={submit} className="dash-row" aria-label="Assign form">
        <select aria-label="Checklist" value={templateId} onChange={(e) => setTemplateId(e.target.value)}>
          <option value="">— checklist —</option>
          {templates.filter((t) => t.active).map((t) => (
            <option key={t.id} value={t.id}>{t.title}</option>
          ))}
        </select>{" "}
        <select aria-label="Assignee" value={assignee} onChange={(e) => setAssignee(e.target.value)}>
          <option value="">— person —</option>
          {people.map((p) => (
            <option key={p.id} value={p.id}>{p.name}{p.trade ? ` (${p.trade})` : ""}</option>
          ))}
        </select>{" "}
        <select aria-label="Job (optional)" value={jobId} onChange={(e) => setJobId(e.target.value)}>
          <option value="">— job (optional) —</option>
          {jobs.map((j) => (
            <option key={j.job_id} value={j.job_id}>{j.project_name ?? j.job_id}</option>
          ))}
        </select>{" "}
        <input
          type="date"
          aria-label="Due date (optional)"
          value={dueDate}
          onChange={(e) => setDueDate(e.target.value)}
        />{" "}
        <button type="submit" className="btn btn--primary" disabled={busy}>Assign</button>
      </form>
    </section>
  );
}

/**
 * Assigned-Tasks tab (P4 S6) — the admin Inspection-checklists library. Author generic_inspection
 * templates (title + items) and assign them to a manager/subcontractor. cap.checklist.manage gates the
 * page card + every call (the Worker re-gates — Invariant 2).
 */
export function FieldOpsInspections({ onBack }: { onBack: () => void }) {
  const [templates, setTemplates] = useState<checklist.InspectionTemplate[]>([]);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [newTitle, setNewTitle] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);

  async function reload() {
    try {
      const { templates: t } = await checklist.fetchInspectionTemplates();
      setTemplates(t);
    } catch {
      setMsg({ ok: false, text: "Could not load the inspection library." });
    }
  }
  useEffect(() => {
    void reload();
  }, []);

  async function create(e: FormEvent) {
    e.preventDefault();
    if (busy) return;
    if (newTitle.trim() === "") {
      setMsg({ ok: false, text: "A title is required." });
      return;
    }
    setBusy(true);
    try {
      const res = await checklist.createInspectionTemplate(newTitle.trim());
      setNewTitle("");
      await reload();
      if (res.id) setSelectedId(res.id);
      setMsg({ ok: true, text: "Checklist created." });
    } catch (err) {
      setMsg({ ok: false, text: err instanceof Error ? err.message : "Create failed." });
    } finally {
      setBusy(false);
    }
  }

  async function remove(templateId: number) {
    if (busy) return;
    setBusy(true);
    try {
      await checklist.deleteInspectionTemplate(templateId);
      if (selectedId === templateId) setSelectedId(null);
      await reload();
      setMsg({ ok: true, text: "Checklist deleted." });
    } catch (err) {
      setMsg({ ok: false, text: err instanceof Error ? err.message : "Delete failed." });
    } finally {
      setBusy(false);
    }
  }

  return (
    <PageShell onHome={onBack}>
      <h2 className="page__heading">Inspection checklists</h2>
      <p className="dash__intro">
        Author a library of inspection checklists, then assign one to a manager or subcontractor. Assigned
        checklists appear in that person's My Tasks tab.
      </p>
      {msg && <div className={`banner ${msg.ok ? "banner--ok" : "banner--err"}`}>{msg.text}</div>}

      <section className="card dash-section" aria-label="Inspection library">
        <h3 className="dash-detail__h2">Library</h3>
        {templates.length === 0 ? (
          <div className="muted">No inspection checklists yet — create one below.</div>
        ) : (
          <ul className="dash-tasklist">
            {templates.map((t) => (
              <li key={t.id}>
                <button
                  type="button"
                  className={selectedId === t.id ? "btn btn--primary" : "btn btn--secondary"}
                  aria-label={`Edit ${t.title}`}
                  onClick={() => setSelectedId(selectedId === t.id ? null : t.id)}
                >
                  {t.title}
                </button>
                <span className="dash-card__sub"> · {t.item_count} item{t.item_count === 1 ? "" : "s"}</span>{" "}
                <button
                  type="button"
                  className="btn btn--danger"
                  aria-label={`Delete ${t.title}`}
                  disabled={busy}
                  onClick={() => remove(t.id)}
                >
                  Delete
                </button>
              </li>
            ))}
          </ul>
        )}
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
      </section>

      {selectedId !== null && (
        <section className="card dash-section" aria-label="Edit checklist items">
          <TemplateItemsEditor templateId={selectedId} onMessage={setMsg} />
        </section>
      )}

      <AssignForm templates={templates} onMessage={setMsg} />
    </PageShell>
  );
}
