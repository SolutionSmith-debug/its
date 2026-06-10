import type { Env } from "./types";

// Retention windows for the D1 store. D1 here is a TRANSPORT CACHE / event log, NOT the
// system of record (Box + the week sheet hold the durable submission; ITS_Errors / the
// portal monitor surface security events). Nothing user-facing depends on old rows.
export const SUBMISSION_RETENTION_DAYS = 90;
export const AUDIT_LOG_RETENTION_DAYS = 365;
// M4 (PR-4): a rejected (bad-HMAC) submission is terminal at box_verified=-1; keep it 30d for
// forensics, then prune (it is never re-served — /pending selects box_verified=0).
export const REJECTED_RETENTION_DAYS = 30;
const DAY_S = 86_400;

/**
 * Prune aged rows from the D1 store (A3 housekeeping). Pure on (db, nowSec) so it is
 * unit-testable without the scheduled-controller machinery.
 *
 *  - submissions: delete only rows CONFIRMED filed to Box (`box_verified = 1` AND `filed_at`
 *    set) older than 90d. An UNFILED row (`box_verified = 0`) is **NEVER** evicted — Box does
 *    not yet hold it, so the D1 row is still the only copy and the portal_poll daemon keeps
 *    re-pulling it until it files. Evicting it would silently drop a submission.
 *  - rejected submissions (`box_verified = -1`, bad-HMAC terminal, M4/PR-4): keep 30d for
 *    forensics, then prune. Never re-served (`/pending` selects =0), so safe to evict.
 *  - audit_log: keep ~1 year of the security event stream, then prune.
 *
 * Returns the per-table delete counts (surfaced for the scheduled-handler log).
 */
export async function pruneOldData(
  db: Env["DB"],
  nowSec: number,
): Promise<{ submissions: number; rejected: number; audit: number }> {
  const subCutoff = nowSec - SUBMISSION_RETENTION_DAYS * DAY_S;
  const rejectedCutoff = nowSec - REJECTED_RETENTION_DAYS * DAY_S;
  const auditCutoff = nowSec - AUDIT_LOG_RETENTION_DAYS * DAY_S;

  const sub = await db
    .prepare(
      "DELETE FROM submissions WHERE box_verified = 1 AND filed_at IS NOT NULL AND filed_at < ?",
    )
    .bind(subCutoff)
    .run();
  const rejected = await db
    .prepare(
      "DELETE FROM submissions WHERE box_verified = -1 AND filed_at IS NOT NULL AND filed_at < ?",
    )
    .bind(rejectedCutoff)
    .run();
  const audit = await db
    .prepare("DELETE FROM audit_log WHERE created_at < ?")
    .bind(auditCutoff)
    .run();

  return {
    submissions: sub.meta.changes ?? 0,
    rejected: rejected.meta.changes ?? 0,
    audit: audit.meta.changes ?? 0,
  };
}
