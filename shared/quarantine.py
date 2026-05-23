"""Sender-allowlist quarantine logging.

Per Foundation Mission v6 Invariant 2, non-allowlisted email is routed by Mail.app rule to
a Quarantine folder, not to the workstream's hot folder. A scheduled script walks the
Quarantine folder and logs each quarantined message to ITS_Quarantine Smartsheet — no
Anthropic API call on quarantined content (defense-in-depth: nothing that didn't pass the
sender allowlist reaches Anthropic).

`is_allowlisted()` is the in-code helper used by any script that needs to double-check a
sender after Mail.app has already done the routing. `log_quarantined_message()` is the
sheet-writer used by the quarantine-walk script (wired to the live ITS_Quarantine
sheet 2026-05-18).

Failure isolation: `log_quarantined_message()` propagates SmartsheetError. The quarantine-
walk script is expected to catch and log CRITICAL via `error_log` so the triple-fire
alert path fires — silent failure of quarantine logging is itself a security-relevant
incident (we lose the audit record of who got quarantined).
"""
from __future__ import annotations

from enum import StrEnum

from . import sheet_ids, smartsheet_client

# Live ITS_Quarantine.Workstream picklist (verified 2026-05-18). Note that
# the catch-all is `other`, NOT `global` — differs from ITS_Review_Queue.
VALID_WORKSTREAMS = frozenset({
    "safety_reports",
    "po_materials",
    "subcontracts",
    "email_triage",
    "ai_employee",
    "other",
})


class QuarantineReason(StrEnum):
    """Disposition reasons for ITS_Quarantine writes.

    Added 2026-05-23 alongside the ITS_Trusted_Contacts cluster. The live
    ITS_Quarantine sheet does NOT currently have a `Reason` column (verified
    2026-05-18) — the value is written into Notes as `[reason: <code>]` so
    operators can grep without a schema change. A future picklist-hardening
    pass may add a dedicated column; until then graceful-degrade into Notes
    preserves the audit trail without blocking on operator UI work.
    """

    UNKNOWN_SENDER = "unknown_sender"
    SENDER_DISABLED = "sender_disabled"
    WORKSTREAM_OUT_OF_SCOPE = "workstream_out_of_scope"
    HEADER_FORGERY_SUSPECTED = "header_forgery_suspected"
    LEGACY_ALLOWLIST_MISS = "legacy_allowlist_miss"


def is_allowlisted(sender: str, allowlist: list[str]) -> bool:
    """Check whether a sender email matches the configured allowlist.

    Matches by exact address or by domain — entries starting with '@' are treated as
    domains (e.g., '@evergreenmirror.com' matches any address at that domain).

    Args:
        sender: The sender's email address (e.g., "jacob@evergreenmirror.com").
        allowlist: Mixed list of addresses (exact match) and domain patterns
            (e.g., ['@evergreenmirror.com', 'partner@external.com']).

    Returns:
        True if the sender matches at least one allowlist entry. Comparison is
        case-insensitive; surrounding whitespace is stripped from both sides.
    """
    sender_lower = sender.lower().strip()
    for entry in allowlist:
        entry_lower = entry.lower().strip()
        if not entry_lower:
            continue
        if entry_lower.startswith("@"):
            if sender_lower.endswith(entry_lower):
                return True
        else:
            if sender_lower == entry_lower:
                return True
    return False


def log_quarantined_message(
    *,
    sender: str,
    subject: str,
    timestamp: str,
    summary: str,
    workstream: str,
    reason: QuarantineReason | None = None,
) -> int:
    """Log a quarantined message to ITS_Quarantine Smartsheet.

    The summary should be a short snippet of the body (e.g., first 200 chars) — no AI
    call on quarantined content. The owner reviews ITS_Quarantine periodically and
    explicitly adds senders to the allowlist via the ITS_Config sheet.

    Args:
        sender: The sender's email address.
        subject: Message subject (truncated if long).
        timestamp: ISO 8601 timestamp of receipt — lands in the `Received At` cell.
        summary: Brief content summary — first ~200 chars of body. No AI call.
        workstream: Which workstream's allowlist rejected this. Must be one of
            `VALID_WORKSTREAMS`.
        reason: Optional `QuarantineReason` disposition code. Written into Notes
            as `[reason: <code>]` because the live ITS_Quarantine sheet has no
            dedicated Reason column. Omit for legacy callers without disposition
            data.

    Returns:
        Smartsheet row ID of the newly-added row.

    Raises:
        ValueError: workstream is not in `VALID_WORKSTREAMS`.
        SmartsheetError: from `smartsheet_client.add_rows`. Propagated — the
            quarantine-walk script must catch and log CRITICAL so the triple-fire
            alert fires; silent failure here means we lose the audit record of
            who got quarantined.
    """
    if workstream not in VALID_WORKSTREAMS:
        raise ValueError(
            f"workstream={workstream!r} not in {sorted(VALID_WORKSTREAMS)}"
        )

    row: dict[str, object] = {
        # Primary column — short operator-facing label. Format mirrors
        # ITS_Errors' "Error" column convention (short stable string).
        "Quarantined Message": f"quarantined: {sender}",
        "Received At": timestamp,
        "Sender": sender,
        "Subject": subject,
        "Summary": summary,
        "Workstream": workstream,
        # Reviewed / Added to Allowlist / Reviewed By / Reviewed At
        # are operator-workflow cells left blank at write time.
    }
    if reason is not None:
        row["Notes"] = f"[reason: {reason.value}]"
    [row_id] = smartsheet_client.add_rows(sheet_ids.SHEET_QUARANTINE, [row])
    return row_id
