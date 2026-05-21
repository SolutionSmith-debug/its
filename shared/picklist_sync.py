"""Cross-sheet PICKLIST option sync from master DBs.

Smartsheet has no native cross-sheet picklist sync. When a row is added
to Vendor DB / Subcontractor DB / Equipment Master, the corresponding
PICKLIST options on downstream sheets stay stale until manually updated.
This module closes the gap: read source unique values, diff against
target options, update target's PICKLIST options list.

Architecture:
    Pure-function core (extract, diff, hash, validate thresholds) sits
    behind a sync_one_mapping / sync_all driver. The driver consults
    Picklist_Sync_Config for enabled mappings, performs reference-checked
    removals (no orphaning of live cell values), and updates the
    mapping row's last_run_at + last_run_hash on success for the
    idempotency short-circuit.

Op Stds v9 §27 — push-vs-record separation:
    Failures route to ITS_Errors via error_log (record). The triple-fire
    CRITICAL path (Sentry + Resend + ITS_Errors row) fires only when an
    aggregate failure threshold is reached (>=3 mappings fail in one
    run); single-mapping failures stay at ERROR severity.

Op Stds v9 §22 — MCP-gap REST fallback:
    Column-option mutations are not exposed cleanly through the SDK's
    high-level row APIs, so this module's writes go through the
    smartsheet_client helpers added in PR #45 (update_column_options,
    list_columns_with_options) which wrap the SDK's column-level calls.

Removals are gated:
    Before removing a picklist option, count live cells in the target
    column that still hold that value. If non-zero, the removal is
    skipped and an ITS_Review_Queue row is written
    (reason=MISMATCHED_REFERENCE, severity=WARN) so the operator can
    decide whether to clean up the cells, update the master DB, or
    accept the option staying.

Size guardrails (two-stage per operator directive 2026-05-20):
    WARN at >200 options (configurable via
      ITS_Config.picklist_sync.size_warn_threshold)
    HARD-HALT-that-mapping at >400 options (configurable via
      ITS_Config.picklist_sync.size_hard_halt_threshold).
    Hard-halt skips the mapping write entirely with ERROR; mapping
    auto-resumes when source returns to <= the halt threshold.
    Threshold validation: positive ints, warn<halt, both <=1000;
    all-or-nothing fallback to defaults on any invalid configured value
    plus a single WARN to ITS_Errors naming the offending input.

Idempotency:
    SHA-256 of sorted unique source values is stored in last_run_hash
    on each Picklist_Sync_Config row. Unchanged source → matching hash
    → skip writes entirely (no API calls beyond the source read).
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from . import defaults, review_queue, sheet_ids, smartsheet_client
from .error_log import Severity, log
from .review_queue import ReviewReason, SlaTier
from .smartsheet_client import SmartsheetError

_SCRIPT = "shared.picklist_sync"

# Mappings whose Resend leg should fire CRITICAL when failures across a
# single run exceed this count. Below this, per-mapping failures stay at
# ERROR (still write to ITS_Errors, no operator wake-up).
TRIPLE_FIRE_FAILURE_THRESHOLD = 3


# ---- Pure-function core --------------------------------------------------


def extract_unique_values(rows: list[dict[str, Any]], column_title: str) -> list[str]:
    """Return sorted, deduplicated, case-sensitive non-blank values from
    `column_title` across `rows`.

    Blank cells (None, empty string, whitespace-only) are excluded.
    Whitespace inside values is preserved; sort order is the default
    Python string comparison (case-sensitive — Smartsheet picklists are
    case-sensitive). Case-sensitivity is deliberate: an operator typing
    "Acme" and "acme" creates two distinct picklist options, and the
    sync surface preserves that distinction so master-DB hygiene
    surfaces in the sheet rather than getting silently collapsed.
    """
    seen: set[str] = set()
    for row in rows:
        value = row.get(column_title)
        if value is None:
            continue
        if not isinstance(value, str):
            value = str(value)
        stripped = value.strip()
        if not stripped:
            continue
        seen.add(stripped)
    return sorted(seen)


def compute_diff(
    current_options: list[str], desired_options: list[str]
) -> tuple[list[str], list[str]]:
    """Return (additions, removals) as sorted lists.

    `additions` is values in `desired_options` not in `current_options`.
    `removals` is values in `current_options` not in `desired_options`.
    Both inputs are taken as-is (caller is responsible for casing /
    whitespace normalization upstream).
    """
    current_set = set(current_options)
    desired_set = set(desired_options)
    additions = sorted(desired_set - current_set)
    removals = sorted(current_set - desired_set)
    return additions, removals


def compute_hash(values: list[str]) -> str:
    """SHA-256 of the sorted, JSON-encoded unique value list.

    Used for last_run_hash short-circuit. Caller should pass the same
    sorted/deduped list that would be written as picklist options so
    the hash captures exactly the "did the source change?" question.
    """
    canonical = json.dumps(sorted(set(values)), separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _resolve_size_thresholds() -> tuple[int, int]:
    """Read size thresholds from ITS_Config; fall back to defaults on
    any validation failure.

    Rules:
      - Both keys missing → silent fallback (documented default state).
      - Either key present but invalid (non-int, <=0, >MAX, warn>=halt)
        → all-or-nothing fallback + single WARN to ITS_Errors naming
        both raw values.

    Returns (warn_threshold, halt_threshold). Both are positive ints,
    warn < halt, both <= PICKLIST_SIZE_THRESHOLD_MAX.
    """
    code_warn = defaults.PICKLIST_SIZE_WARN_THRESHOLD
    code_halt = defaults.PICKLIST_SIZE_HARD_HALT_THRESHOLD
    max_value = defaults.PICKLIST_SIZE_THRESHOLD_MAX

    try:
        raw_warn = smartsheet_client.get_setting(
            "picklist_sync.size_warn_threshold", workstream="global"
        )
    except smartsheet_client.SmartsheetNotFoundError:
        raw_warn = None
    except Exception as e:
        log(Severity.WARN, _SCRIPT,
            f"size-threshold config read failed for size_warn_threshold: {e!r}; "
            f"falling back to defaults ({code_warn}, {code_halt})")
        return code_warn, code_halt

    try:
        raw_halt = smartsheet_client.get_setting(
            "picklist_sync.size_hard_halt_threshold", workstream="global"
        )
    except smartsheet_client.SmartsheetNotFoundError:
        raw_halt = None
    except Exception as e:
        log(Severity.WARN, _SCRIPT,
            f"size-threshold config read failed for size_hard_halt_threshold: {e!r}; "
            f"falling back to defaults ({code_warn}, {code_halt})")
        return code_warn, code_halt

    if raw_warn is None and raw_halt is None:
        return code_warn, code_halt

    def _parse(raw: str | None, code_default: int) -> int | None:
        if raw is None:
            return code_default
        try:
            return int(raw)
        except (TypeError, ValueError):
            return None

    parsed_warn = _parse(raw_warn, code_warn)
    parsed_halt = _parse(raw_halt, code_halt)

    invalid_reason: str | None = None
    if parsed_warn is None or parsed_halt is None:
        invalid_reason = "non-integer"
    elif parsed_warn <= 0 or parsed_halt <= 0:
        invalid_reason = "non-positive"
    elif parsed_warn > max_value or parsed_halt > max_value:
        invalid_reason = f"exceeds sanity ceiling ({max_value})"
    elif parsed_warn >= parsed_halt:
        invalid_reason = "inverted (warn >= halt)"

    if invalid_reason is not None:
        log(Severity.WARN, _SCRIPT,
            f"picklist_sync size thresholds invalid ({invalid_reason}): "
            f"warn={raw_warn!r}, halt={raw_halt!r}; "
            f"falling back to defaults ({code_warn}, {code_halt})")
        return code_warn, code_halt

    # Both parsed values are validated non-None ints by this point.
    assert parsed_warn is not None and parsed_halt is not None
    return parsed_warn, parsed_halt


# ---- Mapping data + result types ----------------------------------------


@dataclass(frozen=True)
class Mapping:
    """One row from Picklist_Sync_Config — defines a source→target sync."""
    mapping_id: str
    source_sheet_id: int
    source_column: str
    target_sheet_id: int
    target_column: str
    enabled: bool
    last_run_at: str | None
    last_run_hash: str | None
    notes: str | None
    _row_id: int  # Smartsheet row ID for update_rows


@dataclass
class MappingResult:
    """One mapping's outcome after sync_one_mapping."""
    mapping_id: str
    status: str  # "skipped_unchanged" | "applied" | "dry_run" | "halted_oversize" | "failed"
    additions: list[str] = field(default_factory=list)
    removals_applied: list[str] = field(default_factory=list)
    removals_blocked: list[str] = field(default_factory=list)
    review_queue_rows: list[int] = field(default_factory=list)
    error: str | None = None


@dataclass
class SyncStats:
    """Aggregate stats across one sync_all invocation."""
    mappings_examined: int = 0
    mappings_applied: int = 0
    mappings_skipped_unchanged: int = 0
    mappings_dry_run: int = 0
    mappings_halted_oversize: int = 0
    mappings_failed: int = 0
    additions_total: int = 0
    removals_applied_total: int = 0
    removals_blocked_total: int = 0
    results: list[MappingResult] = field(default_factory=list)


# ---- Config reads / writes ----------------------------------------------


def read_mappings_from_config() -> list[Mapping]:
    """Load every row from Picklist_Sync_Config as a Mapping.

    Disabled rows are returned too — caller filters. Returns [] on any
    error (callers see "nothing to sync" and proceed); the underlying
    SmartsheetError surfaces via the standard error_log path.
    """
    rows = smartsheet_client.get_rows(sheet_ids.SHEET_PICKLIST_SYNC_CONFIG)
    out: list[Mapping] = []
    for row in rows:
        mapping_id = row.get("mapping_id")
        if not isinstance(mapping_id, str) or not mapping_id.strip():
            continue
        try:
            source_sheet_id = int(row.get("source_sheet_id") or 0)
            target_sheet_id = int(row.get("target_sheet_id") or 0)
        except (TypeError, ValueError):
            log(Severity.WARN, _SCRIPT,
                f"mapping_id={mapping_id!r} has non-integer sheet id; skipping row")
            continue
        out.append(Mapping(
            mapping_id=mapping_id,
            source_sheet_id=source_sheet_id,
            source_column=str(row.get("source_column") or ""),
            target_sheet_id=target_sheet_id,
            target_column=str(row.get("target_column") or ""),
            enabled=bool(row.get("enabled")),
            last_run_at=row.get("last_run_at") if isinstance(row.get("last_run_at"), str) else None,
            last_run_hash=row.get("last_run_hash") if isinstance(row.get("last_run_hash"), str) else None,
            notes=row.get("notes") if isinstance(row.get("notes"), str) else None,
            _row_id=int(row["_row_id"]),
        ))
    return out


def update_run_state(mapping: Mapping, run_hash: str, run_at: datetime) -> None:
    """Write last_run_at + last_run_hash for a mapping. Best-effort.

    Failures here only affect the idempotency short-circuit on the next
    run (worst case: same-source run re-applies the same options, which
    is a no-op at Smartsheet's column-level write). Failures log WARN.
    """
    try:
        smartsheet_client.update_rows(
            sheet_ids.SHEET_PICKLIST_SYNC_CONFIG,
            [{
                "_row_id": mapping._row_id,
                "last_run_at": run_at.isoformat(),
                "last_run_hash": run_hash,
            }],
        )
    except SmartsheetError as e:
        log(Severity.WARN, _SCRIPT,
            f"failed to update run-state for mapping={mapping.mapping_id!r}: {e!r}")


# ---- Reference-check (remove-gating) -------------------------------------


def find_cells_using_option(
    sheet_id: int, column_title: str, option: str
) -> int:
    """Count cells in `column_title` on `sheet_id` whose value equals `option`.

    Used to gate option removal: if any live cell still holds the
    value, the option cannot be removed without orphaning data. Returns
    0 on read failure (fail-safe — if we can't verify safety, behave as
    if the option is in use; the operator gets a Review Queue row
    rather than a destructive removal).
    """
    try:
        rows = smartsheet_client.get_rows(sheet_id, filters={column_title: option})
    except SmartsheetError as e:
        log(Severity.WARN, _SCRIPT,
            f"reference check read failed for sheet_id={sheet_id} "
            f"column={column_title!r} option={option!r}: {e!r}; "
            f"treating as in-use to gate removal")
        return 1  # >0 → blocks removal
    return len(rows)


def _log_removal_blocked(
    mapping: Mapping, option: str, in_use_count: int
) -> int:
    """Write an ITS_Review_Queue row for a removal blocked by live cells.

    Returns the new row ID. Failures propagate — the caller will catch
    them at the mapping level.
    """
    summary = (
        f"picklist removal blocked: {option!r} still used by "
        f"{in_use_count} cell(s) in {mapping.target_column!r} "
        f"on sheet {mapping.target_sheet_id}"
    )
    return review_queue.add(
        workstream="global",
        summary=summary,
        payload={
            "type": "picklist_removal_blocked",
            "mapping_id": mapping.mapping_id,
            "source_sheet_id": mapping.source_sheet_id,
            "source_column": mapping.source_column,
            "target_sheet_id": mapping.target_sheet_id,
            "target_column": mapping.target_column,
            "option_text": option,
            "in_use_count": in_use_count,
        },
        sla_tier=SlaTier.SUBCONTRACT_DRAFT,
        reason=ReviewReason.MISMATCHED_REFERENCE,
        severity=Severity.WARN,
        source_file=__file__,
        security_flag=False,
    )


# ---- Per-mapping sync ----------------------------------------------------


def _find_column(columns: list[dict[str, Any]], title: str) -> dict[str, Any] | None:
    for col in columns:
        if col["title"] == title:
            return col
    return None


def sync_one_mapping(mapping: Mapping, *, dry_run: bool = False) -> MappingResult:
    """Sync one mapping. Pure-result; caller routes the result to logs.

    Steps:
      1. Read source column values; compute hash.
      2. If hash == mapping.last_run_hash → short-circuit (no API write).
      3. Read target columns; locate the target PICKLIST column.
      4. Validate proposed size against thresholds.
         - >halt → return halted_oversize, no write.
         - >warn → log WARN, continue.
      5. Compute diff (additions, removals).
      6. For each removal, reference-check the target column. Blocked
         removals route to Review Queue and stay in the picklist.
      7. Apply: write the new option list (additions + kept removals +
         remaining options), sorted alphabetically.
      8. update_run_state if not dry-run.
    """
    result = MappingResult(mapping_id=mapping.mapping_id, status="applied")
    try:
        # 1. Source extract + hash
        source_rows = smartsheet_client.get_rows(mapping.source_sheet_id)
        source_values = extract_unique_values(source_rows, mapping.source_column)
        run_hash = compute_hash(source_values)

        # 2. Short-circuit on unchanged source
        if mapping.last_run_hash == run_hash and not dry_run:
            result.status = "skipped_unchanged"
            return result

        # 3. Target columns + locate target column
        target_columns = smartsheet_client.list_columns_with_options(mapping.target_sheet_id)
        target_col = _find_column(target_columns, mapping.target_column)
        if target_col is None:
            result.status = "failed"
            result.error = (
                f"target column {mapping.target_column!r} not found on "
                f"sheet {mapping.target_sheet_id}"
            )
            return result
        if target_col["type"] not in ("PICKLIST", "MULTI_PICKLIST"):
            result.status = "failed"
            result.error = (
                f"target column {mapping.target_column!r} is type "
                f"{target_col['type']!r}, expected PICKLIST"
            )
            return result

        current_options = list(target_col["options"])
        additions, removals = compute_diff(current_options, source_values)
        result.additions = additions

        # 4. Size guardrails (against the proposed final size, BEFORE
        # reference-blocked removals are added back in — caller should
        # rarely hit halt on a master DB but guard anyway)
        warn_threshold, halt_threshold = _resolve_size_thresholds()
        proposed_size = len(source_values)
        if proposed_size > halt_threshold:
            log(Severity.ERROR, _SCRIPT,
                f"mapping_id={mapping.mapping_id!r}: proposed {proposed_size} options "
                f"exceeds hard-halt threshold ({halt_threshold}); skipping write")
            result.status = "halted_oversize"
            result.error = (
                f"proposed size {proposed_size} > halt {halt_threshold}"
            )
            return result
        if proposed_size > warn_threshold:
            log(Severity.WARN, _SCRIPT,
                f"mapping_id={mapping.mapping_id!r}: proposed {proposed_size} options "
                f"exceeds warn threshold ({warn_threshold})")

        # 5-6. Gate removals via reference check
        final_options = set(source_values)
        for opt in removals:
            in_use = find_cells_using_option(
                mapping.target_sheet_id, mapping.target_column, opt
            )
            if in_use > 0:
                # Reference-blocked → keep option in target; log Review Queue
                final_options.add(opt)
                result.removals_blocked.append(opt)
                if not dry_run:
                    try:
                        rq_row = _log_removal_blocked(mapping, opt, in_use)
                        result.review_queue_rows.append(rq_row)
                    except SmartsheetError as e:
                        log(Severity.ERROR, _SCRIPT,
                            f"failed to log Review Queue row for blocked removal "
                            f"option={opt!r}: {e!r}")
            else:
                result.removals_applied.append(opt)

        # 7. Apply or dry-run
        sorted_final = sorted(final_options)
        if dry_run:
            result.status = "dry_run"
            return result

        smartsheet_client.update_column_options(
            mapping.target_sheet_id,
            target_col["id"],
            sorted_final,
            column_type=target_col["type"],
        )

        # 8. Persist run-state
        update_run_state(mapping, run_hash, datetime.now(UTC))
        result.status = "applied"
        return result

    except SmartsheetError as e:
        result.status = "failed"
        result.error = repr(e)
        return result
    except Exception as e:
        result.status = "failed"
        result.error = repr(e)
        return result


# ---- Driver --------------------------------------------------------------


def sync_all(
    *, only: str | None = None, dry_run: bool = False
) -> SyncStats:
    """Run every enabled mapping in Picklist_Sync_Config.

    `only` narrows to a single mapping_id. `dry_run` skips all writes.

    Failures per mapping are logged at ERROR and counted toward the
    triple-fire threshold; sync_all itself returns normally so the
    watchdog / cron has stable success semantics. If failures >= the
    triple-fire threshold, an ERROR row is logged that surfaces
    through Resend/Sentry per the existing CRITICAL alert path.
    """
    stats = SyncStats()
    try:
        mappings = read_mappings_from_config()
    except SmartsheetError as e:
        log(Severity.ERROR, _SCRIPT,
            f"failed to read Picklist_Sync_Config: {e!r}")
        return stats

    selected = [m for m in mappings if m.enabled]
    if only is not None:
        selected = [m for m in selected if m.mapping_id == only]

    stats.mappings_examined = len(selected)
    for mapping in selected:
        result = sync_one_mapping(mapping, dry_run=dry_run)
        stats.results.append(result)
        if result.status == "applied":
            stats.mappings_applied += 1
        elif result.status == "skipped_unchanged":
            stats.mappings_skipped_unchanged += 1
        elif result.status == "dry_run":
            stats.mappings_dry_run += 1
        elif result.status == "halted_oversize":
            stats.mappings_halted_oversize += 1
        elif result.status == "failed":
            stats.mappings_failed += 1
            log(Severity.ERROR, _SCRIPT,
                f"mapping_id={result.mapping_id!r} failed: {result.error}")
        stats.additions_total += len(result.additions)
        stats.removals_applied_total += len(result.removals_applied)
        stats.removals_blocked_total += len(result.removals_blocked)

    if stats.mappings_failed >= TRIPLE_FIRE_FAILURE_THRESHOLD:
        # CRITICAL escalation. Per Op Stds v9 §3, this routes through
        # the triple-fire path (ITS_Errors + Resend + Sentry via
        # error_log's _alert_critical). The log() with CRITICAL severity
        # writes the row; the decorator on the caller (run_picklist_sync.py)
        # handles the alert leg via its uncaught_exception branch only if
        # the caller raises. We DO NOT raise here — sync_all returns
        # normally on partial failure — so the alert escalation has to be
        # explicit. Lazy-import to avoid the circular with error_log.
        from uuid import uuid4

        from .error_log import _alert_critical
        correlation_id = str(uuid4())
        message = (
            f"{stats.mappings_failed} picklist sync mappings failed in one run "
            f"(threshold {TRIPLE_FIRE_FAILURE_THRESHOLD})"
        )
        log(Severity.CRITICAL, _SCRIPT, message,
            error_code="picklist_sync_multi_failure",
            correlation_id=correlation_id)
        _alert_critical(
            _SCRIPT, message, "(no exception — aggregate failure signal)",
            correlation_id=correlation_id,
            error_code="picklist_sync_multi_failure",
        )

    return stats
