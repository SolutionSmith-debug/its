/// <reference types="vite/client" />

// Registry split (2026-07-03): the eager/lazy definition maps served at build time by
// vite-plugin-eager-forms.ts from catalog.json. Consumed only by src/forms/registry.ts.
declare module "virtual:eager-form-definitions" {
  export const EAGER: Record<string, import("./forms/types").FormDefinition>;
  export const LAZY_LOADERS: Record<
    string,
    () => Promise<import("./forms/types").FormDefinition>
  >;
}
