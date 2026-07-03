import { readdirSync, readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import type { Plugin } from "vite";

// ─────────────────────────────────────────────────────────────────────────────
// REGISTRY SPLIT (operator-APPROVED 2026-07-03) — the build-time half.
//
// Serves `virtual:eager-form-definitions` to the SPA registry (src/forms/registry.ts):
//   • EAGER        — static imports of every ACTIVE catalog form's CURRENT version plus
//                    its immediately-previous version (the operator-recommended buffer).
//                    These land in the synchronous bundle: the fill flow keeps its
//                    no-network fast path.
//   • LAZY_LOADERS — a `() => import(...)` per every OTHER shipped definition file
//                    (older history + retired identities). Vite/rolldown auto-chunks
//                    each dynamic import, so the append-only version pool stops growing
//                    the main chunk (~25KB per SOP edit, forever, under the old
//                    bundle-everything C1/C9 design this split reverses with sign-off).
//
// DATA-DRIVEN ON PURPOSE: the eager window is recomputed from the git-committed
// catalog.json on EVERY build. The form-publish daemon (safety_reports/publish_daemon.py)
// commits catalog.json + forms/<code>.json and redeploys via `npm run deploy` → `vite
// build`; any hand-maintained import list (or tree-writing codegen step) would rot on
// the first auto-publish or pollute the daemon's git flow. A virtual module touches
// nothing on disk.
//
// Filename convention (load-bearing, shared with the Python renderer's
// load_definition): forms/<form_code>.json. The registry re-validates content↔key at
// load; loadLazyDefinition re-validates content↔request per lazy load.
// ─────────────────────────────────────────────────────────────────────────────

const VIRTUAL_ID = "virtual:eager-form-definitions";
const RESOLVED_VIRTUAL_ID = "\0" + VIRTUAL_ID;

interface CatalogVersionEntry {
  version: number;
  form_code: string;
}
interface CatalogFormEntry {
  status: string;
  current_version: number;
  versions: CatalogVersionEntry[];
}
interface CatalogManifest {
  parents: { parent_form_code: string; forms: CatalogFormEntry[] }[];
}

/**
 * The eager window: for each ACTIVE catalog form entry, its current version's form_code
 * plus the immediately-previous version's (when one exists). Retired identities are
 * deliberately excluded — nothing in the SPA can reach them (formCatalog() filters
 * active), and including them would let the retired pool grow the eager bundle forever,
 * which is exactly what this split exists to stop. They stay shipped + lazy-loadable.
 *
 * FAILS LOUD on a malformed manifest (current_version absent from versions[]): a build
 * must never silently ship a portal whose fill flow can't resolve a current form.
 */
export function eagerFormCodes(manifest: CatalogManifest): string[] {
  const out = new Set<string>();
  for (const parent of manifest.parents ?? []) {
    for (const form of parent.forms ?? []) {
      if (form.status !== "active") continue;
      const versions = [...form.versions].sort((a, b) => a.version - b.version);
      const idx = versions.findIndex((v) => v.version === form.current_version);
      if (idx === -1) {
        throw new Error(
          `catalog.json: an active form under ${parent.parent_form_code} names ` +
            `current_version ${form.current_version} but versions[] has no such entry — ` +
            `refusing to build a portal that cannot resolve a current form`,
        );
      }
      out.add(versions[idx].form_code);
      if (idx > 0) out.add(versions[idx - 1].form_code);
    }
  }
  return [...out].sort();
}

export function eagerFormDefinitionsPlugin(): Plugin {
  // Both consumers of this plugin (vite.config.ts, vitest.config.spa.ts) live in
  // safety_portal/ alongside this file, catalog.json, and forms/.
  const here = path.dirname(fileURLToPath(import.meta.url));
  const catalogPath = path.join(here, "catalog.json");
  const formsDir = path.join(here, "forms");

  return {
    name: "its:eager-form-definitions",
    resolveId(id) {
      return id === VIRTUAL_ID ? RESOLVED_VIRTUAL_ID : undefined;
    },
    load(id) {
      if (id !== RESOLVED_VIRTUAL_ID) return undefined;
      // Dev-server correctness: a catalog edit invalidates the virtual module.
      this.addWatchFile(catalogPath);

      const manifest = JSON.parse(readFileSync(catalogPath, "utf8")) as CatalogManifest;
      const eager = eagerFormCodes(manifest);
      const shipped = readdirSync(formsDir)
        .filter((f) => f.endsWith(".json") && f !== "meta-schema.json")
        .map((f) => f.replace(/\.json$/, ""))
        .sort();
      const shippedSet = new Set(shipped);
      for (const code of eager) {
        if (!shippedSet.has(code)) {
          throw new Error(
            `catalog.json places ${code} in the eager window but forms/${code}.json ` +
              `does not exist — catalog and definition files are committed together; ` +
              `this build is inconsistent`,
          );
        }
      }

      const fileOf = (code: string) => JSON.stringify(path.join(formsDir, `${code}.json`));
      const lines: string[] = [];
      eager.forEach((code, i) => lines.push(`import e${i} from ${fileOf(code)};`));
      lines.push("export const EAGER = {");
      eager.forEach((code, i) => lines.push(`  ${JSON.stringify(code)}: e${i},`));
      lines.push("};");
      lines.push("export const LAZY_LOADERS = {");
      for (const code of shipped) {
        if (shippedSet.has(code) && !eager.includes(code)) {
          lines.push(
            `  ${JSON.stringify(code)}: () => import(${fileOf(code)}).then((m) => m.default),`,
          );
        }
      }
      lines.push("};");
      return lines.join("\n");
    },
  };
}
