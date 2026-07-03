import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import {
  addRequirement,
  deactivateRequirement,
  editRequirement,
  fetchDailyRequirements,
  type DailyRequirementItem,
  type DailyRequirementKind,
  type RequirementInput,
} from "../lib/fieldops_daily_requirements";
import { ConfirmDelete, nextSeq, planRenumber } from "./ChecklistItemForm";
import { SectionError, errMsg } from "./myTasksShared";
import { formCatalog } from "../forms/registry";

// ─────────────────────────────────────────────────────────────────────────────
// SOP daily form slice D4 — the "Daily form — job requirements" admin editor, mounted on the Job
// Tracker JOB DETAIL (its natural per-job home; cap.checklist.manage — the Worker re-gates every
// call). Self-contained on purpose (the parallel-build rule): one section component + one mount
// line in FieldOpsJobTracker.
//
// What it edits: the job's ADDITIVE requirement overlay (D1 job_daily_requirements, migration
// 0030) — the items the Daily Report form renders in its "Job-specific requirements" section for
// every manager placed on this job. Kinds: note (read-only guidance) / confirm (checkbox) / text
// (fill-in answer) / form_link (a Create-form deep link; catalog form picker, daily-tab parents
// excluded — same rule as ChecklistItemForm). Reorder = seq re-writes through the edit route
// (planRenumber, the shared 10/20/30 convention); remove = ConfirmDelete-gated DEACTIVATE (soft
// delete — historical submissions keep their self-describing filed answers).
//
// Never-silent: load failure → SectionError + Retry; every action failure lands in an inline
// banner with the controls re-enabled.
// ─────────────────────────────────────────────────────────────────────────────

export const REQUIREMENT_KINDS: DailyRequirementKind[] = ["note", "confirm", "text", "form_link"];

const KIND_LABELS: Record<DailyRequirementKind, string> = {
  note: "Note",
  confirm: "Confirm",
  text: "Text answer",
  form_link: "Form link",
};

// One-line "what does this kind do" helper, shown under the kind select (the ChecklistItemForm
// TYPE_HELP convention).
const KIND_HELP: Record<DailyRequirementKind, string> = {
  note: "Note — read-only guidance shown inside the daily form (no answer).",
  confirm: "Confirm — a checkbox the manager checks; files as “Confirmed”.",
  text: "Text answer — the manager types a short answer.",
  form_link: "Form link — a “Create <form> →” button that deep-links to the picked form type.",
};

/** Human label for a kind (exported for the row meta line + tests). */
export function requirementKindLabel(kind: string): string {
  return KIND_LABELS[kind as DailyRequirementKind] ?? kind;
}

const EMPTY_DRAFT: RequirementInput = { kind: "note", label: "" };

/** Rebuild the full write payload from a stored row (edit prefill / reorder re-writes) — the edit
 *  route REPLACES every field, so a partial payload would clobber. */
function requirementInputFromRow(row: DailyRequirementItem): RequirementInput {
  const input: RequirementInput = { kind: row.kind, label: row.label, seq: row.seq };
  if (row.form_code !== null) input.form_code = row.form_code;
  return input;
}

/** The shared add/edit requirement form (kind select with human labels + helper, label input,
 *  catalog form picker for form_link — daily-tab parents excluded). */
function RequirementForm({
  label,
  draft,
  onChange,
  onSubmit,
  busy,
  submitLabel,
  onCancel,
}: {
  label: string;
  draft: RequirementInput;
  onChange: (next: RequirementInput) => void;
  onSubmit: (e: FormEvent) => void;
  busy: boolean;
  submitLabel: string;
  onCancel?: () => void;
}) {
  const set = (patch: Partial<RequirementInput>) => onChange({ ...draft, ...patch });
  // Tab-launched parents (launch:"daily-tab" — the daily form itself) are excluded: a requirement
  // deep-linking the daily form back into itself would be circular (the Worker 422s it too).
  const catalog = formCatalog().filter((p) => p.launch !== "daily-tab");
  // A stored code that fell out of the catalog stays selectable + marked, never silently swapped.
  const orphanCode =
    draft.form_code && !catalog.some((p) => p.parent_form_code === draft.form_code) ? draft.form_code : null;
  return (
    <form onSubmit={onSubmit} className="dash-row" aria-label={label}>
      <input
        aria-label={`${label} label`}
        value={draft.label}
        onChange={(e) => set({ label: e.target.value })}
        placeholder="Requirement text"
        maxLength={256}
      />{" "}
      <select
        aria-label={`${label} kind`}
        value={draft.kind}
        onChange={(e) => set({ kind: e.target.value as DailyRequirementKind })}
      >
        {REQUIREMENT_KINDS.map((k) => (
          <option key={k} value={k}>{KIND_LABELS[k]}</option>
        ))}
      </select>{" "}
      {draft.kind === "form_link" && (
        <select
          aria-label={`${label} form code`}
          value={draft.form_code ?? ""}
          onChange={(e) => set({ form_code: e.target.value || undefined })}
        >
          <option value="">— pick a form —</option>
          {orphanCode !== null && <option value={orphanCode}>{orphanCode} (not in catalog)</option>}
          {catalog.map((p) => (
            <option key={p.parent_form_code} value={p.parent_form_code}>{p.name}</option>
          ))}
        </select>
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
        {KIND_HELP[draft.kind]}
      </span>
    </form>
  );
}

export function JobDailyRequirementsSection({ jobId }: { jobId: string }) {
  const [items, setItems] = useState<DailyRequirementItem[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);

  const [addDraft, setAddDraft] = useState<RequirementInput>(EMPTY_DRAFT);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [editDraft, setEditDraft] = useState<RequirementInput>(EMPTY_DRAFT);

  async function load() {
    setLoadError(null);
    try {
      setItems(await fetchDailyRequirements(jobId));
    } catch (err) {
      setLoadError(errMsg(err, "Could not load this job's daily-form requirements."));
    }
  }

  useEffect(() => {
    setItems(null);
    setEditingId(null);
    setAddDraft(EMPTY_DRAFT);
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [jobId]);

  // Run one mutation with the shared busy/error/reload discipline.
  async function run(action: () => Promise<unknown>) {
    setBusy(true);
    setActionError(null);
    try {
      await action();
      await load();
      return true;
    } catch (err) {
      setActionError(errMsg(err, "That change didn't save — try again."));
      return false;
    } finally {
      setBusy(false);
    }
  }

  async function submitAdd(e: FormEvent) {
    e.preventDefault();
    if (!addDraft.label.trim()) return;
    const ok = await run(() => addRequirement(jobId, { ...addDraft, seq: nextSeq(items ?? []) }));
    if (ok) setAddDraft(EMPTY_DRAFT);
  }

  async function submitEdit(e: FormEvent) {
    e.preventDefault();
    if (editingId === null || !editDraft.label.trim()) return;
    const ok = await run(() => editRequirement(jobId, editingId, editDraft));
    if (ok) setEditingId(null);
  }

  // Reorder = seq re-writes through the edit route (only the rows whose seq changes are written).
  async function move(index: number, dir: -1 | 1) {
    if (!items) return;
    const changes = planRenumber(items, index, dir);
    if (changes.length === 0) return;
    await run(async () => {
      for (const ch of changes) {
        await editRequirement(jobId, ch.row.id, { ...requirementInputFromRow(ch.row), seq: ch.seq });
      }
    });
  }

  return (
    <section className="card dash-section" aria-label="Daily form job requirements">
      <h3 className="dash-detail__h2">Daily form — job requirements</h3>
      <p className="dash-card__sub muted">
        Job-specific items (client requirements) rendered inside every Daily Report filed for this
        job. The base daily form is edited in the form builder; these items are additive and file
        with each submission.
      </p>

      {loadError && <SectionError message={loadError} onRetry={() => void load()} what="loading job requirements" />}
      {actionError && <p className="banner banner--err" role="alert">{actionError}</p>}

      {items && items.length === 0 && <div className="dash-unavail">No job-specific requirements yet.</div>}
      {items && items.length > 0 && (
        <ul className="dash-tasklist" aria-label="Job requirement items">
          {items.map((it, i) =>
            editingId === it.id ? (
              <li key={it.id} className="dash-row">
                <RequirementForm
                  label="Edit requirement"
                  draft={editDraft}
                  onChange={setEditDraft}
                  onSubmit={(e) => void submitEdit(e)}
                  busy={busy}
                  submitLabel="Save"
                  onCancel={() => setEditingId(null)}
                />
              </li>
            ) : (
              <li key={it.id} className="dash-row">
                <span>{it.label}</span>{" "}
                <span className="dash-card__sub muted">
                  {requirementKindLabel(it.kind)}
                  {it.kind === "form_link" && it.form_code
                    ? ` · ${formCatalog().find((p) => p.parent_form_code === it.form_code)?.name ?? it.form_code}`
                    : ""}
                </span>{" "}
                <button
                  type="button"
                  className="btn--secondary"
                  aria-label={`Move requirement ${it.label} up`}
                  disabled={busy || i === 0}
                  onClick={() => void move(i, -1)}
                >
                  ↑
                </button>{" "}
                <button
                  type="button"
                  className="btn--secondary"
                  aria-label={`Move requirement ${it.label} down`}
                  disabled={busy || i === items.length - 1}
                  onClick={() => void move(i, 1)}
                >
                  ↓
                </button>{" "}
                <button
                  type="button"
                  className="btn--edit"
                  aria-label={`Edit requirement ${it.label}`}
                  disabled={busy}
                  onClick={() => {
                    setEditingId(it.id);
                    setEditDraft(requirementInputFromRow(it));
                  }}
                >
                  Edit
                </button>{" "}
                <ConfirmDelete
                  actionLabel="Remove"
                  ariaLabel={`Remove requirement ${it.label}`}
                  copy={`Remove “${it.label}” from this job's daily form? Already-filed reports keep their answers.`}
                  busy={busy}
                  onConfirm={() => void run(() => deactivateRequirement(jobId, it.id))}
                />
              </li>
            ),
          )}
        </ul>
      )}

      <RequirementForm
        label="Add requirement"
        draft={addDraft}
        onChange={setAddDraft}
        onSubmit={(e) => void submitAdd(e)}
        busy={busy || items === null}
        submitLabel="Add requirement"
      />
    </section>
  );
}
