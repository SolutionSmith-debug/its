"""Class-B error-log operation for the operator dashboard: CLEAR the ITS_Errors log.

The operator-triggered, no-age-floor complement to watchdog Check O's automatic row-cap
rotation. It reuses Check O's OWN terminality predicate (`shared.errors_rotation` — the
single source of truth) so it inherits the never-delete-an-OPEN-CRITICAL invariant: every
INFO/WARN/ERROR row and every RESOLVED CRITICAL is deletable; an open CRITICAL (blank
"Resolved At") is the "am I on fire" surface watchdog Check B counts and is NEVER touched.

This deletes from the §3.1 forensic surface, so it is heavier than the sibling
`state_ops.clear_circuit_breaker` (which resets a state file). Within Class-B it is hardened
with: the never-delete-open-CRITICAL invariant, a preserved clear/rotation AUDIT trail, the
same bounded per-run cap as Check O (200-batches x 23 = 4,600 — a large backlog clears over
repeated invocations, so one synchronous htmx request can't time out), a snapshot-then-delete
ordering so the single audit row is written LAST and can never be in its own delete set, and a
loud never-silent audit row. The elevated-confirm ceremony (re-PIN + typed "clear-error-log")
is verified by the router before this runs. No secret, no send, no ITS_Config write.

§43: symptoms + Tier-2 repairs in docs/runbooks/operator_dashboard_config_editor.md.
"""
from __future__ import annotations

import importlib
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

# Rows whose error_code is PRESERVED across a clear — the thin who-cleared / what-rotated
# audit trail, excluded from the eligible set so repeated clears never erase the record of
# the clears (or Check O's rotations) themselves.
_PRESERVED_CODES = frozenset({"errors_log_cleared", "row_cap_rotation"})

# The ITS_Errors date column parsed for the older-than filter.
_DATE_COLUMN = "Timestamp"


@dataclass
class ErrorsOutcome:
    kind: str  # ok | noop | error (also CSS status class + test assertion)
    message: str


def _load(name: str) -> Any:
    return importlib.import_module(name)


def clear_error_log(
    operator: str, *, older_than_days: int | None = None, dry_run: bool = False
) -> ErrorsOutcome:
    """Delete TERMINAL ITS_Errors rows (never an OPEN CRITICAL) on demand.

    older_than_days: keep rows newer than this many days (None => every terminal row).
    dry_run: count eligible + report, delete nothing (live-smoke / preview).
    """
    er = _load("shared.errors_rotation")
    ss = _load("shared.smartsheet_client")
    sid = _load("shared.sheet_ids")
    defaults = _load("shared.defaults")

    cutoff: date | None = None
    if older_than_days is not None:
        if older_than_days < 0:
            return ErrorsOutcome("error", f"older_than_days must be >= 0 (got {older_than_days})")
        cutoff = datetime.now(UTC).date() - timedelta(days=older_than_days)

    # Fenced read — a breaker-open / transient Smartsheet error becomes an error outcome,
    # never a raise (mirrors the daemon fences).
    try:
        rows = ss.get_rows(sid.SHEET_ERRORS)
    except Exception as exc:
        return ErrorsOutcome("error", f"could not read ITS_Errors: {type(exc).__name__}: {exc}")

    eligible_ids: list[int] = []
    for row in rows:
        if not er.errors_row_is_terminal(row):
            continue  # open CRITICAL — never deletable
        if str(row.get("Error") or "").strip() in _PRESERVED_CODES:
            continue  # preserve the clear / rotation audit trail
        if cutoff is not None:
            d = er.row_age_date(row, _DATE_COLUMN)
            if d is None or d >= cutoff:
                continue  # unprovable age, or newer than the cutoff => keep
        rid = row.get("_row_id")
        if isinstance(rid, int):
            eligible_ids.append(rid)

    total_eligible = len(eligible_ids)
    scope = f"older than {older_than_days}d" if older_than_days is not None else "all ages"

    if total_eligible == 0:
        return ErrorsOutcome(
            "noop", f"no terminal rows to clear ({scope}); open CRITICALs are never touched"
        )

    batch = defaults.SHEET_ROW_ROTATION_DELETE_BATCH
    per_run_cap = batch * defaults.SHEET_ROW_ROTATION_MAX_BATCHES_PER_RUN
    to_delete = eligible_ids[:per_run_cap]

    if dry_run:
        return ErrorsOutcome(
            "ok",
            f"DRY RUN — would delete {len(to_delete)} of {total_eligible} terminal rows "
            f"({scope}, batches of {batch}); open CRITICALs excluded",
        )

    deleted = 0
    for start in range(0, len(to_delete), batch):
        chunk = to_delete[start : start + batch]
        try:
            ss.delete_rows(sid.SHEET_ERRORS, chunk)
        except Exception as exc:
            # Partial progress is real + honest — audit what got deleted, report, don't raise.
            _audit_errors_clear(operator, deleted, total_eligible, scope, partial=True)
            return ErrorsOutcome(
                "error",
                f"deleted {deleted} of {total_eligible} then FAILED: "
                f"{type(exc).__name__}: {exc} — run again to continue",
            )
        deleted += len(chunk)

    # Audit row written LAST — the eligible ids were snapshot before any delete, so this row
    # (error_code=errors_log_cleared, in _PRESERVED_CODES) is never in its own delete set.
    _audit_errors_clear(operator, deleted, total_eligible, scope, partial=False)
    remaining = total_eligible - deleted
    tail = "" if remaining == 0 else f" — {remaining} remain (cap {per_run_cap}); run again to continue"
    return ErrorsOutcome(
        "ok", f"cleared {deleted} of {total_eligible} terminal ITS_Errors rows ({scope}){tail}"
    )


def _audit_errors_clear(
    operator: str, deleted: int, eligible: int, scope: str, *, partial: bool
) -> None:
    try:
        el = _load("shared.error_log")
        ts = datetime.now(UTC).isoformat()
        verb = "PARTIAL clear" if partial else "cleared"
        el.log(
            el.Severity.WARN,
            "operator_dashboard.errors_clear",
            f"error log {verb} — deleted {deleted} of {eligible} terminal rows ({scope}) by "
            f"{operator} (elevated-confirm) at {ts}",
            error_code="errors_log_cleared",
            alert=False,
        )
    except Exception:
        pass
