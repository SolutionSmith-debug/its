"""Sender-allowlist quarantine logging.

Per Foundation Mission v4 Invariant 2, non-allowlisted email is routed by Mail.app rule to
a Quarantine folder, not to the workstream's hot folder. A scheduled script walks the
Quarantine folder and logs each quarantined message to ITS_Quarantine Smartsheet — no
Anthropic API call on quarantined content (defense-in-depth: nothing that didn't pass the
sender allowlist reaches Anthropic).

`is_allowlisted()` is the in-code helper used by any script that needs to double-check a
sender after Mail.app has already done the routing. `log_quarantined_message()` is the
sheet-writer used by the quarantine-walk script.
"""
from __future__ import annotations


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
) -> None:
    """Log a quarantined message to ITS_Quarantine Smartsheet.

    The summary should be a short snippet of the body (e.g., first 200 chars) — no AI
    call on quarantined content. The owner reviews ITS_Quarantine periodically and
    explicitly adds senders to the allowlist via the ITS_Config sheet.

    Args:
        sender: The sender's email address.
        subject: Message subject (truncated if long).
        timestamp: ISO 8601 timestamp of receipt.
        summary: Brief content summary — first ~200 chars of body. No AI call.
        workstream: Which workstream's allowlist rejected this (e.g., "safety_reports").
    """
    raise NotImplementedError(
        "Quarantine logging not yet wired. "
        "Awaiting ITS_Quarantine Smartsheet provisioning."
    )
