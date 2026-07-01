// Shared TYPE-ONLY contract for the field-ops READ-layer route modules (P2.2). Type-only so it
// carries no runtime + creates no import cycle: index.ts builds the concrete gates and passes
// them into each tab's register*Routes(app, gates), so the per-tab modules never import index.ts.
import type { Hono, MiddlewareHandler } from "hono";
import type { Env, Vars } from "./types";

export type FieldopsApp = Hono<{ Bindings: Env; Variables: Vars }>;

export type FieldopsGates = {
  requireSession: MiddlewareHandler<{ Bindings: Env; Variables: Vars }>;
  requireCapability: (cap: string) => MiddlewareHandler<{ Bindings: Env; Variables: Vars }>;
  // OR-capability gate: authorizes if the session holds ANY of `caps`. Same fail-closed style as
  // requireCapability (empty/missing capability set → 403). Used by routes that accept more than one
  // capability, e.g. the task write routes (cap.jobtracker.manage OR cap.tasks.assign).
  requireAnyCapability: (caps: readonly string[]) => MiddlewareHandler<{ Bindings: Env; Variables: Vars }>;
};
