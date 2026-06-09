import { useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import { AppHeader } from "../components/AppHeader";
import { formCatalog, getDefinition } from "../forms/registry";
import { FormRenderer, initialValues, type FormValues } from "../forms/FormRenderer";

/**
 * Admin "Forms" tab — the READ-ONLY form catalog manager (Phase-2 slice 2). Lists
 * every active form from the git catalog manifest (via formCatalog, which slice 1b
 * made manifest-driven) grouped by parent, and previews the selected form with the
 * REAL SPA FormRenderer (render-parity confidence in-tab). Authoring (create / edit
 * / version / delete + publish) lands in later slices; this is the viewer they build
 * on. Reached only through the admin shell (App.tsx routes by role). It is read-only
 * and calls NO API — the catalog + definitions are bundled, so there is nothing to
 * gate server-side here; the preview's fill state is throwaway (never submitted).
 */
export function FormsPage({ tabBar }: { tabBar: ReactNode }) {
  const catalog = useMemo(() => formCatalog(), []);

  // Flatten the parent→variant catalog into a selectable, parent-grouped list.
  const items = useMemo(() => {
    const out: { form_code: string; parent: string; label: string }[] = [];
    for (const p of catalog) {
      if (p.variants.length === 0 && p.form_code) {
        out.push({ form_code: p.form_code, parent: p.name, label: p.name });
      } else {
        for (const v of p.variants) {
          out.push({ form_code: v.form_code, parent: p.name, label: v.variant_label });
        }
      }
    }
    return out;
  }, [catalog]);

  const [selected, setSelected] = useState<string>(items[0]?.form_code ?? "");
  const def = selected ? getDefinition(selected) : null;

  // Preview fill-state, re-initialised whenever the selected form changes (mirrors
  // FormFillPage). Interactive but throwaway — nothing is submitted from this tab.
  const [values, setValues] = useState<FormValues>({});
  useEffect(() => {
    const d = selected ? getDefinition(selected) : null;
    setValues(d ? initialValues(d) : {});
  }, [selected]);

  return (
    <div className="page">
      <AppHeader title="Safety Portal" />
      {tabBar}
      <main className="page__main forms-mgr">
        <aside className="forms-mgr__list" aria-label="Form catalog">
          <h2 className="forms-mgr__heading">
            Forms <span className="forms-mgr__count">{items.length}</span>
          </h2>
          <ul className="forms-mgr__items">
            {items.map((it) => (
              <li key={it.form_code}>
                <button
                  type="button"
                  className={`forms-mgr__item${it.form_code === selected ? " forms-mgr__item--active" : ""}`}
                  aria-current={it.form_code === selected}
                  onClick={() => setSelected(it.form_code)}
                >
                  <span className="forms-mgr__item-label">{it.label}</span>
                  <span className="forms-mgr__item-parent">{it.parent}</span>
                </button>
              </li>
            ))}
          </ul>
        </aside>
        <section className="forms-mgr__detail">
          {def ? (
            <>
              <header className="forms-mgr__meta">
                <h1 className="page__heading">{def.form_name}</h1>
                <dl className="forms-mgr__meta-grid">
                  <div><dt>Form code</dt><dd>{def.form_code}</dd></div>
                  <div><dt>Parent</dt><dd>{def.parent_form_code}</dd></div>
                  <div><dt>Variant</dt><dd>{def.variant_label ?? "—"}</dd></div>
                  <div><dt>Version</dt><dd>v{def.version}</dd></div>
                  <div><dt>Archetype</dt><dd>{def.archetype}</dd></div>
                  <div><dt>Sections</dt><dd>{def.sections.length}</dd></div>
                </dl>
                <p className="muted">Read-only preview — form authoring lands in a later release.</p>
              </header>
              <div className="forms-mgr__preview card" aria-label="Live preview">
                <FormRenderer def={def} values={values} setValues={setValues} />
              </div>
            </>
          ) : (
            <p className="muted">No forms in the catalog.</p>
          )}
        </section>
      </main>
    </div>
  );
}
