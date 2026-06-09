// Pure, UI-free helpers for the admin Form Editor (Phase-2 slice 3, the B8 sectioned
// builder). The editor composes a FormDefinition from the CLOSED vocabulary
// (forms/meta-schema.json mirrored in types.ts); these factories + transforms keep the
// React component declarative and let the same logic drive create / edit / add-version
// without duplicating the shape rules. NOTHING here sends or fetches — the only mutation
// path off the SPA is the send-free publish enqueue (lib/api.ts).

import type {
  ContentBlock,
  Field,
  FormDefinition,
  Group,
  Input,
  Item,
  Section,
} from "./types";

export const ARCHETYPES = [
  "rows_signatures",
  "grouped_checklist",
  "content_signin",
  "visitor_rows",
  "sectioned_assessment",
] as const;
export type Archetype = (typeof ARCHETYPES)[number];

export const FIELD_INPUTS: Input[] = [
  "text",
  "textarea",
  "date",
  "time",
  "number",
  "select",
  "signature",
];

export const ITEM_KINDS = ["rated", "numeric", "circle_one", "text"] as const;
export type ItemKind = (typeof ITEM_KINDS)[number];

export const SECTION_TYPES = [
  "header",
  "static_text",
  "repeating_table",
  "signature_table",
  "checklist",
  "freeform",
  "content_blocks",
] as const;
export type SectionType = (typeof SECTION_TYPES)[number];

/** Human labels for the section-type picker (the closed set, surfaced as-is). */
export const SECTION_TYPE_LABELS: Record<SectionType, string> = {
  header: "Header fields",
  static_text: "Static text",
  repeating_table: "Repeating table",
  signature_table: "Signature table",
  checklist: "Checklist",
  freeform: "Free-form text",
  content_blocks: "Content blocks",
};

// The validator's KEY_RE: every field/section/group/item key is snake_case lowercase.
const KEY_RE = /^[a-z0-9_]+$/;
const SLUG_RE = /^[a-z0-9-]+$/;

/** Lowercase a free-text label into a snake_case key candidate ([a-z0-9_]). */
export function slugifyKey(label: string): string {
  return label
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "")
    .slice(0, 60);
}

/** Lowercase a free-text label into a hyphen identity slug ([a-z0-9-]). */
export function slugifyIdentity(text: string): string {
  return text
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 80);
}

export function isValidKey(k: string): boolean {
  return KEY_RE.test(k);
}
export function isValidIdentity(k: string): boolean {
  return SLUG_RE.test(k);
}

/** A unique key in `existing`, derived from `base` (or a fallback prefix), suffixing
 *  _2, _3, … on collision. Used when adding fields/items/sections so the editor never
 *  emits a duplicate key (which the validator rejects). */
export function uniqueKey(base: string, existing: Set<string>, fallback = "key"): string {
  let candidate = slugifyKey(base) || fallback;
  if (!existing.has(candidate)) return candidate;
  let n = 2;
  while (existing.has(`${candidate}_${n}`)) n++;
  return `${candidate}_${n}`;
}

// ── Blank factories for each vocabulary element ─────────────────────────────────

export function blankField(key: string, input: Input = "text"): Field {
  const f: Field = { key, label: "", input };
  if (input === "select") f.options = [""];
  return f;
}

export function blankItem(key: string): Item {
  return { key, label: "" };
}

export function blankGroup(key: string): Group {
  return { key, label: "", scale: ["OK", "NOT OK", "N/A"], items: [blankItem(`${key}_item`)] };
}

export function blankBlock(): ContentBlock {
  return { heading: "", body: "" };
}

/** A blank section of the requested type, with one starter child where the schema
 *  requires a non-empty collection (columns / groups / blocks). */
export function blankSection(type: SectionType): Section {
  switch (type) {
    case "header":
      return { type: "header", fields: [blankField("field_1")] };
    case "static_text":
      return { type: "static_text", text: "", emphasis: "heading" };
    case "repeating_table":
      return {
        type: "repeating_table",
        key: "table",
        columns: [blankField("col_1")],
        min_rows: 1,
        allow_add: true,
      };
    case "signature_table":
      return {
        type: "signature_table",
        key: "sign_in",
        columns: [blankField("name"), blankField("signature", "signature")],
        min_rows: 1,
        allow_add: true,
      };
    case "checklist":
      return { type: "checklist", key: "checklist", groups: [blankGroup("group_1")] };
    case "freeform":
      return { type: "freeform", key: "notes", label: "", input: "textarea" };
    case "content_blocks":
      return { type: "content_blocks", key: "content", blocks: [blankBlock()] };
  }
}

/** A brand-new blank FormDefinition for the create flow. The identity / parent are set
 *  by the editor's identity panel; version is always 1 for a new identity. */
export function blankDefinition(): FormDefinition {
  return {
    form_code: "",
    parent_form_code: "",
    form_name: "",
    variant_label: null,
    version: 1,
    archetype: "sectioned_assessment",
    source_pdf: "",
    sections: [blankSection("header")],
  };
}

/** Deep clone via JSON (definitions are plain JSON — no functions / dates). */
export function cloneDefinition(def: FormDefinition): FormDefinition {
  return JSON.parse(JSON.stringify(def)) as FormDefinition;
}

/** Recompute form_code from identity + version (the validator's invariant). */
export function formCodeFor(identity: string, version: number): string {
  return `${identity}-v${version}`;
}

/**
 * EDIT transform: keep the same identity, bump the version (jha-v1 → jha-v2), and
 * recompute form_code. The caller supplies the prior version so the bump is N+1.
 */
export function toEditDraft(def: FormDefinition, identity: string): FormDefinition {
  const next = cloneDefinition(def);
  next.version = def.version + 1;
  next.form_code = formCodeFor(identity, next.version);
  next.parent_form_code = def.parent_form_code;
  return next;
}

/**
 * ADD-VERSION / clone transform: a brand-new identity (manual slug), version 1, cloning
 * the source form's sections + archetype + parent. The new identity + name are filled in
 * by the editor (we blank the name so the admin must title it). source_pdf is carried
 * (optional) but blanked — editor-authored forms don't need a reference PDF.
 */
export function toClonedDraft(
  def: FormDefinition,
  newIdentity: string,
  parentFormCode: string,
): FormDefinition {
  const next = cloneDefinition(def);
  next.version = 1;
  next.parent_form_code = parentFormCode;
  next.form_code = formCodeFor(newIdentity, 1);
  next.form_name = def.form_name; // pre-fill; admin renames
  next.source_pdf = "";
  return next;
}

/** All top-level value keys a definition contributes (header non-reserved fields +
 *  keyed section keys) — used for cross-section-unique-key checks AND duplicate guards
 *  when generating new keys. Mirrors the validator's `topLevel` accumulation. */
const RESERVED_KEYS = new Set(["job", "work_date"]);
export function topLevelKeys(def: FormDefinition): string[] {
  const out: string[] = [];
  for (const s of def.sections) {
    if (s.type === "header") {
      for (const f of s.fields) if (!RESERVED_KEYS.has(f.key)) out.push(f.key);
    } else if (s.type !== "static_text") {
      out.push(s.key);
    }
  }
  return out;
}

export { RESERVED_KEYS };
