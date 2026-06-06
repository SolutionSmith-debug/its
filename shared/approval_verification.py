"""Approval-attestation verification against Smartsheet cell history (F22).

Purpose
-------
Close the trust gap in the External Send Gate (Foundation Mission v8,
Invariant 1): a `WPR_Pending_Review` row marked "Approved for Send" is
otherwise trusted on the strength of a column VALUE alone, with no proof
that the approval was set by an authorized human reviewer. A row could be
hand-edited, or the approval checkbox flipped by automation or an
unauthorized account. `verify_approval` inspects the approval cell's
Smartsheet modification history and confirms the CURRENT approved value
was set by one of an explicit, config-driven set of authorized actors,
BEFORE the weekly send dispatches. The send process never trusts the
column value alone again.

Invariants
----------
- **Fail-CLOSED.** Any inability to verify — history unreadable, no
  approving event found, the most-recent modifier not in the authorized
  set, an empty authorized set, the cell no longer reading "approved" —
  yields a verdict with `verified=False`. The caller MUST treat any
  non-verified verdict as "do NOT send." This is the deliberate opposite
  of the heartbeat/observability fail-OPEN posture: for a customer-facing
  send gate the safe default is to withhold (a missed send is recoverable;
  a send authorized by the wrong party is not). Op Stds v14 §3.1
  posture-selection.
- **Total function — `verify_approval` NEVER raises.** Every path returns
  an `ApprovalVerdict`. This is a deliberate choice over the typed-error
  hierarchy used by sibling clients (`resend_client`, `smartsheet_client`):
  a raising API has a fail-OPEN footgun — a caller that forgets the
  `try/except` would let the exception propagate past the send decision, or
  (worse) a broad caller `except` could swallow it and proceed. A verdict
  the caller must inspect cannot be silently bypassed. The brief's
  "for symmetry" suggestion was weighed and rejected on this safety ground.
- **Identity is matched on email, case-insensitively.** Smartsheet's
  cell-history `modifiedBy` exposes only `{name, email}` — there is NO
  stable user ID in that payload (verified empirically against the
  documented API shape; `User.id_` comes back `None`). Email is therefore
  the only available match key. This makes the authorized set FRAGILE
  across the `evergreenmirror.com` → `evergreenrenewables.com` cutover:
  the production reviewers are different email identities, so the production
  ITS — Safety Portal workspace MUST be shared with them at delivery (the
  authorized set IS the workspace's share list) or every send silently
  fail-closes. See `docs/operations/cutover_checklist.md` (cutover item 1).
- **Network egress stays inside the audited boundary.** History is read via
  `smartsheet_client.get_cell_history` (a `*_client` method on the F02
  network allowlist), never the Smartsheet SDK directly.

Failure modes
-------------
All fold into a non-verified `ApprovalVerdict` carrying a `VerdictReason`
the caller can use to choose alerting severity:
- `EMPTY_ALLOWLIST` — the authorized set is empty (the workspace has no
  individual shares, or a transient membership-read miss); no history is even
  read. Cutover/sharing failure → caller should alert.
- `HISTORY_READ_FAILED` — the Smartsheet history read OR the deciding-event
  selection raised (any exception — `SmartsheetError`, `KeyError` for an
  unknown column, or a pathological timestamp set). Fail-closed; typically
  transient infra.
- `NO_HISTORY` — the cell has no modification history at all.
- `NOT_CURRENTLY_APPROVED` — the most-recent value is not "approved"
  (benign race: the cell was un-approved between the poller's filter and
  this check).
- `UNAUTHORIZED_ACTOR` — the most-recent approving modifier is not in the
  authorized set. The security-relevant case → caller should alert loudly.

Consumers
---------
- `safety_reports/weekly_send_poll.py` — calls `verify_approval` per
  candidate row in the dispatch loop; a non-verified verdict blocks that
  row's send and fires a forensic `approval_unverified` event (severity
  chosen by `VerdictReason`). Other rows still dispatch (per-row gate).
- `parse_authorized_actors` is a retained generic comma-separated-email parser
  (one interpretation of such a value); the poller now builds the
  `authorized_actors` set from ITS — Safety Portal workspace membership via
  `smartsheet_client.list_workspace_share_emails`.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from . import smartsheet_client


class VerdictReason(StrEnum):
    """Why a verdict landed the way it did. Drives caller alerting severity."""

    AUTHORIZED = "authorized"
    UNAUTHORIZED_ACTOR = "unauthorized_actor"
    NOT_CURRENTLY_APPROVED = "not_currently_approved"
    NO_HISTORY = "no_history"
    EMPTY_ALLOWLIST = "empty_allowlist"
    HISTORY_READ_FAILED = "history_read_failed"


@dataclass(frozen=True)
class ApprovalVerdict:
    """Outcome of an approval-attestation check.

    Carries enough for the caller to write a forensic audit line without a
    second lookup: whether the approval verified, the structured reason, the
    actor email found on the deciding history event (if any), the
    opportunistic user ID (None today — see module identity note), the ISO
    timestamp of the deciding event, and a human-readable detail string.
    """

    verified: bool
    reason: VerdictReason
    actor: str | None = None
    actor_user_id: int | None = None
    modified_at: str | None = None
    detail: str = ""


# Checkbox-column truthiness mirrors the poller's gate predicate
# (`bool(row.get("Approved for Send"))`). The approval column is a Smartsheet
# CHECKBOX, so a history `value` of True means checked/approved.
def _is_approving(value: Any) -> bool:
    return bool(value)


def _normalize(email: str | None) -> str:
    return (email or "").strip().lower()


def _latest_event(
    events: list[smartsheet_client.CellHistoryEvent],
) -> smartsheet_client.CellHistoryEvent:
    """Return the most-recent event, by timestamp where available.

    Smartsheet returns history newest-first, but we do not trust list order:
    prefer the maximum `modified_at` among timestamped events; fall back to
    the API's first element only when no timestamps are present.
    """
    dated = [e for e in events if e.modified_at is not None]
    if dated:
        return max(dated, key=lambda e: e.modified_at)  # type: ignore[arg-type,return-value]
    return events[0]


def parse_authorized_actors(raw: str | None) -> frozenset[str]:
    """Generic comma-separated email-list parser → frozenset of normalized
    (lowercased, stripped) emails.

    Retained as a utility; the F22 gate NO LONGER uses this — the authorized set
    now comes from ITS — Safety Portal workspace membership via
    `smartsheet_client.list_workspace_share_emails`. A missing/blank value yields
    the empty set — which `verify_approval` treats as EMPTY_ALLOWLIST (fail-closed,
    "no one is authorized"), NEVER as "allow all."
    """
    if not raw:
        return frozenset()
    return frozenset(
        _normalize(part) for part in raw.split(",") if _normalize(part)
    )


def verify_approval(
    sheet_id: int,
    row_id: int,
    approval_column: str,
    *,
    authorized_actors: frozenset[str],
) -> ApprovalVerdict:
    """Verify, against Smartsheet cell history, that the current approved
    value on `approval_column` of `row_id` was set by one of
    `authorized_actors`.

    Fail-CLOSED and total: never raises; every failure path returns a
    `verified=False` verdict the caller MUST treat as "do not send." See the
    module docstring for the full posture and identity rationale.
    """
    # Empty allowlist → no one is authorized. Do not even read history.
    if not authorized_actors:
        return ApprovalVerdict(
            verified=False,
            reason=VerdictReason.EMPTY_ALLOWLIST,
            detail=(
                "authorized_actors is empty (no workspace members) — "
                "blocking all sends fail-closed"
            ),
        )

    normalized_allow = {_normalize(a) for a in authorized_actors}

    # Read history AND select the deciding event under ONE fail-closed guard,
    # so the "never raises" contract is structurally true rather than dependent
    # on the SDK returning uniformly tz-aware timestamps. (A mixed naive/aware
    # set would make `max()` in _latest_event raise TypeError; we fold any such
    # read/parse failure to HISTORY_READ_FAILED → blocked.) NO_HISTORY returns
    # normally from inside the try; it is not an error path.
    try:
        events = smartsheet_client.get_cell_history(
            sheet_id, row_id, approval_column
        )
        if not events:
            return ApprovalVerdict(
                verified=False,
                reason=VerdictReason.NO_HISTORY,
                detail=(
                    f"no cell history for column {approval_column!r} "
                    f"on row {row_id}"
                ),
            )
        latest = _latest_event(events)
        actor = _normalize(latest.actor_email)
        modified_at = (
            latest.modified_at.isoformat()
            if latest.modified_at is not None
            else None
        )
    except Exception as exc:  # noqa: BLE001 — fail-closed on ANY read/parse failure
        return ApprovalVerdict(
            verified=False,
            reason=VerdictReason.HISTORY_READ_FAILED,
            detail=f"cell-history read/parse failed: {exc!r}",
        )

    if not _is_approving(latest.value):
        return ApprovalVerdict(
            verified=False,
            reason=VerdictReason.NOT_CURRENTLY_APPROVED,
            actor=actor or None,
            actor_user_id=latest.actor_user_id,
            modified_at=modified_at,
            detail=(
                f"most-recent value {latest.value!r} is not approved — "
                "cell un-approved since the poller filtered it"
            ),
        )

    if actor and actor in normalized_allow:
        return ApprovalVerdict(
            verified=True,
            reason=VerdictReason.AUTHORIZED,
            actor=actor,
            actor_user_id=latest.actor_user_id,
            modified_at=modified_at,
            detail=f"approved by authorized actor {actor!r}",
        )

    return ApprovalVerdict(
        verified=False,
        reason=VerdictReason.UNAUTHORIZED_ACTOR,
        actor=actor or None,
        actor_user_id=latest.actor_user_id,
        modified_at=modified_at,
        detail=(
            f"approval set by {actor!r}, who is not in the authorized "
            "approver set"
        ),
    )
