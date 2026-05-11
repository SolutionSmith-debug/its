"""ITS_Review_Queue helpers — write to the queue, update statuses.

Status enum (per Operational Standards v4):
    PENDING / IN_REVIEW / APPROVED / REJECTED / ESCALATED

SLA tiers:
    safety intake review: 4 business hours
    RFQ drafts:           24 hours
    subcontract drafts:   48 hours

Items past 2x SLA auto-escalate (mechanism TBD — gated on Smartsheet sheet schema).
"""
from __future__ import annotations

from enum import StrEnum
from typing import Any


class ReviewStatus(StrEnum):
    PENDING = "PENDING"
    IN_REVIEW = "IN_REVIEW"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    ESCALATED = "ESCALATED"


class SlaTier(StrEnum):
    SAFETY_INTAKE = "4h"
    RFQ_DRAFT = "24h"
    SUBCONTRACT_DRAFT = "48h"


def add(
    *,
    workstream: str,
    summary: str,
    payload: dict[str, Any],
    sla_tier: SlaTier,
    reason: str = "",
):
    """Add an item to the review queue.

    Args:
        workstream: e.g., "safety_reports", "po_materials".
        summary: One-line human-readable description.
        payload: Structured data the reviewer needs to make the decision.
        sla_tier: SLA tier per Operational Standards.
        reason: Why this is in the queue (e.g., "low confidence on job match").
    """
    raise NotImplementedError("Awaiting ITS_Review_Queue sheet schema.")


def get_status(item_id: str) -> ReviewStatus:
    raise NotImplementedError
