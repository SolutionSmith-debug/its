import type { Env } from "./types";

// Retention windows for the D1 store. For SAFETY SUBMISSIONS, D1 is a TRANSPORT CACHE / event
// log, NOT the system of record (Box + the week sheet hold the durable submission; ITS_Errors /
// the portal monitor surface security events). HOWEVER the P2 field-ops integrity-bar tables
// (time_entries, task_assignments, inspections — keyed on job_id) are D1-PRIMARY operational SoR
// (mirrored UP to Smartsheet by the Mac daemon); their job-context rows must NEVER be evicted
// while those records exist — the jobs-delete guard below enforces this.
export const SUBMISSION_RETENTION_DAYS = 90;
export const AUDIT_LOG_RETENTION_DAYS = 365;
// M4 (PR-4): a rejected (bad-HMAC) submission is terminal at box_verified=-1; keep it 30d for
// forensics, then prune (it is never re-served — /pending selects box_verified=0).
export const REJECTED_RETENTION_DAYS = 30;
// PR-5: a filed form stays browseable/requestable as long as its job is ACTIVE. Once the job
// is inactive, delete its filed rows 30d later (the inactive-job grace).
export const INACTIVE_JOB_GRACE_DAYS = 30;
const DAY_S = 86_400;
// PR-4 Part A: a cached PDF (the filed_pdfs base64 chunks) is transient — re-requestable.
// 24h past pdf_ready_at the chunks are deleted and the request flags reset, so a stale
// cache never lingers and the user can re-request.
export const PDF_CACHE_TTL_S = 86_400;
// GS2 rider: a TERMINAL publish_requests row (status archived | failed — the two statuses
// outside index.ts NON_TERMINAL_STATUSES; the admin publish-dismiss button clears the same
// set) keeps its full definition_json blob (~33 KB/version) forever unless a human dismisses
// it. 90d after it last moved (updated_at — the terminal-state stamp) the row is hygiene-
// pruned. NON-terminal rows (queued/validated/tested/merged/live) are NEVER touched — the
// publish daemon / stuck-sweep own those.
export const PUBLISH_TERMINAL_RETENTION_DAYS = 90;
// WARN above 6GB of D1 usage (Cloudflare's per-DB ceiling is 10GB) so the chunk cache
// never silently approaches the limit. GS2: no longer console-only — the condition is
// RECORDED in prune_meta (size_warn) and the Mac watchdog (Check V) escalates it to a
// CRITICAL page. The console.warn stays as the local trace.
export const DB_SIZE_WARN_BYTES = 6_000_000_000;

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
 *  - jobs: delete an INACTIVE job (active=0) only when it holds NO job-level records in ANY of
 *    submissions / time_entries / task_assignments / inspections — not in the dropdown (the form
 *    filters active=1) and nothing references it, so the row is dead weight. The field-ops
 *    integrity-bar tables are D1-PRIMARY SoR (P2.1), so a job holding any of them is NEVER deleted
 *    (it would orphan payroll/billing-grade records). A re-add via /api/internal/sync's upsert
 *    recreates a truly-empty pruned row.
 *
 * Also samples the D1 size (telemetry — WARN above 6GB of the 10GB ceiling so the
 * chunk cache can never silently grow toward the limit; GS2 records the condition in
 * the result so prune_meta / watchdog Check V escalate it).
 *
 * STAGE ISOLATION (GS2): each retention stage runs in its OWN try/catch. Before GS2 a
 * single throw mid-sequence silently skipped every later stage — forever, if the cause was
 * persistent (the unbounded-growth audit's #4 time bomb: a dead prune at 20×20 scale is a
 * 10 GB D1 wall → every INSERT fails → total field-capture outage). Now a failed stage is
 * counted in `failedStages` (its counter reads 0), later stages still run, and the failure
 * flag rides the prune_meta record to the Mac watchdog, which pages CRITICAL. This function
 * therefore NEVER throws for a per-stage SQL failure.
 *
 * Returns the per-table delete counts + pdfChunks deleted + dbSizeBytes + sizeWarn +
 * failedStages (surfaced for the scheduled-handler log AND the prune_meta record).
 */
export interface PruneResult {
  submissions: number;
  stripped: number;
  rejected: number;
  audit: number;
  pdfRequests: number;
  pdfChunks: number;
  publishRequests: number;
  jobs: number;
  dbSizeBytes: number;
  sizeWarn: boolean;
  failedStages: string[];
}

/**
 * Run one prune stage inside its own fence. A throw is RECORDED (stage name pushed onto
 * `failedStages`, console.error trace) and converted to a 0-count so every later stage
 * still runs — never-silent is provided by the prune_meta record + watchdog Check V, not
 * by crashing the scheduled handler.
 */
async function runStage(
  name: string,
  failedStages: string[],
  fn: () => Promise<number>,
): Promise<number> {
  try {
    return await fn();
  } catch (err) {
    failedStages.push(name);
    console.error(`prune: stage '${name}' FAILED (later stages still run): ${String(err)}`);
    return 0;
  }
}

export async function pruneOldData(db: Env["DB"], nowSec: number): Promise<PruneResult> {
  const subCutoff = nowSec - SUBMISSION_RETENTION_DAYS * DAY_S;          // Stage 1: strip payload
  const inactiveCutoff = nowSec - INACTIVE_JOB_GRACE_DAYS * DAY_S;       // Stage 2: delete inactive-job rows
  const rejectedCutoff = nowSec - REJECTED_RETENTION_DAYS * DAY_S;
  const auditCutoff = nowSec - AUDIT_LOG_RETENTION_DAYS * DAY_S;
  const pdfCutoff = nowSec - PDF_CACHE_TTL_S;                            // pdf_requests 24h window
  const publishCutoff = nowSec - PUBLISH_TERMINAL_RETENTION_DAYS * DAY_S;

  const failedStages: string[] = [];

  const rejected = await runStage("rejected", failedStages, async () => {
    const r = await db
      .prepare("DELETE FROM submissions WHERE box_verified = -1 AND filed_at IS NOT NULL AND filed_at < ?")
      .bind(rejectedCutoff)
      .run();
    return r.meta.changes ?? 0;
  });

  const audit = await runStage("audit", failedStages, async () => {
    const r = await db.prepare("DELETE FROM audit_log WHERE created_at < ?").bind(auditCutoff).run();
    return r.meta.changes ?? 0;
  });

  // PR-5 two-stage submission lifecycle.
  // Stage 1 — at 90d STRIP payload_json (the bulk; photos ride in it) but KEEP the metadata
  // row, so a filed form stays browseable/requestable as long as its job is active (downloads
  // re-fetch the PDF from Box via box_file_id — they never need payload_json; amend-prefill
  // only reads recent rows). Unfiled rows are never touched.
  const stripped = await runStage("strip", failedStages, async () => {
    const r = await db
      .prepare("UPDATE submissions SET payload_json='' WHERE box_verified = 1 AND filed_at IS NOT NULL AND filed_at < ? AND payload_json != ''")
      .bind(subCutoff)
      .run();
    return r.meta.changes ?? 0;
  });

  // Stage 2 — delete filed rows whose job is INACTIVE and that are 30d+ past filing (the
  // inactive-job grace). An UNFILED row (box_verified=0) is NEVER evicted (still the only copy).
  const submissions = await runStage("inactive_delete", failedStages, async () => {
    const r = await db
      .prepare(
        "DELETE FROM submissions WHERE box_verified = 1 AND filed_at IS NOT NULL AND filed_at < ? " +
          "AND job_id IN (SELECT job_id FROM jobs WHERE active = 0)",
      )
      .bind(inactiveCutoff)
      .run();
    return r.meta.changes ?? 0;
  });

  // pdf_requests: expire requests older than 24h, then drop any orphaned by a Stage-2 delete.
  const pdfRequests = await runStage("pdf_requests", failedStages, async () => {
    const expiredReq = await db.prepare("DELETE FROM pdf_requests WHERE requested_at < ?").bind(pdfCutoff).run();
    const orphanReq = await db
      .prepare("DELETE FROM pdf_requests WHERE submission_uuid NOT IN (SELECT submission_uuid FROM submissions)")
      .run();
    return (expiredReq.meta.changes ?? 0) + (orphanReq.meta.changes ?? 0);
  });

  // filed_pdfs: a cached PDF is kept only while a LIVE pdf_requests row references it. Once no
  // live request remains (all expired, or the parent was deleted), drop the chunks and reset
  // pdf_ready_at/pdf_requested so a fresh request re-services the cache from Box.
  const pdfChunks = await runStage("pdf_chunks", failedStages, async () => {
    const droppedChunks = await db
      .prepare("DELETE FROM filed_pdfs WHERE submission_uuid NOT IN (SELECT submission_uuid FROM pdf_requests)")
      .run();
    await db
      .prepare(
        "UPDATE submissions SET pdf_ready_at=NULL, pdf_requested=0 WHERE pdf_ready_at IS NOT NULL " +
          "AND submission_uuid NOT IN (SELECT submission_uuid FROM pdf_requests)",
      )
      .run();
    return droppedChunks.meta.changes ?? 0;
  });

  // publish_requests (GS2 rider): hygiene-prune TERMINAL rows (archived | failed — exactly the
  // set the admin publish-dismiss button clears) 90d after their terminal-state stamp
  // (updated_at). Their definition_json blobs are the true sibling of the bundle-bloat class
  // (~33 KB/publish op, never auto-pruned before this). NON-terminal statuses (queued /
  // validated / tested / merged / live — index.ts NON_TERMINAL_STATUSES) are NEVER touched:
  // the publish daemon + stuck-sweep own live rows.
  const publishRequests = await runStage("publish_requests", failedStages, async () => {
    const r = await db
      .prepare("DELETE FROM publish_requests WHERE status IN ('archived', 'failed') AND updated_at < ?")
      .bind(publishCutoff)
      .run();
    return r.meta.changes ?? 0;
  });

  // jobs: an INACTIVE job with no remaining job-level records is dead weight (not in the
  // dropdown, nothing behind it). PR-5 guarded on `submissions`; P2.1 added the field-ops
  // integrity-bar tables (time_entries / task_assignments / inspections) keyed on job_id —
  // those are D1-PRIMARY operational SoR (payroll/billing-grade), so a job holding ANY of them
  // must NEVER be deleted here (it would orphan unrecoverable records). Slice 1 (R3-F4) added
  // job_daily_requirements (0030/0032) + job_expected_materials (0031) — also D1-PRIMARY
  // (admin-authored per-job content with no copy outside D1; restore path is D1 Time Travel),
  // so they join the guard: deleting their job would orphan them invisibly. GS2 added
  // checklist_instances (0026) + equipment_location (0014) — both job-context D1-PRIMARY
  // records (a checklist trail / a location trail behind an inactive job would otherwise be
  // orphaned invisibly by this delete). The explicit operator cleanup path is
  // POST /api/internal/admin/purge-job (cascades both). equipment_logs
  // is keyed on equipment_id (not job_id), so it is not a job-context guard. A truly-empty
  // pruned job is recreated by /api/internal/sync's upsert if it re-appears in Smartsheet.
  // Shape note: one NOT IN per table, NOT a single UNION — D1 caps compound-SELECT terms at
  // 5 (SQLITE_MAX_COMPOUND_SELECT), and the 6th guard table blew it up ("too many terms in
  // compound SELECT", caught by test/prune.test.ts). Per-table NOT IN is set-equivalent
  // (job_id ∉ A∪B∪… ⇔ ∉A ∧ ∉B ∧ …) and each subquery can use its own job_id index.
  // NULL discipline: the original six guard tables declare job_id NOT NULL; the two GS2
  // tables declare job_id NULLABLE (0026 / 0014 — rows can exist without a job context),
  // and a single NULL inside a NOT-IN subquery poisons the whole predicate to NULL
  // (nothing would EVER be deleted — a silent full-stage disable). Their subqueries
  // therefore filter `WHERE job_id IS NOT NULL`.
  const jobs = await runStage("jobs", failedStages, async () => {
    const r = await db
      .prepare(
        "DELETE FROM jobs WHERE active = 0 " +
          "AND job_id NOT IN (SELECT job_id FROM submissions) " +
          "AND job_id NOT IN (SELECT job_id FROM time_entries) " +
          "AND job_id NOT IN (SELECT job_id FROM task_assignments) " +
          "AND job_id NOT IN (SELECT job_id FROM inspections) " +
          "AND job_id NOT IN (SELECT job_id FROM job_daily_requirements) " +
          "AND job_id NOT IN (SELECT job_id FROM job_expected_materials) " +
          "AND job_id NOT IN (SELECT job_id FROM checklist_instances WHERE job_id IS NOT NULL) " +
          "AND job_id NOT IN (SELECT job_id FROM equipment_location WHERE job_id IS NOT NULL)",
      )
      .run();
    return r.meta.changes ?? 0;
  });

  const dbSizeBytes = await sampleDbSizeBytes(db);
  const sizeWarn = dbSizeBytes > DB_SIZE_WARN_BYTES;
  if (sizeWarn) {
    console.warn(`prune: D1 size ${dbSizeBytes} bytes exceeds the ${DB_SIZE_WARN_BYTES}-byte WARN threshold`);
  }

  return {
    submissions,
    stripped,
    rejected,
    audit,
    pdfRequests,
    pdfChunks,
    publishRequests,
    jobs,
    dbSizeBytes,
    sizeWarn,
    failedStages,
  };
}

/**
 * Persist the one-row prune_meta record (migration 0033) after a prune run — the
 * observability half of GS2. The Mac watchdog reads it back over the bearer-gated
 * GET /api/internal/prune-status (Check V): WARN when last_run_at goes >48h stale,
 * CRITICAL on failed_stages non-empty or db_size_bytes over the 6 GB threshold.
 *
 * FENCED: a meta-write failure must never take down the scheduled handler (the prune
 * itself already ran). It is also NOT silent-by-fence — an unwritable meta row simply
 * stops advancing last_run_at, which is EXACTLY the staleness condition Check V WARNs
 * on within 48h. console.error keeps the local trace.
 */
export async function writePruneMeta(
  db: Env["DB"],
  nowSec: number,
  result: PruneResult,
): Promise<void> {
  const counters = {
    submissions: result.submissions,
    stripped: result.stripped,
    rejected: result.rejected,
    audit: result.audit,
    pdfRequests: result.pdfRequests,
    pdfChunks: result.pdfChunks,
    publishRequests: result.publishRequests,
    jobs: result.jobs,
  };
  try {
    await db
      .prepare(
        "INSERT INTO prune_meta (id, last_run_at, db_size_bytes, size_warn, counters_json, failed_stages_json) " +
          "VALUES (1, ?, ?, ?, ?, ?) " +
          "ON CONFLICT(id) DO UPDATE SET last_run_at=excluded.last_run_at, " +
          "db_size_bytes=excluded.db_size_bytes, size_warn=excluded.size_warn, " +
          "counters_json=excluded.counters_json, failed_stages_json=excluded.failed_stages_json",
      )
      .bind(
        nowSec,
        result.dbSizeBytes,
        result.sizeWarn ? 1 : 0,
        JSON.stringify(counters),
        JSON.stringify(result.failedStages),
      )
      .run();
  } catch (err) {
    console.error(`prune: prune_meta write FAILED (Check V will WARN on staleness): ${String(err)}`);
  }
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
