"""ITS_Trusted_Contacts sheet reads + scope enforcement.

The sheet (Op Stds v11 §33) replaces the JSON-list `*.allowed_senders`
ITS_Config rows from Phase 0. One row per (email × scope); per-sender
status and audit columns persist after disable so the operator keeps the
historical record.

Public surface:
    lookup(email)                                 → TrustedContact | None
    check_scope(email, *, workstream, project)    → ScopeVerdict

The `Project Scope` and `Workstream Scope` columns store JSON lists of
slugs as TEXT_NUMBER. The wildcard `"*"` matches anything — the seed
migration writes `["*"]` for both to preserve the legacy allowlist's
"any project, this workstream" semantics. Malformed JSON parses to an
empty list which (without a wildcard) blocks the contact from any scope;
a WARN is the only visible signal so the operator can repair the cell.

In-process cache: 60-second TTL on the read of `SHEET_TRUSTED_CONTACTS`.
A single poll cycle of `intake_poll` (currently 60s cadence) ingests
multiple messages from cached state; an operator-side edit takes effect
on the next cycle. Cache invalidation is best-effort — there's no need
to wire a webhook for the volume we see in Phase 1.
"""
from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from . import sheet_ids, smartsheet_client

LOGGER = logging.getLogger(__name__)

CACHE_TTL_SECONDS = 60.0
WILDCARD = "*"

# Layer-1 allowlist-drift gate (Invariant 2 §33). A FORMAT-invalid `Email` cell —
# missing or duplicate '@', no dot in the domain, embedded whitespace — is skipped
# at sheet read and WARNed with the greppable `trusted_contacts_row_malformed`
# marker, so a typo that mangles the address SHAPE surfaces instead of silently
# materializing an un-matchable contact (which would route a legitimate sender to
# Quarantine with no operator signal). Deliberately *basic*: a format-VALID
# transposition (`joe.smtih@…`) passes here — catching that is the deferred Layer-2
# Levenshtein reconciliation sweep (see docs/tech_debt.md "Allowlist drift
# detection"). The push-to-ITS_Errors part of the original entry is folded into
# Layer 2: the intake daemon is one-shot-every-60s, so a per-cache-load sheet write
# would spam ITS_Errors; the spam-free operator surface is the periodic Layer-2 sweep.
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _is_valid_email_format(email: str) -> bool:
    """True if `email` has a basic well-formed address shape. See `_EMAIL_RE`."""
    return _EMAIL_RE.match(email) is not None


class ContactStatus(StrEnum):
    """Values for the `Status` picklist column."""

    ACTIVE = "ACTIVE"
    DISABLED = "DISABLED"
    PENDING_VERIFICATION = "PENDING_VERIFICATION"


@dataclass(frozen=True)
class TrustedContact:
    """One ITS_Trusted_Contacts row projected to typed form.

    `email` is always case-normalized (`.strip().lower()`) on read; lookups
    normalize the input the same way before compare.
    """

    email: str
    display_name: str
    role: str
    project_scope: tuple[str, ...]
    workstream_scope: tuple[str, ...]
    status: ContactStatus
    row_id: int


@dataclass(frozen=True)
class ScopeVerdict:
    """Result of `check_scope`. Maps directly to Stage 2 disposition routing.

    `reason` values:
      "allowed"                       — proceed
      "unknown_sender"                — email not in sheet
      "status_disabled"               — contact found, Status=DISABLED
      "status_pending_verification"   — contact found, Status=PENDING_VERIFICATION
      "workstream_out_of_scope"       — contact ACTIVE, workstream not in scope
      "project_out_of_scope"          — contact ACTIVE, project specified
                                        and not in scope (Stage 4b check)
    """

    allowed: bool
    contact: TrustedContact | None
    reason: str


_cache: tuple[list[TrustedContact], float] | None = None


def _normalize_email(email: str) -> str:
    return email.strip().lower()


def _parse_scope(raw: Any, *, email: str, column: str) -> tuple[str, ...]:
    """Parse a JSON-list scope column into a tuple of slugs.

    Malformed JSON or non-list payloads return an empty tuple and emit a
    WARN so the operator can repair the cell. An empty tuple paired with
    no wildcard means the contact passes no scope check (deny-by-default).
    """
    if raw is None or raw == "":
        return ()
    if not isinstance(raw, str):
        LOGGER.warning(
            "trusted_contacts: %s column for %r is non-string (%r); treating as empty",
            column, email, type(raw).__name__,
        )
        return ()
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        LOGGER.warning(
            "trusted_contacts: %s column for %r is invalid JSON (%s); treating as empty",
            column, email, exc,
        )
        return ()
    if not isinstance(parsed, list):
        LOGGER.warning(
            "trusted_contacts: %s column for %r parsed to non-list (%r); treating as empty",
            column, email, type(parsed).__name__,
        )
        return ()
    return tuple(str(s).strip() for s in parsed if isinstance(s, str) and s.strip())


def _row_to_contact(row: dict[str, Any]) -> TrustedContact | None:
    """Project one Smartsheet row dict to a TrustedContact, or None on bad data."""
    raw_email = row.get("Email")
    if not isinstance(raw_email, str) or not raw_email.strip():
        return None
    email = _normalize_email(raw_email)
    if not _is_valid_email_format(email):
        # Allowlist-drift Layer 1: a malformed Email cell silently quarantines a
        # legitimate sender. Skip the row and surface a greppable WARN so the
        # operator can repair the typo (rather than discovering it via a missed
        # report downstream).
        LOGGER.warning(
            "trusted_contacts: Email %r is not a valid address format "
            "(error_code=trusted_contacts_row_malformed); skipping row — "
            "a typo in the Email cell silently quarantines a legitimate sender "
            "(Invariant 2 §33)",
            raw_email,
        )
        return None
    raw_status = row.get("Status") or ""
    try:
        status = ContactStatus(raw_status)
    except ValueError:
        LOGGER.warning(
            "trusted_contacts: Status %r for %r is not a known value; skipping row",
            raw_status, email,
        )
        return None
    row_id = row.get("_row_id")
    if not isinstance(row_id, int):
        return None
    return TrustedContact(
        email=email,
        display_name=str(row.get("Display Name") or ""),
        role=str(row.get("Role") or ""),
        project_scope=_parse_scope(
            row.get("Project Scope"), email=email, column="Project Scope",
        ),
        workstream_scope=_parse_scope(
            row.get("Workstream Scope"), email=email, column="Workstream Scope",
        ),
        status=status,
        row_id=row_id,
    )


def _load_contacts() -> list[TrustedContact]:
    """Fetch + cache the trusted-contacts sheet. TTL-keyed at module scope."""
    global _cache
    now = time.monotonic()
    if _cache is not None:
        contacts, expires_at = _cache
        if now < expires_at:
            return contacts

    try:
        rows = smartsheet_client.get_rows(sheet_ids.SHEET_TRUSTED_CONTACTS)
    except smartsheet_client.SmartsheetNotFoundError:
        # Sheet not yet wired (SHEET_TRUSTED_CONTACTS=0 placeholder). Caller's
        # fallback path covers this; cache empty so repeated lookups don't
        # hammer Smartsheet during cutover.
        contacts = []
        _cache = (contacts, now + CACHE_TTL_SECONDS)
        return contacts

    contacts = [c for c in (_row_to_contact(r) for r in rows) if c is not None]
    _cache = (contacts, now + CACHE_TTL_SECONDS)
    return contacts


def invalidate_cache() -> None:
    """Drop the in-process cache. Used by tests + ad-hoc operator scripts."""
    global _cache
    _cache = None


def lookup(email: str) -> TrustedContact | None:
    """Return the trusted contact for `email`, case-insensitive. None if absent."""
    normalized = _normalize_email(email)
    for contact in _load_contacts():
        if contact.email == normalized:
            return contact
    return None


def _scope_matches(scope: tuple[str, ...], value: str) -> bool:
    return WILDCARD in scope or value in scope


def check_scope(
    email: str,
    *,
    workstream: str,
    project: str | None = None,
) -> ScopeVerdict:
    """Apply the Stage 2 trusted-contacts gate.

    `project=None` means "Stage 2, before project resolution" — workstream
    scope is enforced, project deferred. A second call with the resolved
    project (Stage 4b) re-checks project scope.
    """
    contact = lookup(email)
    if contact is None:
        return ScopeVerdict(allowed=False, contact=None, reason="unknown_sender")
    if contact.status is ContactStatus.DISABLED:
        return ScopeVerdict(allowed=False, contact=contact, reason="status_disabled")
    if contact.status is ContactStatus.PENDING_VERIFICATION:
        return ScopeVerdict(
            allowed=False, contact=contact, reason="status_pending_verification",
        )
    if not _scope_matches(contact.workstream_scope, workstream):
        return ScopeVerdict(
            allowed=False, contact=contact, reason="workstream_out_of_scope",
        )
    if project is not None and not _scope_matches(contact.project_scope, project):
        return ScopeVerdict(
            allowed=False, contact=contact, reason="project_out_of_scope",
        )
    return ScopeVerdict(allowed=True, contact=contact, reason="allowed")
