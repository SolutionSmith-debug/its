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
// PR-4 Part A: a cached PDF (the filed_pdfs base64 chunks) is transient — re-requestable.
// 24h past pdf_ready_at the chunks are deleted and the request flags reset, so a stale
// cache never lingers and the user can re-request.
export const PDF_CACHE_TTL_S = 86_400;
// WARN above 6GB of D1 usage (Cloudflare's per-DB ceiling is 10GB) so the chunk cache
// never silently approaches the limit. Telemetry-only — prune still runs.
const DB_SIZE_WARN_BYTES = 6_000_000_000;

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
 *  - filed_pdfs (PR-4 PDF cache): delete the base64 chunks of a submission whose cache
 *    aged out (>24h past pdf_ready_at) and RESET its request flags so it is re-requestable;
 *    also delete ORPHAN chunks whose parent submission was already pruned away.
 *
 * Also samples the D1 size (telemetry only — WARN above 6GB of the 10GB ceiling so the
 * chunk cache can never silently grow toward the limit).
 *
 * Returns the per-table delete counts + pdfChunks deleted + dbSizeBytes (surfaced for
 * the scheduled-handler log).
 */
export async function pruneOldData(
  db: Env["DB"],
  nowSec: number,
): Promise<{ submissions: number; rejected: number; audit: number; pdfChunks: number; dbSizeBytes: number }> {
  const subCutoff = nowSec - SUBMISSION_RETENTION_DAYS * DAY_S;
  const rejectedCutoff = nowSec - REJECTED_RETENTION_DAYS * DAY_S;
  const auditCutoff = nowSec - AUDIT_LOG_RETENTION_DAYS * DAY_S;
  const pdfCutoff = nowSec - PDF_CACHE_TTL_S;

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

  // PR-4 PDF cache: (1) delete the chunks of any submission whose cache aged out, then
  // reset its request flags so the user can re-request; (2) delete orphan chunks whose
  // parent submission was already pruned. Order: expired-by-time first, then orphans.
  const expiredChunks = await db
    .prepare(
      "DELETE FROM filed_pdfs WHERE submission_uuid IN " +
        "(SELECT submission_uuid FROM submissions WHERE pdf_ready_at IS NOT NULL AND pdf_ready_at < ?)",
    )
    .bind(pdfCutoff)
    .run();
  await db
    .prepare("UPDATE submissions SET pdf_requested=0, pdf_ready_at=NULL WHERE pdf_ready_at IS NOT NULL AND pdf_ready_at < ?")
    .bind(pdfCutoff)
    .run();
  const orphanChunks = await db
    .prepare("DELETE FROM filed_pdfs WHERE submission_uuid NOT IN (SELECT submission_uuid FROM submissions)")
    .run();
  const pdfChunks = (expiredChunks.meta.changes ?? 0) + (orphanChunks.meta.changes ?? 0);

  const dbSizeBytes = await sampleDbSizeBytes(db);
  if (dbSizeBytes > DB_SIZE_WARN_BYTES) {
    console.warn(`prune: D1 size ${dbSizeBytes} bytes exceeds the ${DB_SIZE_WARN_BYTES}-byte WARN threshold`);
  }

  return {
    submissions: sub.meta.changes ?? 0,
    rejected: rejected.meta.changes ?? 0,
    audit: audit.meta.changes ?? 0,
    pdfChunks,
    dbSizeBytes,
  };
}

/**
 * Best-effort D1 size sample. Prefers `PRAGMA page_count` × `PRAGMA page_size` (whole-DB
 * size including all tables + overhead). If PRAGMA is rejected (some D1/Miniflare builds
 * disallow it), falls back to summing the byte-length of the largest payloads
 * (filed_pdfs.chunk_b64 + submissions.payload_json) — an under-count, but enough to trip
 * the WARN. Telemetry only — never throws into the prune path.
 */
async function sampleDbSizeBytes(db: Env["DB"]): Promise<number> {
  try {
    const pages = await db.prepare("PRAGMA page_count").first<{ page_count: number }>();
    const size = await db.prepare("PRAGMA page_size").first<{ page_size: number }>();
    const pageCount = pages?.page_count ?? 0;
    const pageSize = size?.page_size ?? 0;
    if (pageCount > 0 && pageSize > 0) return pageCount * pageSize;
  } catch {
    // fall through to the LENGTH-sum estimate
  }
  try {
    const chunks = await db
      .prepare("SELECT COALESCE(SUM(LENGTH(chunk_b64)), 0) AS n FROM filed_pdfs")
      .first<{ n: number }>();
    const subs = await db
      .prepare("SELECT COALESCE(SUM(LENGTH(payload_json)), 0) AS n FROM submissions")
      .first<{ n: number }>();
    return (chunks?.n ?? 0) + (subs?.n ?? 0);
  } catch {
    return 0;
  }
}
