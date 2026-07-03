import { useCallback, useEffect, useState } from "react";
import type { FormEvent } from "react";
import * as api from "../lib/fieldops_expected_materials";
import { fetchMaterials, type CatalogRow } from "../lib/fieldops_materials";
import { useAuth } from "../lib/auth";
import { ConfirmDelete, planRenumber, nextSeq } from "./ChecklistItemForm";

// Material receipts (M1) — the Job Tracker job-detail "Expected materials" section. SELF-CONTAINED
// on purpose (one component + one mount line in FieldOpsJobTracker — the D4 parallel-build rule):
// it resolves its own caps via useAuth and owns all of its fetch/mutation state.
//   • cap.materials.manage (admin/office): full CRUD — add an expectation from the material
//     catalog OR as free text, qty/unit/expected-date, inline edit (expected rows only — a
//     received/incident row is a receipt record), ▲/▼ reorder (the checklist planRenumber
//     convention via the seq route), ConfirmDelete deactivate.
//   • cap.materials.receive WITHOUT manage (manager/submitter): the READ-ONLY list with status
//     pills — their receive ACTION arrives in M2 through the daily form (the note says so).
//   • Neither cap → renders nothing.
// Never-silent: loading, load-failure-with-Retry, explicit empty state, and a visible
// catalog-picker failure that leaves the free-text add path working. The Worker re-gates and
// ownership-scopes every call — caps here drive affordances only.

function fmtDateTime(epochSeconds: number | null): string {
  if (!epochSeconds) return "—";
  return new Date(epochSeconds * 1000).toLocaleString();
}

function statusPill(status: api.ExpectedMaterialStatus): { className: string; label: string } {
  if (status === "received") return { className: "dash-pill dash-pill--ok", label: "Received" };
  if (status === "incident") return { className: "dash-pill dash-pill--danger", label: "Incident" };
  return { className: "dash-pill", label: "Expected" };
}

/** Row title: the resolved catalog name for catalog rows, the free text otherwise. */
function rowTitle(r: api.ExpectedMaterialRow): string {
  return r.material_name ?? r.description ?? "—";
}

type FormState = { source: "catalog" | "custom"; materialId: string; description: string; qty: string; unit: string; expectedDate: string };
const EMPTY_FORM: FormState = { source: "catalog", materialId: "", description: "", qty: "", unit: "", expectedDate: "" };

// Form state → wire fields, or an error string the banner shows (client half of the Worker's 400s).
function toFields(f: FormState): api.ExpectedMaterialFields | string {
  const out: api.ExpectedMaterialFields = {};
  if (f.source === "catalog") {
    const id = Number(f.materialId);
    if (f.materialId === "" || !Number.isInteger(id)) return "Pick a material from the catalog.";
    out.material_id = id;
    if (f.description.trim()) out.description = f.description.trim();
  } else {
    if (!f.description.trim()) return "A description is required for a custom material.";
    out.description = f.description.trim();
  }
  if (f.qty.trim() !== "") {
    const n = Number(f.qty);
    if (!Number.isFinite(n) || n <= 0) return "Quantity must be a number greater than 0.";
    out.qty = n;
  }
  if (f.unit.trim()) out.unit = f.unit.trim();
  if (f.expectedDate.trim()) out.expected_date = f.expectedDate.trim();
  return out;
}

function formFromRow(r: api.ExpectedMaterialRow): FormState {
  return {
    source: r.material_id !== null ? "catalog" : "custom",
    materialId: r.material_id !== null ? String(r.material_id) : "",
    description: r.description ?? "",
    qty: r.qty == null ? "" : String(r.qty),
    unit: r.unit ?? "",
    expectedDate: r.expected_date ?? "",
  };
}

// The shared add/edit expectation form (catalog-pick OR free-text). `label` prefixes aria-labels —
// keep it unique per mounted instance (the ChecklistItemForm convention).
function ExpectationForm({
  label,
  draft,
  onChange,
  onSubmit,
  busy,
  submitLabel,
  onCancel,
  catalog,
}: {
  label: string;
  draft: FormState;
  onChange: (next: FormState) => void;
  onSubmit: (e: FormEvent) => void;
  busy: boolean;
  submitLabel: string;
  onCancel?: () => void;
  catalog: CatalogRow[];
}) {
  const set = (patch: Partial<FormState>) => onChange({ ...draft, ...patch });
  // An edited row whose stored material fell out of the loaded catalog (retired) still shows it —
  // marked, never silently swapped (the ChecklistItemForm orphan convention).
  const orphanId =
    draft.materialId !== "" && !catalog.some((m) => String(m.id) === draft.materialId) ? draft.materialId : null;
  return (
    <form onSubmit={onSubmit} className="dash-row" aria-label={label}>
      <select
        aria-label={`${label} source`}
        value={draft.source}
        onChange={(e) => set({ source: e.target.value as FormState["source"] })}
      >
        <option value="catalog">From catalog</option>
        <option value="custom">Custom (free text)</option>
      </select>{" "}
      {draft.source === "catalog" && (
        <select
          aria-label={`${label} material`}
          value={draft.materialId}
          onChange={(e) => set({ materialId: e.target.value })}
        >
          <option value="">— pick a material —</option>
          {orphanId !== null && <option value={orphanId}>#{orphanId} (not in catalog)</option>}
          {catalog.map((m) => (
            <option key={m.id} value={m.id}>
              {m.model_id}
              {m.manufacturer ? ` · ${m.manufacturer}` : ""}
            </option>
          ))}
        </select>
      )}{" "}
      <input
        aria-label={`${label} description`}
        value={draft.description}
        onChange={(e) => set({ description: e.target.value })}
        placeholder={draft.source === "catalog" ? "Note (optional)" : "Description (required)"}
        maxLength={256}
      />{" "}
      <input
        aria-label={`${label} quantity`}
        value={draft.qty}
        onChange={(e) => set({ qty: e.target.value })}
        placeholder="Qty"
        inputMode="decimal"
        size={6}
      />{" "}
      <input
        aria-label={`${label} unit`}
        value={draft.unit}
        onChange={(e) => set({ unit: e.target.value })}
        placeholder="Unit"
        maxLength={32}
        size={8}
      />{" "}
      <input
        aria-label={`${label} expected date`}
        type="date"
        value={draft.expectedDate}
        onChange={(e) => set({ expectedDate: e.target.value })}
      />{" "}
      <button type="submit" disabled={busy} className="btn btn--primary">{submitLabel}</button>
      {onCancel && (
        <>
          {" "}
          <button type="button" className="btn btn--secondary" aria-label={`${label} cancel`} onClick={onCancel}>
            Cancel
          </button>
        </>
      )}
    </form>
  );
}

export function ExpectedMaterialsSection({ jobId }: { jobId: string }) {
  const { user } = useAuth();
  const caps = user?.capabilities ?? [];
  const canManage = caps.includes("cap.materials.manage"); // UI affordance only — the Worker re-gates
  const canReceive = caps.includes("cap.materials.receive");

  const [rows, setRows] = useState<api.ExpectedMaterialRow[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);

  // Catalog picker options (manage only). A load failure is SAID (never a silently-empty picker)
  // and the free-text add path keeps working.
  const [catalog, setCatalog] = useState<CatalogRow[]>([]);
  const [catalogError, setCatalogError] = useState<string | null>(null);

  const [addOpen, setAddOpen] = useState(false);
  const [addForm, setAddForm] = useState<FormState>({ ...EMPTY_FORM });
  const [editId, setEditId] = useState<number | null>(null);
  const [editForm, setEditForm] = useState<FormState>({ ...EMPTY_FORM });

  const visible = canManage || canReceive;

  const reload = useCallback(() => {
    setLoadError(null);
    api
      .fetchExpectedMaterials(jobId)
      .then((d) => setRows(d.expected_materials))
      .catch(() => setLoadError("Failed to load expected materials."));
  }, [jobId]);

  useEffect(() => {
    if (!visible) return;
    setRows(null);
    reload();
  }, [visible, reload]);

  const loadCatalog = useCallback(async () => {
    setCatalogError(null);
    try {
      // Collect the active catalog across pages (36 seeded types today; cap the walk defensively).
      const all: CatalogRow[] = [];
      let cursor: string | undefined;
      for (let page = 0; page < 5; page++) {
        const d = await fetchMaterials(cursor ? { cursor } : undefined);
        all.push(...d.materials);
        if (!d.next_cursor) break;
        cursor = d.next_cursor;
      }
      setCatalog(all);
    } catch {
      setCatalogError("Couldn't load the material catalog — the custom (free-text) add still works.");
    }
  }, []);

  useEffect(() => {
    if (visible && canManage) void loadCatalog();
  }, [visible, canManage, loadCatalog]);

  if (!visible) return null;

  async function runMutation(okText: string, failText: string, fn: () => Promise<void>) {
    if (busy) return;
    setBusy(true);
    setMsg(null);
    try {
      await fn();
    } catch (err) {
      setMsg({ ok: false, text: err instanceof Error ? err.message : failText });
      setBusy(false);
      return;
    }
    setMsg({ ok: true, text: okText });
    reload();
    setBusy(false);
  }

  function submitAdd(e: FormEvent) {
    e.preventDefault();
    const fields = toFields(addForm);
    if (typeof fields === "string") {
      setMsg({ ok: false, text: fields });
      return;
    }
    void runMutation("Expected material added.", "Add failed.", async () => {
      await api.createExpectedMaterial(jobId, { ...fields, seq: nextSeq(rows ?? []) });
      setAddForm({ ...EMPTY_FORM });
      setAddOpen(false);
    });
  }

  function submitEdit(e: FormEvent) {
    e.preventDefault();
    if (editId === null) return;
    const fields = toFields(editForm);
    if (typeof fields === "string") {
      setMsg({ ok: false, text: fields });
      return;
    }
    const id = editId;
    void runMutation("Expected material updated.", "Update failed.", async () => {
      await api.updateExpectedMaterial(id, fields);
      setEditId(null);
    });
  }

  // ▲/▼ reorder via the checklist planRenumber convention: swap + renumber to 10/20/30, write only
  // the rows whose seq changed (one seq call per changed row), then reload the canonical order.
  function move(index: number, dir: -1 | 1) {
    const current = rows ?? [];
    const changes = planRenumber(current, index, dir);
    if (changes.length === 0) return;
    void runMutation("Order updated.", "Reorder failed.", async () => {
      for (const change of changes) {
        await api.setExpectedMaterialSeq(change.row.id, change.seq);
      }
    });
  }

  function deactivate(r: api.ExpectedMaterialRow) {
    void runMutation("Expected material removed.", "Remove failed.", () => api.deactivateExpectedMaterial(r.id));
  }

  const list = rows ?? [];
  return (
    <section className="card dash-section" aria-label="Expected materials">
      <h3 className="dash-detail__h2">Expected materials ({list.length})</h3>

      {!canManage && (
        <p className="dash-card__sub muted">
          Read-only — you'll confirm receipt (or flag a delivery problem) from the daily report once
          the daily-form integration arrives.
        </p>
      )}

      {msg && <p className={`banner ${msg.ok ? "banner--ok" : "banner--err"}`}>{msg.text}</p>}
      {loadError && (
        <p className="banner banner--err">
          {loadError}{" "}
          <button type="button" className="btn btn--secondary" onClick={reload}>
            Retry
          </button>
        </p>
      )}
      {canManage && catalogError && <p className="banner banner--err">{catalogError}</p>}

      {rows === null && !loadError ? (
        <div className="muted">Loading expected materials…</div>
      ) : loadError ? null : list.length === 0 ? (
        <div className="dash-unavail">No expected materials for this job.</div>
      ) : (
        <ul className="dash-tasklist">
          {list.map((r, i) => {
            const pill = statusPill(r.status);
            return (
              <li key={r.id}>
                <span className={pill.className}>{pill.label}</span> <strong>{rowTitle(r)}</strong>
                {r.material_name && r.description ? <span className="muted"> — {r.description}</span> : null}
                {r.qty != null ? (
                  <span className="dash-chip">
                    {r.qty}
                    {r.unit ? ` ${r.unit}` : ""}
                  </span>
                ) : r.unit ? (
                  <span className="dash-chip">{r.unit}</span>
                ) : null}
                {r.expected_date ? <span className="dash-chip">expected {r.expected_date}</span> : null}
                {r.status !== "expected" && (
                  <div className="dash-card__sub muted">
                    {r.status === "received" ? "Received" : "Flagged"} {fmtDateTime(r.received_at)}
                    {r.received_by_name ? ` by ${r.received_by_name}` : ""}
                    {r.qty_received != null ? ` · qty received ${r.qty_received}` : ""}
                    {r.note ? ` · ${r.note}` : ""}
                  </div>
                )}
                {canManage && editId !== r.id && (
                  <div className="dash-row">
                    <button
                      type="button"
                      className="btn btn--secondary"
                      aria-label={`Move expected material ${r.id} up`}
                      disabled={busy || i === 0}
                      onClick={() => move(i, -1)}
                    >
                      ▲
                    </button>{" "}
                    <button
                      type="button"
                      className="btn btn--secondary"
                      aria-label={`Move expected material ${r.id} down`}
                      disabled={busy || i === list.length - 1}
                      onClick={() => move(i, 1)}
                    >
                      ▼
                    </button>{" "}
                    {r.status === "expected" && (
                      <button
                        type="button"
                        className="btn btn--edit"
                        aria-label={`Edit expected material ${r.id}`}
                        disabled={busy}
                        onClick={() => {
                          setEditId(r.id);
                          setEditForm(formFromRow(r));
                        }}
                      >
                        Edit
                      </button>
                    )}{" "}
                    <ConfirmDelete
                      actionLabel="Remove"
                      ariaLabel={`Remove expected material ${r.id}`}
                      copy={`Remove "${rowTitle(r)}" from this job's expected materials? Its history is kept.`}
                      busy={busy}
                      onConfirm={() => deactivate(r)}
                    />
                  </div>
                )}
                {canManage && editId === r.id && (
                  <ExpectationForm
                    label={`Edit expected material ${r.id}`}
                    draft={editForm}
                    onChange={setEditForm}
                    onSubmit={submitEdit}
                    busy={busy}
                    submitLabel="Save"
                    onCancel={() => setEditId(null)}
                    catalog={catalog}
                  />
                )}
              </li>
            );
          })}
        </ul>
      )}

      {canManage &&
        (addOpen ? (
          <ExpectationForm
            label="Add expected material"
            draft={addForm}
            onChange={setAddForm}
            onSubmit={submitAdd}
            busy={busy}
            submitLabel="Add"
            onCancel={() => setAddOpen(false)}
            catalog={catalog}
          />
        ) : (
          <div className="dash-row">
            <button type="button" className="btn btn--primary" onClick={() => setAddOpen(true)}>
              + Add expected material
            </button>
          </div>
        ))}
    </section>
  );
}
