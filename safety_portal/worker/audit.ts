// Shared audit + error helpers for D1 mutation routes. Extracted from index.ts (P2.3) so the
// per-entity field-ops WRITE modules can build the audit_log INSERT and map UNIQUE races to 409
// without importing index.ts (a runtime import cycle — index.ts registers the write modules).
// Depends only on the shared Env/Vars types, so no cycle.
import type { Context } from "hono";
import type { Env, Vars } from "./types";

/** True if a D1 error is a UNIQUE-constraint violation. Lets the create/rename/amend routes
 *  map a lost check-then-act race (the second writer hits UNIQUE) to a clean 409 instead of
 *  letting it bubble to a 500 (audit #5). */
export function isUniqueViolation(e: unknown): boolean {
  const msg = e instanceof Error ? e.message : String(e);
  return /UNIQUE constraint failed/i.test(msg);
}

/** Build (not execute) the audit_log INSERT — included in the mutation's batch so the record
 *  is atomic with the change it describes (W4). `detail` is JSON-encoded. */
export function auditStmt(
  c: Context<{ Bindings: Env; Variables: Vars }>,
  actor: string,
  action: string,
  target: string | null,
  detail: Record<string, unknown> | null,
) {
  return c.env.DB
    .prepare("INSERT INTO audit_log (actor_username, action, target_username, detail) VALUES (?,?,?,?)")
    .bind(actor, action, target, detail === null ? null : JSON.stringify(detail));
}

/** The changes()=1 CONDITIONAL twin of auditStmt: the INSERT lands ONLY when the immediately
 *  preceding statement in the same D1 batch changed exactly one row (SELECT … WHERE changes()=1).
 *  This is the guarded-mutation shape used across the write modules — UPDATE/DELETE guarded
 *  in-WHERE, audit conditional in the SAME batch (W4 atomicity), so a no-op (lost race, repeat
 *  click, wrong-state row) never writes a lying audit row. MUST be placed directly after the
 *  mutation it describes: changes() reads the last data-modifying statement on the connection. */
export function auditStmtIfChanged(
  c: Context<{ Bindings: Env; Variables: Vars }>,
  actor: string,
  action: string,
  target: string | null,
  detail: Record<string, unknown> | null,
) {
  return c.env.DB
    .prepare("INSERT INTO audit_log (actor_username, action, target_username, detail) SELECT ?1,?2,?3,?4 WHERE changes()=1")
    .bind(actor, action, target, detail === null ? null : JSON.stringify(detail));
}

/** DB-handle twin of auditStmtIfChanged for the scheduled()/cron context, where there is no Hono
 *  Context (auditStmt* only ever used `c.env.DB`). Same guarded-mutation shape — the audit INSERT
 *  lands ONLY when the immediately preceding statement in the same batch changed a row (W4). Used by
 *  the recurring-checklist generation engine (fieldops_recurrence.ts). */
export function auditStmtIfChangedDb(
  db: D1Database,
  actor: string,
  action: string,
  target: string | null,
  detail: Record<string, unknown> | null,
) {
  return db
    .prepare("INSERT INTO audit_log (actor_username, action, target_username, detail) SELECT ?1,?2,?3,?4 WHERE changes()=1")
    .bind(actor, action, target, detail === null ? null : JSON.stringify(detail));
}
