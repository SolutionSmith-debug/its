"""Recipient-health reporting — a stale/empty/invalid send recipient is surfaced LOUD.

When the send half of the External Send Gate cannot resolve a usable recipient for an
approved review row, it HELDs the row (it never transmits a half-formed packet). A bare
HELD is operator-actionable but **silent** — it sits in the review sheet until someone
notices. This module upgrades that to "never silent" (the cross-cutting ITS invariant):
a HELD-for-recipient also files an `ITS_Review_Queue` row AND fires a dedupe-gated
operator alert. Built ONCE over BOTH the safety (WSR) and progress (WPR) send paths
(P5 — parameterize-not-clone): `weekly_send.send_one_row` calls it from its
`held_no_recipient` branches, so both workstreams inherit the loud behavior.

Design invariants
-----------------
- **Fail-soft, NEVER raises.** The send-path HELD already happened; a Review-Queue /
  alert failure here must not crash the send cycle or mask the HELD. Every leg is
  broad-except-isolated and logged.
- **Dedupe-gated** (`shared.alert_dedupe`): one Review-Queue row + one alert per
  (workstream, row) per dedupe window (default 60 min), not one per 15-min poll cycle.
  A stuck HELD therefore surfaces ~hourly, not every cycle — loud, not spammy. The
  longer-horizon backstop is the HELD-row watchdog scan.
- **Workstream-scoped:** the Review-Queue row is tagged with the send config's
  `config_workstream` ("safety_reports" / "progress_reports"), both in
  `review_queue.VALID_WORKSTREAMS`.
"""
from __future__ import annotations

import logging

from shared import alert_dedupe, error_log, review_queue
from shared.error_log import Severity

LOGGER = logging.getLogger(__name__)

# Dedupe window key prefix (distinct from error_log's Resend-leg keys so the two never
# collide in the shared alert_dedupe state file).
_DEDUPE_PREFIX = "recipient_health"


def report_unhealthy_recipient(
    *,
    config_workstream: str,
    script_name: str,
    row_id: int,
    job_id: str,
    project_name: str,
    reason_detail: str,
) -> None:
    """Surface an empty/invalid/unknown send recipient: Review-Queue row + dedupe-gated
    alert. Fail-soft — never raises (the caller has already HELD the row).

    Args:
        config_workstream: the SendConfig.config_workstream ("safety_reports" /
            "progress_reports") — a VALID_WORKSTREAMS value; tags the Review-Queue row.
        script_name: the sender's error_log actor (e.g. "safety_reports.weekly_send").
        row_id: the HELD review-sheet row.
        job_id: the row's Job ID (the recipient lookup key that failed).
        project_name: the row's project (operator-friendly).
        reason_detail: why the recipient is unhealthy (e.g. "unknown job_id",
            "empty/invalid TO contact").
    """
    dedupe_key = f"{_DEDUPE_PREFIX}:{config_workstream}:{row_id}"
    try:
        if not alert_dedupe.should_fire(dedupe_key):
            # Already surfaced this row within the window — stay quiet until it expires.
            return
    except Exception as exc:  # pragma: no cover - alert_dedupe is itself fail-open
        LOGGER.warning("recipient_health: dedupe check failed (%r); proceeding loud", exc)

    summary = (
        f"Unhealthy send recipient HELD: job_id={job_id!r} project={project_name!r} "
        f"(row {row_id}) — {reason_detail}. Fix the contact on the workstream's "
        f"Active-Jobs row, then clear the HELD to re-dispatch."
    )

    # Leg 1: Review-Queue row (broad-except isolated; losing the queue write must not
    # block the alert leg).
    try:
        review_queue.add(
            workstream=config_workstream,
            summary=summary,
            payload={
                "row_id": row_id,
                "job_id": job_id,
                "project_name": project_name,
                "reason_detail": reason_detail,
                "source": script_name,
            },
            sla_tier=review_queue.SlaTier.SAFETY_INTAKE,
            reason=review_queue.ReviewReason.POLICY_EDGE,
            severity=Severity.WARN,
        )
    except Exception as exc:
        LOGGER.warning("recipient_health: review_queue.add failed (%r)", exc)

    # Leg 2: operator alert (ERROR — louder than the WARN the HELD itself logs, so it is
    # not lost in the WARN stream; not CRITICAL because it is operator-actionable, not a
    # security/auth failure).
    try:
        error_log.log(
            Severity.ERROR,
            script_name,
            summary,
            error_code="recipient_health.unhealthy_recipient",
        )
    except Exception as exc:  # pragma: no cover
        LOGGER.warning("recipient_health: alert log failed (%r)", exc)

    # Open the dedupe window AFTER surfacing (mirrors alert_dedupe's record-after-send).
    try:
        alert_dedupe.record_fire(dedupe_key)
    except Exception as exc:  # pragma: no cover
        LOGGER.warning("recipient_health: record_fire failed (%r)", exc)
