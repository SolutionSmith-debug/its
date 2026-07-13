"""Subcontract_Log access — the operator-visible ledger MIRROR of the D1 subcontract store (SC S4).

One row per generated subcontract. `subcontract_poll`'s drafts pass APPENDS the row at
filing time (Status=pending_review — the D1 machine's state the moment mark-filed
lands); the status pass STAMPS the later transitions (approved / sent / executed /
superseded / canceled) as it mirrors them to the Worker. D1 (via the Worker) remains the
authoritative subcontract status machine — this sheet is the ledger the operator reads,
plus the transition home of HAND-ISSUED subcontracts (which is why
`numbering.check_collision` scans it before any filing).

Notes-encoded D1 id (§19)
-------------------------
The sheet schema (S1, live) has no dedicated D1-id column, so each filed row's Notes
cell is seeded with a machine-parsable ``d1_id=<n>`` prefix (the Notes-encoding
pattern weekly_send already uses for retry state). That id is the join used by
(a) the crash-retry branch of the collision check (this row is OURS → resume, not
collide) and (b) the supersession helpers (`find_sc_number_by_d1_id` resolves a
predecessor's contractual number from its D1 id — the /pending payload carries only
`supersedes_sc_id`).

Write discipline
----------------
* `append_filed_row` is APPEND-ONLY and idempotent-by-caller: the caller checks
  `find_row_by_sc_number` first (the collision/retry logic in `numbering` +
  subcontract_poll).
* `stamp_status` writes ONLY REGISTRY-legal Status values (the lowercase D1
  vocabulary; `shared.picklist_validation` gates the actual write) and SKIPS the
  update when the row is already at the target — the status pass runs every cycle,
  so a no-op re-stamp must not generate API chatter.
* Smartsheet failures propagate typed — the caller's fence classifies them.
"""
from __future__ import annotations

import re
from typing import Any

from shared import sheet_ids, smartsheet_client

SHEET_ID = sheet_ids.SHEET_SUBCONTRACT_LOG

# ---- Column titles (mirror scripts/migrations/build_subcontract_log_sheet.py) ----
COL_SC_NUMBER = "SC Number"          # primary — the contractual D7 identity
COL_JOB_PROJECT = "Job / Project"
COL_JOB_ID = "Job ID"
COL_SUBCONTRACTOR = "Subcontractor"  # subcontractor display name at generate time
COL_SUB_KEY = "Sub Key"
COL_STATUS = "Status"                # PICKLIST — lowercase D1 vocabulary
COL_TOTAL = "Total"
COL_SC_PDF = "Subcontract PDF"
COL_SUPERSEDES = "Supersedes"        # display: the predecessor's SC number
COL_SUPERSEDED_BY = "Superseded By"  # display: the successor's SC number
COL_TERMS_PROFILE = "Terms Profile"
COL_CREATED_BY = "Created By"
COL_CREATED_AT = "Created At"        # DATE
COL_SENT_AT = "Sent At"              # DATE
COL_NOTES = "Notes"

# The D1 status vocabulary this ledger mirrors (matches the S1 builder's
# STATUS_OPTIONS and picklist_validation._SUBCONTRACT_LOG_STATUS_VALUES verbatim —
# 'draft'/'queued' are deliberately absent: the ledger row is first written AT FILING
# (pending_review), so a draft/queued subcontract has not been filed and has no ledger
# row yet). PARITY: picklist_validation MUST register this exact set for
# SHEET_SUBCONTRACT_LOG's "Status" column, or a legal stamp is rejected at the API layer.
STATUS_PENDING_REVIEW = "pending_review"
STATUS_APPROVED = "approved"
STATUS_SENT = "sent"
STATUS_EXECUTED = "executed"
STATUS_SUPERSEDED = "superseded"
STATUS_CANCELED = "canceled"
LEGAL_STATUSES: frozenset[str] = frozenset({
    STATUS_PENDING_REVIEW, STATUS_APPROVED, STATUS_SENT,
    STATUS_EXECUTED, STATUS_SUPERSEDED, STATUS_CANCELED,
})

_D1_ID_RE = re.compile(r"(?:^|;\s*)d1_id=(\d+)(?:;|$)")


def notes_for_filed_row(d1_id: int, *, extra: str = "") -> str:
    """Build the Notes cell for a freshly-filed row: the machine-parsable
    ``d1_id=<n>`` prefix (§19 Notes-encoding — see module docstring) + optional
    human-readable extra."""
    base = f"d1_id={d1_id}"
    return f"{base}; {extra}" if extra else base


def row_d1_id(row: dict[str, Any]) -> int | None:
    """Extract the Notes-encoded D1 id from a Subcontract_Log row, or None (a
    hand-issued row keyed in by the operator carries no d1_id — that absence is exactly
    what makes it a COLLISION when its number matches a D1 allocation)."""
    m = _D1_ID_RE.search(str(row.get(COL_NOTES) or ""))
    return int(m.group(1)) if m else None


def format_total_cents(total_cents: int) -> str:
    """Integer cents → the operator-display dollar string (e.g. 147286 → '$1,472.86')."""
    dollars, cents = divmod(int(total_cents), 100)
    return f"${dollars:,}.{cents:02d}"


def find_row_by_sc_number(
    sc_number: str, *, sheet_id: int | None = None
) -> dict[str, Any] | None:
    """The ledger row whose SC Number == `sc_number`, or None. The identity lookup
    behind the collision double-check + the status stamps.

    `sheet_id` defaults to the flat Subcontract_Log; pass a per-job tracking sheet
    ID (shared/job_sheet.py) so the per-job append's idempotency guard runs against
    the TARGET sheet, independent of the flat ledger."""
    rows = smartsheet_client.get_rows(
        SHEET_ID if sheet_id is None else sheet_id,
        filters={COL_SC_NUMBER: sc_number},
    )
    return rows[0] if rows else None


def find_sc_number_by_d1_id(d1_id: int) -> str | None:
    """Resolve a D1 subcontract id → its ledger SC Number via the Notes-encoded d1_id.

    Used for supersession: the /pending payload names a predecessor only by
    `supersedes_sc_id`, but the in-body clause + the Superseded-By display need the
    predecessor's CONTRACTUAL number. None when no filed row carries the id (a
    pre-pipeline / hand-issued predecessor) — the caller degrades to family-form
    wording rather than inventing a number.
    """
    for row in smartsheet_client.get_rows(SHEET_ID):
        if row_d1_id(row) == d1_id:
            value = str(row.get(COL_SC_NUMBER) or "").strip()
            return value or None
    return None


def append_filed_row(
    *,
    sc_number: str,
    job_project: str,
    job_id: str,
    subcontractor_name: str,
    sub_key: str,
    total_cents: int,
    pdf_link: str,
    supersedes_display: str,
    terms_profile: str,
    created_by: str,
    created_at_iso: str,
    notes: str,
    sheet_id: int | None = None,
) -> int:
    """APPEND the filing-time ledger row (Status=pending_review); return its row ID.

    `notes` MUST come from `notes_for_filed_row` (the d1_id join rides it).
    `supersedes_display` is the resolved predecessor SC number ('' for a
    non-superseding subcontract). Caller guarantees no-collision/no-duplicate via
    `numbering.check_collision` first.

    `sheet_id` defaults to the flat Subcontract_Log (the ledger SoR mirror); pass a
    per-job tracking sheet ID (shared/job_sheet.py — structure-cloned from this
    very sheet, so the column titles match) to mirror the row there. Callers guard
    duplicates per target via `find_row_by_sc_number(..., sheet_id=...)`.
    """
    [row_id] = smartsheet_client.add_rows(
        SHEET_ID if sheet_id is None else sheet_id,
        [{
            COL_SC_NUMBER: sc_number,
            COL_JOB_PROJECT: job_project,
            COL_JOB_ID: job_id,
            COL_SUBCONTRACTOR: subcontractor_name,
            COL_SUB_KEY: sub_key,
            COL_STATUS: STATUS_PENDING_REVIEW,
            COL_TOTAL: format_total_cents(total_cents),
            COL_SC_PDF: pdf_link,
            COL_SUPERSEDES: supersedes_display,
            COL_TERMS_PROFILE: terms_profile,
            COL_CREATED_BY: created_by,
            COL_CREATED_AT: created_at_iso,
            COL_NOTES: notes,
        }],
    )
    return row_id


def stamp_status(
    sc_number: str,
    status: str,
    *,
    sent_at_iso: str | None = None,
    superseded_by: str | None = None,
) -> bool:
    """Stamp a ledger row's Status (+ optional Sent At / Superseded By). Returns
    True iff an update was written (False = row missing OR already at target).

    Guards:
    * `status` must be REGISTRY-legal (`LEGAL_STATUSES`) — a ValueError here beats a
      `PicklistViolationError` at the API layer because it names the caller's bug.
    * No-op skip: an already-at-target row (same Status, and no new Sent At /
      Superseded By value to add) writes nothing — the per-cycle status pass would
      otherwise re-write every settled row every 90s.
    """
    if status not in LEGAL_STATUSES:
        raise ValueError(
            f"illegal Subcontract_Log status {status!r} (legal: {sorted(LEGAL_STATUSES)})"
        )
    row = find_row_by_sc_number(sc_number)
    if row is None:
        return False

    cells: dict[str, Any] = {}
    if str(row.get(COL_STATUS) or "") != status:
        cells[COL_STATUS] = status
    if sent_at_iso and not row.get(COL_SENT_AT):
        cells[COL_SENT_AT] = sent_at_iso
    if superseded_by and str(row.get(COL_SUPERSEDED_BY) or "").strip() != superseded_by:
        cells[COL_SUPERSEDED_BY] = superseded_by
    if not cells:
        return False

    smartsheet_client.update_rows(
        SHEET_ID, [{"_row_id": int(row["_row_id"]), **cells}]
    )
    return True
