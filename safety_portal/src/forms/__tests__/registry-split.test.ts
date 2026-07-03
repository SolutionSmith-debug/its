/**
 * Registry-split contract (operator-APPROVED 2026-07-03 — reverses the C1/C9
 * bundle-everything design with sign-off; see src/forms/registry.ts header).
 *
 * What this suite locks:
 *   1. The EAGER window is EXACTLY every active catalog form's current version + its
 *      immediately-previous version — no more (bundle growth is the bug this split
 *      fixes), no less (the fill flow's sync fast path must never miss a current form).
 *   2. Every OTHER shipped definition still resolves — via the REAL lazy path
 *      (getDefinitionFor → the virtual module's per-file dynamic import), so the
 *      append-only pool remains fully reachable, just not eagerly bundled.
 *   3. The failure modes are typed and distinct: unknown code → null;
 *      failed/mismatched chunk load → DefinitionLoadError (retryable, never silent).
 *
 * TEST-ONLY EAGER GLOB (deliberate): this file imports EVERY shipped definition
 * eagerly to enumerate the full pool. That is fine HERE — vitest executes it in the
 * test process and nothing in src/ imports this file, so the shipped bundle is
 * unaffected — and it is REQUIRED so a definition file orphaned by the split (present
 * on disk, resolvable by neither the eager map nor a lazy loader) fails loudly instead
 * of surfacing as a filed submission that can never re-render.
 */
import { describe, expect, it, vi } from "vitest";

import catalog from "../../../catalog.json";
import {
  DEFINITIONS,
  DefinitionLoadError,
  getDefinition,
  getDefinitionFor,
  loadLazyDefinition,
} from "../registry";
import type { FormDefinition } from "../types";

// Every shipped definition file (test-only eager glob — see header).
const ALL_SHIPPED = import.meta.glob<FormDefinition>(
  ["../../../forms/*.json", "!../../../forms/meta-schema.json"],
  { eager: true, import: "default" },
);

interface CatalogVersion {
  version: number;
  form_code: string;
}
interface CatalogForm {
  status: string;
  current_version: number;
  versions: CatalogVersion[];
}
interface Catalog {
  parents: { forms: CatalogForm[] }[];
}

/** The window the split promises: active current + immediately-previous per form. */
function expectedEagerCodes(): Set<string> {
  const out = new Set<string>();
  for (const parent of (catalog as Catalog).parents) {
    for (const form of parent.forms) {
      if (form.status !== "active") continue;
      const versions = [...form.versions].sort((a, b) => a.version - b.version);
      const idx = versions.findIndex((v) => v.version === form.current_version);
      expect(idx, `current_version ${form.current_version} missing from versions[]`).toBeGreaterThanOrEqual(0);
      out.add(versions[idx].form_code);
      if (idx > 0) out.add(versions[idx - 1].form_code);
    }
  }
  return out;
}

const EAGER_CODES = expectedEagerCodes();

function stemOf(path: string): string {
  return path.slice(path.lastIndexOf("/") + 1).replace(/\.json$/, "");
}

describe("filename convention (the loaders' contract)", () => {
  it("every shipped definition's form_code matches its filename stem", () => {
    const files = Object.entries(ALL_SHIPPED);
    expect(files.length, "test glob matched no definition files").toBeGreaterThan(0);
    for (const [path, def] of files) {
      expect(def.form_code, path).toBe(stemOf(path));
    }
  });
});

describe("eager window", () => {
  it("is exactly the active catalog forms' current + immediately-previous versions", () => {
    expect(new Set(Object.keys(DEFINITIONS))).toEqual(EAGER_CODES);
  });

  it("sync getDefinition resolves the whole window and NOTHING else shipped", () => {
    const lazyCodes: string[] = [];
    for (const [path, def] of Object.entries(ALL_SHIPPED)) {
      const code = stemOf(path);
      if (EAGER_CODES.has(code)) {
        expect(getDefinition(code)?.form_code, path).toBe(def.form_code);
      } else {
        lazyCodes.push(code);
        expect(getDefinition(code), `${code} leaked into the sync eager window`).toBeNull();
      }
    }
    // Load-bearing: versions are append-only and daily-report already carries v1–v3
    // outside the window, so an empty lazy pool means this suite stopped testing the
    // split at all (e.g. the glob broke) — fail loud rather than pass vacuously.
    expect(lazyCodes.length, "no lazy (non-eager) shipped definitions found").toBeGreaterThan(0);
  });
});

describe("lazy pool (the real dynamic-import path)", () => {
  it("EVERY shipped definition — current and historical — resolves via getDefinitionFor", async () => {
    for (const [path, def] of Object.entries(ALL_SHIPPED)) {
      const code = stemOf(path);
      const resolved = await getDefinitionFor(code);
      expect(resolved, `${code} did not resolve through the split registry`).not.toBeNull();
      expect(resolved?.form_code, path).toBe(def.form_code);
    }
  });

  it("an unknown form_code resolves to null on both paths (not an error)", async () => {
    expect(getDefinition("no-such-form-v99")).toBeNull();
    await expect(getDefinitionFor("no-such-form-v99")).resolves.toBeNull();
  });
});

describe("loadLazyDefinition seam (mocked loaders)", () => {
  const fakeDef = { form_code: "fake-v1", form_name: "Fake", version: 1 } as FormDefinition;

  it("a historical version lazy-loads through its loader exactly once", async () => {
    const loader = vi.fn(async () => fakeDef);
    await expect(loadLazyDefinition("fake-v1", { "fake-v1": loader })).resolves.toBe(fakeDef);
    expect(loader).toHaveBeenCalledTimes(1);
  });

  it("a failed chunk load rejects with the typed, retryable DefinitionLoadError", async () => {
    const boom = new Error("network");
    const p = loadLazyDefinition("fake-v1", {
      "fake-v1": () => Promise.reject(boom),
    });
    await expect(p).rejects.toBeInstanceOf(DefinitionLoadError);
    const err: unknown = await p.catch((e: unknown) => e);
    const typed = err as DefinitionLoadError;
    expect(typed.formCode).toBe("fake-v1");
    expect(typed.cause).toBe(boom);
  });

  it("content that contradicts the requested code is a DefinitionLoadError, not a wrong form", async () => {
    const p = loadLazyDefinition("other-v2", { "other-v2": async () => fakeDef });
    await expect(p).rejects.toBeInstanceOf(DefinitionLoadError);
    await expect(p).rejects.toMatchObject({ formCode: "other-v2" });
  });
});
