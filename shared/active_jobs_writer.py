"""Active-Jobs WRITES — the portal-as-writer up-sync write twin of `shared.active_jobs`.

Purpose (P2.5 Slice 5 — job-tracker pivot, §50/§51 ITS-owned SoR write-back)
---------------------------------------------------------------------------
`shared.active_jobs` is the READ side: it projects an office-PM-maintained Active-Jobs
sheet to `ActiveJob` records and is deliberately READ-ONLY (so Op Stds §30 write-path
integration discipline does not fire on it). This module is its WRITE twin: it mirrors a
portal-created job UP into an ITS-owned Active-Jobs Smartsheet by a non-clobbering
find-or-create against the **"Portal Job Key"** bridge column.

The Cloudflare Worker is the authoritative writer of a portal job's state (D1
`origin='portal'`, the down-sync sweep is fenced off it); `field_ops.fieldops_sync` pulls
the dirty jobs and calls `upsert_job` once per workstream sheet so the safety + progress
Active-Jobs sheets become the downstream source of truth that every existing consumer
reads. One writer ⇒ the two workstreams can never drift.

Parameterization (parameterize-not-clone, Op Stds §14)
------------------------------------------------------
Each workstream writes its OWN physical Active-Jobs sheet with its OWN reports-contact
columns, bound by a required `WriteConfig` (NO module-level default — the caller picks
safety or progress explicitly, exactly like `active_jobs.ActiveJobsConfig`). `WriteConfig`
is the WRITE twin of `ActiveJobsConfig` with one added concern: the READ config's source
and destination are the SAME sheet, so it only names sheet columns; the WRITE config's
*source* is the D1 pending-jobs payload (with `safety_contact_*` / `progress_contact_*`
keys) and its *destination* is the sheet (with "Safety Reports Contact …" /
"Progress Reports Contact …" columns) — two distinct namespaces — so it carries BOTH the
sheet column titles to write AND the payload keys to read.

Invariants
----------
- **Non-clobber (§51).** `upsert_job` writes ONLY the portal-owned columns (Job ID,
  Project Name, Address, Stakeholder Name/Email/Phone, the workstream's reports-contact
  name+email, CC 1–5, Active, Portal Job Key). It NEVER writes Notes or any operator/system
  column. (Slice 6: Job ID is portal-owned — the portal assigns it; see Identity below.) On
  the UPDATE branch this is structural: `smartsheet_client.update_rows` only touches the
  columns present in the payload — an unsupplied column is left exactly as the operator
  left it.
- **One writer, no drift.** The find-or-create key is the "Portal Job Key" bridge column;
  a row without a matching Portal Job Key (a hand-created / legacy smartsheet-origin row)
  is never touched, so the portal can only ever write its OWN rows.
- See "Identity / lifecycle" + "Failure modes" below for the remaining invariants (typed
  identity, never-silent picklist mapping).

Identity / lifecycle
--------------------
- Find-or-create key = the "Portal Job Key" TEXT column == `job["job_id"]` (the D1 primary
  key, the crash-safe idempotency key + the cross-sheet identity bridge).
- `canonical_job_id` (returned) == `job["job_id"]`. Slice 6: the portal ASSIGNS the canonical
  JOB-###### (the worker `job_counter`, migration 0022), so this module WRITES it into the
  "Job ID" column on every upsert — no Smartsheet AUTO_NUMBER, no read-back handshake. The two
  Active-Jobs columns hold the same value: Job ID == Portal Job Key == job_id.
- `lifecycle` (active|inactive|archived) maps to the `Active` PICKLIST
  (Active|Inactive|Archived). An unknown/blank lifecycle passes through verbatim and trips
  the `picklist_validation` REGISTRY at write time → `PicklistViolationError` (a permanent
  error the daemon routes to the Review Queue), never a silent wrong write.

Failure modes (typed, never silent)
------------------------------------
`PicklistViolationError` (bad lifecycle) and `SmartsheetValidationError` (HTTP-400 permanent
reject) propagate to the caller as PERMANENT failures; any other `SmartsheetError` propagates
as TRANSIENT. The daemon (`field_ops.fieldops_sync`) fences per job: permanent → Review Queue,
transient → leave the job dirty for the next cycle (find-or-create no-ops on the existing row).

Consumers
---------
- `field_ops.fieldops_sync` (the P2.5 mirror daemon) — the sole caller today. It pulls each
  dirty portal job from the Worker and calls `upsert_job` once per workstream sheet
  (`SAFETY_WRITE_CONFIG` then `PROGRESS_WRITE_CONFIG`), then commits each sheet's watermark.
  (A future field-ops crew/equipment/materials up-sync would add itself here.)
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from . import sheet_ids, smartsheet_client

# ---- Portal-owned column titles (the ONLY columns this module writes) --------
# Shared identity/stakeholder columns are common across both sheets; the
# reports-contact columns are workstream-specific and carried on the WriteConfig.
COL_JOB_ID = "Job ID"                  # TEXT (portal-owned, Slice 6) — WRITTEN = job_id (==Portal Job Key)
COL_PROJECT_NAME = "Project Name"
COL_ADDRESS = "Address"
COL_STAKEHOLDER_NAME = "Stakeholder Name"
COL_STAKEHOLDER_EMAIL = "Stakeholder Email"
COL_STAKEHOLDER_PHONE = "Stakeholder Phone"
COL_ACTIVE = "Active"                  # PICKLIST {Active, Inactive, Archived}
COL_PORTAL_JOB_KEY = "Portal Job Key"  # TEXT — the find-or-create / cross-sheet identity key
_CC_SLOTS = tuple(f"CC {i}" for i in range(1, 6))

# lifecycle (D1) → Active (sheet PICKLIST). Unknown/blank passes through verbatim so the
# registry (picklist_validation) rejects it at write time rather than guessing a default.
_LIFECYCLE_MAP: dict[str, str] = {
    "active": "Active",
    "inactive": "Inactive",
    "archived": "Archived",
}


@dataclass(frozen=True)
class WriteConfig:
    """Which Active-Jobs sheet + which contact columns/keys a workstream up-syncs.

    The WRITE twin of `active_jobs.ActiveJobsConfig`. Required, no module-level default —
    the caller binds safety or progress explicitly (parameterize-not-clone, §14).

    - `sheet_id`                  — the destination Active-Jobs sheet.
    - `contact_name_column` /
      `contact_email_column`      — the SHEET columns to WRITE (e.g. "Safety Reports
                                    Contact Name"/"…Email").
    - `src_contact_name_key` /
      `src_contact_email_key` /
      `src_cc_key`                — the D1 pending-jobs payload KEYS to READ (e.g.
                                    "safety_contact_name"/"safety_contact_email"/"safety_cc").
    - `label`                     — names the sheet in error/diagnostic context.
    """

    sheet_id: int
    contact_name_column: str
    contact_email_column: str
    src_contact_name_key: str
    src_contact_email_key: str
    src_cc_key: str
    label: str


# The two canonical bindings. Safety writes the original ITS_Active_Jobs; progress writes
# its OWN ITS_Active_Jobs_Progress sheet (Slice 4). Distinct sheets + distinct payload
# blocks ⇒ a progress contact can never land on the safety sheet and vice-versa.
SAFETY_WRITE_CONFIG = WriteConfig(
    sheet_id=sheet_ids.SHEET_ACTIVE_JOBS,
    contact_name_column="Safety Reports Contact Name",
    contact_email_column="Safety Reports Contact Email",
    src_contact_name_key="safety_contact_name",
    src_contact_email_key="safety_contact_email",
    src_cc_key="safety_cc",
    label="ITS_Active_Jobs",
)
PROGRESS_WRITE_CONFIG = WriteConfig(
    sheet_id=sheet_ids.SHEET_ACTIVE_JOBS_PROGRESS,
    contact_name_column="Progress Reports Contact Name",
    contact_email_column="Progress Reports Contact Email",
    src_contact_name_key="progress_contact_name",
    src_contact_email_key="progress_contact_email",
    src_cc_key="progress_cc",
    label="ITS_Active_Jobs_Progress",
)


def _coerce_str(value: Any) -> str:
    """Coerce a payload value to a stripped string ('' when absent/blank).

    Mirrors `active_jobs._cell`: str → stripped; int/float → integer string (a phone typed
    as a number); bool/None/other → ''. (bool is an int subclass, so it is excluded first.)
    """
    if isinstance(value, bool):
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float)):
        return str(int(value))
    return ""


def _map_lifecycle(lifecycle: str) -> str:
    """Map a D1 `lifecycle` to the sheet `Active` PICKLIST value.

    Known {active, inactive, archived} (case-insensitive) → {Active, Inactive, Archived}.
    Anything else (including blank) passes through verbatim so the picklist REGISTRY rejects
    it at write time (`PicklistViolationError`, permanent → Review Queue) — never a silent
    wrong/defaulted status.
    """
    return _LIFECYCLE_MAP.get(lifecycle.strip().lower(), lifecycle)


def _explode_cc(cc_value: Any) -> list[str]:
    """Explode the payload CC array into exactly five slot strings (CC 1–5), padding blanks.

    A list/tuple of emails → slots 1–4 take the first four entries; slot 5 takes the
    remainder comma-joined (lossless — `active_jobs._flatten_cc` re-splits a multi-email
    slot on commas), so no CC is ever dropped even past five. Non-list / empty → five blanks.
    Always returns five entries so every CC slot is overwritten on update (the portal owns
    the CC set: a CC removed in the portal clears the sheet slot rather than lingering).
    """
    items: list[str] = []
    if isinstance(cc_value, (list, tuple)):
        for entry in cc_value:
            email = _coerce_str(entry)
            if email:
                items.append(email)
    slots = ["", "", "", "", ""]
    for i in range(min(len(items), 4)):
        slots[i] = items[i]
    slots[4] = ", ".join(items[4:])  # '' for ≤4, the 5th for ==5, comma-joined for >5
    return slots


def _build_cells(config: WriteConfig, job: Mapping[str, Any], portal_job_key: str) -> dict[str, Any]:
    """Build the portal-owned `{column_title: value}` payload (the ONLY columns written)."""
    cells: dict[str, Any] = {
        # Slice 6: the portal owns the canonical number, so Job ID == Portal Job Key == job_id is
        # WRITTEN on every upsert (create sets it; update self-heals it) — no AUTO_NUMBER, no read-back.
        COL_JOB_ID: portal_job_key,
        COL_PROJECT_NAME: _coerce_str(job.get("project_name")),
        COL_ADDRESS: _coerce_str(job.get("address")),
        COL_STAKEHOLDER_NAME: _coerce_str(job.get("stakeholder_name")),
        COL_STAKEHOLDER_EMAIL: _coerce_str(job.get("stakeholder_email")),
        COL_STAKEHOLDER_PHONE: _coerce_str(job.get("stakeholder_phone")),
        config.contact_name_column: _coerce_str(job.get(config.src_contact_name_key)),
        config.contact_email_column: _coerce_str(job.get(config.src_contact_email_key)),
        COL_ACTIVE: _map_lifecycle(_coerce_str(job.get("lifecycle"))),
        COL_PORTAL_JOB_KEY: portal_job_key,
    }
    for slot, value in zip(_CC_SLOTS, _explode_cc(job.get(config.src_cc_key)), strict=True):
        cells[slot] = value
    return cells


def _find_by_portal_key(sheet_id: int, portal_job_key: str) -> dict[str, Any] | None:
    """Return the row whose "Portal Job Key" == `portal_job_key`, or None (find-or-create key)."""
    rows = smartsheet_client.get_rows(sheet_id, filters={COL_PORTAL_JOB_KEY: portal_job_key})
    return rows[0] if rows else None


def upsert_job(config: WriteConfig, job: Mapping[str, Any]) -> tuple[int, str]:
    """Non-clobbering find-or-create of `job` on `config`'s Active-Jobs sheet.

    Find by "Portal Job Key" == `job["job_id"]`: on a hit → `update_rows` ONLY the
    portal-owned columns of that row (Notes / operator columns untouched); on a miss →
    `add_rows` a new row. Returns `(row_id, canonical_job_id)` where `canonical_job_id` ==
    `job["job_id"]` — Slice 6: the portal ASSIGNS the canonical JOB-###### and this module
    WRITES it into the "Job ID" column (no Smartsheet AUTO_NUMBER, no read-back handshake).

    Raises:
        ValueError: the payload has no `job_id` (a job that can't be keyed/marked-mirrored).
        PicklistViolationError: an unmapped `lifecycle` value (permanent — Review Queue).
        SmartsheetValidationError: an HTTP-400 permanent reject (permanent — Review Queue).
        SmartsheetError: any other Smartsheet failure (transient — leave dirty, retry).
    """
    portal_job_key = _coerce_str(job.get("job_id"))
    if not portal_job_key:
        raise ValueError("active_jobs_writer.upsert_job: job payload missing 'job_id'")

    cells = _build_cells(config, job, portal_job_key)
    existing = _find_by_portal_key(config.sheet_id, portal_job_key)
    if existing is not None:
        row_id = int(existing["_row_id"])
        smartsheet_client.update_rows(config.sheet_id, [{"_row_id": row_id, **cells}])
        return row_id, portal_job_key

    [row_id] = smartsheet_client.add_rows(config.sheet_id, [cells])
    return row_id, portal_job_key
