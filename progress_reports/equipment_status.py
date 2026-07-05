"""Per-job Equipment Status & Location tracker — the P7 standing tracker (Track 2, Slice 2).

One-way-up mirror of the D1 equipment SoR into a per-job **standing** "Equipment" Smartsheet in
the `ITS — Progress Reporting` workspace, in the SAME per-job folder as the Hours Log + week
sheets (so they sit side by side). SEND-FREE + AI-FREE (Op Stds v19 §51 — ITS-owned structured-SoR
write-back); the daemon that drives it (`field_ops.fieldops_sync` equipment pass) is the
capability-gated actuator.

Design (ratified — SNAPSHOT-shaped, operator-ratified):
- **Single standing sheet per job** (`<Job> — Equipment`) — ONE row per equipment item currently on
  the job, showing its **latest location + readiness (status)**. Unlike the Hours Log (an
  append-only event log), this is a SNAPSHOT re-projected every cycle: a row is UPDATED IN PLACE as
  the equipment's location/status changes, and an equipment that leaves the job is RETIRED IN PLACE
  (`On Job → Off Job`) — **NEVER deleted** (§51 SoR rule). No accumulating event history here (the
  full location/status timeline lives in D1 `equipment_location` / `equipment_logs`).
- Because it re-projects the live snapshot each cycle, `upsert_equipment_row` is **CHANGE-ONLY**:
  on a find-hit it compares the incoming values against the row's current cells and issues an
  `update_rows` ONLY when something actually changed — avoiding a needless write (and needless
  `Updated At` churn) every 5 minutes. There is NO watermark / mark-mirrored (the Worker route is a
  re-projected snapshot, not an event drain).
- **Progress workspace only**, single-destination. The per-job FOLDER + capacity tripwire are
  reused from `hours_log` so the two trackers share one folder-resolution authority.
- `check_row_cap` is kept for parity/safety, though a snapshot is bounded by equipment count so it
  ~never fires (the sheet has one row per equipment ever seen on the job, not per event).

The module is a thin Smartsheet write helper (like `hours_log` / `week_sheet`) — NOT a daemon
entry point, so it is not itself in `GATED_SCRIPTS`; its sole caller `fieldops_sync` is.
"""
from __future__ import annotations

from typing import Any

from progress_reports import hours_log
from shared import error_log, review_queue, sheet_capacity, sheet_ids, smartsheet_client
from shared.error_log import Severity

SCRIPT_NAME = "progress_reports.equipment_status"

WORKSPACE_ID = sheet_ids.WORKSPACE_PROGRESS_REPORTING

# Smartsheet sheet-name cap (HTTP 400 errorCode 1041) — same constant as hours_log.SHEET_NAME_MAX.
SHEET_NAME_MAX = 50
SHEET_SUFFIX = " — Equipment"

# §51 A5 row-cap watchdog — a snapshot is bounded by equipment count so this ~never fires, but it
# is kept for parity with the Hours Log (defence in depth). WARN + Review-Queue an operator
# period-split as the sheet nears the Smartsheet ~20k/sheet row cap; NEVER delete (SoR).
CFG_ROW_CAP_WARN = "progress_reports.equipment_status.row_cap_warn_threshold"
DEFAULT_ROW_CAP_WARN = 15000  # conservative WARN mark well under the ~20k Smartsheet per-sheet cap

# ---- Column titles (single source of truth for reads + writes) ----
COL_EQUIPMENT = "Equipment"          # primary (TEXT_NUMBER): equipment.name
COL_EQUIPMENT_ID = "Equipment ID"    # upsert key (== D1 equipment.id, as a string)
COL_KIND = "Kind"
COL_UNIT_NO = "Unit #"               # equipment.identifier (unit # / VIN / asset tag)
COL_STATUS = "Status"                # fmc | degraded | down — TEXT controlled-vocab, NOT a PICKLIST
COL_STATUS_NOTE = "Status Note"
COL_STATUS_CHANGED = "Status Changed"  # DATE (readiness last changed)
COL_LOCATION = "Location"            # human label / 'unavailable'
COL_LAT = "Lat"
COL_LON = "Lon"
COL_LOCATION_READ_AT = "Location Read At"  # field-reported point-in-time claim, pre-formatted
COL_ON_JOB = "On Job"                # Active | Off Job — TEXT controlled-vocab, NOT a PICKLIST
COL_UPDATED_AT = "Updated At"        # server record time of THIS mirror write, pre-formatted

# Controlled vocabulary (TEXT cells, NOT picklist — avoids the REGISTRY-parity footgun; mirrors
# hours_log's Status choice).
ON_JOB_ACTIVE = "Active"
ON_JOB_OFF = "Off Job"

# Readiness values (D1 `equipment.status` domain). TEXT cells (not picklist) for the same reason.
STATUS_FMC = "fmc"
STATUS_DEGRADED = "degraded"
STATUS_DOWN = "down"

# Data columns compared for CHANGE-ONLY upsert (Equipment ID is the key; Updated At is metadata —
# neither participates in change detection). `On Job` IS compared so a reappearing item flips back
# to Active.
_TRACKED_COLS: tuple[str, ...] = (
    COL_EQUIPMENT, COL_KIND, COL_UNIT_NO, COL_STATUS, COL_STATUS_NOTE, COL_STATUS_CHANGED,
    COL_LOCATION, COL_LAT, COL_LON, COL_LOCATION_READ_AT, COL_ON_JOB,
)

# Schema passed to create_sheet_in_folder. Order = left-to-right UI order. Exactly one primary
# (TEXT_NUMBER, Smartsheet requirement).
EQUIPMENT_COLUMNS: list[dict[str, Any]] = [
    {"title": COL_EQUIPMENT, "type": "TEXT_NUMBER", "primary": True},
    {"title": COL_EQUIPMENT_ID, "type": "TEXT_NUMBER"},
    {"title": COL_KIND, "type": "TEXT_NUMBER"},
    {"title": COL_UNIT_NO, "type": "TEXT_NUMBER"},
    {"title": COL_STATUS, "type": "TEXT_NUMBER"},
    {"title": COL_STATUS_NOTE, "type": "TEXT_NUMBER"},
    {"title": COL_STATUS_CHANGED, "type": "DATE"},
    {"title": COL_LOCATION, "type": "TEXT_NUMBER"},
    {"title": COL_LAT, "type": "TEXT_NUMBER"},
    {"title": COL_LON, "type": "TEXT_NUMBER"},
    {"title": COL_LOCATION_READ_AT, "type": "TEXT_NUMBER"},
    {"title": COL_ON_JOB, "type": "TEXT_NUMBER"},
    {"title": COL_UPDATED_AT, "type": "TEXT_NUMBER"},
]

# Cosmetic styling — Smartsheet format-descriptor strings (mirror hours_log's palette). Applied
# AFTER create, best-effort (the API ignores width/format at POST).
FMT_PRIMARY = ",,1,,,,,,38,7,,,,,,,"       # bold + dark-green text + light-green bg
FMT_DATE = ",,,,,,,,,,,,,,,,2"             # MMM_D_YYYY
ON_JOB_ACTIVE_FMT = ",,,,,,,,,7,,,,,,,"    # light-green bg
ON_JOB_OFF_FMT = ",,,,,,,,,18,,,,,,,"      # light-gray bg

EQUIPMENT_STYLES: list[dict[str, Any]] = [
    {"title": COL_EQUIPMENT, "width": 200, "format": FMT_PRIMARY},
    {"title": COL_EQUIPMENT_ID, "width": 90},
    {"title": COL_KIND, "width": 130},
    {"title": COL_UNIT_NO, "width": 110},
    {"title": COL_STATUS, "width": 100},
    {"title": COL_STATUS_NOTE, "width": 260},
    {"title": COL_STATUS_CHANGED, "width": 120, "format": FMT_DATE},
    {"title": COL_LOCATION, "width": 220},
    {"title": COL_LAT, "width": 100},
    {"title": COL_LON, "width": 100},
    {"title": COL_LOCATION_READ_AT, "width": 160},
    {"title": COL_ON_JOB, "width": 100},
    {"title": COL_UPDATED_AT, "width": 160},
]


def equipment_sheet_name(project_name: str) -> str:
    """`'<Job> — Equipment'`, prefix-truncated to the 50-char cap (errorCode 1041).

    The ` — Equipment` suffix is preserved WHOLE (it names the sheet within the per-job folder);
    the project prefix is truncated when needed — no identity is lost because the per-job FOLDER
    already carries the full `project_name` and the sheet is only ever resolved find-or-create
    WITHIN that folder. Names ≤50 chars are byte-identical to the untruncated form.
    """
    prefix = project_name.strip()
    budget = SHEET_NAME_MAX - len(SHEET_SUFFIX)
    if len(prefix) > budget:
        prefix = prefix[:budget].rstrip()
    return f"{prefix}{SHEET_SUFFIX}"


def _apply_styles_best_effort(sheet_id: int) -> None:
    """Apply `EQUIPMENT_STYLES` to a freshly-created sheet. Cosmetic only — a failure WARNs (never
    silent) but must NOT fail the already-created sheet (the data path is unaffected)."""
    try:
        smartsheet_client.apply_column_styles(sheet_id, EQUIPMENT_STYLES)
    except Exception as exc:  # noqa: BLE001 — cosmetic; never fail sheet creation
        error_log.log(
            Severity.WARN, SCRIPT_NAME,
            f"equipment-status styling failed (sheet {sheet_id}): {type(exc).__name__}: {exc!r}",
            error_code="equipment_status_style_failed",
        )


def _folder_name(project_name: str) -> str:
    """Per-job folder title/key — the SAME source of truth the Hours Log + week sheets use, so the
    Equipment sheet lands in the job's existing folder rather than a parallel one."""
    return hours_log._folder_name(project_name)


def _ensure_job_folder(project_name: str) -> int:
    """Find-or-create the per-job folder in the progress workspace (idempotent, race-tolerant).

    Delegates to `hours_log._ensure_job_folder` so the Equipment sheet sits BESIDE the Hours Log +
    week sheets — one folder-resolution authority (DRY). The rare duplicate-folder WARN is logged
    under the Hours Log's script name; that is acceptable given the shared resolver.
    """
    return hours_log._ensure_job_folder(project_name)


def _warn_on_thin_headroom(sheet_name: str) -> None:
    """A1 sheet-count tripwire, run before each CREATE. ADVISORY, never blocking (mirrors
    hours_log._warn_on_thin_headroom): a margin breach WARNs + enqueues the operator signal, then
    the create PROCEEDS. Belt-and-suspenders fail-open — any exception is reduced to a WARN."""
    try:
        headroom = sheet_capacity.check_create_headroom(WORKSPACE_ID)
    except Exception as exc:  # noqa: BLE001 — advisory tripwire; never block the create
        error_log.log(
            Severity.WARN, SCRIPT_NAME,
            f"sheet-capacity headroom check raised (create proceeds unguarded): {exc!r}",
            error_code="sheet_capacity_check_failed",
        )
        return
    if headroom.note:
        error_log.log(
            Severity.WARN, SCRIPT_NAME,
            f"sheet-capacity check fail-open before creating {sheet_name!r}: {headroom.note}",
            error_code="sheet_capacity_check_failed",
        )
        return
    if headroom.ok:
        return
    error_log.log(
        Severity.WARN, SCRIPT_NAME,
        (
            f"sheet-count margin breach in workspace {WORKSPACE_ID}: "
            f"{headroom.current}/{headroom.ceiling} (margin {headroom.margin}) — creating "
            f"{sheet_name!r} anyway (advisory tripwire; see the Review-Queue row)."
        ),
        error_code="sheet_capacity_margin_breach",
    )
    try:
        sheet_capacity.route_breach_to_review_queue(
            WORKSPACE_ID, headroom, workstream="progress_reports"
        )
    except Exception as exc:  # noqa: BLE001 — the enqueue failing must not block the create
        error_log.log(
            Severity.WARN, SCRIPT_NAME,
            f"could not enqueue the sheet-capacity breach to ITS_Review_Queue: {exc!r}",
            error_code="sheet_capacity_rq_failed",
        )


def ensure_equipment_sheet(project_name: str) -> int:
    """Find-or-create the job's single standing Equipment sheet; return its sheet ID.

    Idempotent: a second call returns the same sheet with no write. Race-tolerant at both the
    folder and sheet levels (re-find after create, adopt first, WARN the duplicate). The A1
    capacity tripwire runs ONLY on the create branch.
    """
    folder_id = _ensure_job_folder(project_name)
    name = equipment_sheet_name(project_name)

    existing = smartsheet_client.find_sheet_by_name_in_folder(folder_id, name)
    if existing is not None:
        return existing

    _warn_on_thin_headroom(name)
    sheet_id = smartsheet_client.create_sheet_in_folder(folder_id, name, EQUIPMENT_COLUMNS)
    post_find = smartsheet_client.find_sheet_by_name_in_folder(folder_id, name)
    if post_find is not None and post_find != sheet_id:
        error_log.log(
            Severity.WARN, SCRIPT_NAME,
            f"duplicate Equipment sheet {name!r} under folder {folder_id} "
            f"(project={project_name!r}); using first match {post_find}, manual cleanup "
            f"needed for {sheet_id}.",
            error_code="equipment_status_sheet_race_duplicate",
        )
        return post_find
    _apply_styles_best_effort(sheet_id)  # cosmetic; create path only
    return sheet_id


def find_equipment_sheet(project_name: str) -> int | None:
    """Find (NEVER create) the job's standing Equipment sheet; return its sheet ID or None.

    Resolves the per-job PROGRESS folder find-ONLY then the sheet find-ONLY; None if EITHER is
    absent. Used by the reconcile-zeroed path in `fieldops_sync`: when a job's current equipment
    complement drops to ZERO it produces no snapshot rows, so the daemon reconciles it by finding
    (not creating) its sheet and retiring every remaining Active row. This NEVER creates an empty
    sheet — a job that never had an Equipment sheet is simply skipped.
    """
    folder = smartsheet_client.find_folder_by_name_in_workspace(WORKSPACE_ID, _folder_name(project_name))
    if folder is None:
        return None
    return smartsheet_client.find_sheet_by_name_in_folder(folder, equipment_sheet_name(project_name))


def find_equipment_row(sheet_id: int, equipment_id: str) -> dict[str, Any] | None:
    """Return the Equipment row whose Equipment ID == `equipment_id`, or None. The upsert/retire
    authority — every snapshot re-projection resolves its target through this."""
    key = (equipment_id or "").strip()
    if not key:
        return None
    for row in smartsheet_client.get_rows(sheet_id):
        if str(row.get(COL_EQUIPMENT_ID) or "").strip() == key:
            return row
    return None


def upsert_equipment_row(
    sheet_id: int,
    *,
    equipment_id: str,
    name: str,
    kind: str,
    unit_no: str,
    status: str,
    status_note: str,
    status_changed: str,
    location: str,
    lat: str,
    lon: str,
    location_read_at: str,
    updated_at: str,
) -> int:
    """CHANGE-ONLY find-or-create of one equipment item as an On-Job row; return its row ID.

    - MISS → append a new `On Job=Active` row and return its id.
    - HIT → compare the incoming values against the row's current cells for the tracked data
      columns (and require `On Job == Active`). If NOTHING changed, this is a NO-OP that returns
      the existing row id — so the 5-minute snapshot re-projection does NOT churn the sheet or the
      `Updated At` metadata. If ANYTHING changed (incl. a reappearing item whose `On Job` was `Off
      Job`), issue an `update_rows` refreshing all tracked cells + `On Job=Active` + `Updated At`.

    All values are pre-formatted strings; `name` is the DISPLAY name (equipment.name). NEVER
    deletes — a leaving item is retired in place by `retire_off_job`.
    """
    desired: dict[str, str] = {
        COL_EQUIPMENT: name,
        COL_KIND: kind,
        COL_UNIT_NO: unit_no,
        COL_STATUS: status,
        COL_STATUS_NOTE: status_note,
        COL_STATUS_CHANGED: status_changed,
        COL_LOCATION: location,
        COL_LAT: lat,
        COL_LON: lon,
        COL_LOCATION_READ_AT: location_read_at,
        COL_ON_JOB: ON_JOB_ACTIVE,
    }

    existing = find_equipment_row(sheet_id, equipment_id)
    if existing is None:
        [row_id] = smartsheet_client.add_rows(
            sheet_id,
            [
                {
                    **desired,
                    COL_EQUIPMENT_ID: equipment_id,
                    COL_UPDATED_AT: updated_at,
                    "_formats": {COL_ON_JOB: ON_JOB_ACTIVE_FMT},  # green On-Job cell
                }
            ],
        )
        return row_id

    row_id = int(existing["_row_id"])
    if _row_matches(existing, desired):
        return row_id  # nothing changed — skip the needless write (avoid Updated At churn)

    smartsheet_client.update_rows(
        sheet_id,
        [
            {
                "_row_id": row_id,
                **desired,
                COL_UPDATED_AT: updated_at,
                "_formats": {COL_ON_JOB: ON_JOB_ACTIVE_FMT},  # green On-Job cell
            }
        ],
    )
    return row_id


def _row_matches(existing: dict[str, Any], desired: dict[str, str]) -> bool:
    """True iff every tracked data column on `existing` already equals `desired` (normalized
    str-strip compare). `Equipment ID` (the key) and `Updated At` (metadata) are excluded."""
    for col in _TRACKED_COLS:
        if str(existing.get(col) or "").strip() != str(desired.get(col) or "").strip():
            return False
    return True


def retire_off_job(sheet_id: int, current_equipment_ids: set[str]) -> int:
    """Mark every sheet row whose Equipment ID is NOT in `current_equipment_ids` as `On Job=Off
    Job` (only if not already Off Job); return the number of rows newly retired.

    A snapshot re-projection: any equipment previously on the job but absent from THIS cycle's
    snapshot has left the job → retire it IN PLACE. **NEVER deletes** (§51 SoR rule) — the row
    stays as the historical record of an item that was once on this job. Idempotent: a row already
    `Off Job` is skipped, so a steady state issues no write.
    """
    current = {(eid or "").strip() for eid in current_equipment_ids}
    updates: list[dict[str, Any]] = []
    for row in smartsheet_client.get_rows(sheet_id):
        eid = str(row.get(COL_EQUIPMENT_ID) or "").strip()
        if eid in current:
            continue
        if str(row.get(COL_ON_JOB) or "").strip() == ON_JOB_OFF:
            continue  # already retired — no needless write
        updates.append(
            {
                "_row_id": row["_row_id"],
                COL_ON_JOB: ON_JOB_OFF,
                "_formats": {COL_ON_JOB: ON_JOB_OFF_FMT},  # gray On-Job cell
            }
        )
    if updates:
        smartsheet_client.update_rows(sheet_id, updates)
    return len(updates)


# ---- §51 A5 row-cap watchdog (SoR-safe, WARN-only, never delete) ---------


def _read_int_setting(key: str, fallback: int) -> int:
    """Defensive int ITS_Config read under the progress_reports workstream (missing row /
    circuit-open / non-int all resolve to `fallback`, never raising into the mirror)."""
    try:
        raw = smartsheet_client.get_setting(key, workstream="progress_reports")
    except (smartsheet_client.SmartsheetNotFoundError, smartsheet_client.SmartsheetCircuitOpenError):
        return fallback
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        return fallback


def check_row_cap(sheet_id: int, sheet_name: str, *, row_count: int | None = None) -> None:
    """SoR-safe row-cap monitor — the §51 "A5 row-cap watchdog" for the standing Equipment sheet.

    A snapshot is bounded by equipment count (one row per equipment ever seen on the job), so this
    ~never fires — it is kept for PARITY with the Hours Log (defence in depth). As the sheet nears
    the Smartsheet per-sheet row cap (~20k), WARN (`ITS_Errors`) + enqueue an operator period-split
    to `ITS_Review_Queue`. **NEVER deletes** (§51). ADVISORY: any read/enqueue failure is reduced to
    a WARN and never blocks the mirror.

    `row_count` may be passed by a caller that already read the sheet's rows this cycle (avoids a
    second full read); when None, a `get_rows` count is taken.
    """
    try:
        threshold = _read_int_setting(CFG_ROW_CAP_WARN, DEFAULT_ROW_CAP_WARN)
        count = row_count if row_count is not None else len(smartsheet_client.get_rows(sheet_id))
        if count < threshold:
            return
        error_log.log(
            Severity.WARN, SCRIPT_NAME,
            (
                f"Equipment sheet {sheet_name!r} (sheet {sheet_id}) has {count} rows, at/over the "
                f"row-cap WARN threshold {threshold} (Smartsheet ~20k/sheet) — operator should "
                f"period-split it (archive this sheet, start a fresh one); NEVER delete rows."
            ),
            error_code="equipment_status_row_cap_warn",
        )
        review_queue.add(
            workstream="progress_reports",
            summary=(
                f"Equipment sheet {sheet_name!r} nearing the Smartsheet row cap "
                f"({count}/{threshold}) — period-split needed (archive + start fresh, never delete)"
            ),
            payload={
                "sheet_id": sheet_id,
                "sheet_name": sheet_name,
                "row_count": count,
                "threshold": threshold,
                "action": "period-split (archive this sheet, start a new one); never delete rows",
            },
            sla_tier=review_queue.SlaTier.SAFETY_INTAKE,
            reason=review_queue.ReviewReason.POLICY_EDGE,
            severity=Severity.WARN,
            source_file=str(sheet_id),
        )
    except Exception as exc:  # noqa: BLE001 — advisory tripwire; never block the mirror
        error_log.log(
            Severity.WARN, SCRIPT_NAME,
            f"row-cap check failed for {sheet_name!r} (sheet {sheet_id}): {exc!r}",
            error_code="equipment_status_row_cap_check_failed",
        )
