import type { FormDefinition } from "./types";
import { EAGER, LAZY_LOADERS } from "virtual:eager-form-definitions";

// ─────────────────────────────────────────────────────────────────────────────
// REGISTRY SPLIT (operator-APPROVED 2026-07-03). This REVERSES the documented C1/C9
// "bundle every definition version" design, WITH operator sign-off: "absolutely need
// to split the registry — that would very quickly become a problem and crash our
// website." Definition versions are append-only and SOP-heavy (~25KB per daily-report
// edit, forever); the old eager-everything glob put the whole pool (~160KB and growing)
// in the main chunk.
//
// The split (built by vite-plugin-eager-forms.ts from catalog.json AT BUILD TIME —
// data-driven, so the publish daemon's definition-only auto-commits keep working with
// zero manual steps):
//   • EAGER (synchronous, in the main bundle): every ACTIVE catalog form's CURRENT
//     version + its immediately-previous version (operator-recommended buffer). The
//     fill flow only ever renders current definitions, so it keeps the sync,
//     no-network fast path via getDefinition().
//   • LAZY (dynamic import(), one Vite auto-chunk per file): every OTHER shipped
//     version — older history + retired identities — via getDefinitionFor(). All
//     files stay shipped append-only; only their BUNDLING changed. Verified
//     2026-07-03: NO production SPA surface renders a non-eager definition today
//     (fill + amend render the CURRENT definition; filed-submission viewing is the
//     server-side PDF path), so the async path currently serves future
//     historical-render surfaces and the test net.
// ─────────────────────────────────────────────────────────────────────────────

export const DEFINITIONS: Record<string, FormDefinition> = EAGER;
// Packaging regressions must fail LOUD on load (same standard as the manifest checks
// below): an empty eager window or a filename↔content drift would otherwise surface as
// a blank form picker or a wrong form rendered under a right name.
if (Object.keys(DEFINITIONS).length === 0) {
  throw new Error("virtual:eager-form-definitions produced an empty eager window");
}
for (const [code, def] of Object.entries(DEFINITIONS)) {
  if (def.form_code !== code) {
    throw new Error(
      `eager form definition drift: forms/${code}.json carries form_code ${def.form_code}`,
    );
  }
}

/** The form definition for a Form Code IF it is in the eager window (every active
 * form's current + immediately-previous version), else null. Historical / retired
 * codes are no longer sync-resolvable (registry split, 2026-07-03) — use
 * getDefinitionFor() for those. Every production fill/render surface passes
 * catalog-derived CURRENT codes, so this stays their synchronous fast path. */
export function getDefinition(formCode: string): FormDefinition | null {
  return DEFINITIONS[formCode] ?? null;
}

/** A lazy definition chunk failed to LOAD (network / packaging), or loaded content
 * that contradicts the requested code — distinct from "unknown form_code", which
 * resolves to null. UI consumers must treat this as retryable and NEVER silent:
 * render an error state with a Retry that re-calls getDefinitionFor (a failed
 * dynamic-import fetch is not cached, so a retry re-fetches). */
export class DefinitionLoadError extends Error {
  readonly formCode: string;
  constructor(formCode: string, cause: unknown) {
    super(`form definition ${formCode} failed to load`, { cause });
    this.name = "DefinitionLoadError";
    this.formCode = formCode;
  }
}

/** Internal seam of getDefinitionFor (exported for unit tests): resolve `formCode`
 * against a loader map. null = unknown code; DefinitionLoadError = load/content
 * failure (typed so callers can render error+Retry per the house standard). */
export async function loadLazyDefinition(
  formCode: string,
  loaders: Record<string, () => Promise<FormDefinition>>,
): Promise<FormDefinition | null> {
  const loader = loaders[formCode];
  if (!loader) return null;
  let def: FormDefinition;
  try {
    def = await loader();
  } catch (e) {
    throw new DefinitionLoadError(formCode, e);
  }
  if (def.form_code !== formCode) {
    throw new DefinitionLoadError(
      formCode,
      new Error(`forms/${formCode}.json carries form_code ${def.form_code}`),
    );
  }
  return def;
}

/** The form definition for ANY shipped Form Code — current, superseded, or retired
 * (the append-only pool; old C1/C9 resolve-anything contract, now async). Eager codes
 * resolve immediately from the bundle; anything else dynamically imports its own
 * chunk. Returns null for an unknown code; throws DefinitionLoadError on a failed
 * chunk load (see its doc: consumers show loading, then never-silent error+Retry).
 * No production surface needs this today (see header comment) — any future
 * historical-render surface (e.g. viewing a filed submission's original version
 * in-SPA) goes through here instead of re-widening the eager bundle. */
export async function getDefinitionFor(formCode: string): Promise<FormDefinition | null> {
  return DEFINITIONS[formCode] ?? loadLazyDefinition(formCode, LAZY_LOADERS);
}

// ── catalog manifest: the ACTIVE-set / current-version / order / name overlay ──
// Source of truth for WHICH forms are active, their parent→variant grouping,
// display order, and parent display names: the git-committed safety_portal/
// catalog.json. This REPLACES the never-built ITS_Forms_Catalog→D1→/api/forms sync
// (design B2). Loaded via the same Vite glob as the definitions. Phase-2 (the form
// editor) WRITES the manifest; here it is read-only. Shape is enforced by
// safety_portal/catalog.schema.json + tests/test_form_catalog.py.
interface CatalogFormEntry {
  identity: string;
  variant_label: string | null;
  status: "active" | "retired";
  current_version: number;
  current_form_code: string;
  versions: { version: number; form_code: string }[];
  display_order: number;
}
// ── workflow registry: the single source of truth for the workflow SET ──────────
// safety_portal/workflows.json (mirrored by shared/form_category.py + worker/publishValidation.ts).
// Drives the form-builder workflow selector + the submitter picker tabs. Adding a workflow there
// is a DATA change, not a code change across the stack. Loaded via the same Vite glob as the
// catalog manifest below.
interface WorkflowEntry {
  id: string;
  label: string;
  display_order?: number;
}
interface WorkflowRegistry {
  registry_version: number;
  default: string;
  workflows: WorkflowEntry[];
}
const workflowModules = import.meta.glob<WorkflowRegistry>("../../workflows.json", {
  eager: true,
  import: "default",
});
const WORKFLOWS = Object.values(workflowModules)[0];
if (!WORKFLOWS?.workflows?.length) {
  throw new Error("safety_portal/workflows.json failed to load (Vite glob matched no registry)");
}
/** Workflow entries in display order — drives the form-builder selector + the submitter tabs. */
export const WORKFLOWS_ORDERED: WorkflowEntry[] = [...WORKFLOWS.workflows].sort(
  (a, b) => (a.display_order ?? 0) - (b.display_order ?? 0),
);
export const WORKFLOW_IDS: ReadonlySet<string> = new Set(WORKFLOWS_ORDERED.map((w) => w.id));
export const DEFAULT_WORKFLOW: string = WORKFLOWS.default;
export function workflowLabel(id: string): string {
  return WORKFLOWS_ORDERED.find((w) => w.id === id)?.label ?? id;
}

/** A workflow id (e.g. "safety", "progress"). A runtime-validated string, NOT a compile-time
 *  union — the valid SET is config-driven in workflows.json (so a future workflow is data, not a
 *  5-surface code change). An absent catalog `category` defaults to DEFAULT_WORKFLOW. */
export type FormCategory = string;
interface CatalogParentEntry {
  parent_form_code: string;
  name: string;
  display_order: number;
  category?: FormCategory;
  /** OPTIONAL launch surface ("daily-tab") — see catalog.schema.json. D2 consumes it. */
  launch?: string;
  forms: CatalogFormEntry[];
}
interface CatalogManifest {
  manifest_version: number;
  parents: CatalogParentEntry[];
}

const catalogModules = import.meta.glob<CatalogManifest>("../../catalog.json", {
  eager: true,
  import: "default",
});
// The single git-committed manifest, bundled at build time.
const MANIFEST = Object.values(catalogModules)[0];
// A packaging regression (the glob matching no file) must fail LOUD on load — never
// silently render an empty form picker.
if (!MANIFEST?.parents) {
  throw new Error("safety_portal/catalog.json failed to load (Vite glob matched no manifest)");
}

export interface CatalogVariant {
  variant_label: string;
  form_code: string;
}
export interface CatalogParent {
  parent_form_code: string;
  name: string;
  /** Safety vs Progress (P1); an absent manifest category defaults to "safety". */
  category: FormCategory;
  /** OPTIONAL launch surface (SOP daily form D1): "daily-tab" = launched from the Daily
   *  tab experience. Slice D2 consumes it (hide from the Submit-a-Form CREATE picker);
   *  carried through here so pickers can read it without re-parsing the manifest. */
  launch?: string;
  /** definition code for a no-variant parent; null when the parent has variants */
  form_code: string | null;
  variants: CatalogVariant[];
}

/**
 * Parent → variant catalog for the PM form picker, driven by the git-committed
 * manifest: only ACTIVE forms, in the manifest's display order, with parent names +
 * current-version form_codes from the manifest. A no-variant parent renders its own
 * definition; a variant parent shows the 3rd picklist. 1a's snapshot test proves
 * this reproduces the prior all-bundled derivation EXACTLY (a PM-visible no-op flip);
 * the manifest now OWNS the active set / order / names, so slices 4–6 + rollback can
 * change them without touching this code.
 */
/**
 * Resolve a checklist item's stored form_code (the PARENT family, e.g. 'daily-report') to the
 * FormFillPage deep-link selection: { parentCode, variantCode }.
 *   • The daily checklist stores the parent form_code (catalog.json parent_form_code); FormFillPage
 *     selects a parent + (for variant parents) the current variant's form_code.
 *   • No-variant parent (e.g. daily-report) → variantCode '' (FormFillPage renders the parent directly).
 *   • Single-variant parent → pre-select that variant. Multi-variant (e.g. equipment-preinspection) →
 *     variantCode '' so the user picks the type (we cannot know which one the item means).
 *   • Robust to an item that (unusually) stored a VARIANT current_form_code: match it to its parent.
 *   • Unknown code → { parentCode: formCode, variantCode: '' } (a harmless best-effort; the picker
 *     simply shows nothing selected).
 */
export function resolveFormTarget(formCode: string): { parentCode: string; variantCode: string } {
  const catalog = formCatalog();
  const asParent = catalog.find((p) => p.parent_form_code === formCode);
  if (asParent) {
    const variantCode = asParent.variants.length === 1 ? asParent.variants[0].form_code : "";
    return { parentCode: formCode, variantCode };
  }
  const owningParent = catalog.find((p) => p.variants.some((v) => v.form_code === formCode));
  if (owningParent) return { parentCode: owningParent.parent_form_code, variantCode: formCode };
  return { parentCode: formCode, variantCode: "" };
}

export function formCatalog(): CatalogParent[] {
  const parents: CatalogParent[] = [];
  const ordered = [...MANIFEST.parents].sort((a, b) => a.display_order - b.display_order);
  for (const parent of ordered) {
    const active = parent.forms
      .filter((f) => f.status === "active")
      .sort((a, b) => a.display_order - b.display_order);
    if (active.length === 0) continue; // a fully-retired parent drops from the picker
    const variants = active.filter((f) => f.variant_label != null);
    if (variants.length === 0) {
      // no-variant parent: a single null-variant form, rendered directly
      parents.push({
        parent_form_code: parent.parent_form_code,
        name: parent.name,
        category: parent.category ?? DEFAULT_WORKFLOW,
        ...(parent.launch !== undefined ? { launch: parent.launch } : {}),
        form_code: active[0].current_form_code,
        variants: [],
      });
    } else {
      parents.push({
        parent_form_code: parent.parent_form_code,
        name: parent.name,
        category: parent.category ?? DEFAULT_WORKFLOW,
        ...(parent.launch !== undefined ? { launch: parent.launch } : {}),
        form_code: null,
        variants: variants.map((f) => ({
          variant_label: f.variant_label as string,
          form_code: f.current_form_code,
        })),
      });
    }
  }
  return parents;
}
