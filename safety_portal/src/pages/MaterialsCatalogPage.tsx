import { useState, useEffect, useCallback, useMemo } from "react";
import type { FormEvent } from "react";
import * as api from "../lib/fieldops_materials";
import { useAuth } from "../lib/auth";
import { PageShell } from "../components/PageShell";

// P3 Materials (M1) — admin editor for the material_catalog TYPE vocabulary. cap.materials.manage
// drives the write affordances (the Worker re-gates every call). List + create + per-row edit +
// soft-retire; reload-after-write + banner. VISUAL: the refined URS-Marine dash look (matching the
// FieldOps Equipment port) — a `.dash-grid` of `.card` type-cards (title + category/unit `.dash-chip`s,
// retired `.dash-pill--warn`), design-language buttons (add=`.btn--primary`, edit=`.btn--edit`,
// retire=`.btn--retire`, cancel/load-more=`.btn--secondary`). Behavior/caps/API unchanged.
//
// Category grouping (frontend-only — `category` is a NOT NULL column already returned by
// GET /api/fieldops/materials): rows are bucketed by `category` client-side into one `.dash-section`
// per category (gold-underlined `.jha__section-title` head + a `.dash-pill` count), sorted
// alphabetically with an "Uncategorized" bucket (blank/whitespace category — shouldn't happen given
// the NOT NULL + required-field form validation, but handled defensively) pinned last. A chip filter
// bar (only rendered once there's more than one category) narrows the view to a single category;
// clicking the active chip again (or "Clear filter") restores "All". Pagination (`Load more`) is
// unaffected — newly-loaded rows just fold into their category's bucket.

type FormState = { model_id: string; manufacturer: string; category: string; key_specs: string; unit_cost: string };
const EMPTY_FORM: FormState = { model_id: "", manufacturer: "", category: "", key_specs: "", unit_cost: "" };

const UNCATEGORIZED = "Uncategorized";

function categoryOf(r: api.CatalogRow): string {
  const c = r.category?.trim();
  return c ? c : UNCATEGORIZED;
}

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
  const [activeCategory, setActiveCategory] = useState<string | null>(null);

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

  const rowsByCategory = useMemo(() => {
    const m = new Map<string, api.CatalogRow[]>();
    for (const r of rows) {
      const cat = categoryOf(r);
      const list = m.get(cat);
      if (list) list.push(r);
      else m.set(cat, [r]);
    }
    return m;
  }, [rows]);

  const categoryCounts = useMemo(() => {
    return Array.from(rowsByCategory.entries())
      .map(([cat, list]): [string, number] => [cat, list.length])
      .sort(([a], [b]) => {
        if (a === UNCATEGORIZED) return b === UNCATEGORIZED ? 0 : 1;
        if (b === UNCATEGORIZED) return -1;
        return a.localeCompare(b);
      });
  }, [rowsByCategory]);

  const visibleCategories = activeCategory ? categoryCounts.filter(([cat]) => cat === activeCategory) : categoryCounts;

  return (
    <PageShell onHome={onBack}>
      <h2 className="page__heading">Materials Catalog</h2>
      <p className="dash__intro">
        The datasheet-backed material type vocabulary. The per-job Material List draws from these types.
        Per-job expectations live on each job's detail in the Job Tracker ("Expected materials").
      </p>

      {msg && <div className={`banner ${msg.ok ? "banner--ok" : "banner--err"}`}>{msg.text}</div>}
      {error && <div className="banner banner--err">{error}</div>}

      <label className="field">
        <span className="field__label">
          <input type="checkbox" checked={showRetired} onChange={(e) => setShowRetired(e.target.checked)} /> Show retired
        </span>
      </label>

      {canManage && (
        <section className="card dash-section">
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
              <h3 className="dash-detail__h2">Add a material type</h3>
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

      {loading && rows.length === 0 ? (
        <p className="muted">Loading…</p>
      ) : rows.length === 0 ? (
        <div className="dash-empty">No material types.</div>
      ) : (
        <>
          {categoryCounts.length > 1 ? (
            <div className="mats-cat-filter" role="group" aria-label="Filter by category">
              <button
                type="button"
                className={`mats-cat-filter__chip${activeCategory === null ? " mats-cat-filter__chip--active" : ""}`}
                aria-pressed={activeCategory === null}
                onClick={() => setActiveCategory(null)}
              >
                All <span className="dash-pill">{rows.length}</span>
              </button>
              {categoryCounts.map(([cat, count]) => (
                <button
                  key={cat}
                  type="button"
                  className={`mats-cat-filter__chip${activeCategory === cat ? " mats-cat-filter__chip--active" : ""}`}
                  aria-pressed={activeCategory === cat}
                  onClick={() => setActiveCategory(activeCategory === cat ? null : cat)}
                >
                  {cat} <span className="dash-pill">{count}</span>
                </button>
              ))}
            </div>
          ) : null}

          {visibleCategories.length === 0 ? (
            <div className="dash-empty">
              No material types in this category.{" "}
              <button className="btn btn--secondary" onClick={() => setActiveCategory(null)}>
                Clear filter
              </button>
            </div>
          ) : (
            visibleCategories.map(([cat, count]) => (
              <section key={cat} className="card dash-section" aria-label={`${cat} material types`}>
                <h3 className="jha__section-title mats-cat-heading">
                  {cat}
                  <span className="dash-pill mats-cat-heading__count">{count}</span>
                </h3>
                <div className="dash-grid">
                  {(rowsByCategory.get(cat) ?? []).map((r) => (
                    <section key={r.id} className="card">
                      <div className="dash-card__head">
                        <h3 className="dash-card__title">{r.model_id}</h3>
                        {r.active ? null : <span className="dash-pill dash-pill--warn">Retired</span>}
                      </div>
                      {r.manufacturer ? <div className="dash-card__sub">{r.manufacturer}</div> : null}

                      <div className="dash-card__row">
                        <div className="dash-chips">
                          <span className="dash-chip">{r.category}</span>
                          {r.unit_cost != null ? <span className="dash-chip">Ref. cost: {r.unit_cost}</span> : null}
                        </div>
                      </div>

                      {r.key_specs ? (
                        <div className="dash-card__row">
                          <span className="dash-card__label">Key specs</span>
                          <span>{r.key_specs}</span>
                        </div>
                      ) : null}

                      {canManage && r.active === 1 && editId !== r.id ? (
                        <div className="dash-row">
                          <button className="btn btn--edit" onClick={() => openEdit(r)}>
                            Edit
                          </button>
                          <button className="btn btn--retire" onClick={() => void retire(r.id)}>
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
                    </section>
                  ))}
                </div>
              </section>
            ))
          )}

          {cursor ? (
            <div className="dash-row dash-load-more">
              <button className="btn btn--secondary" onClick={() => void loadMore()} disabled={loading}>
                Load more
              </button>
            </div>
          ) : null}
        </>
      )}
    </PageShell>
  );
}
