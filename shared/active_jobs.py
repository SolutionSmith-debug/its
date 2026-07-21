"""Active-Jobs reads — Job ID → job record (Safety Portal Phase 3; parameterized P4).

Purpose
-------
Read-only lookup against an office-PM-maintained Active-Jobs sheet — the single source
of truth for which jobs exist, their routing contacts, and their Active/Inactive/Archived
status. The portal never reads Smartsheet; only this module + the Python pipeline do.

Parameterization (P4 — parameterize-not-clone, Op Stds §14)
-----------------------------------------------------------
Each workstream reads its OWN physical Active-Jobs sheet with its OWN reports-contact
columns, bound by a required `ActiveJobsConfig` (NO default at the config level — the
caller picks safety or progress explicitly). The two canonical configs are
`SAFETY_ACTIVE_JOBS_CONFIG` (`ITS_Active_Jobs` + "Safety Reports Contact …") and
`PROGRESS_ACTIVE_JOBS_CONFIG` (`ITS_Active_Jobs_Progress` + "Progress Reports Contact …").
The public functions DEFAULT to the safety config so every existing safety caller is
byte-identical (no call-site change); progress callers pass the progress config. The TTL
cache is keyed per sheet id, so the safety and progress reads never collide — a hard part
of the "the two workstreams can never get mixed up" guarantee (recipients resolve only
from the workstream's own sheet).

Invariants
----------
- READ-ONLY: no write path (so Op Stds §30 integration-test discipline does not fire).
- The sheet IS the source — there is NO hardcoded fallback (unlike project_routing's
  BOX_PROJECT_FOLDERS), so a read failure surfaces resolutions rather than guessing.
- Deny-by-default: a row missing Job ID or Project Name is skipped; a blank Active
  status is treated as not-Active.
- Join key is the `Job ID` column (a Smartsheet AUTO_NUMBER per the Phase-3
  decision), with a fallback to the `Portal Job Key` TEXT column — the P2.5
  cross-sheet identity bridge the mirror daemon writes. `get_job` OR-matches
  **Job ID first**, then (only if no Job ID matched) `Portal Job Key`; an empty
  `Portal Job Key` never matches. The former kebab `Job Slug` key is RETIRED (no
  consumer); the Smartsheet column delete is operator-manual. The lookup is
  agnostic to the key format — it matches whatever string the cell holds.
- 60-second TTL cache, keyed per sheet id (mirrors shared.project_routing, §33 family).

Failure modes
-------------
A `get_rows` SmartsheetError (sheet not wired / transient) → WARN-announce and
return an empty list (cached). Never raises; never crashes intake. Every resolution
then routes to the Review Queue until the next successful read — surfaced, not silent.

That contract is load-bearing for the resolution callers, but it makes an outage
indistinguishable from "no jobs" for an ITERATING caller: a daemon that loops
`list_active_jobs()` sees zero jobs and reports a clean cycle while the sheet is
down. `last_read_failed(config)` is the additive companion that closes it — every
return shape above is unchanged (see its docstring).

Consumers
---------
- safety_reports/intake.py `resolve_project()` — resolves a submission's Job ID
  (carried in the portal payload, Phase 5) to its project; refuses unknown/inactive.
- the Phase-5 D1 sync (`safety_reports/portal_poll`) — `list_all_jobs()` pushes
  the FULL set (each row's active flag) to the Worker, which upserts the dropdown
  cache + deactivates any job_id absent from the push.
- safety_reports/weekly_generate.py + (P4) progress_reports/progress_weekly_generate.py —
  `list_active_jobs(<config>)` iterates the workstream's Active jobs to compile.
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


@dataclass(frozen=True)
class ActiveJobsConfig:
    """Which Active-Jobs sheet + reports-contact columns a workstream reads.

    Required, no module-level default — the caller binds safety or progress explicitly
    (parameterize-not-clone, §14). `label` names the sheet in log lines so a read miss is
    attributable to the right workstream.
    """

    sheet_id: int
    contact_email_column: str
    contact_name_column: str
    label: str


# The two canonical bindings. Safety reads the original ITS_Active_Jobs; progress reads its
# OWN ITS_Active_Jobs_Progress sheet (P2.5 Slice 4) with the "Progress Reports Contact"
# columns. Distinct sheets = a progress send can only ever resolve a progress contact.
SAFETY_ACTIVE_JOBS_CONFIG = ActiveJobsConfig(
    sheet_id=sheet_ids.SHEET_ACTIVE_JOBS,
    contact_email_column="Safety Reports Contact Email",
    contact_name_column="Safety Reports Contact Name",
    label="ITS_Active_Jobs",
)
PROGRESS_ACTIVE_JOBS_CONFIG = ActiveJobsConfig(
    sheet_id=sheet_ids.SHEET_ACTIVE_JOBS_PROGRESS,
    contact_email_column="Progress Reports Contact Email",
    contact_name_column="Progress Reports Contact Name",
    label="ITS_Active_Jobs_Progress",
)


@dataclass(frozen=True)
class ActiveJob:
    """One Active-Jobs row projected to typed form."""

    job_id: str          # AUTO_NUMBER immutable key (e.g. "JOB-0001"); the join key
    project_name: str    # primary; portal dropdown display; == ITS_Project_Routing key
    address: str
    stakeholder_name: str
    stakeholder_email: str
    stakeholder_phone: str
    # The workstream's reports-contact TO recipient (TEXT). The field name is historical
    # (safety landed first); for a non-safety workstream the ActiveJobsConfig maps THAT
    # workstream's contact column into it. Workstream-neutral code should read the
    # `reports_contact_email` / `reports_contact_name` aliases below.
    safety_reports_contact_email: str
    safety_reports_contact_name: str
    cc_emails: tuple[str, ...]         # CC 1–5 flattened + de-duped (weekly_send CCs all)
    active_status: str   # "Active" / "Inactive" / "Archived" / "" (deny-by-default)
    row_id: int
    # P2.5 cross-sheet identity bridge: the typed Job ID carried in this sheet's
    # "Portal Job Key" TEXT column (written by the mirror daemon); "" when absent.
    # `get_job` OR-matches on it after Job ID so either key resolves the row.
    portal_job_key: str = ""

    @property
    def is_active(self) -> bool:
        return self.active_status == ACTIVE_STATUS

    @property
    def reports_contact_email(self) -> str:
        """Workstream-neutral alias for the reports-contact TO recipient. Safety code may
        use `safety_reports_contact_email`; new (progress) code uses this. SAME value — the
        config decides which sheet column populated it."""
        return self.safety_reports_contact_email

    @property
    def reports_contact_name(self) -> str:
        """Workstream-neutral alias for the greeting target (see `reports_contact_email`)."""
        return self.safety_reports_contact_name


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


# Per-sheet TTL cache: {sheet_id: (jobs, expires_at)}. Keyed per sheet so a safety read and
# a progress read never share state (mix-up prevention) and each keeps its own 60s window.
_cache: dict[int, tuple[list[ActiveJob], float]] = {}

# Per-sheet outcome of the read that produced the CURRENT cache entry: {sheet_id: read_failed}.
# Written only on a cache MISS, so it keeps describing the read behind whatever list a caller
# just got — including during the 60s window in which a failed read's empty list is served.
# Read via last_read_failed(); never consulted by the functions above (their contracts are
# untouched).
_last_read_failed: dict[int, bool] = {}


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


def _row_to_job(row: dict[str, Any], config: ActiveJobsConfig) -> ActiveJob | None:
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
        address=_cell(row, "Address"),
        stakeholder_name=_cell(row, "Stakeholder Name"),
        stakeholder_email=_cell(row, "Stakeholder Email"),
        stakeholder_phone=_cell(row, "Stakeholder Phone"),
        safety_reports_contact_email=_cell(row, config.contact_email_column),
        safety_reports_contact_name=_cell(row, config.contact_name_column),
        cc_emails=_flatten_cc([_cell(row, slot) for slot in _CC_SLOTS], project_name),
        active_status=_cell(row, "Active"),
        row_id=row_id,
        portal_job_key=_cell(row, "Portal Job Key"),
    )


def _load_jobs(config: ActiveJobsConfig = SAFETY_ACTIVE_JOBS_CONFIG) -> list[ActiveJob]:
    """Fetch + cache all rows for `config`'s sheet. TTL-keyed per sheet id.

    On a read failure (sheet not wired / transient): WARN-announce and return an
    empty list (cached) — the caller surfaces the miss to the Review Queue rather
    than the pipeline crashing or silently succeeding.
    """
    now = time.monotonic()
    cached = _cache.get(config.sheet_id)
    if cached is not None:
        jobs, expires_at = cached
        if now < expires_at:
            return jobs

    try:
        rows = smartsheet_client.get_rows(config.sheet_id)
    except smartsheet_client.SmartsheetError as exc:
        LOGGER.warning(
            "active_jobs: %s read failed (%r); resolutions will route "
            "to the Review Queue until the next successful read.",
            config.label,
            exc,
        )
        jobs = []
        _cache[config.sheet_id] = (jobs, now + CACHE_TTL_SECONDS)
        _last_read_failed[config.sheet_id] = True
        return jobs

    jobs = [j for j in (_row_to_job(row, config) for row in rows) if j is not None]
    _cache[config.sheet_id] = (jobs, now + CACHE_TTL_SECONDS)
    _last_read_failed[config.sheet_id] = False
    return jobs


def last_read_failed(config: ActiveJobsConfig = SAFETY_ACTIVE_JOBS_CONFIG) -> bool:
    """Did the read behind the CURRENTLY-CACHED job list for `config`'s sheet fail?

    Additive companion to the return-empty-on-failure contract of the accessors below,
    which is depended on by 5+ consumers (notably `portal_poll._push_active_jobs`, whose
    empty-set refusal is the mirror-loop guard) and therefore must not change. Call this
    immediately AFTER `list_active_jobs()` / `list_all_jobs()` / `get_job()`: it describes
    the read that produced the list just returned, including a cache HIT inside the TTL of
    a failed read (a caller in that window is equally blind).

    Returns False when the sheet has never been read — "no evidence of failure", never a
    fabricated one. The intended consumer is an ITERATING daemon
    (`safety_reports/compile_now_poll`), which otherwise cannot tell a sheet outage from a
    genuinely empty job list and reports a clean cycle through an outage.
    """
    return _last_read_failed.get(config.sheet_id, False)


def invalidate_cache() -> None:
    """Drop the in-process cache (ALL sheets). Used by tests + ad-hoc operator scripts."""
    _cache.clear()
    _last_read_failed.clear()


def get_job(
    job_id: str, config: ActiveJobsConfig = SAFETY_ACTIVE_JOBS_CONFIG
) -> ActiveJob | None:
    """Return the job matching `job_id` (ANY status), or None.

    OR-match over two identity columns, **Job ID first**: across all rows a
    `Job ID == key` wins; only if no Job ID matched do we fall back to a row whose
    `Portal Job Key` (the P2.5 cross-sheet bridge column the mirror daemon writes)
    equals the key. An empty `Portal Job Key` never matches, so a key-less row can't
    spuriously bind a missing key. Read-only — no write path, no new external call.

    Returns regardless of Active/Inactive/Archived so the caller can distinguish
    'unknown job' (None) from 'known but not Active' (job with `is_active` False) and
    announce the precise reason.
    """
    key = (job_id or "").strip()
    if not key:
        return None
    jobs = _load_jobs(config)
    for job in jobs:  # Job ID takes precedence
        if job.job_id == key:
            return job
    for job in jobs:  # fall back to the Portal Job Key bridge (skip empty keys)
        if job.portal_job_key and job.portal_job_key == key:
            return job
    return None


def list_active_jobs(
    config: ActiveJobsConfig = SAFETY_ACTIVE_JOBS_CONFIG,
) -> list[ActiveJob]:
    """All Active jobs for `config`'s sheet (e.g. the weekly compile iterates these)."""
    return [job for job in _load_jobs(config) if job.is_active]


def list_all_jobs(
    config: ActiveJobsConfig = SAFETY_ACTIVE_JOBS_CONFIG,
) -> list[ActiveJob]:
    """Every job (ANY status) — the FULL set the Phase-5 D1 sync pushes.

    The portal sync (`safety_reports/portal_poll`) pushes this complete set, each
    `ActiveJob` carrying its `is_active` flag, so the Worker can reconcile: upsert
    each row + deactivate any job_id absent from the push. `list_active_jobs()`
    (Active-only) cannot drive a deactivate-missing reconcile — a job flipped to
    Inactive would simply vanish from the pushed set and the Worker couldn't tell
    'gone' from 'deactivated'. On a read miss `_load_jobs()` returns [] (the caller
    MUST refuse to push an empty set — see portal_poll._push_active_jobs)."""
    return list(_load_jobs(config))
