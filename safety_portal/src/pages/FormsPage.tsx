import { useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import { AppHeader } from "../components/AppHeader";
import { FormEditor } from "../components/FormEditor";
import { PublishMonitor } from "../components/PublishMonitor";
import { formCatalog, getDefinition } from "../forms/registry";
import { FormRenderer, initialValues, type FormValues } from "../forms/FormRenderer";
import type { FormDefinition } from "../forms/types";
import {
  blankDefinition,
  formCodeFor,
  toClonedDraft,
  toEditDraft,
} from "../forms/editorModel";
import { validateDraft } from "../forms/editorValidation";
import * as api from "../lib/api";

/**
 * Admin "Forms" tab — the form catalog manager (Phase-2). VIEW mode lists every active
 * form from the git catalog manifest grouped by parent and previews the selected form
 * with the REAL SPA FormRenderer (render-parity confidence in-tab). EDITOR mode is the
 * B8 sectioned builder: create-from-blank, edit (version bump), add-version (clone to a
 * new identity), and retire (delete) — each composing a FormDefinition from the closed
 * vocabulary, validated client-side and PUBLISHED via the send-free enqueue
 * (/api/admin/publish; the Mac daemon is the sole actuator). A live FormRenderer preview
 * sits beside the builder. Every publish is re-gated + re-validated server-side; this tab
 * never writes a form file.
 *
 * Rollback is DEFERRED (see the report) — the publish contract accepts op:"rollback" but
 * the editor surfaces only create/edit/add_version/delete this slice.
 */

type Mode =
  | { kind: "view" }
  | { kind: "create" }
  | { kind: "edit"; sourceCode: string; identity: string }
  | { kind: "add_version"; sourceCode: string };

type Banner = { kind: "ok" | "err"; msg: string } | null;

/** Derive the identity (parent-vN stripped) from a form_code: jha-v2 → jha. */
function identityFromCode(formCode: string): string {
  return formCode.replace(/-v[0-9]+$/, "");
}

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

  const knownParents = useMemo(
    () => Array.from(new Set(catalog.map((p) => p.parent_form_code))).sort(),
    [catalog],
  );

  const [selected, setSelected] = useState<string>(items[0]?.form_code ?? "");
  const [mode, setMode] = useState<Mode>({ kind: "view" });

  // ── VIEW-mode preview state (mirrors the prior read-only viewer) ──────────────
  const viewDef = mode.kind === "view" && selected ? getDefinition(selected) : null;
  const [viewValues, setViewValues] = useState<FormValues>({});
  useEffect(() => {
    if (mode.kind !== "view") return;
    const d = selected ? getDefinition(selected) : null;
    setViewValues(d ? initialValues(d) : {});
  }, [selected, mode.kind]);

  // ── EDITOR-mode draft state ───────────────────────────────────────────────────
  const [draft, setDraft] = useState<FormDefinition | null>(null);
  const [identity, setIdentity] = useState("");
  const [parent, setParent] = useState("");
  const [banner, setBanner] = useState<Banner>(null);
  const [busy, setBusy] = useState(false);
  const [refreshSignal, setRefreshSignal] = useState(0);

  // Preview fill-state for the editor draft — re-initialised whenever the section
  // structure changes (keyed on form_code + section count so retitling doesn't reset it).
  const [draftValues, setDraftValues] = useState<FormValues>({});
  const draftShape = draft ? `${draft.sections.length}:${draft.sections.map((s) => s.type).join(",")}` : "";
  useEffect(() => {
    if (draft) setDraftValues(initialValues(draft));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [draftShape]);

  function startCreate() {
    const d = blankDefinition();
    setDraft(d);
    setIdentity("");
    setParent("");
    setBanner(null);
    setMode({ kind: "create" });
  }

  function startEdit(sourceCode: string) {
    const src = getDefinition(sourceCode);
    if (!src) return;
    const id = identityFromCode(sourceCode);
    const d = toEditDraft(src, id);
    setDraft(d);
    setIdentity(id);
    setParent(src.parent_form_code);
    setBanner(null);
    setMode({ kind: "edit", sourceCode, identity: id });
  }

  function startAddVersion(sourceCode: string) {
    const src = getDefinition(sourceCode);
    if (!src) return;
    // Clone to a NEW identity — pre-fill a "-copy" suffix the admin renames.
    const newIdentity = `${identityFromCode(sourceCode)}-copy`;
    const d = toClonedDraft(src, newIdentity, src.parent_form_code);
    setDraft(d);
    setIdentity(newIdentity);
    setParent(src.parent_form_code);
    setBanner(null);
    setMode({ kind: "add_version", sourceCode });
  }

  function cancelEditor() {
    setDraft(null);
    setBanner(null);
    setMode({ kind: "view" });
  }

  // Keep form_code + parent_form_code on the draft in lockstep with the identity/version
  // inputs (form_code is DERIVED, never typed — the server enforces this too).
  useEffect(() => {
    if (!draft) return;
    const wantCode = identity ? formCodeFor(identity, draft.version) : "";
    if (draft.form_code !== wantCode || draft.parent_form_code !== parent) {
      setDraft({ ...draft, form_code: wantCode, parent_form_code: parent });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [identity, parent, draft?.version]);

  const editorOp: api.PublishOp | null =
    mode.kind === "create" ? "create" : mode.kind === "edit" ? "edit" : mode.kind === "add_version" ? "add_version" : null;

  const clientErrors = useMemo(() => {
    if (!draft || !editorOp) return [];
    return validateDraft(draft, { identity, parentFormCode: parent });
  }, [draft, editorOp, identity, parent]);

  async function onPublish() {
    if (!draft || !editorOp) return;
    setBusy(true);
    setBanner(null);
    try {
      await api.publishForm({
        op: editorOp,
        identity,
        parent_form_code: parent,
        definition: draft,
      });
      setBanner({ kind: "ok", msg: `Publish queued for ${draft.form_code}. Track it below.` });
      setRefreshSignal((n) => n + 1);
      setMode({ kind: "view" });
      setDraft(null);
    } catch (e) {
      setBanner({ kind: "err", msg: explainPublish(e) });
    } finally {
      setBusy(false);
    }
  }

  async function onRetire(formCode: string) {
    const id = identityFromCode(formCode);
    const src = getDefinition(formCode);
    if (!window.confirm(`Retire ${formCode}? It will be removed from the active picker (filed submissions still render).`)) {
      return;
    }
    setBusy(true);
    setBanner(null);
    try {
      await api.publishForm({
        op: "delete",
        identity: id,
        parent_form_code: src?.parent_form_code ?? id,
        target_form_code: formCode,
      });
      setBanner({ kind: "ok", msg: `Retire queued for ${formCode}. Track it below.` });
      setRefreshSignal((n) => n + 1);
    } catch (e) {
      setBanner({ kind: "err", msg: explainPublish(e) });
    } finally {
      setBusy(false);
    }
  }

  const inEditor = mode.kind === "create" || mode.kind === "edit" || mode.kind === "add_version";

  return (
    <div className="page">
      <AppHeader title="Safety Portal" />
      {tabBar}
      <main className="page__main">
        {banner ? (
          <p className={banner.kind === "ok" ? "banner banner--ok" : "banner banner--err"} role="status">
            {banner.msg}
          </p>
        ) : null}

        {inEditor && draft ? (
          // ── EDITOR LAYOUT: builder on the left, live preview on the right ──────
          <>
            <div className="form-editor__toolbar">
              <h1 className="page__heading">
                {mode.kind === "create"
                  ? "New form"
                  : mode.kind === "edit"
                    ? `Edit ${mode.identity} → v${draft.version}`
                    : "Add version (clone)"}
              </h1>
              <div className="jha__actions" style={{ marginTop: 0 }}>
                <button
                  type="button"
                  className="btn btn--primary"
                  disabled={busy || clientErrors.length > 0}
                  onClick={() => void onPublish()}
                >
                  {busy ? "Publishing…" : "Publish"}
                </button>
                <button type="button" className="btn btn--secondary" disabled={busy} onClick={cancelEditor}>
                  Cancel
                </button>
              </div>
            </div>

            {clientErrors.length > 0 ? (
              <div className="form-editor__errors" role="alert">
                <p className="form-editor__errors-title">Fix before publishing:</p>
                <ul>
                  {clientErrors.map((e, i) => (
                    <li key={i}>{e}</li>
                  ))}
                </ul>
              </div>
            ) : (
              <p className="banner banner--ok" role="status">Ready to publish — no client-side errors.</p>
            )}

            <div className="form-editor__split">
              <div className="form-editor__build-pane">
                <FormEditor
                  def={draft}
                  onChange={setDraft}
                  mode={mode.kind}
                  identity={identity}
                  onIdentityChange={setIdentity}
                  parentFormCode={parent}
                  onParentChange={setParent}
                  knownParents={knownParents}
                />
              </div>
              <div className="form-editor__preview-pane">
                <h2 className="form-editor__sub-heading">Live preview</h2>
                <div className="card forms-mgr__preview" aria-label="Live preview">
                  <FormRenderer def={draft} values={draftValues} setValues={setDraftValues} />
                </div>
              </div>
            </div>
          </>
        ) : (
          // ── VIEW LAYOUT: catalog list + read-only preview + per-form actions ──
          <>
            <div className="form-editor__toolbar">
              <h1 className="page__heading">Forms</h1>
              <button type="button" className="btn btn--primary" onClick={startCreate}>
                + New form
              </button>
            </div>
            <div className="forms-mgr">
              <aside className="forms-mgr__list" aria-label="Form catalog">
                <h2 className="forms-mgr__heading">
                  Catalog <span className="forms-mgr__count">{items.length}</span>
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
                {viewDef ? (
                  <>
                    <header className="forms-mgr__meta">
                      <h1 className="page__heading">{viewDef.form_name}</h1>
                      <dl className="forms-mgr__meta-grid">
                        <div><dt>Form code</dt><dd>{viewDef.form_code}</dd></div>
                        <div><dt>Parent</dt><dd>{viewDef.parent_form_code}</dd></div>
                        <div><dt>Variant</dt><dd>{viewDef.variant_label ?? "—"}</dd></div>
                        <div><dt>Version</dt><dd>v{viewDef.version}</dd></div>
                        <div><dt>Archetype</dt><dd>{viewDef.archetype}</dd></div>
                        <div><dt>Sections</dt><dd>{viewDef.sections.length}</dd></div>
                      </dl>
                      <div className="jha__actions" style={{ marginTop: 8 }}>
                        <button type="button" className="btn btn--secondary" disabled={busy} onClick={() => startEdit(viewDef.form_code)}>
                          Edit (new version)
                        </button>
                        <button type="button" className="btn btn--secondary" disabled={busy} onClick={() => startAddVersion(viewDef.form_code)}>
                          Add version (clone)
                        </button>
                        <button type="button" className="btn btn--danger" disabled={busy} onClick={() => void onRetire(viewDef.form_code)}>
                          Retire
                        </button>
                      </div>
                    </header>
                    <div className="forms-mgr__preview card" aria-label="Live preview">
                      <FormRenderer def={viewDef} values={viewValues} setValues={setViewValues} />
                    </div>
                  </>
                ) : (
                  <p className="muted">No forms in the catalog.</p>
                )}
              </section>
            </div>
          </>
        )}

        <PublishMonitor refreshSignal={refreshSignal} />
      </main>
    </div>
  );
}

/** Map a publish error to an operator-readable message, surfacing the server `reason`. */
function explainPublish(e: unknown): string {
  if (e instanceof api.PublishError) {
    if (e.status === 409) return "Another publish for this form type is still in progress — wait for it to finish.";
    if (e.status === 403) return "You're not authorized to publish forms.";
    if (e.reason) return `Rejected: ${e.reason}`;
    if (e.code === "invalid_op") return "Invalid operation.";
    if (e.code === "invalid_identity") return "Invalid identity slug.";
    if (e.code === "invalid_parent_form_code") return "Invalid form type (parent).";
    return "Publish was rejected. Please review and try again.";
  }
  return "Something went wrong while publishing. Please try again.";
}
