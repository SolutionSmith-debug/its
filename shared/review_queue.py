"""ITS_Review_Queue helpers — write items to the queue, read their status.

Wires to the live `ITS_Review_Queue` Smartsheet sheet (id in
`shared.sheet_ids.SHEET_REVIEW_QUEUE`). Schema verified live
2026-05-18 — see `docs/session_logs/2026-05-18_sentry_and_phase1_unblock.md`
for the schema-drift notes vs. the original brief.

Concepts (per Operational Standards v8):

Status enum:
    PENDING / IN_REVIEW / APPROVED / REJECTED / ESCALATED.

SLA tiers:
    safety intake review: 4 business hours
    RFQ drafts:           24 hours
    subcontract drafts:   48 hours

Items past 2x SLA auto-escalate (mechanism TBD — gated on a future
scheduled-walker script that reads SLA Tier + Created At and bumps
Status to ESCALATED).

Reason picklist:
    Standardized so reviewers can filter by why-it-landed. See the
    `ReviewReason` enum below.

Severity:
    Reuses `shared.error_log.Severity` because the live ITS_Review_Queue
    `Severity` picklist (INFO / WARN / ERROR / CRITICAL) matches that
    enum exactly. Most review items are WARN; security-flagged items
    typically CRITICAL.

Security flag:
    Items routed here because of an anomaly_logger sentinel (per
    Foundation Mission v6 Invariant 2) set `security_flag=True`. The
    owner is notified separately for security-flagged items.

Failure isolation:
    Smartsheet failures propagate. Callers (workstream code) need to
    know if the queue write failed so they can log CRITICAL via
    `error_log` and fire the triple-fire alert path.
"""
from __future__ import annotations

import json
from datetime import UTC, date, datetime
from enum import StrEnum
from typing import Any

from . import sheet_ids, smartsheet_client
from .error_log import Severity


class ReviewStatus(StrEnum):
    """Values for the `Status` picklist column."""

    PENDING = "PENDING"
    IN_REVIEW = "IN_REVIEW"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    ESCALATED = "ESCALATED"


class SlaTier(StrEnum):
    """Values for the `SLA Tier` picklist column."""

    SAFETY_INTAKE = "4h"
    RFQ_DRAFT = "24h"
    SUBCONTRACT_DRAFT = "48h"


class ReviewReason(StrEnum):
    """Values for the `Reason` picklist column.

    Surfaced 2026-05-18 during live schema inspection — the original brief
    documented `Reason` as TEXT_NUMBER (free-text), but the live column is
    PICKLIST with the 9 options below.
    """

    LOW_CONFIDENCE_EXTRACTION = "low-confidence-extraction"
    AMBIGUOUS_CLASSIFICATION = "ambiguous-classification"
    STRUCTURED_OUTPUT_EDGE = "structured-output-edge"
    ZERO_DATA_WINDOW = "zero-data-window"
    MISMATCHED_REFERENCE = "mismatched-reference"
    SECURITY_TRIGGER = "security-trigger"
    POLICY_EDGE = "policy-edge"
    MANUAL = "manual"
    OTHER = "other"


# Valid Workstream picklist values for the `Workstream` column.
# Same set as ITS_Errors / ITS_Config workstream coverage, plus `global`.
VALID_WORKSTREAMS = frozenset({
    "safety_reports",
    "po_materials",
    "subcontracts",
    "email_triage",
    "ai_employee",
    "global",
})


class ReviewQueueError(Exception):
    """Base exception for review_queue helpers."""


class ItemNotFoundError(ReviewQueueError):
    """get_status() couldn't find a row with the given Item ID."""


def _generate_item_id(workstream: str) -> str:
    """Build an operator-friendly stable item ID.

    Format: `<workstream>-<YYYYMMDD>-<HHMMSS>` in UTC. Sortable; unique
    enough for sandbox volume (collisions only if the same workstream
    enqueues two items within the same second, which is fine for
    operator-facing review queues).
    """
    return f"{workstream}-{datetime.now(UTC):%Y%m%d-%H%M%S}"


def add(
    *,
    workstream: str,
    summary: str,
    payload: dict[str, Any],
    sla_tier: SlaTier,
    reason: ReviewReason = ReviewReason.OTHER,
    severity: Severity = Severity.WARN,
    source_file: str | None = None,
    security_flag: bool = False,
) -> int:
    """Add an item to ITS_Review_Queue. Returns the Smartsheet row ID.

    Args:
        workstream: Which workstream owns this item. Must be a valid
            Workstream picklist value (see `VALID_WORKSTREAMS`).
        summary: One-line human-readable description.
        payload: Structured data the reviewer needs to make the decision.
            JSON-encoded into the `Payload` cell.
        sla_tier: SLA tier per Operational Standards v8.
        reason: Why this is in the queue (default `OTHER` if unspecified).
        severity: Item severity. Defaults to `WARN`; use `CRITICAL` for
            security-flagged items and `INFO` for manual nudges.
        source_file: Optional reference to a source document path or
            inbox URL. Lands in the `Source File` cell.
        security_flag: True if a `shared.anomaly_logger` sentinel fired.
            Reviewers may filter on this to triage suspected prompt
            injection items first.

    Returns:
        Smartsheet row ID of the newly-added row. The operator-facing
        identifier is the `Item ID` cell value (returned via `get_status`
        and visible in the Smartsheet UI); the row ID returned here is
        for code-side lookups (e.g., `smartsheet_client.update_rows`).

    Raises:
        ValueError: workstream is not in `VALID_WORKSTREAMS`.
        SmartsheetError: from `smartsheet_client.add_rows`. Propagated —
            callers should log CRITICAL via `error_log` if the queue
            write is itself a failure-mode signal.
    """
    if workstream not in VALID_WORKSTREAMS:
        raise ValueError(
            f"workstream={workstream!r} not in {sorted(VALID_WORKSTREAMS)}"
        )

    item_id = _generate_item_id(workstream)
    row = {
        "Item ID": item_id,
        "Created At": date.today().isoformat(),
        "Workstream": workstream,
        "Summary": summary,
        "Reason": reason.value,
        "Severity": severity.value,
        "SLA Tier": sla_tier.value,
        "Source File": source_file or "",
        "Payload": json.dumps(payload, separators=(",", ":")),
        "Status": ReviewStatus.PENDING.value,
        "Security Flag": security_flag,
        # Assigned To / Resolved By / Resolved At / Resolution Notes are
        # operator-workflow cells left blank at write time.
    }
    [row_id] = smartsheet_client.add_rows(sheet_ids.SHEET_REVIEW_QUEUE, [row])
    return row_id


def get_status(item_id: str) -> ReviewStatus:
    """Read the current `Status` for the given `Item ID`.

    Args:
        item_id: The operator-facing string identifier returned in the
            `Item ID` cell when `add()` ran.

    Returns:
        The parsed `ReviewStatus` enum value.

    Raises:
        ItemNotFoundError: No row with that Item ID in ITS_Review_Queue.
        SmartsheetError: from `smartsheet_client.get_rows` on any
            underlying API failure.
    """
    rows = smartsheet_client.get_rows(
        sheet_ids.SHEET_REVIEW_QUEUE,
        filters={"Item ID": item_id},
    )
    if not rows:
        raise ItemNotFoundError(
            f"no ITS_Review_Queue row with Item ID={item_id!r}"
        )
    raw = rows[0].get("Status")
    if not isinstance(raw, str):
        # PICKLIST cells should always come back as str, but guard anyway
        # so type narrowing is explicit.
        raise ReviewQueueError(
            f"Item ID={item_id!r} has non-string Status cell: {raw!r}"
        )
    return ReviewStatus(raw)
