// Shared TYPE-ONLY contract for the field-ops READ-layer route modules (P2.2). Type-only so it
// carries no runtime + creates no import cycle: index.ts builds the concrete gates and passes
// them into each tab's register*Routes(app, gates), so the per-tab modules never import index.ts.
import type { Hono, MiddlewareHandler } from "hono";
import type { Env, Vars } from "./types";

export type FieldopsApp = Hono<{ Bindings: Env; Variables: Vars }>;

export type FieldopsGates = {
  requireSession: MiddlewareHandler<{ Bindings: Env; Variables: Vars }>;
  requireCapability: (cap: string) => MiddlewareHandler<{ Bindings: Env; Variables: Vars }>;
};
