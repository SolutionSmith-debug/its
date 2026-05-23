"""Picklist registry + write-path validation per Op Stds v11 §35.

Two-layer enforcement of bounded-enum Smartsheet columns:

  1. Client-side (this module): `validate_row` runs before every
     `add_rows` / `update_rows` payload construction. Composes the
     allowed set from the source-of-truth StrEnum classes (Severity,
     ReviewReason, etc.) so a code-side rename automatically propagates
     to the registry.
  2. Server-side (operator UI work, tracked in
     `docs/picklist_hardening_audit.md`): Smartsheet's "Restrict to
     picklist values only" toggle ON the columns. This catches writes
     from outside the codebase (manual edits, third-party integrations,
     legacy migration scripts that bypass `shared.smartsheet_client`).

The registry is opt-in: unregistered (sheet_id, column) pairs pass-through.
This keeps the rollout safe — adding a column to the registry is the
explicit hardening step, not the default. Likewise, None values and
booleans pass-through (CHECKBOX columns are type-enforced; blanks are
intentional).

ITS_Config rows are NOT registered by column (the `Value` column type
depends on `Key`). The kill_switch's `SystemState` enum + try/except
is the per-key registry pattern for `system.state`; other config rows
remain free-form by design.
"""
from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from . import sheet_ids
from .error_log import Severity
from .header_forgery import HeaderVerdict
from .kill_switch import SystemState
from .quarantine import QuarantineReason
from .review_queue import ReviewReason, ReviewStatus, SlaTier
from .trusted_contacts import ContactStatus

LOGGER = logging.getLogger(__name__)


class PicklistViolationError(ValueError):
    """Raised when a write contains a value outside the registered allowed set."""

    def __init__(
        self,
        sheet_id: int,
        column: str,
        value: Any,
        allowed: frozenset[str],
    ) -> None:
        self.sheet_id = sheet_id
        self.column = column
        self.value = value
        self.allowed = allowed
        super().__init__(
            f"PicklistViolationError: sheet={sheet_id} column={column!r} "
            f"value={value!r} not in allowed={sorted(allowed)!r}"
        )


# Workstream picklist values used by ITS_Errors / ITS_Config / ITS_Review_Queue.
# Matches the live ITS_Review_Queue picklist (verified 2026-05-18) — superset
# of ITS_Quarantine which uses `other` instead of `global`. Per-sheet entries
# in the registry below pick the right set.
_WORKSTREAM_VALUES_GLOBAL: frozenset[str] = frozenset({
    "safety_reports",
    "po_materials",
    "subcontracts",
    "email_triage",
    "ai_employee",
    "global",
})

_WORKSTREAM_VALUES_OTHER: frozenset[str] = frozenset({
    "safety_reports",
    "po_materials",
    "subcontracts",
    "email_triage",
    "ai_employee",
    "other",
})

# WPR_Pending_Review Send Status (per PR #68 schema-drift finding — the
# live picklist enforces `FAILED`, brief had `SEND_FAILED`).
_WPR_SEND_STATUS_VALUES: frozenset[str] = frozenset({
    "PENDING", "SENT", "FAILED", "HELD",
})

# ITS_Quarantine disposition (operator review action). Not yet a picklist
# in the live sheet — adding here so writes from `shared/quarantine.py`
# (when it grows a disposition write path) are validated client-side
# pre-conversion.
_QUARANTINE_DISPOSITION_VALUES: frozenset[str] = frozenset({
    "RELEASE", "DELETE", "ESCALATE",
})

# ITS_Trusted_Contacts Role (per PR #72 build_its_trusted_contacts_sheet.py).
_TRUSTED_CONTACTS_ROLE_VALUES: frozenset[str] = frozenset({
    "Field PM",
    "Safety Officer",
    "Subcontractor PM",
    "Site Supervisor",
    "Operator",
    "Other",
})


def _build_per_project_entries() -> dict[int, dict[str, frozenset[str]]]:
    """Build registry entries for the 6 project sheets (Daily Reports + Weekly Rollups).

    Per-project sheet IDs are not yet pre-wired in `shared/sheet_ids.py` —
    `safety_reports.week_folder.ensure_current_week_folder` discovers them
    dynamically per week. When (and if) `DAILY_REPORTS_SHEET_BY_PROJECT` /
    `WEEKLY_ROLLUP_SHEET_BY_PROJECT` constants land, iterate them here and
    register the same enum sets. Until then this returns an empty dict —
    the registry's opt-in semantics handle the absent-constant case.

    Tracked in `docs/picklist_hardening_audit.md` "Per-project sheets"
    section for the operator's manual UI conversion pass.
    """
    out: dict[int, dict[str, frozenset[str]]] = {}
    daily_constants = getattr(sheet_ids, "DAILY_REPORTS_SHEET_BY_PROJECT", None)
    if isinstance(daily_constants, Mapping):
        for _project_name, sheet_id in daily_constants.items():
            if isinstance(sheet_id, int) and sheet_id > 0:
                out[sheet_id] = {
                    # Daily Reports doesn't currently have bounded-enum
                    # columns we control — Report Category is enum-ish
                    # but managed by the per-week template, not by our
                    # writes. Leave the registry entry shell here so
                    # future hardening only needs to add the column key.
                }
    weekly_constants = getattr(sheet_ids, "WEEKLY_ROLLUP_SHEET_BY_PROJECT", None)
    if isinstance(weekly_constants, Mapping):
        for _project_name, sheet_id in weekly_constants.items():
            if isinstance(sheet_id, int) and sheet_id > 0:
                out[sheet_id] = {}
    return out


REGISTRY: dict[int, dict[str, frozenset[str]]] = {
    sheet_ids.SHEET_ERRORS: {
        "Severity": frozenset(s.value for s in Severity),
        "Workstream": _WORKSTREAM_VALUES_GLOBAL,
    },
    sheet_ids.SHEET_REVIEW_QUEUE: {
        "Reason": frozenset(r.value for r in ReviewReason),
        "SLA Tier": frozenset(t.value for t in SlaTier),
        "Workstream": _WORKSTREAM_VALUES_GLOBAL,
        "Status": frozenset(s.value for s in ReviewStatus),
        "Severity": frozenset(s.value for s in Severity),
    },
    sheet_ids.SHEET_QUARANTINE: {
        "Workstream": _WORKSTREAM_VALUES_OTHER,
        "Disposition": _QUARANTINE_DISPOSITION_VALUES,
    },
    sheet_ids.SHEET_WPR_PENDING_REVIEW: {
        "Send Status": _WPR_SEND_STATUS_VALUES,
    },
}

# Trusted Contacts: registered only if the operator has wired the real sheet
# ID (PR #72 left it as placeholder `0`). Registering against `0` would
# fire spurious violations against unrelated sheet IDs in tests; skip until
# the placeholder is replaced.
if sheet_ids.SHEET_TRUSTED_CONTACTS:
    REGISTRY[sheet_ids.SHEET_TRUSTED_CONTACTS] = {
        "Status": frozenset(s.value for s in ContactStatus),
        "Role": _TRUSTED_CONTACTS_ROLE_VALUES,
    }

REGISTRY.update(_build_per_project_entries())

# Re-export StrEnum members so callers can introspect the registry's source
# of truth without crawling individual modules. Mainly diagnostic — the
# canonical reference is the StrEnum class itself.
__all__ = [
    "PicklistViolationError",
    "REGISTRY",
    "ContactStatus",
    "HeaderVerdict",
    "QuarantineReason",
    "ReviewReason",
    "ReviewStatus",
    "Severity",
    "SlaTier",
    "SystemState",
    "validate_cell",
    "validate_row",
]


def _is_validatable(value: Any) -> bool:
    """Whether the value should be picklist-checked.

    Pass-through cases (return False):
      - None: blank cells are intentional (operator-cleared, etc.).
      - bool: CHECKBOX columns are type-enforced; no picklist applies.
    """
    if value is None:
        return False
    if isinstance(value, bool):
        return False
    return True


def validate_cell(sheet_id: int, column: str, value: Any) -> None:
    """Raise PicklistViolationError if (sheet_id, column) is registered and `value` is disallowed.

    Pass-through for:
      - Unregistered sheet_id.
      - Registered sheet_id with unregistered column.
      - None values (blank cells).
      - Boolean values (CHECKBOX columns).
    """
    sheet_columns = REGISTRY.get(sheet_id)
    if sheet_columns is None:
        return
    allowed = sheet_columns.get(column)
    if allowed is None:
        return
    if not _is_validatable(value):
        return
    # Smartsheet picklist values are strings; cast for safety so a numeric
    # 0/1 written into a string-enum column raises rather than slipping past.
    str_value = str(value)
    if str_value not in allowed:
        raise PicklistViolationError(sheet_id, column, value, allowed)


def validate_row(sheet_id: int, row: Mapping[str, Any]) -> None:
    """Apply `validate_cell` to every non-meta key in `row`.

    Meta keys (anything starting with `_`, e.g. `_row_id`) are skipped —
    `shared.smartsheet_client.update_rows` carries the row ID inside the
    payload dict, and picklist validation doesn't apply to it.

    Raises on the first violation; iteration order matches dict insertion
    order (Python 3.7+ guaranteed) so the failure is deterministic.
    """
    for column, value in row.items():
        if column.startswith("_"):
            continue
        validate_cell(sheet_id, column, value)
