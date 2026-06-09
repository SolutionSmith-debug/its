import { useCallback, useState } from "react";

/**
 * A submission id (UUID) that is STABLE across retries of the SAME submission, and renewed
 * only when a NEW submission begins.
 *
 * Why this matters (A1, lost-ACK idempotency): the submit endpoint does an
 * `INSERT OR REPLACE ... ON submission_uuid` (worker/index.ts), so a resend that reuses the
 * id collapses to ONE row → one Box file, one WSR. If the fill page minted a FRESH
 * `crypto.randomUUID()` on every Submit click (the bug), a lost-ACK retry would carry a
 * NEW id → a SECOND row → a DUPLICATE filing. Holding the id in state (not regenerating it
 * per click) makes the retry idempotent; `renew()` is called only after a confirmed success
 * (when the form resets for the next submission). NO natural-key UNIQUE is introduced — a
 * legitimate second same-day submission/amendment gets its own id via `renew()`.
 */
export function useSubmissionId(): { submissionUuid: string; renew: () => void } {
  const [submissionUuid, setSubmissionUuid] = useState(() => crypto.randomUUID());
  const renew = useCallback(() => setSubmissionUuid(crypto.randomUUID()), []);
  return { submissionUuid, renew };
}
