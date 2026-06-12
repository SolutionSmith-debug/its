import type { Dispatch, SetStateAction } from "react";
import { PhotoField } from "../components/PhotoField";
import { SignaturePad } from "../components/SignaturePad";
import type { Field, FormDefinition, Group, PhotoValue, Section } from "./types";

// The fill state, keyed per section:
//   header field key          -> string (signature field -> SVG path string)
//   repeating/signature table -> Array<Record<colKey, string>>
//   checklist section key     -> Record<itemKey, { response?: string; comment?: string }>
//   freeform section key      -> string
export type FormValues = Record<string, unknown>;

type Row = Record<string, string>;
type ChecklistState = Record<string, { response?: string; comment?: string }>;

// Envelope-bound header keys — the fill page provides these top-level (job
// dropdown + work-date picker), so the renderer skips them to avoid duplicate UI.
const ENVELOPE_KEYS = new Set(["work_date", "job"]);

/** Build the initial fill state for a definition (empty header fields, min_rows table rows). */
export function initialValues(def: FormDefinition): FormValues {
  const v: FormValues = {};
  for (const s of def.sections) {
    if (s.type === "header") {
      for (const f of s.fields) v[f.key] = f.input === "photo" ? [] : "";
    } else if (s.type === "repeating_table" || s.type === "signature_table") {
      const n = Math.max(1, s.min_rows ?? 1);
      v[s.key] = Array.from({ length: n }, () => emptyRow(s.columns));
    } else if (s.type === "checklist") {
      v[s.key] = {};
    } else if (s.type === "freeform") {
      v[s.key] = "";
    }
  }
  return v;
}

const emptyRow = (cols: Field[]): Row => Object.fromEntries(cols.map((c) => [c.key, ""]));

interface Props {
  def: FormDefinition;
  values: FormValues;
  setValues: Dispatch<SetStateAction<FormValues>>;
}

export function FormRenderer({ def, values, setValues }: Props) {
  const setField = (key: string, val: string) =>
    setValues((v) => ({ ...v, [key]: val }));

  // Photo header fields hold PhotoValue[] (not string) — see types.PhotoValue.
  const setPhotos = (key: string, next: PhotoValue[]) =>
    setValues((v) => ({ ...v, [key]: next }));

  const setCell = (secKey: string, idx: number, colKey: string, val: string) =>
    setValues((v) => {
      const rows = [...((v[secKey] as Row[]) ?? [])];
      rows[idx] = { ...rows[idx], [colKey]: val };
      return { ...v, [secKey]: rows };
    });

  const addRow = (secKey: string, cols: Field[]) =>
    setValues((v) => ({ ...v, [secKey]: [...((v[secKey] as Row[]) ?? []), emptyRow(cols)] }));

  const removeRow = (secKey: string, idx: number) =>
    setValues((v) => {
      const rows = (v[secKey] as Row[]) ?? [];
      return rows.length > 1 ? { ...v, [secKey]: rows.filter((_, i) => i !== idx) } : v;
    });

  const setChecklist = (secKey: string, itemKey: string, patch: { response?: string; comment?: string }) =>
    setValues((v) => {
      const cl = { ...((v[secKey] as ChecklistState) ?? {}) };
      cl[itemKey] = { ...cl[itemKey], ...patch };
      return { ...v, [secKey]: cl };
    });

  return (
    <div className="fr">
      {def.sections.map((s, i) => (
        <SectionView
          key={i}
          section={s}
          values={values}
          setField={setField}
          setPhotos={setPhotos}
          setCell={setCell}
          addRow={addRow}
          removeRow={removeRow}
          setChecklist={setChecklist}
        />
      ))}
    </div>
  );
}

interface SectionProps {
  section: Section;
  values: FormValues;
  setField: (k: string, v: string) => void;
  setPhotos: (k: string, next: PhotoValue[]) => void;
  setCell: (sec: string, idx: number, col: string, v: string) => void;
  addRow: (sec: string, cols: Field[]) => void;
  removeRow: (sec: string, idx: number) => void;
  setChecklist: (sec: string, item: string, patch: { response?: string; comment?: string }) => void;
}

function SectionView(p: SectionProps) {
  const s = p.section;
  switch (s.type) {
    case "header": {
      const fields = s.fields.filter((f) => !ENVELOPE_KEYS.has(f.key));
      if (fields.length === 0) return null; // whole header was envelope-bound
      return (
        <section className="fr__section">
          {s.title ? <h2 className="fr__section-title">{s.title}</h2> : null}
          <div className="fr__grid">
            {fields.map((f) =>
              f.input === "photo" ? (
                <PhotoField key={f.key} field={f}
                  photos={(p.values[f.key] as PhotoValue[]) ?? []}
                  onChange={(next) => p.setPhotos(f.key, next)} />
              ) : (
                <FieldView key={f.key} field={f} value={String(p.values[f.key] ?? "")}
                  onChange={(v) => p.setField(f.key, v)} />
              ))}
          </div>
        </section>
      );
    }
    case "static_text":
      return <p className={`fr__static fr__static--${s.emphasis ?? "heading"}`}>{s.text}</p>;
    case "freeform":
      return (
        <section className="fr__section">
          <label className="field">
            <span className="field__label">{s.label}</span>
            <textarea className="field__textarea" value={String(p.values[s.key] ?? "")}
              onChange={(e) => p.setField(s.key, e.target.value)} />
          </label>
        </section>
      );
    case "content_blocks":
      return (
        <section className="fr__section fr__content">
          {s.title ? <h2 className="fr__section-title">{s.title}</h2> : null}
          {s.blocks.map((b, i) => (
            <div className="fr__content-block" key={i}>
              {b.heading ? <h3 className="fr__content-heading">{b.heading}</h3> : null}
              <p className="fr__content-body">{b.body}</p>
            </div>
          ))}
        </section>
      );
    case "repeating_table":
    case "signature_table":
      return <TableView section={s} rows={(p.values[s.key] as Row[]) ?? []}
        onCell={(i, c, v) => p.setCell(s.key, i, c, v)}
        onAdd={() => p.addRow(s.key, s.columns)} onRemove={(i) => p.removeRow(s.key, i)} />;
    case "checklist":
      return <ChecklistView section={s} state={(p.values[s.key] as ChecklistState) ?? {}}
        onChange={(item, patch) => p.setChecklist(s.key, item, patch)} />;
  }
}

function FieldView({ field, value, onChange }: { field: Field; value: string; onChange: (v: string) => void }) {
  if (field.input === "signature") {
    return (
      <div className="field">
        <span className="field__label">{field.label}</span>
        <SignaturePad onChange={(svg, empty) => onChange(empty ? "" : svg)} />
      </div>
    );
  }
  if (field.input === "select") {
    return (
      <label className="field">
        <span className="field__label">{field.label}{field.required ? " *" : ""}</span>
        <select className="field__input" value={value} onChange={(e) => onChange(e.target.value)}>
          <option value="">—</option>
          {(field.options ?? []).map((o) => <option key={o} value={o}>{o}</option>)}
        </select>
      </label>
    );
  }
  if (field.input === "textarea") {
    return (
      <label className="field">
        <span className="field__label">{field.label}{field.required ? " *" : ""}</span>
        <textarea className="field__textarea" value={value} onChange={(e) => onChange(e.target.value)} />
      </label>
    );
  }
  // text / date / time / number
  return (
    <label className="field">
      <span className="field__label">{field.label}{field.required ? " *" : ""}</span>
      <input className="field__input" type={field.input} value={value}
        onChange={(e) => onChange(e.target.value)} />
    </label>
  );
}

function TableView({ section, rows, onCell, onAdd, onRemove }: {
  section: Extract<Section, { type: "repeating_table" | "signature_table" }>;
  rows: Row[]; onCell: (i: number, c: string, v: string) => void;
  onAdd: () => void; onRemove: (i: number) => void;
}) {
  return (
    <section className="fr__section">
      {section.title ? <h2 className="fr__section-title">{section.title}</h2> : null}
      <div className="fr__rows">
        {rows.map((row, i) => (
          <div className="fr__row" key={i}>
            {rows.length > 1 ? (
              <button type="button" className="fr__row-remove" aria-label={`Remove row ${i + 1}`}
                onClick={() => onRemove(i)}>✕</button>
            ) : null}
            {section.columns.map((c) => (
              <div className="fr__cell" key={c.key}>
                <span className="fr__cell-label">{c.label}</span>
                {c.input === "signature" ? (
                  <SignaturePad onChange={(svg, empty) => onCell(i, c.key, empty ? "" : svg)} />
                ) : (
                  <input className="field__input"
                    type={c.input === "date" || c.input === "time" || c.input === "number" ? c.input : "text"}
                    value={row[c.key] ?? ""} onChange={(e) => onCell(i, c.key, e.target.value)} />
                )}
              </div>
            ))}
          </div>
        ))}
      </div>
      {section.allow_add !== false ? (
        <button type="button" className="btn btn--secondary" onClick={onAdd}>+ Add row</button>
      ) : null}
    </section>
  );
}

function ChecklistView({ section, state, onChange }: {
  section: Extract<Section, { type: "checklist" }>;
  state: ChecklistState; onChange: (item: string, patch: { response?: string; comment?: string }) => void;
}) {
  return (
    <section className="fr__section">
      {section.title ? <h2 className="fr__section-title">{section.title}</h2> : null}
      {section.groups.map((g) => <GroupView key={g.key} group={g} state={state} onChange={onChange} />)}
    </section>
  );
}

function GroupView({ group, state, onChange }: {
  group: Group; state: ChecklistState; onChange: (item: string, patch: { response?: string; comment?: string }) => void;
}) {
  return (
    <div className="fr__group">
      <h3 className="fr__group-title">{group.label}</h3>
      {group.items.map((it) => {
        const cur = state[it.key] ?? {};
        const showComment = it.comment ?? group.comment_per_item ?? false;
        return (
          <div className="fr__item" key={it.key}>
            <span className="fr__item-label">{it.label}</span>
            <div className="fr__item-control">
              {it.kind === "numeric" ? (
                <input className="field__input fr__item-num" type="number" value={cur.response ?? ""}
                  onChange={(e) => onChange(it.key, { response: e.target.value })} />
              ) : it.kind === "text" ? (
                <input className="field__input" type="text" value={cur.response ?? ""}
                  onChange={(e) => onChange(it.key, { response: e.target.value })} />
              ) : (
                <div className="fr__scale" role="radiogroup" aria-label={it.label}>
                  {(it.scale ?? (it.kind === "circle_one" ? it.options : group.scale) ?? []).map((opt) => (
                    <button type="button" key={opt}
                      className={`fr__scale-opt${cur.response === opt ? " fr__scale-opt--on" : ""}`}
                      aria-pressed={cur.response === opt}
                      onClick={() => onChange(it.key, { response: opt })}>{opt}</button>
                  ))}
                </div>
              )}
            </div>
            {showComment ? (
              <input className="field__input fr__item-comment" type="text" placeholder="Comments"
                value={cur.comment ?? ""} onChange={(e) => onChange(it.key, { comment: e.target.value })} />
            ) : null}
          </div>
        );
      })}
    </div>
  );
}
