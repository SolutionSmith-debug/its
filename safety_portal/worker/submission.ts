import { hmacHex } from "./hmac";

// Shared submission-INSERT builder — EXTRACTED from worker/index.ts /api/submit (#17, §14) so a
// SECOND producer of a `submissions` row (the checklist-completion emit,
// worker/fieldops_checklist.ts) mints a BYTE-IDENTICAL row — the same 5-field canonical HMAC,
// box_verified=0, and the same attribution columns — without importing index.ts (a runtime import
// cycle: index.ts registers the fieldops modules). One definition, N producers: the canonical
// string + the INSERT shape can never drift between /api/submit and the emit, so the Mac-side
// portal_poll daemon (shared/portal_hmac.py) verifies both identically. Depends only on ./hmac +
// the global D1 types, so no cycle.

/**
 * Canonical payload for the submission HMAC. The Mac-side portal_poll daemon recomputes this
 * byte-for-byte (shared/portal_hmac.py) to verify integrity + authenticity before intake trusts a
 * pulled submission. ORDER + SEPARATOR are load-bearing and mirrored on the Python side:
 *   submission_uuid \n job_id \n form_code \n work_date \n payload_json
 * payload_json is the EXACT stored JSON string, used verbatim on both sides.
 */
export function canonicalPayload(p: {
  submission_uuid: string; job_id: string; form_code: string; work_date: string; payload_json: string;
}): string {
  return [p.submission_uuid, p.job_id, p.form_code, p.work_date, p.payload_json].join("\n");
}

/**
 * Build (not execute) the `INSERT OR REPLACE INTO submissions` statement for one submission,
 * signing it with the canonical 5-field HMAC. Returns the prepared statement so the caller can
 * batch it atomically with its own audit / marker writes (W4). Contract, byte-for-byte with the
 * legacy /api/submit inline:
 *   • payload_json  = JSON.stringify(values)  — the EXACT stored string the HMAC covers.
 *   • hmac          = hmacHex(secret, canonicalPayload{…, payload_json}) — attribution columns are
 *                     deliberately NOT part of the canonical string, so a submit-as (or an emit
 *                     with a distinct submitted_as) signs identically to a plain self-submit.
 *   • box_verified  = 0  — a fresh/replaced row re-queues for Mac-side filing.
 *   • actor_username = the TRUE authenticated actor (always recorded, never dropped).
 *   • submitted_as   = the attributed account, defaulting to `actor` on a self-submit.
 * Fail-closed on the secret is the CALLER's job (never sign with an undefined secret — that would
 * produce signatures the Mac side could never verify → silent loss); this builder assumes a
 * validated secret, exactly as the /api/submit path did.
 */
export async function buildSubmissionInsert(
  db: D1Database,
  secret: string,
  p: {
    submission_uuid: string;
    job_id: string;
    form_code: string;
    work_date: string;
    values: unknown;
    actor: string;
    submitted_as?: string;
    amends_uuid?: string | null;
  },
  opts?: {
    /** (#17) Guarded variant: insert the row ONLY if `checklist_instances.emitted_submission_uuid`
     *  IS NULL for this instance id. Folds the one-shot marker check INTO the INSERT so a concurrent
     *  double-emit's loser writes ZERO `submissions` rows (the marker UPDATE alone would strand a
     *  duplicate — portal-worker-security BLOCK). The STORED ROW is byte-identical to the unguarded
     *  path (same 5-field HMAC, box_verified=0, attribution) — only the INSERT gating differs, so
     *  Mac-side poll verification is unchanged. Batch this with the guarded marker UPDATE + audit;
     *  the caller checks `meta.changes` on THIS statement to learn whether it won the race. */
    guardInstanceNotEmitted?: number;
  },
): Promise<D1PreparedStatement> {
  const payload = JSON.stringify(p.values);
  const hmac = await hmacHex(
    secret,
    canonicalPayload({
      submission_uuid: p.submission_uuid,
      job_id: p.job_id,
      form_code: p.form_code,
      work_date: p.work_date,
      payload_json: payload,
    }),
  );
  const cols =
    "(submission_uuid, job_id, form_code, work_date, payload_json, amends_uuid, hmac, box_verified, " +
    "actor_username, submitted_as)";
  const vals = [
    p.submission_uuid,
    p.job_id,
    p.form_code,
    p.work_date,
    payload,
    p.amends_uuid ?? null,
    hmac,
    p.actor,
    p.submitted_as ?? p.actor,
  ] as const;
  if (opts?.guardInstanceNotEmitted !== undefined) {
    return db
      .prepare(
        "INSERT OR IGNORE INTO submissions " +
          cols +
          " SELECT ?,?,?,?,?,?,?,0,?,? " +
          "WHERE (SELECT emitted_submission_uuid FROM checklist_instances WHERE id = ?) IS NULL",
      )
      .bind(...vals, opts.guardInstanceNotEmitted);
  }
  return db
    .prepare("INSERT OR REPLACE INTO submissions " + cols + " VALUES (?,?,?,?,?,?,?,0,?,?)")
    .bind(...vals);
}
