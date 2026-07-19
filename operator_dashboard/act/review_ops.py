"""Class-B review-queue operations (DASH-13): resolve PENDING ITS_Review_Queue
rows matching a filter.

The review-queue twin of `errors_ops.mark_errors_resolved`. ITS_Review_Queue
rows are append-only signals — nothing in the system ever moves them out of
PENDING, so stale classes (a deleted sandbox job's compile-failure storm)
accumulate forever and drown watchdog Check A. This verb moves matching
PENDING rows to a terminal Status (REJECTED for noise/stale, APPROVED for
handled-out-of-band), stamps `Resolved At` + `Resolution Notes`, and preserves
the sheet as the audit trail (no deletion here — row rotation stays with
watchdog Check O).

Same hardening as the errors verbs: a FILTER IS REQUIRED (an unfiltered
mass-resolve would blank the review surface Check A watches); only PENDING
rows are touched (idempotent re-runs); bounded per-run cap; dry-run preview;
snapshot-then-write with honest partial-failure reporting; a durable audit row
via error_log. `Status` values APPROVED/REJECTED are already registered in
`shared.picklist_validation.REGISTRY`, so the gated `update_rows` passes
without a registry change. Elevated-confirm (re-PIN + typed "resolve-review")
is enforced by the router.

§43: symptom + Tier-2 repair in docs/runbooks/operator_dashboard_config_editor.md.
"""
from __future__ import annotations

import importlib
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

_STATUS_COLUMN = "Status"
_PENDING = "PENDING"
_RESOLVED_AT_COLUMN = "Resolved At"
_NOTES_COLUMN = "Resolution Notes"
# The two terminal statuses this verb may write (both in picklist REGISTRY).
RESOLUTIONS = ("REJECTED", "APPROVED")
_MAX_NOTE_CHARS = 300


@dataclass
class ReviewOutcome:
    kind: str  # ok | noop | rejected | error (also CSS status class + test assertion)
    message: str


def _load(name: str) -> Any:
    return importlib.import_module(name)


def resolve_review_rows(
    operator: str,
    *,
    workstream: str | None = None,
    summary_prefix: str | None = None,
    resolution: str = "REJECTED",
    note: str = "",
    dry_run: bool = False,
) -> ReviewOutcome:
    """Move PENDING review rows matching the filter to a terminal Status.

    workstream: match the "Workstream" cell exactly. summary_prefix: match rows
    whose "Summary" cell STARTS WITH this text (the recurring classes share a
    stable prefix, e.g. 'weekly compile failed for job JOB-000013'). At least
    one filter is required. dry_run: count + report, write nothing.
    """
    ss = _load("shared.smartsheet_client")
    sid = _load("shared.sheet_ids")
    defaults = _load("shared.defaults")

    ws = (workstream or "").strip()
    prefix = (summary_prefix or "").strip()
    if not ws and not prefix:
        return ReviewOutcome(
            "rejected",
            "a Workstream and/or Summary-prefix filter is required — refusing to resolve "
            "EVERY pending review row (that would blank the review surface Check A watches)",
        )
    if resolution not in RESOLUTIONS:
        return ReviewOutcome(
            "rejected", f"resolution must be one of {RESOLUTIONS} (got {resolution!r})"
        )

    # Fenced read — a breaker-open / transient Smartsheet error becomes an error
    # outcome, never a raise (mirrors errors_ops + the daemon fences).
    try:
        rows = ss.get_rows(sid.SHEET_REVIEW_QUEUE)
    except Exception as exc:
        return ReviewOutcome(
            "error", f"could not read ITS_Review_Queue: {type(exc).__name__}: {exc}"
        )

    matched_ids: list[int] = []
    for row in rows:
        if str(row.get(_STATUS_COLUMN) or "").strip().upper() != _PENDING:
            continue  # only PENDING rows are resolvable; re-runs are idempotent
        if ws and str(row.get("Workstream") or "").strip() != ws:
            continue
        if prefix and not str(row.get("Summary") or "").strip().startswith(prefix):
            continue
        rid = row.get("_row_id")
        if isinstance(rid, int):
            matched_ids.append(rid)

    total = len(matched_ids)
    filt = ", ".join(
        p
        for p in (
            f"Workstream={ws!r}" if ws else "",
            f"Summary startswith {prefix!r}" if prefix else "",
        )
        if p
    )

    if total == 0:
        return ReviewOutcome("noop", f"no PENDING review rows match ({filt})")

    batch = defaults.SHEET_ROW_ROTATION_DELETE_BATCH
    per_run_cap = batch * defaults.SHEET_ROW_ROTATION_MAX_BATCHES_PER_RUN
    to_mark = matched_ids[:per_run_cap]

    if dry_run:
        return ReviewOutcome(
            "ok",
            f"DRY RUN — would mark {len(to_mark)} of {total} PENDING review row(s) "
            f"{resolution} ({filt}, batches of {batch})",
        )

    stamp = datetime.now(UTC).date().isoformat()
    # The operator identity rides in Resolution Notes (the "Resolved By" column
    # is CONTACT_LIST-typed; a bare login string can fail its validation).
    note_text = f"resolved via dashboard by {operator}"
    if note.strip():
        note_text += f": {note.strip()[:_MAX_NOTE_CHARS]}"

    marked = 0
    for start in range(0, len(to_mark), batch):
        chunk = to_mark[start : start + batch]
        updates = [
            {
                "_row_id": rid,
                _STATUS_COLUMN: resolution,
                _RESOLVED_AT_COLUMN: stamp,
                _NOTES_COLUMN: note_text,
            }
            for rid in chunk
        ]
        try:
            ss.update_rows(sid.SHEET_REVIEW_QUEUE, updates)
        except Exception as exc:
            # Partial progress is real + honest — audit what got marked, report, don't raise.
            _audit(operator, marked, total, filt, resolution, partial=True)
            return ReviewOutcome(
                "error",
                f"marked {marked} of {total} then FAILED: {type(exc).__name__}: {exc} "
                f"— run again to continue",
            )
        marked += len(chunk)

    _audit(operator, marked, total, filt, resolution, partial=False)
    remaining = total - marked
    tail = (
        ""
        if remaining == 0
        else f" — {remaining} remain (cap {per_run_cap}); run again to continue"
    )
    return ReviewOutcome(
        "ok", f"marked {marked} of {total} PENDING review row(s) {resolution} ({filt}){tail}"
    )


def _audit(
    operator: str, marked: int, matched: int, filt: str, resolution: str, *, partial: bool
) -> None:
    try:
        el = _load("shared.error_log")
        ts = datetime.now(UTC).isoformat()
        verb = "PARTIAL resolve" if partial else "resolved"
        el.log(
            el.Severity.WARN,
            "operator_dashboard.review_resolve",
            f"review-queue rows {verb} — {marked} of {matched} matching rows set "
            f"{resolution} ({filt}) by {operator} (elevated-confirm) at {ts}",
            error_code="review_rows_resolved",
            alert=False,
        )
    except Exception:
        pass
