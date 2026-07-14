"""Class-B error-log operations for the operator dashboard: MARK open CRITICALs resolved,
and CLEAR the ITS_Errors log.

Two halves of "solve it → sweep it". `mark_errors_resolved` stamps "Resolved At" on OPEN
CRITICAL rows matching a Script / Error-code filter, making them TERMINAL; `clear_error_log`
then deletes terminal rows. Both share `shared.errors_rotation`'s terminality predicate, so a
row a mark makes terminal is exactly a row a clear may delete — no predicate divergence.

`clear_error_log` is the operator-triggered, no-age-floor complement to watchdog Check O's
automatic row-cap rotation. It reuses Check O's OWN terminality predicate (`shared.errors_rotation` — the
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

# Rows whose error_code is PRESERVED across a clear — the thin who-cleared / what-rotated /
# what-was-marked audit trail, excluded from the eligible set so repeated clears never erase
# the record of the clears (Check O's rotations, or the mark-resolved actions) themselves.
_PRESERVED_CODES = frozenset({"errors_log_cleared", "row_cap_rotation", "errors_resolved_marked"})

# The ITS_Errors date column parsed for the older-than filter.
_DATE_COLUMN = "Timestamp"

# The ITS_Errors column stamped to make an open CRITICAL terminal (errors_rotation reads it).
# Stamped with a bare YYYY-MM-DD, matching the sibling "Timestamp" column's proven-good format.
_RESOLVED_AT_COLUMN = "Resolved At"


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


def mark_errors_resolved(
    operator: str,
    *,
    script: str | None = None,
    error_code: str | None = None,
    dry_run: bool = False,
) -> ErrorsOutcome:
    """Stamp "Resolved At" on OPEN CRITICAL ITS_Errors rows matching a filter — the "solve it"
    half; `clear_error_log` is the "sweep it" half.

    A resolved CRITICAL becomes TERMINAL (`errors_rotation.errors_row_is_terminal` reuses the
    exact "Resolved At" set/blank predicate), so once marked, the sibling clear verb (or
    watchdog Check O's rotation) can delete it. Only OPEN CRITICALs are touched: already-terminal
    rows (every INFO/WARN/ERROR, and any already-resolved CRITICAL) are skipped, so re-running is
    idempotent and this never "un-resolves" or re-stamps anything.

    At least ONE of `script` / `error_code` MUST be given. An unfiltered mass-resolve would
    empty the entire "am I on fire" working set (watchdog Check B counts open CRITICALs), so it
    is refused — the operator resolves a KNOWN class (a retired daemon's Script, a fixed Error
    code), never "everything".

    script: match ITS_Errors "Script" cell exactly.  error_code: match the "Error" cell exactly.
    dry_run: count the matches + report, stamp nothing (preview / live-smoke).
    """
    er = _load("shared.errors_rotation")
    ss = _load("shared.smartsheet_client")
    sid = _load("shared.sheet_ids")
    defaults = _load("shared.defaults")

    script = (script or "").strip()
    code = (error_code or "").strip()
    if not script and not code:
        return ErrorsOutcome(
            "error",
            "a Script and/or Error-code filter is required — refusing to mark EVERY open "
            "CRITICAL resolved (that would empty the 'am I on fire' surface)",
        )

    # Fenced read — a breaker-open / transient Smartsheet error becomes an error outcome,
    # never a raise (mirrors clear_error_log + the daemon fences).
    try:
        rows = ss.get_rows(sid.SHEET_ERRORS)
    except Exception as exc:
        return ErrorsOutcome("error", f"could not read ITS_Errors: {type(exc).__name__}: {exc}")

    matched_ids: list[int] = []
    for row in rows:
        if er.errors_row_is_terminal(row):
            continue  # only OPEN CRITICALs are markable (terminal => already resolved / non-critical)
        if script and str(row.get("Script") or "").strip() != script:
            continue
        if code and str(row.get("Error") or "").strip() != code:
            continue
        rid = row.get("_row_id")
        if isinstance(rid, int):
            matched_ids.append(rid)

    total = len(matched_ids)
    filt = ", ".join(
        p for p in (f"Script={script!r}" if script else "", f"Error={code!r}" if code else "") if p
    )

    if total == 0:
        return ErrorsOutcome("noop", f"no OPEN CRITICAL rows match ({filt})")

    batch = defaults.SHEET_ROW_ROTATION_DELETE_BATCH
    per_run_cap = batch * defaults.SHEET_ROW_ROTATION_MAX_BATCHES_PER_RUN
    to_mark = matched_ids[:per_run_cap]

    if dry_run:
        return ErrorsOutcome(
            "ok",
            f"DRY RUN — would mark {len(to_mark)} of {total} OPEN CRITICAL row(s) resolved "
            f"({filt}, batches of {batch})",
        )

    stamp = datetime.now(UTC).date().isoformat()
    marked = 0
    for start in range(0, len(to_mark), batch):
        chunk = to_mark[start : start + batch]
        updates = [{"_row_id": rid, _RESOLVED_AT_COLUMN: stamp} for rid in chunk]
        try:
            ss.update_rows(sid.SHEET_ERRORS, updates)
        except Exception as exc:
            # Partial progress is real + honest — audit what got marked, report, don't raise.
            _audit_errors_resolved(operator, marked, total, filt, partial=True)
            return ErrorsOutcome(
                "error",
                f"marked {marked} of {total} then FAILED: {type(exc).__name__}: {exc} "
                f"— run again to continue",
            )
        marked += len(chunk)

    _audit_errors_resolved(operator, marked, total, filt, partial=False)
    remaining = total - marked
    tail = (
        "" if remaining == 0
        else f" — {remaining} remain (cap {per_run_cap}); run again to continue"
    )
    return ErrorsOutcome(
        "ok",
        f"marked {marked} of {total} OPEN CRITICAL row(s) resolved ({filt}){tail}. They are now "
        f"terminal — clear them with the clear-error-log button.",
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


def _audit_errors_resolved(
    operator: str, marked: int, matched: int, filt: str, *, partial: bool
) -> None:
    try:
        el = _load("shared.error_log")
        ts = datetime.now(UTC).isoformat()
        verb = "PARTIAL mark-resolved" if partial else "marked resolved"
        el.log(
            el.Severity.WARN,
            "operator_dashboard.errors_resolve",
            f"open CRITICALs {verb} — stamped {marked} of {matched} matching rows ({filt}) by "
            f"{operator} (elevated-confirm) at {ts}",
            error_code="errors_resolved_marked",
            alert=False,
        )
    except Exception:
        pass
