import { useState, useEffect, useCallback } from "react";
import type { FormEvent } from "react";
import * as api from "../lib/fieldops_materials";
import { useAuth } from "../lib/auth";

// P3 Materials (M1) — admin editor for the material_catalog TYPE vocabulary. cap.materials.manage
// drives the write affordances (the Worker re-gates every call). List + create + per-row edit +
// soft-retire, mirroring the AccountsPage / equipment-roster shape; reload-after-write + banner.

type FormState = { model_id: string; manufacturer: string; category: string; key_specs: string; unit_cost: string };
const EMPTY_FORM: FormState = { model_id: "", manufacturer: "", category: "", key_specs: "", unit_cost: "" };

function toFields(f: FormState): api.MaterialFields {
  const out: api.MaterialFields = { model_id: f.model_id.trim(), category: f.category.trim() };
  if (f.manufacturer.trim()) out.manufacturer = f.manufacturer.trim();
  if (f.key_specs.trim()) out.key_specs = f.key_specs.trim();
  if (f.unit_cost.trim() !== "") {
    const n = Number(f.unit_cost);
    if (Number.isFinite(n)) out.unit_cost = n;
  }
  return out;
}

function FieldInput({ label, value, onChange }: { label: string; value: string; onChange: (v: string) => void }) {
  return (
    <label className="field">
      <span className="field__label">{label}</span>
      <input className="field__input" value={value} onChange={(e) => onChange(e.target.value)} />
    </label>
  );
}

export function MaterialsCatalogPage({ onBack }: { onBack: () => void }) {
  const { user } = useAuth();
  const caps = user?.capabilities ?? [];
  const canManage = caps.includes("cap.materials.manage"); // UI affordance only — the Worker re-gates

  const [rows, setRows] = useState<api.CatalogRow[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [showRetired, setShowRetired] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);

  const [createOpen, setCreateOpen] = useState(false);
  const [cf, setCf] = useState<FormState>({ ...EMPTY_FORM });
  const [editId, setEditId] = useState<number | null>(null);
  const [ef, setEf] = useState<FormState>({ ...EMPTY_FORM });

  const reload = useCallback(() => {
    setLoading(true);
    setError(null);
    api
      .fetchMaterials({ all: showRetired })
      .then((d) => {
        setRows(d.materials);
        setCursor(d.next_cursor);
      })
      .catch(() => setError("Failed to load the material catalog."))
      .finally(() => setLoading(false));
  }, [showRetired]);

  useEffect(() => {
    reload();
  }, [reload]);

  async function loadMore() {
    if (!cursor || loading) return;
    setLoading(true);
    try {
      const d = await api.fetchMaterials({ all: showRetired, cursor });
      setRows((prev) => [...prev, ...d.materials]);
      setCursor(d.next_cursor);
    } catch {
      setError("Failed to load more.");
    } finally {
      setLoading(false);
    }
  }

  async function submitCreate(e: FormEvent) {
    e.preventDefault();
    if (busy) return;
    if (!cf.model_id.trim() || !cf.category.trim()) {
      setMsg({ ok: false, text: "Model and category are required." });
      return;
    }
    setBusy(true);
    setMsg(null);
    try {
      await api.createMaterial(toFields(cf));
      setCf({ ...EMPTY_FORM });
      setCreateOpen(false);
      reload();
      setMsg({ ok: true, text: "Material type added." });
    } catch (err) {
      setMsg({ ok: false, text: err instanceof Error ? err.message : "Create failed." });
    } finally {
      setBusy(false);
    }
  }

  function openEdit(r: api.CatalogRow) {
    setEditId(r.id);
    setEf({
      model_id: r.model_id,
      manufacturer: r.manufacturer ?? "",
      category: r.category,
      key_specs: r.key_specs ?? "",
      unit_cost: r.unit_cost == null ? "" : String(r.unit_cost),
    });
  }

  async function saveEdit(id: number) {
    if (busy) return;
    if (!ef.model_id.trim() || !ef.category.trim()) {
      setMsg({ ok: false, text: "Model and category are required." });
      return;
    }
    setBusy(true);
    setMsg(null);
    try {
      await api.updateMaterial(id, toFields(ef));
      setEditId(null);
      reload();
      setMsg({ ok: true, text: "Material type updated." });
    } catch (err) {
      setMsg({ ok: false, text: err instanceof Error ? err.message : "Update failed." });
    } finally {
      setBusy(false);
    }
  }

  async function retire(id: number) {
    if (busy) return;
    setBusy(true);
    setMsg(null);
    try {
      await api.retireMaterial(id);
      reload();
      setMsg({ ok: true, text: "Material type retired." });
    } catch (err) {
      setMsg({ ok: false, text: err instanceof Error ? err.message : "Retire failed." });
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="page">
      <main className="page__main">
        <button type="button" className="btn btn--ghost" onClick={onBack}>
          ← Home
        </button>
        <h1 className="page__heading">Materials Catalog</h1>
        <p className="muted">
          The datasheet-backed material type vocabulary. The per-job Material List draws from these types.
        </p>

        {msg && (
          <p className="muted" style={{ color: msg.ok ? "green" : "red" }}>
            {msg.text}
          </p>
        )}
        {error && <p className="muted" style={{ color: "red" }}>{error}</p>}

        <label className="field">
          <span className="field__label">
            <input type="checkbox" checked={showRetired} onChange={(e) => setShowRetired(e.target.checked)} /> Show retired
          </span>
        </label>

        {canManage && (
          <section className="card">
            {!createOpen ? (
              <button
                className="btn btn--primary"
                onClick={() => {
                  setCreateOpen(true);
                  setCf({ ...EMPTY_FORM });
                }}
              >
                + Add a type
              </button>
            ) : (
              <form onSubmit={submitCreate}>
                <h2 className="page__heading">Add a material type</h2>
                <FieldInput label="Model / type (required)" value={cf.model_id} onChange={(v) => setCf({ ...cf, model_id: v })} />
                <FieldInput label="Manufacturer" value={cf.manufacturer} onChange={(v) => setCf({ ...cf, manufacturer: v })} />
                <FieldInput label="Category (required, e.g. module / inverter)" value={cf.category} onChange={(v) => setCf({ ...cf, category: v })} />
                <FieldInput label="Key specs" value={cf.key_specs} onChange={(v) => setCf({ ...cf, key_specs: v })} />
                <FieldInput label="Reference unit cost (optional)" value={cf.unit_cost} onChange={(v) => setCf({ ...cf, unit_cost: v })} />
                <div className="jha__actions">
                  <button className="btn btn--primary" type="submit">
                    {busy ? "Working…" : "Add type"}
                  </button>
                  <button className="btn btn--secondary" type="button" onClick={() => setCreateOpen(false)}>
                    Cancel
                  </button>
                </div>
              </form>
            )}
          </section>
        )}

        <section className="card">
          <h2 className="page__heading">Types</h2>
          {loading && rows.length === 0 ? (
            <p className="muted">Loading…</p>
          ) : rows.length === 0 ? (
            <p className="muted">No material types.</p>
          ) : (
            <ul className="accounts__list">
              {rows.map((r) => (
                <li key={r.id} className="accounts__row">
                  <div className="accounts__id">
                    <span className="accounts__name">{r.model_id}</span>
                    <span className="dash-pill">{r.category}</span>
                    {r.manufacturer ? <span className="muted">{r.manufacturer}</span> : null}
                    {r.active ? null : <span className="dash-pill dash-pill--warn">retired</span>}
                  </div>
                  {r.key_specs ? <p className="muted">{r.key_specs}</p> : null}
                  {r.unit_cost != null ? <p className="muted">Ref. cost: {r.unit_cost}</p> : null}
                  {canManage && r.active === 1 && editId !== r.id ? (
                    <div className="accounts__actions">
                      <button className="btn btn--secondary" onClick={() => openEdit(r)}>
                        Edit
                      </button>
                      <button className="btn btn--danger" onClick={() => void retire(r.id)}>
                        Retire
                      </button>
                    </div>
                  ) : null}
                  {canManage && editId === r.id ? (
                    <div className="accounts__editor">
                      <FieldInput label="Model / type" value={ef.model_id} onChange={(v) => setEf({ ...ef, model_id: v })} />
                      <FieldInput label="Manufacturer" value={ef.manufacturer} onChange={(v) => setEf({ ...ef, manufacturer: v })} />
                      <FieldInput label="Category" value={ef.category} onChange={(v) => setEf({ ...ef, category: v })} />
                      <FieldInput label="Key specs" value={ef.key_specs} onChange={(v) => setEf({ ...ef, key_specs: v })} />
                      <FieldInput label="Reference unit cost" value={ef.unit_cost} onChange={(v) => setEf({ ...ef, unit_cost: v })} />
                      <div className="jha__actions">
                        <button className="btn btn--primary" onClick={() => void saveEdit(r.id)}>
                          {busy ? "Saving…" : "Save"}
                        </button>
                        <button className="btn btn--secondary" onClick={() => setEditId(null)}>
                          Cancel
                        </button>
                      </div>
                    </div>
                  ) : null}
                </li>
              ))}
            </ul>
          )}
          {cursor ? (
            <button className="btn btn--secondary" onClick={() => void loadMore()} disabled={loading}>
              Load more
            </button>
          ) : null}
        </section>
      </main>
    </div>
  );
}
