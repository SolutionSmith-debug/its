"""Picklist registry + write-path validation per Op Stds v11 §35.

Two-layer enforcement of bounded-enum Smartsheet columns:

  1. Client-side (this module): `validate_row` runs before every
     `add_rows` / `update_rows` payload construction. Composes the
     allowed set from the source-of-truth StrEnum classes (Severity,
     ReviewReason, etc.) so a code-side rename automatically propagates
     to the registry.
  2. Server-side (operator UI work, tracked in
     `docs/audits/picklist_hardening_audit.md`): Smartsheet's "Restrict to
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
# Matches the live ITS_Review_Queue picklist (verified 2026-05-18; `progress_reports`
# appended 2026-07-03 for the Progress Reporting go-live) — superset of ITS_Quarantine
# which uses `other` instead of `global`. Per-sheet entries in the registry below pick
# the right set.
#
# `progress_reports` (added 2026-07-03) MUST stay in lockstep with
# `review_queue.VALID_WORKSTREAMS`: `review_queue.add(workstream="progress_reports", …)`
# passes its own VALID_WORKSTREAMS check (progress joined at P5) and then calls
# `smartsheet_client.add_rows(SHEET_REVIEW_QUEUE, {… "Workstream": "progress_reports"})`,
# which validates against THIS set. Omitting it here raised `PicklistViolationError` at
# the first progress per-job compile fence / capacity-breach enqueue (latent since P4 —
# the two sets drifted; the review_queue side was fixed, this write-gate side was not).
# error_log writes NO Workstream cell to ITS_Errors, so widening SHEET_ERRORS.Workstream
# here is harmless (nothing wrote a now-newly-allowed value; the set only grows).
_WORKSTREAM_VALUES_GLOBAL: frozenset[str] = frozenset({
    "safety_reports",
    "progress_reports",
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

# WSR_human_review Send Status = the WPR set PLUS `SENDING` — the write-ahead intent marker
# weekly_send flips the row to immediately BEFORE the irreversible Graph send (PR #247), then
# to SENT. SENDING is NOT a poller dispatch candidate, so a post-send stamp failure leaves the
# row in SENDING and it is never re-sent (no double-send). This registry gates every
# update_rows, so SENDING MUST be allowed here or the marker write raises PicklistViolationError
# and the send is blocked — the regression this fixes (weekly_send_poll went DEGRADED, approved
# reports could not send). WPR (decommissioned) never writes SENDING, so it keeps the old set.
_WSR_SEND_STATUS_VALUES: frozenset[str] = _WPR_SEND_STATUS_VALUES | frozenset({"SENDING"})

# WSR_human_review Workstream tag (P1b cross-workstream contamination guard). A DEDICATED,
# tight set: the WSR sheet is the SAFETY review sheet, so the only legal tag there is `safety`
# (a `progress` value on WSR is itself a contamination signal — defense in depth; `progress`
# joins the future WPR set in P2). This is the report-family vocabulary (`safety` / `progress`),
# intentionally NOT _WORKSTREAM_VALUES_GLOBAL (`safety_reports`, the ITS_Config scope). The
# weekly_send guard READS this column; add_wsr_row + the backfill migration WRITE `safety` — so
# this entry gates those writes (a wrong tag raises PicklistViolationError, never silently routes).
_WSR_WORKSTREAM_VALUES: frozenset[str] = frozenset({"safety"})

# WPR_human_review (the PROGRESS review surface, P2) mirrors WSR exactly: the Send Status
# set is SENDING-inclusive (the same write-ahead-marker contract; progress_send flips a row
# to SENDING immediately before the irreversible Graph send, then to SENT), and the
# Workstream tag is the report family `progress` — the dedicated tight counterpart to
# _WSR_WORKSTREAM_VALUES={"safety"} (a `safety` tag on the WPR sheet is itself a
# contamination signal the progress send guard HARD-HELDs). Realises the anticipation noted
# above ("`progress` joins the future WPR set in P2"). add_wpr_row WRITES `progress` → this
# entry gates that write (a wrong tag raises PicklistViolationError, never silently routes).
_WPR_HR_SEND_STATUS_VALUES: frozenset[str] = _WSR_SEND_STATUS_VALUES
_WPR_WORKSTREAM_VALUES: frozenset[str] = frozenset({"progress"})

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

    Tracked in `docs/audits/picklist_hardening_audit.md` "Per-project sheets"
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
    # WSR_human_review (Phase-5 portal review surface) — Send Status is the WPR set PLUS the
    # SENDING write-ahead marker (PR #247; see _WSR_SEND_STATUS_VALUES). Supersedes WPR for the
    # portal flow; both stay registered until the WPR sheet itself is operator-deleted.
    sheet_ids.SHEET_WSR_HUMAN_REVIEW: {
        "Send Status": _WSR_SEND_STATUS_VALUES,
        "Workstream": _WSR_WORKSTREAM_VALUES,
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

# Safety Portal config sheets (ITS — Operations / Safety Portal). Both carry an
# identical "Active" lifecycle picklist. Registered only once the operator has
# flipped the real sheet ID in (the build migration prints it) — registering
# against the placeholder 0 would fire spurious violations on unrelated sheet
# IDs in tests, the same guard as Trusted Contacts above.
_ACTIVE_LIFECYCLE_VALUES = frozenset({"Active", "Inactive", "Archived"})
if sheet_ids.SHEET_ACTIVE_JOBS:
    REGISTRY[sheet_ids.SHEET_ACTIVE_JOBS] = {"Active": _ACTIVE_LIFECYCLE_VALUES}
if sheet_ids.SHEET_FORMS_CATALOG:
    REGISTRY[sheet_ids.SHEET_FORMS_CATALOG] = {"Active": _ACTIVE_LIFECYCLE_VALUES}

# Progress Reporting sheets (ITS — Progress Reporting / Control). Registered only once the
# operator flips the real sheet ID in (the build migration prints it) — registering against
# the placeholder 0 would fire spurious violations on unrelated sheet IDs in tests, the same
# guard as Trusted Contacts / the Safety-Portal sheets above. WPR_human_review mirrors the WSR
# Send Status + Workstream entry; ITS_Active_Jobs_Progress reuses the Active lifecycle set.
if sheet_ids.SHEET_WPR_HUMAN_REVIEW:
    REGISTRY[sheet_ids.SHEET_WPR_HUMAN_REVIEW] = {
        "Send Status": _WPR_HR_SEND_STATUS_VALUES,
        "Workstream": _WPR_WORKSTREAM_VALUES,
    }
if sheet_ids.SHEET_ACTIVE_JOBS_PROGRESS:
    REGISTRY[sheet_ids.SHEET_ACTIVE_JOBS_PROGRESS] = {"Active": _ACTIVE_LIFECYCLE_VALUES}

# Purchase Orders sheets (ITS — Purchase Orders / Control; WS1 S1). Same placeholder-0
# guard as above. Value sets are the write-gate side of the S1 builders — the builder
# option lists MUST stay set-equal to these (tests/test_po_s1_sheets.py pins the parity;
# the #247→#253 lesson: an option the builder has but the REGISTRY lacks blocks the
# live write path with PicklistViolationError, invisible to mocks).
#
# PO_Log Status is LOWERCASE — it mirrors the D1 `purchase_orders.status` vocabulary
# verbatim (D7 status machine: draft → pending_review → approved → sent; superseded /
# canceled off-path). PO_Pending_Review reuses the WSR SENDING-inclusive Send Status
# set (same shared send engine, same write-ahead-marker contract) and gates the P1b
# Workstream tag to {po_materials} — a `safety`/`progress` tag on the PO review sheet
# is contamination the send guard HARD-HELDs.
_PO_LOG_STATUS_VALUES: frozenset[str] = frozenset({
    "draft", "pending_review", "approved", "sent", "superseded", "canceled",
})
_PO_SEND_STATUS_VALUES: frozenset[str] = _WSR_SEND_STATUS_VALUES
_PO_WORKSTREAM_VALUES: frozenset[str] = frozenset({"po_materials"})
_VENDOR_REGION_VALUES: frozenset[str] = frozenset({"West", "Midwest", "East", "National"})
_VENDOR_SUPPLY_CATEGORY_VALUES: frozenset[str] = frozenset({
    "modules", "racking", "inverters", "electrical_bos", "wire", "switchgear",
    "combiners", "transformers", "fencing", "aggregate", "concrete",
    "tools_rentals", "other",
})
_VENDOR_TERMS_PROFILE_VALUES: frozenset[str] = frozenset({
    "standard_17", "chint_vendor", "negotiated_gtc",
})
if sheet_ids.SHEET_ITS_VENDORS:
    REGISTRY[sheet_ids.SHEET_ITS_VENDORS] = {
        "Active": _ACTIVE_LIFECYCLE_VALUES,
        "Region": _VENDOR_REGION_VALUES,
        "Supply Categories": _VENDOR_SUPPLY_CATEGORY_VALUES,
        "Default Terms Profile": _VENDOR_TERMS_PROFILE_VALUES,
    }
if sheet_ids.SHEET_PO_LOG:
    REGISTRY[sheet_ids.SHEET_PO_LOG] = {"Status": _PO_LOG_STATUS_VALUES}
if sheet_ids.SHEET_PO_PENDING_REVIEW:
    REGISTRY[sheet_ids.SHEET_PO_PENDING_REVIEW] = {
        "Send Status": _PO_SEND_STATUS_VALUES,
        "Workstream": _PO_WORKSTREAM_VALUES,
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

    A list/tuple/set value is a MULTI_PICKLIST cell (S1 — ITS_Vendors Supply
    Categories is the first): each element is validated individually against the
    registered set, so one bad element fails the whole write pre-API (a stringified
    `"['a', 'b']"` compare would spuriously reject every multi-value otherwise).
    """
    sheet_columns = REGISTRY.get(sheet_id)
    if sheet_columns is None:
        return
    allowed = sheet_columns.get(column)
    if allowed is None:
        return
    if isinstance(value, (list, tuple, set, frozenset)):
        for item in value:
            validate_cell(sheet_id, column, item)
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
