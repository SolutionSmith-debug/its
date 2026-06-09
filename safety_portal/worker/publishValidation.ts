// Server-side validation of a composed form definition at the POST /api/admin/publish
// enqueue gate (design brief C3). The Worker is the FIRST gate; the Mac daemon (slice
// 3b) RE-validates against the live git HEAD (authoritative) and CI runs the 3-renderer
// smoke (3c). ajv can't run in Cloudflare Workers (the runtime forbids eval / new
// Function, which ajv's runtime schema compilation needs), so this is a hand-rolled
// validator of the CLOSED vocabulary (forms/meta-schema.json + src/forms/types.ts) plus
// the rules a JSON-schema can't express: the reserved-key denylist, cross-section-unique
// value keys, and hard bounds on adversarial input. It returns a STABLE reason string so
// the editor can surface it; it never throws.

const INPUTS = new Set(["text", "textarea", "date", "time", "number", "select", "signature"]);
const ITEM_KINDS = new Set(["rated", "numeric", "circle_one", "text"]);
const FREEFORM_INPUTS = new Set(["textarea", "text"]);
const EMPHASES = new Set(["footer", "heading", "legal"]);
const ARCHETYPES = new Set([
  "rows_signatures", "grouped_checklist", "content_signin", "visitor_rows", "sectioned_assessment",
]);
// The submission envelope (job dropdown + work-date picker) owns these top-level keys;
// the SPA FormRenderer skips header fields named these (ENVELOPE_KEYS). They are allowed
// ONLY as header fields (the existing convention) — never as a non-header value key,
// which would collide with the envelope in the submission's top-level namespace.
const RESERVED_KEYS = new Set(["job", "work_date"]);

const KEY_RE = /^[a-z0-9_]+$/; // every field/section key in the shipped forms is snake_case
const FORM_CODE_RE = /^[a-z0-9-]+-v[0-9]+$/;
const SLUG_RE = /^[a-z0-9-]+$/;

// Hard bounds — bound adversarial input; generous vs the real forms (the telehandler
// checklist has 64 items; HSS&E has 8 sections).
const MAX_SECTIONS = 40;
const MAX_FIELDS = 250;
const MAX_GROUPS = 40;
const MAX_ITEMS = 250;
const MAX_BLOCKS = 100;
const MAX_STR = 8000;

export interface DefinitionContext {
  identity: string;
  parentFormCode: string;
}
export type ValidationResult = { ok: true } | { ok: false; reason: string };

function isObject(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}
function isStr(v: unknown, max = MAX_STR): v is string {
  return typeof v === "string" && v.length > 0 && v.length <= max;
}
function fail(reason: string): ValidationResult {
  return { ok: false, reason };
}

/** A header/table column field: { key, label, input, options?, required? }. */
function validateField(f: unknown, where: string): { key: string } | string {
  if (!isObject(f)) return `${where}: field is not an object`;
  if (!isStr(f.key) || !KEY_RE.test(f.key as string)) return `${where}: invalid field key`;
  if (!isStr(f.label)) return `${where}: field ${f.key as string} missing label`;
  if (typeof f.input !== "string" || !INPUTS.has(f.input)) return `${where}: field ${f.key as string} invalid input`;
  if (f.options !== undefined) {
    if (!Array.isArray(f.options) || !f.options.every((o) => isStr(o, 500))) {
      return `${where}: field ${f.key as string} invalid options`;
    }
  }
  if (f.input === "select" && (!Array.isArray(f.options) || f.options.length === 0)) {
    return `${where}: select field ${f.key as string} needs non-empty options`;
  }
  if (f.required !== undefined && typeof f.required !== "boolean") return `${where}: field ${f.key as string} invalid required`;
  return { key: f.key as string };
}

/**
 * Validate ONE section, accumulating its top-level value keys into `topLevel` (header
 * non-reserved fields + the keyed section's own key) so the caller can enforce global
 * uniqueness + the reserved-key rule across the whole form.
 */
function validateSection(s: unknown, idx: number, topLevel: string[]): string | null {
  const where = `section[${idx}]`;
  if (!isObject(s)) return `${where} is not an object`;
  const type = s.type;
  if (typeof type !== "string") return `${where} missing type`;

  const keyedSectionKey = (): string | null => {
    if (!isStr(s.key) || !KEY_RE.test(s.key as string)) return `${where}: invalid section key`;
    if (RESERVED_KEYS.has(s.key as string)) return `${where}: '${s.key as string}' is reserved for the submission envelope`;
    topLevel.push(s.key as string);
    return null;
  };
  const localUnique = (keys: string[], label: string): string | null => {
    const seen = new Set<string>();
    for (const k of keys) {
      if (seen.has(k)) return `${where}: duplicate ${label} key '${k}'`;
      seen.add(k);
    }
    return null;
  };

  switch (type) {
    case "header": {
      if (!Array.isArray(s.fields) || s.fields.length > MAX_FIELDS) return `${where}: invalid header fields`;
      const keys: string[] = [];
      for (const f of s.fields) {
        const r = validateField(f, where);
        if (typeof r === "string") return r;
        keys.push(r.key);
        // job/work_date header fields are envelope-skipped → NOT top-level value keys.
        if (!RESERVED_KEYS.has(r.key)) topLevel.push(r.key);
      }
      return localUnique(keys, "header field");
    }
    case "static_text": {
      if (!isStr(s.text)) return `${where}: static_text missing text`;
      if (s.emphasis !== undefined && (typeof s.emphasis !== "string" || !EMPHASES.has(s.emphasis))) {
        return `${where}: invalid emphasis`;
      }
      return null;
    }
    case "repeating_table":
    case "signature_table": {
      const e = keyedSectionKey();
      if (e) return e;
      if (!Array.isArray(s.columns) || s.columns.length === 0 || s.columns.length > MAX_FIELDS) {
        return `${where}: invalid columns`;
      }
      const colKeys: string[] = [];
      let sigCount = 0;
      for (const col of s.columns) {
        const r = validateField(col, where);
        if (typeof r === "string") return r;
        colKeys.push(r.key);
        if (isObject(col) && col.input === "signature") sigCount++;
      }
      if (type === "signature_table" && sigCount !== 1) return `${where}: signature_table needs exactly one signature column`;
      if (s.min_rows !== undefined && (typeof s.min_rows !== "number" || !Number.isInteger(s.min_rows) || s.min_rows < 0)) {
        return `${where}: invalid min_rows`;
      }
      if (s.allow_add !== undefined && typeof s.allow_add !== "boolean") return `${where}: invalid allow_add`;
      return localUnique(colKeys, "column");
    }
    case "checklist": {
      const e = keyedSectionKey();
      if (e) return e;
      if (!Array.isArray(s.groups) || s.groups.length === 0 || s.groups.length > MAX_GROUPS) {
        return `${where}: invalid checklist groups`;
      }
      const allKeys: string[] = [];
      for (const g of s.groups) {
        if (!isObject(g)) return `${where}: group is not an object`;
        if (!isStr(g.key) || !KEY_RE.test(g.key as string)) return `${where}: invalid group key`;
        if (!isStr(g.label)) return `${where}: group ${g.key as string} missing label`;
        if (!Array.isArray(g.scale) || g.scale.length === 0 || !g.scale.every((x) => isStr(x, 200))) {
          return `${where}: group ${g.key as string} invalid scale`;
        }
        if (!Array.isArray(g.items) || g.items.length === 0 || g.items.length > MAX_ITEMS) {
          return `${where}: group ${g.key as string} invalid items`;
        }
        allKeys.push(g.key as string);
        for (const it of g.items) {
          if (!isObject(it)) return `${where}: item is not an object`;
          if (!isStr(it.key) || !KEY_RE.test(it.key as string)) return `${where}: invalid item key`;
          if (!isStr(it.label)) return `${where}: item ${it.key as string} missing label`;
          if (it.kind !== undefined && (typeof it.kind !== "string" || !ITEM_KINDS.has(it.kind))) {
            return `${where}: item ${it.key as string} invalid kind`;
          }
          allKeys.push(it.key as string);
        }
      }
      return localUnique(allKeys, "checklist group/item");
    }
    case "freeform": {
      const e = keyedSectionKey();
      if (e) return e;
      if (!isStr(s.label)) return `${where}: freeform missing label`;
      if (s.input !== undefined && (typeof s.input !== "string" || !FREEFORM_INPUTS.has(s.input))) {
        return `${where}: freeform invalid input`;
      }
      return null;
    }
    case "content_blocks": {
      const e = keyedSectionKey();
      if (e) return e;
      if (!Array.isArray(s.blocks) || s.blocks.length === 0 || s.blocks.length > MAX_BLOCKS) {
        return `${where}: invalid content blocks`;
      }
      for (const b of s.blocks) {
        if (!isObject(b)) return `${where}: block is not an object`;
        if (!isStr(b.body)) return `${where}: block missing body`;
        if (b.heading !== undefined && !isStr(b.heading)) return `${where}: block invalid heading`;
      }
      return null;
    }
    default:
      return `${where}: unknown section type '${type}'`;
  }
}

/**
 * Validate a composed FormDefinition for the create / edit / add_version publish ops.
 * `ctx.identity` + `ctx.parentFormCode` come from the request envelope; the definition
 * must agree with them (form_code = identity-v<version>, parent matches).
 */
export function validateDefinition(def: unknown, ctx: DefinitionContext): ValidationResult {
  if (!isObject(def)) return fail("definition is not an object");

  if (typeof def.version !== "number" || !Number.isInteger(def.version) || def.version < 1) {
    return fail("invalid version");
  }
  if (!isStr(def.form_code) || !FORM_CODE_RE.test(def.form_code)) return fail("invalid form_code");
  if (def.form_code !== `${ctx.identity}-v${def.version}`) {
    return fail(`form_code must be ${ctx.identity}-v${def.version}`);
  }
  if (!isStr(def.parent_form_code) || !SLUG_RE.test(def.parent_form_code)) return fail("invalid parent_form_code");
  if (def.parent_form_code !== ctx.parentFormCode) return fail("parent_form_code does not match the request");
  if (!isStr(def.form_name)) return fail("invalid form_name");
  if (typeof def.archetype !== "string" || !ARCHETYPES.has(def.archetype)) return fail("invalid archetype");
  if (def.variant_label !== undefined && def.variant_label !== null && !isStr(def.variant_label)) {
    return fail("invalid variant_label");
  }
  // source_pdf is OPTIONAL for editor-authored forms (design B9); when present it must
  // be a plain string.
  if (def.source_pdf !== undefined && typeof def.source_pdf !== "string") return fail("invalid source_pdf");

  if (!Array.isArray(def.sections) || def.sections.length === 0 || def.sections.length > MAX_SECTIONS) {
    return fail("invalid sections");
  }
  const topLevel: string[] = [];
  for (let i = 0; i < def.sections.length; i++) {
    const e = validateSection(def.sections[i], i, topLevel);
    if (e) return fail(e);
  }
  // Cross-section-unique top-level value keys (header non-reserved fields + keyed
  // section keys) — a collision would make the submission ambiguous.
  const seen = new Set<string>();
  for (const k of topLevel) {
    if (seen.has(k)) return fail(`duplicate value key '${k}' across sections`);
    seen.add(k);
  }
  return { ok: true };
}
