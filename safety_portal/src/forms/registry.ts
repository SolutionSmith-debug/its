import type { FormDefinition } from "./types";

// Bundle every form definition at build time (Vite eager glob). The definitions
// are CODE (the rendering contract, byte-unchanged across slices) and stay
// ALL-bundled on purpose: getDefinition() must resolve ANY historical form_code —
// including retired / superseded versions — so filed and in-flight submissions
// always render (append-only files; design C1/C9). meta-schema.json is excluded.
const modules = import.meta.glob<FormDefinition>("../../forms/*.json", {
  eager: true,
  import: "default",
});

export const DEFINITIONS: Record<string, FormDefinition> = {};
for (const [path, def] of Object.entries(modules)) {
  if (path.endsWith("meta-schema.json")) continue;
  DEFINITIONS[def.form_code] = def;
}

/** The form definition for a Form Code, or null if not bundled. Resolves ANY
 * historical code (active, retired, or superseded) — never gated by the active set. */
export function getDefinition(formCode: string): FormDefinition | null {
  return DEFINITIONS[formCode] ?? null;
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
interface CatalogParentEntry {
  parent_form_code: string;
  name: string;
  display_order: number;
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
        form_code: active[0].current_form_code,
        variants: [],
      });
    } else {
      parents.push({
        parent_form_code: parent.parent_form_code,
        name: parent.name,
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
