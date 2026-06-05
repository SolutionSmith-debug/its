"""ITS_Active_Jobs reads — Job ID → job record (Safety Portal Phase 3).

Purpose
-------
Read-only lookup against the office-PM-maintained `ITS_Active_Jobs` sheet — the
single source of truth for which jobs exist, their routing contacts, and their
Active/Inactive/Archived status. The portal never reads Smartsheet; only this
module + the Python pipeline do.

Invariants
----------
- READ-ONLY: no write path (so Op Stds §30 integration-test discipline does not fire).
- The sheet IS the source — there is NO hardcoded fallback (unlike project_routing's
  BOX_PROJECT_FOLDERS), so a read failure surfaces resolutions rather than guessing.
- Deny-by-default: a row missing Job ID or Project Name is skipped; a blank Active
  status is treated as not-Active.
- Join key is the `Job ID` column (a Smartsheet AUTO_NUMBER per the Phase-3
  decision; the former kebab key lives on as `Job Slug`). The lookup is agnostic to
  the key format — it matches whatever string the `Job ID` cell holds.
- 60-second TTL cache at module scope (mirrors shared.project_routing, §33 family).

Failure modes
-------------
A `get_rows` SmartsheetError (sheet not wired / transient) → WARN-announce and
return an empty list (cached). Never raises; never crashes intake. Every resolution
then routes to the Review Queue until the next successful read — surfaced, not silent.

Consumers
---------
- safety_reports/intake.py `resolve_project()` — resolves a submission's Job ID
  (carried in the portal payload, Phase 5) to its project; refuses unknown/inactive.
- the Phase-5 D1 sync job — `list_active_jobs()` pushes the Active set to the
  portal dropdown.
"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from typing import Any

from . import sheet_ids, smartsheet_client

LOGGER = logging.getLogger(__name__)

CACHE_TTL_SECONDS = 60.0

ACTIVE_STATUS = "Active"

# CC slots are TEXT (operator decision 2026-06-05 — MULTI_CONTACT_LIST loses
# external emails on API read-back; rationale + live-probe evidence in
# scripts/migrations/add_active_jobs_contact_routing_columns.py + the 2026-06-05
# session log). A slot may hold one email or several comma-separated. Crude
# email-shape check: no whitespace, one @, a dotted domain.
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_CC_SLOTS = tuple(f"CC {i}" for i in range(1, 6))


def _flatten_cc(slots: list[str], job_label: str) -> tuple[str, ...]:
    """Flatten the CC 1–5 slots → an ordered, de-duplicated email tuple.

    Each slot is TEXT and may hold one email or several comma-separated. De-dup is
    case-insensitive (first spelling wins). A malformed entry is **skipped and
    WARN-announced** (soft-fail) — never silently dropped (cross-cutting #1).
    """
    seen: set[str] = set()
    out: list[str] = []
    for slot in slots:
        for piece in slot.split(","):
            email = piece.strip()
            if not email:
                continue
            if not _EMAIL_RE.match(email):
                LOGGER.warning(
                    "active_jobs: skipping malformed CC %r on job %r", email, job_label
                )
                continue
            key = email.lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(email)
    return tuple(out)


@dataclass(frozen=True)
class ActiveJob:
    """One ITS_Active_Jobs row projected to typed form."""

    job_id: str          # AUTO_NUMBER immutable key (e.g. "JOB-0001"); the join key
    project_name: str    # primary; portal dropdown display; == ITS_Project_Routing key
    job_slug: str        # human-readable kebab (e.g. "bradley-1"); was the old key
    address: str
    stakeholder_name: str
    stakeholder_email: str
    stakeholder_phone: str
    safety_reports_contact_email: str  # the weekly-rollup TO recipient (TEXT)
    safety_reports_contact_name: str   # greeting target on the weekly email
    cc_emails: tuple[str, ...]         # CC 1–5 flattened + de-duped (weekly_send CCs all)
    active_status: str   # "Active" / "Inactive" / "Archived" / "" (deny-by-default)
    row_id: int

    @property
    def is_active(self) -> bool:
        return self.active_status == ACTIVE_STATUS


_cache: tuple[list[ActiveJob], float] | None = None


def _cell(row: dict[str, Any], key: str) -> str:
    """Coerce a Smartsheet cell to a stripped string ('' when absent/blank).

    AUTO_NUMBER and TEXT cells read as str; a numeric cell (e.g. a phone typed as a
    number) coerces to its integer string; bool/None/other → ''.
    """
    raw = row.get(key)
    if isinstance(raw, bool):  # bool is an int subclass — never a real cell value here
        return ""
    if isinstance(raw, str):
        return raw.strip()
    if isinstance(raw, (int, float)):
        return str(int(raw))
    return ""


def _row_to_job(row: dict[str, Any]) -> ActiveJob | None:
    """Project one Smartsheet row dict to an ActiveJob, or None on unusable data."""
    job_id = _cell(row, "Job ID")
    project_name = _cell(row, "Project Name")
    row_id = row.get("_row_id")
    # A row with no Job ID can't be a join target; no Project Name can't route.
    if not job_id or not project_name or not isinstance(row_id, int):
        return None
    return ActiveJob(
        job_id=job_id,
        project_name=project_name,
        job_slug=_cell(row, "Job Slug"),
        address=_cell(row, "Address"),
        stakeholder_name=_cell(row, "Stakeholder Name"),
        stakeholder_email=_cell(row, "Stakeholder Email"),
        stakeholder_phone=_cell(row, "Stakeholder Phone"),
        safety_reports_contact_email=_cell(row, "Safety Reports Contact Email"),
        safety_reports_contact_name=_cell(row, "Safety Reports Contact Name"),
        cc_emails=_flatten_cc([_cell(row, slot) for slot in _CC_SLOTS], project_name),
        active_status=_cell(row, "Active"),
        row_id=row_id,
    )


def _load_jobs() -> list[ActiveJob]:
    """Fetch + cache all ITS_Active_Jobs rows. TTL-keyed at module scope.

    On a read failure (sheet not wired / transient): WARN-announce and return an
    empty list (cached) — the caller surfaces the miss to the Review Queue rather
    than the pipeline crashing or silently succeeding.
    """
    global _cache
    now = time.monotonic()
    if _cache is not None:
        jobs, expires_at = _cache
        if now < expires_at:
            return jobs

    try:
        rows = smartsheet_client.get_rows(sheet_ids.SHEET_ACTIVE_JOBS)
    except smartsheet_client.SmartsheetError as exc:
        LOGGER.warning(
            "active_jobs: ITS_Active_Jobs read failed (%r); resolutions will route "
            "to the Review Queue until the next successful read.",
            exc,
        )
        jobs = []
        _cache = (jobs, now + CACHE_TTL_SECONDS)
        return jobs

    jobs = [j for j in (_row_to_job(row) for row in rows) if j is not None]
    _cache = (jobs, now + CACHE_TTL_SECONDS)
    return jobs


def invalidate_cache() -> None:
    """Drop the in-process cache. Used by tests + ad-hoc operator scripts."""
    global _cache
    _cache = None


def get_job(job_id: str) -> ActiveJob | None:
    """Return the job whose `Job ID` equals `job_id` (ANY status), or None.

    Returns regardless of Active/Inactive/Archived so the caller can distinguish
    'unknown job' (None) from 'known but not Active' (job with `is_active` False) and
    announce the precise reason.
    """
    key = (job_id or "").strip()
    if not key:
        return None
    for job in _load_jobs():
        if job.job_id == key:
            return job
    return None


def list_active_jobs() -> list[ActiveJob]:
    """All Active jobs (the portal dropdown source for the Phase-5 D1 sync)."""
    return [job for job in _load_jobs() if job.is_active]
