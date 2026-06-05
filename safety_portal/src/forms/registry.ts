import type { FormDefinition } from "./types";

// Bundle every form definition at build time (Vite eager glob). The definitions
// are CODE (the rendering contract); which forms are *active* and the
// parent→variant structure come from the catalog (D1, via /api/forms — synced
// from ITS_Forms_Catalog). meta-schema.json is excluded.
const modules = import.meta.glob<FormDefinition>("../../forms/*.json", {
  eager: true,
  import: "default",
});

export const DEFINITIONS: Record<string, FormDefinition> = {};
for (const [path, def] of Object.entries(modules)) {
  if (path.endsWith("meta-schema.json")) continue;
  DEFINITIONS[def.form_code] = def;
}

/** The form definition for a catalog Form Code, or null if not bundled. */
export function getDefinition(formCode: string): FormDefinition | null {
  return DEFINITIONS[formCode] ?? null;
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
 * Parent → variant catalog, derived from the bundled definitions (a no-variant
 * parent renders its own definition; a variant parent shows the 3rd picklist).
 * Mirrors the ITS_Forms_Catalog parent/variant rows. (When the catalog→D1 sync
 * lands, filter by the D1 Active set + Display Order instead of all-bundled.)
 */
export function formCatalog(): CatalogParent[] {
  const byParent = new Map<string, FormDefinition[]>();
  for (const def of Object.values(DEFINITIONS)) {
    const arr = byParent.get(def.parent_form_code) ?? [];
    arr.push(def);
    byParent.set(def.parent_form_code, arr);
  }
  const parents: CatalogParent[] = [];
  for (const [pcode, defs] of byParent) {
    const variants = defs.filter((d) => d.variant_label != null);
    if (variants.length === 0) {
      parents.push({ parent_form_code: pcode, name: defs[0].form_name, form_code: defs[0].form_code, variants: [] });
    } else {
      parents.push({
        parent_form_code: pcode,
        name: variants[0].form_name.split("—")[0].trim(), // "Equipment Pre-Inspection — X" → parent
        form_code: null,
        variants: variants
          .map((d) => ({ variant_label: d.variant_label as string, form_code: d.form_code }))
          .sort((a, b) => a.variant_label.localeCompare(b.variant_label)),
      });
    }
  }
  return parents.sort((a, b) => a.name.localeCompare(b.name));
}
