"""Find-or-create a per-job Smartsheet tracking folder + sheet (Feature A).

Purpose
    Per-job OPERATOR VISIBILITY for the subcontract + purchase-order
    workstreams: every job that files a subcontract or PO gets its own folder
    (named by `safety_reports.safety_naming.job_folder_name`, the SAME
    sanitized name the per-job Box folder already uses — Box + Smartsheet line
    up) under the workspace's "Jobs" parent (`sheet_ids.FOLDER_SC_JOBS` /
    `FOLDER_PO_JOBS`, built by scripts/migrations/build_job_folders.py),
    holding one tracking sheet whose rows mirror that job's flat-Log ledger
    rows. The flat Logs (Subcontract_Log / PO_Log) STAY the ledger SoR mirror
    of D1 — the per-job sheet is purely supplementary, appended best-effort by
    the poll daemons.

Invariants
    - **Dynamic find-or-create** (unlike safety's `week_folder`, which
      pre-wires one folder constant per KNOWN project): subcontract/PO jobs
      come from ITS_Active_Jobs via the portal, so the per-job folder is
      resolved by NAME at filing time — exactly like the per-job Box folder
      (`_resolve_subcontract_box_folder` / `_resolve_po_box_folder`). Same
      model as `progress_reports.hours_log._ensure_job_folder`, one level down
      (folder parent, not workspace parent).
    - **Template = the flat Log itself**: the per-job sheet is created
      structure-only (`include=[]`) from `SHEET_SUBCONTRACT_LOG` /
      `SHEET_PO_LOG`, so its columns are byte-identical to the ledger's and
      the SAME `append_filed_row(..., sheet_id=...)` writes both — no schema
      fork.
    - **Idempotent + race-safe**: calling `ensure_job_sheet` twice returns the
      same sheet ID with no API writes on the second call. Smartsheet does not
      enforce folder/sheet name uniqueness, so both levels re-find after
      create, WARN via `shared.error_log`, and adopt the FIRST match (the
      `week_folder` / `hours_log` pattern — duplicate cleanup is
      operator-manual; bounded blast radius: one empty orphan folder/sheet).
    - **§51 A1 margin-check on the create branch** ("per-job sheets resolved
      by validated find-or-create with the A1 margin-check — never a default,
      never silently past the cap"): `sheet_capacity.check_create_headroom`
      runs BEFORE every sheet create, ADVISORY posture (WARN + Review-Queue
      breach signal, create proceeds — mirrors
      `hours_log._warn_on_thin_headroom`).
    - Sheet names are defensively truncated to the 50-char cap (errorCode
      1041); folder names are uncapped.
    - No send capability, no AI — I/O is `shared.smartsheet_client` plus the
      Review-Queue breach enqueue via `shared.sheet_capacity`.

Failure modes
    - `SmartsheetError` propagates — the callers (the poll daemons' fenced
      per-job append helpers) classify and WARN; a per-job failure must never
      fail the filing (Box + the flat Log are the SoR).
    - A JUST-CREATED sheet can 404 for a few seconds (Smartsheet create→read
      eventual consistency, errorCode 1006); the create path absorbs that
      window with a bounded readiness probe before returning — see
      `_wait_until_readable`. On probe exhaustion the id is returned anyway
      (WARN `job_sheet_ready_probe_exhausted`) — never hangs a daemon pass.
    - The A1 capacity check is FAIL-OPEN (a flaky count read must never block
      a filing-path create): any check error reduces to WARN
      `sheet_capacity_check_failed` and the create proceeds unguarded.

Consumers
    `subcontracts/subcontract_poll._append_perjob_row_best_effort` and
    `po_materials/po_poll._append_perjob_row_best_effort` — the per-job mirror
    step (9b) of each daemon's filing path, both BEST-EFFORT fenced
    (`subcontract_perjob_sheet_failed` / `po_perjob_sheet_failed`).
    Operator-run integration coverage: tests/test_job_sheet_integration.py.
"""
from __future__ import annotations

import time

from shared import error_log, sheet_capacity, smartsheet_client
from shared.error_log import Severity

SCRIPT_NAME = "shared.job_sheet"

# Patchable sleep seam — tests stub `job_sheet._sleep` so the readiness probe
# never wall-clocks a test run.
_sleep = time.sleep

# Smartsheet sheet-name cap (HTTP 400 errorCode 1041) — same constant as
# safety_reports.week_sheet.SHEET_NAME_MAX / progress_reports.hours_log. The
# per-job sheet names in use today ("Subcontracts" / "Purchase Orders") are
# fixed short strings; the truncation below is a defensive guard so a future
# composite name cannot 400 at create time. Folder names are uncapped.
SHEET_NAME_MAX = 50

# Readiness-probe bounds for the CREATE path (§42 — why this exists): the
# 2026-07-13 live mirror smoke hit Smartsheet's create→read propagation window —
# `add_rows` against the sheet id returned ~2s earlier by
# `create_sheet_in_folder_from_template` failed HTTP 404 (errorCode 1006); a
# retry ~60s later on the SAME id succeeded. Without absorbing that window, a
# brand-new job's FIRST filing (create-then-append in one daemon pass) can 404,
# get reduced to the caller's best-effort WARN, and that filing's per-job
# mirror row is then permanently missing (each filing appends only its own
# row; the flat Log SoR is unaffected). Probing ONLY on the create path keeps
# the hot find path zero-cost.
READY_PROBE_ATTEMPTS = 5
READY_PROBE_DELAY_SECONDS = 2.0


def _wait_until_readable(sheet_id: int, correlation_id: str | None) -> None:
    """Absorb the create→read propagation window on a JUST-CREATED sheet.

    Cheap probe: `get_rows` on the new (empty) sheet — one GET, zero rows.
    `SmartsheetNotFoundError` = not-ready-yet → sleep + retry, up to
    `READY_PROBE_ATTEMPTS`; any OTHER error re-raises immediately (a real
    fault, not propagation lag). Bounded + non-blocking on exhaustion: WARN
    and return anyway — the caller's fence absorbs a residual 404; a daemon
    pass must never hang here.
    """
    for attempt in range(READY_PROBE_ATTEMPTS):
        try:
            smartsheet_client.get_rows(sheet_id)
            return
        except smartsheet_client.SmartsheetNotFoundError:
            if attempt == READY_PROBE_ATTEMPTS - 1:
                error_log.log(
                    Severity.WARN,
                    SCRIPT_NAME,
                    (
                        f"just-created sheet {sheet_id} still 404s after "
                        f"{READY_PROBE_ATTEMPTS} readiness probes — returning "
                        f"the id anyway; the caller's fence absorbs a residual "
                        f"404 (create→read eventual consistency, errorCode 1006)."
                    ),
                    error_code="job_sheet_ready_probe_exhausted",
                    correlation_id=correlation_id,
                )
                return
            _sleep(READY_PROBE_DELAY_SECONDS)


def _warn_on_thin_headroom(
    workspace_id: int, sheet_name: str, workstream: str, correlation_id: str | None
) -> None:
    """§51 A1 sheet-count tripwire, run before each CREATE. ADVISORY, never blocking
    (mirrors `hours_log._warn_on_thin_headroom` / `week_sheet`): a margin breach WARNs +
    enqueues the operator signal, then the create PROCEEDS. Belt-and-suspenders
    fail-open — any exception is reduced to a WARN (a flaky Smartsheet count read
    must never block a filing-path create)."""
    try:
        headroom = sheet_capacity.check_create_headroom(workspace_id)
    except Exception as exc:  # noqa: BLE001 — advisory tripwire; never block the create
        error_log.log(
            Severity.WARN, SCRIPT_NAME,
            f"sheet-capacity headroom check raised (create proceeds unguarded): {exc!r}",
            error_code="sheet_capacity_check_failed",
            correlation_id=correlation_id,
        )
        return
    if headroom.note:
        error_log.log(
            Severity.WARN, SCRIPT_NAME,
            f"sheet-capacity check fail-open before creating {sheet_name!r}: {headroom.note}",
            error_code="sheet_capacity_check_failed",
            correlation_id=correlation_id,
        )
        return
    if headroom.ok:
        return
    error_log.log(
        Severity.WARN, SCRIPT_NAME,
        (
            f"sheet-count margin breach in workspace {workspace_id}: "
            f"{headroom.current}/{headroom.ceiling} (margin {headroom.margin}) — creating "
            f"{sheet_name!r} anyway (advisory tripwire; see the Review-Queue row)."
        ),
        error_code="sheet_capacity_margin_breach",
        correlation_id=correlation_id,
    )
    try:
        sheet_capacity.route_breach_to_review_queue(
            workspace_id, headroom, workstream=workstream
        )
    except Exception as exc:  # noqa: BLE001 — the enqueue failing must not block the create
        error_log.log(
            Severity.WARN, SCRIPT_NAME,
            f"could not enqueue the sheet-capacity breach to ITS_Review_Queue: {exc!r}",
            error_code="sheet_capacity_rq_failed",
            correlation_id=correlation_id,
        )


def ensure_job_sheet(
    parent_folder_id: int,
    template_sheet_id: int,
    job_folder_name: str,
    sheet_name: str,
    *,
    workspace_id: int,
    workstream: str,
    correlation_id: str | None = None,
) -> int:
    """Find-or-create the per-job folder + tracking sheet; return the sheet ID.

    Args:
        parent_folder_id: the workspace's "Jobs" parent folder
            (`sheet_ids.FOLDER_SC_JOBS` / `FOLDER_PO_JOBS`).
        template_sheet_id: the sheet whose STRUCTURE the per-job sheet clones
            (`include=[]` — columns/picklists/descriptions, no rows). Pass the
            flat Log itself so the per-job columns match the ledger exactly.
        job_folder_name: the per-job folder title. Callers MUST pass
            `safety_naming.job_folder_name(job_name)` so the Smartsheet folder
            matches the per-job Box folder byte-for-byte.
        sheet_name: the tracking sheet's title inside the per-job folder
            (defensively truncated to the 50-char cap, errorCode 1041).
        workspace_id: the workspace hosting `parent_folder_id`
            (`WORKSPACE_SUBCONTRACTS` / `WORKSPACE_PURCHASE_ORDERS`) — the §51
            A1 `check_create_headroom` target, consulted before every sheet
            create (advisory; see `_warn_on_thin_headroom`).
        workstream: the caller's workstream tag (the daemons' `WORKSTREAM`
            constants) — attributes the A1 breach Review-Queue row.
        correlation_id: optional — threads the caller's correlation id into
            every WARN this helper logs (race duplicates, probe exhaustion,
            capacity check), matching the caller-side fence WARNs.

    Returns:
        The per-job tracking sheet's ID. Same call twice → same ID, zero
        API writes on the second invocation.

    Raises:
        SmartsheetError (typed hierarchy) — propagates for the caller's fence.
    """
    name = sheet_name.strip()
    if len(name) > SHEET_NAME_MAX:
        name = name[:SHEET_NAME_MAX].rstrip()

    folder_id = smartsheet_client.find_folder_by_name_in_folder(
        parent_folder_id, job_folder_name
    )
    if folder_id is None:
        folder_id = smartsheet_client.create_folder_in_folder(
            parent_folder_id, job_folder_name
        )
        # Race-safety post-create check (the week_folder pattern): two
        # concurrent creators can both pass the find above; adopt the first
        # match and WARN the orphan for manual cleanup.
        post_find = smartsheet_client.find_folder_by_name_in_folder(
            parent_folder_id, job_folder_name
        )
        if post_find is not None and post_find != folder_id:
            error_log.log(
                Severity.WARN,
                SCRIPT_NAME,
                (
                    f"duplicate per-job folder {job_folder_name!r} under parent "
                    f"{parent_folder_id}; using first match {post_find}, manual "
                    f"cleanup needed for {folder_id}."
                ),
                error_code="job_sheet_folder_race_duplicate",
                correlation_id=correlation_id,
            )
            folder_id = post_find

    sheet_id = smartsheet_client.find_sheet_by_name_in_folder(folder_id, name)
    if sheet_id is None:
        # §51 A1 margin-check BEFORE the create — advisory, never blocking.
        _warn_on_thin_headroom(workspace_id, name, workstream, correlation_id)
        sheet_id = smartsheet_client.create_sheet_in_folder_from_template(
            folder_id=folder_id,
            name=name,
            template_sheet_id=template_sheet_id,
            include=[],
        )
        # Same race-safety at the sheet level (hours_log does both levels; a
        # duplicate sheet is worse than a duplicate folder — rows would split).
        post_find = smartsheet_client.find_sheet_by_name_in_folder(folder_id, name)
        if post_find is not None and post_find != sheet_id:
            error_log.log(
                Severity.WARN,
                SCRIPT_NAME,
                (
                    f"duplicate per-job sheet {name!r} under folder {folder_id} "
                    f"(job folder {job_folder_name!r}); using first match "
                    f"{post_find}, manual cleanup needed for {sheet_id}."
                ),
                error_code="job_sheet_sheet_race_duplicate",
                correlation_id=correlation_id,
            )
            sheet_id = post_find
        # CREATE path only (the find path returns above-established sheets that
        # are long-readable): absorb the create→read 404 window before handing
        # the id to the caller's add_rows (2026-07-13 live-smoke finding).
        _wait_until_readable(sheet_id, correlation_id)

    return sheet_id
