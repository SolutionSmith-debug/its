// Recurring checklists per job (#16) — the generation ENGINE.
//
// A checklist_recurrences row (migration 0040) is a DEFINITION: "spawn generic_inspection template T
// for person P on job J every <cadence>, off anchor date A." This module turns those definitions into
// concrete kind='inspection' checklist_instances rows on their cadence dates, reusing the EXISTING
// instance machinery (the same rows POST /checklist/assign creates) so the assignee's Assigned-Tasks
// tab + the admin instances list surface them with zero new read code.
//
// IDEMPOTENT BY CONSTRUCTION: each spawned instance is keyed on its on-cadence instance_date, so the
// existing UNIQUE(kind, job_id, assignee_personnel_id, instance_date) (0026:78) dedupes a re-run for
// the same date via INSERT OR IGNORE. `last_generated_date` is the per-recurrence watermark advanced
// each pass so only NEW on-cadence dates are enumerated; a crash before the advance is self-healing
// (the next pass re-enumerates the same window and INSERT OR IGNORE absorbs the already-created dates).
//
// SHIPS DARK: the caller (the scheduled() cron in index.ts + the assign route) only runs this when the
// Worker var RECURRING_CHECKLISTS_ENABLED === "true". Nothing here reads the flag — the callers gate.
//
// Consumers: index.ts scheduled() (the daily cron pass) + fieldops_checklist.ts (the assign route's
// immediate first-materialization so a manager sees today's instance without waiting for the cron).
// Invariants: send-free (D1 only, no fetch/egress); every mutation batched with its audit row (W4);
// per-recurrence fenced so one bad definition never starves the others; never-silent (capped catch-up
// + per-recurrence errors are logged and returned in the summary).
import { auditStmtIfChangedDb } from "./audit";

/** The extensible cadence set — the SINGLE validation authority (the D1 table deliberately carries no
 *  CHECK so adding a cadence is a code-only change; see migration 0040's header). A cadence not in
 *  this set is rejected 400 at the assign route, before it can reach the table. */
export const RECURRENCE_CADENCES: ReadonlySet<string> = new Set(["daily", "weekly", "biweekly", "monthly"]);

export function isValidCadence(v: unknown): v is string {
  return typeof v === "string" && RECURRENCE_CADENCES.has(v);
}

/** YYYY-MM-DD (a Pacific calendar date, no time/offset — same shape as instance_date / due_date). */
export const ANCHOR_DATE_RE = /^\d{4}-\d{2}-\d{2}$/;

/** True iff `ymd` (already ANCHOR_DATE_RE-shaped) is a REAL calendar date — rejects regex-passing
 *  nonsense like "2026-13-32" or "2026-02-30" that Date.UTC would SILENTLY normalize (yielding a
 *  recurrence anchored on a shifted date, or one that never fires). Round-trips through Date.UTC and
 *  compares the y/m/d components back. Cheap; the assign route calls it before persisting the anchor. */
export function isRealCalendarDate(ymd: string): boolean {
  const [y, m, d] = ymd.split("-").map(Number);
  const dt = new Date(Date.UTC(y, m - 1, d));
  return dt.getUTCFullYear() === y && dt.getUTCMonth() === m - 1 && dt.getUTCDate() === d;
}

/** Bound the catch-up backfill: if the cron missed days (or an anchor sits far in the past), spawn at
 *  most this many days of on-cadence history so a stale definition can't flood the tab with old,
 *  un-actionable instances. Older dropped dates are reported as `capped` (never silent). Generous
 *  enough that a normal daily cadence never trips it (a cron outage of >45 days is a separate alarm). */
export const MAX_CATCHUP_LOOKBACK_DAYS = 45;

/** The audit actor for cron/engine-created rows (no session user in the scheduled context). */
const SYSTEM_ACTOR = "system:recurrence";

// ── Pure date helpers (calendar-day arithmetic; DST-agnostic — all in date-only space) ──────────────

/** The Pacific (America/Los_Angeles) calendar date of an instant, as YYYY-MM-DD. DST-correct via Intl
 *  (en-CA yields ISO order). The cron fires at 09:00 UTC = ~01–02 Pacific, still "today" in Pacific. */
export function pacificDateString(nowMs: number): string {
  return new Intl.DateTimeFormat("en-CA", {
    timeZone: "America/Los_Angeles",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(new Date(nowMs));
}

/** YYYY-MM-DD → integer days since the Unix epoch (UTC midnight basis — a pure day index, no TZ). */
function ymdToDayNum(ymd: string): number {
  const [y, m, d] = ymd.split("-").map(Number);
  return Math.floor(Date.UTC(y, m - 1, d) / 86_400_000);
}

/** Inverse of ymdToDayNum. */
function dayNumToYmd(n: number): string {
  const dt = new Date(n * 86_400_000);
  const y = dt.getUTCFullYear();
  const m = String(dt.getUTCMonth() + 1).padStart(2, "0");
  const d = String(dt.getUTCDate()).padStart(2, "0");
  return `${y}-${m}-${d}`;
}

/** Days in a given month (m1 = 1..12). Day 0 of the NEXT month = the last day of this one. */
function daysInMonth(y: number, m1: number): number {
  return new Date(Date.UTC(y, m1, 0)).getUTCDate();
}

/**
 * The on-cadence dates to (re)materialize this pass: every cadence date d with
 *   anchor ≤ d ≤ today  AND  (afterYmd === null ? true : d > afterYmd)
 * bounded to the most recent `maxLookbackDays` (older dropped → capped=true).
 * - daily/weekly/biweekly step off the anchor GRID (anchor + k·step) — weekly keeps the anchor's
 *   day-of-week, biweekly its two-week phase.
 * - monthly keeps the anchor's day-of-MONTH, clamped to each month's length (Jan-31 → Feb-28/29,
 *   Mar-31, …) so no month is skipped.
 * Pure + deterministic (no clock read) → unit-testable.
 */
export function enumerateCadenceDates(
  cadence: string,
  anchor: string,
  today: string,
  afterYmd: string | null,
  maxLookbackDays: number = MAX_CATCHUP_LOOKBACK_DAYS,
): { dates: string[]; capped: boolean } {
  const anchorN = ymdToDayNum(anchor);
  const todayN = ymdToDayNum(today);
  if (todayN < anchorN) return { dates: [], capped: false };

  // Lower bound: at/after the anchor, and strictly after the watermark if one exists.
  let lowN = anchorN;
  if (afterYmd) lowN = Math.max(lowN, ymdToDayNum(afterYmd) + 1);
  // Bound the catch-up lookback (see MAX_CATCHUP_LOOKBACK_DAYS). Raising the floor drops older dates.
  const flooredLowN = Math.max(lowN, todayN - maxLookbackDays + 1);
  const capped = flooredLowN > lowN;
  const startN = flooredLowN;

  const out: string[] = [];
  if (cadence === "daily" || cadence === "weekly" || cadence === "biweekly") {
    const step = cadence === "daily" ? 1 : cadence === "weekly" ? 7 : 14;
    // First on-grid date ≥ startN.
    const k = Math.max(0, Math.ceil((startN - anchorN) / step));
    for (let n = anchorN + k * step; n <= todayN; n += step) out.push(dayNumToYmd(n));
  } else if (cadence === "monthly") {
    const [ay, am, ad] = anchor.split("-").map(Number);
    // Walk months from the anchor month forward; terminate when the occurrence passes today. The
    // guard (50 years) is a belt-and-braces bound — occN > todayN is the real terminator.
    let y = ay;
    let m = am;
    for (let guard = 0; guard < 600; guard++) {
      const occDay = Math.min(ad, daysInMonth(y, m));
      const occN = ymdToDayNum(`${y}-${String(m).padStart(2, "0")}-${String(occDay).padStart(2, "0")}`);
      if (occN > todayN) break;
      if (occN >= startN) out.push(dayNumToYmd(occN));
      m += 1;
      if (m > 12) {
        m = 1;
        y += 1;
      }
    }
  }
  return { dates: out, capped };
}

// ── Generation ──────────────────────────────────────────────────────────────────────────────────

/** A live recurrence definition row (the SELECT shape in generateRecurringChecklists). */
export interface RecurrenceRow {
  id: number;
  template_id: number;
  assignee_personnel_id: number;
  job_id: string;
  cadence: string;
  anchor_date: string;
  active: number;
  last_generated_date: string | null;
  template_title: string | null;
}

/** The snapshot-source shape (a template's items, suppressor rows excluded). */
interface SnapshotItem {
  source_item_id: number;
  item_type: string;
  label: string | null;
  form_code: string | null;
  target_count: number | null;
}

export interface MaterializeResult {
  recurrence_id: number;
  /** Instances newly created this call (INSERT OR IGNORE that changed a row). */
  created: number;
  /** On-cadence dates considered this call. */
  scanned: number;
  /** The catch-up lookback dropped older on-cadence dates. */
  capped: boolean;
  /** The job was inactive → the recurrence was auto-deactivated. */
  autostopped: boolean;
}

/**
 * Spawn every due on-cadence instance for ONE recurrence, up to `today` (Pacific YYYY-MM-DD), and
 * advance its watermark. Shared by the cron (all active recurrences) and the assign route (immediate
 * first materialization). Stops + auto-deactivates when the job is no longer active.
 */
export async function materializeDueInstances(
  db: D1Database,
  rec: RecurrenceRow,
  today: string,
  maxLookbackDays: number = MAX_CATCHUP_LOOKBACK_DAYS,
): Promise<MaterializeResult> {
  const base: MaterializeResult = { recurrence_id: rec.id, created: 0, scanned: 0, capped: false, autostopped: false };
  if (!rec.active) return base;

  // STOP CONDITION — the job closed. A recurrence is per-job; when the job is no longer active
  // (lifecycle closed/archived, or the row is gone) auto-deactivate + audit and stop generating.
  const job = await db.prepare("SELECT active FROM jobs WHERE job_id = ?1").bind(rec.job_id).first<{ active: number }>();
  if (!job || job.active !== 1) {
    await db.batch([
      db.prepare("UPDATE checklist_recurrences SET active = 0 WHERE id = ?1 AND active = 1").bind(rec.id),
      auditStmtIfChangedDb(db, SYSTEM_ACTOR, "checklist_recurrence_autostop", String(rec.assignee_personnel_id), {
        recurrence_id: rec.id,
        job_id: rec.job_id,
        reason: "job_inactive",
      }),
    ]);
    return { ...base, autostopped: true };
  }

  // STOP CONDITION — the source template was emptied or DELETED after this recurrence was defined
  // (a same-privilege admin footgun: a cap.checklist.manage holder can delete the template or its last
  // item — neither of those routes checks for a live recurrence, and D1 does not enforce the FK). The
  // define-time empty_template guard is a point-in-time check, not a standing invariant. Continuing to
  // spawn from a 0-item template would create empty, permanently-open, un-completable instances every
  // cadence date forever — silent junk. Auto-deactivate + audit and stop, exactly like the job-closure
  // branch above (never-silent: the stop is audited + surfaced in the summary, not swallowed).
  const tItems = await db
    .prepare("SELECT COUNT(*) AS n FROM checklist_items WHERE template_id = ?1 AND suppresses_default_item_id IS NULL")
    .bind(rec.template_id)
    .first<{ n: number }>();
  if ((tItems?.n ?? 0) === 0) {
    await db.batch([
      db.prepare("UPDATE checklist_recurrences SET active = 0 WHERE id = ?1 AND active = 1").bind(rec.id),
      auditStmtIfChangedDb(db, SYSTEM_ACTOR, "checklist_recurrence_autostop", String(rec.assignee_personnel_id), {
        recurrence_id: rec.id,
        job_id: rec.job_id,
        reason: "template_empty",
      }),
    ]);
    return { ...base, autostopped: true };
  }

  const { dates, capped } = enumerateCadenceDates(rec.cadence, rec.anchor_date, today, rec.last_generated_date, maxLookbackDays);

  // Fetch the template's items ONCE (stable within this call) for the per-date snapshot — mirrors the
  // assign route's snapshot SELECT (suppressor rows excluded).
  let srcItems: SnapshotItem[] = [];
  if (dates.length) {
    const rows = await db
      .prepare(
        "SELECT id AS source_item_id, item_type, label, form_code, target_count FROM checklist_items WHERE template_id = ?1 AND suppresses_default_item_id IS NULL ORDER BY seq ASC, id ASC",
      )
      .bind(rec.template_id)
      .all<SnapshotItem>();
    srcItems = rows.results ?? [];
  }

  let created = 0;
  for (const d of dates) {
    // (W4) INSERT the instance + its audit ATOMICALLY. INSERT OR IGNORE dedupes on the existing
    // UNIQUE(kind, job_id, assignee, instance_date) — a re-run for the same date is a no-op.
    const ins = await db.batch([
      db
        .prepare(
          "INSERT OR IGNORE INTO checklist_instances (kind, job_id, assignee_personnel_id, instance_date, status, template_title) VALUES ('inspection', ?1, ?2, ?3, 'open', ?4)",
        )
        .bind(rec.job_id, rec.assignee_personnel_id, d, rec.template_title),
      auditStmtIfChangedDb(db, SYSTEM_ACTOR, "checklist_recurrence_generate", String(rec.assignee_personnel_id), {
        recurrence_id: rec.id,
        job_id: rec.job_id,
        instance_date: d,
        template_id: rec.template_id,
      }),
    ]);
    if ((ins[0].meta.changes ?? 0) === 1) created++;

    // Snapshot the template items into this instance IFF it has none yet (self-heal a partial prior
    // spawn; identical posture to the assign route). job_id + instance_date are both non-null here.
    if (srcItems.length) {
      const inst = await db
        .prepare(
          "SELECT id FROM checklist_instances WHERE kind = 'inspection' AND assignee_personnel_id = ?1 AND job_id = ?2 AND instance_date = ?3 LIMIT 1",
        )
        .bind(rec.assignee_personnel_id, rec.job_id, d)
        .first<{ id: number }>();
      if (inst) {
        const have =
          (await db.prepare("SELECT COUNT(*) AS n FROM checklist_item_states WHERE instance_id = ?1").bind(inst.id).first<{ n: number }>())?.n ?? 0;
        if (have === 0) {
          await db.batch(
            srcItems.map((it) =>
              db
                .prepare(
                  "INSERT INTO checklist_item_states (instance_id, source_item_id, item_type, label, form_code, target_count, status) VALUES (?1,?2,?3,?4,?5,?6,'open')",
                )
                .bind(inst.id, it.source_item_id, it.item_type, it.label, it.form_code, it.target_count),
            ),
          );
        }
      }
    }
  }

  // Advance the watermark THROUGH today (even a non-cadence today) so the next pass enumerates only
  // dates after today. Idempotent: a repeated same-day call re-enumerates nothing new.
  await db.prepare("UPDATE checklist_recurrences SET last_generated_date = ?1 WHERE id = ?2").bind(today, rec.id).run();

  return { recurrence_id: rec.id, created, scanned: dates.length, capped, autostopped: false };
}

export interface GenerateSummary {
  recurrences: number;
  instances_created: number;
  autostopped: number;
  capped: number;
  errors: number;
}

/**
 * The cron pass: materialize every ACTIVE recurrence up to today. Per-recurrence fenced (one bad
 * definition never starves the others) + never-silent (capped catch-up + errors logged + summarized).
 * The caller (scheduled()) runs this ONLY when RECURRING_CHECKLISTS_ENABLED === "true".
 */
export async function generateRecurringChecklists(db: D1Database, nowMs: number): Promise<GenerateSummary> {
  const today = pacificDateString(nowMs);
  const summary: GenerateSummary = { recurrences: 0, instances_created: 0, autostopped: 0, capped: 0, errors: 0 };
  const recs = await db
    .prepare(
      "SELECT id, template_id, assignee_personnel_id, job_id, cadence, anchor_date, active, last_generated_date, template_title FROM checklist_recurrences WHERE active = 1",
    )
    .all<RecurrenceRow>();
  for (const rec of recs.results ?? []) {
    summary.recurrences++;
    try {
      const r = await materializeDueInstances(db, rec, today);
      summary.instances_created += r.created;
      if (r.autostopped) summary.autostopped++;
      if (r.capped) {
        summary.capped++;
        console.warn(`recurrence ${rec.id} (job ${rec.job_id}) catch-up capped at ${MAX_CATCHUP_LOOKBACK_DAYS}d — older dates skipped`);
      }
    } catch (e) {
      // Per-recurrence fence — never-silent.
      summary.errors++;
      console.error(`recurrence ${rec.id} generation failed: ${e instanceof Error ? e.message : String(e)}`);
    }
  }
  return summary;
}
