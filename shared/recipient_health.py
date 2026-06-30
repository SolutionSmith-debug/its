"""Recipient-health reporting — an unhealthy send recipient is surfaced as a tracked record.

Purpose
-------
When the send half of the External Send Gate cannot resolve a usable recipient for an approved
review row, it HELDs the row (it never transmits a half-formed packet). A bare HELD is
operator-actionable but easy to miss. This module makes it "never silent" (the cross-cutting
ITS invariant): a HELD-for-recipient also files a structured, SLA-tracked ``ITS_Review_Queue``
record (job id, row id, reason, source) — a first-class operator-review item that
``scripts/watchdog.py`` Check A escalates if it goes stale. Built ONCE over BOTH the safety
(WSR) and progress (WPR) send paths (P5): ``weekly_send.send_one_row`` calls it from its
``held_no_recipient`` branches via ``_held_no_recipient``, so both workstreams inherit it.

This is a RECORD leg, not a push (Op Stds §3.1, Push-vs-Record Separation): a record must
ALWAYS write and is never gated by ``alert_dedupe`` (a push-only primitive). De-duplication
here is therefore RECORD-state idempotency — skip the write only when an OPEN ``ITS_Review_Queue``
row for the same ``(workstream, row_id)`` already exists — NOT push suppression, so a genuine
new incident is never silently swallowed and a flapping re-HELD does not spam the queue with
duplicate rows. (If an actual operator PAGE is ever wanted on top of this, that is a separate
``Severity.CRITICAL`` push leg — the only thing §3.1 permits ``alert_dedupe`` to gate — and a
deliberate severity-posture decision, not this record surface.)

Invariants
----------
- **§3.1 record-leg:** the ``ITS_Review_Queue`` write is a record — always attempted, never
  gated by a push-dedup primitive. The only suppression is open-row idempotency (record state).
- **Fail-soft, NEVER raises:** the send-path HELD has already happened; a Review-Queue read or
  write failure here must not crash the send cycle or mask the HELD. Every leg is
  broad-except-isolated and logged. On an idempotency-read failure it fails toward surfacing
  (adds the row) rather than toward silence.
- **Workstream-scoped:** the record is tagged with the send config's ``config_workstream``
  ("safety_reports" / "progress_reports"), both in ``review_queue.VALID_WORKSTREAMS``.

Failure modes
-------------
A ``review_queue.get_pending`` read failure → fall through and add (a possible duplicate beats
silence). A ``review_queue.add`` failure → WARN-logged and swallowed (the caller's HELD + its
own ``_mark_held`` WARN row still stand). Neither propagates.

Consumers
---------
- ``safety_reports.weekly_send.send_one_row`` (via ``_held_no_recipient``) — the safety (WSR)
  send path.
- ``progress_reports.progress_send.send_one_row`` — the progress (WPR) send path, which reuses
  the same ``weekly_send`` dispatch (so the same ``_held_no_recipient`` call site).
"""
from __future__ import annotations

import json
import logging

from shared import review_queue
from shared.error_log import Severity

LOGGER = logging.getLogger(__name__)

# Tags the recipient_health Review-Queue rows in their Payload so the idempotency check can
# recognise its own prior rows (vs. an unrelated open row for the same workstream).
_PAYLOAD_SOURCE = "recipient_health"


def _open_row_already_exists(config_workstream: str, row_id: int) -> bool:
    """True iff an OPEN ITS_Review_Queue row already tracks this (workstream, row_id) incident.

    Record-state idempotency (NOT push suppression): prevents a flapping re-HELD from filing a
    duplicate row every poll cycle, while still letting a genuinely new incident through. Reads
    the workstream's pending rows and matches our own `source` tag + `row_id` in the Payload.
    Fail-soft: on ANY read/parse error returns False (fall through and add — a possible
    duplicate beats a swallowed record)."""
    try:
        pending = review_queue.get_pending(config_workstream)
    except Exception as exc:  # noqa: BLE001 — fail toward surfacing
        LOGGER.warning("recipient_health: get_pending failed (%r); will add (no dedup)", exc)
        return False
    for row in pending:
        raw = row.get("Payload")
        if not raw:
            continue
        try:
            payload = json.loads(raw)
        except (ValueError, TypeError):
            continue
        if (
            isinstance(payload, dict)
            and payload.get("source") == _PAYLOAD_SOURCE
            and payload.get("row_id") == row_id
        ):
            return True
    return False


def report_unhealthy_recipient(
    *,
    config_workstream: str,
    script_name: str,
    row_id: int,
    job_id: str,
    project_name: str,
    reason_detail: str,
) -> None:
    """File a queryable ITS_Review_Queue record for an unhealthy/unknown send recipient.

    RECORD leg (§3.1) — idempotent on open-row state, never push-deduped. Fail-soft — never
    raises (the caller has already HELD the row).

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
    if _open_row_already_exists(config_workstream, row_id):
        # An open record already tracks this incident — don't duplicate it (record idempotency,
        # §3.1-compliant: this is open-row state, not a push-dedup window suppressing a record).
        return

    summary = (
        f"Unhealthy send recipient HELD: job_id={job_id!r} project={project_name!r} "
        f"(row {row_id}) — {reason_detail}. Fix the contact on the workstream's "
        f"Active-Jobs row, then clear the HELD to re-dispatch."
    )
    try:
        review_queue.add(
            workstream=config_workstream,
            summary=summary,
            payload={
                "row_id": row_id,
                "job_id": job_id,
                "project_name": project_name,
                "reason_detail": reason_detail,
                "source": _PAYLOAD_SOURCE,
                "script": script_name,
            },
            sla_tier=review_queue.SlaTier.SAFETY_INTAKE,
            reason=review_queue.ReviewReason.POLICY_EDGE,
            severity=Severity.WARN,
        )
    except Exception as exc:  # noqa: BLE001 — fail-soft; the caller's HELD + WARN still stand
        LOGGER.warning("recipient_health: review_queue.add failed (%r)", exc)


__all__ = ["report_unhealthy_recipient"]
