"""ROADMAP Track 6 — relocate a closed job's containers into the archive, and back.

WHAT THIS REPLACES, AND WHY
---------------------------
The pre-Track-6 archive fired from inside `fieldops_sync._mirror_job`: any mirror of a job whose
lifecycle read `archived` moved four tracker SHEETS into a flat `Closed Projects` folder. Its
LOCATION was the defect. That is the job-dirty path, and the job is mark-synced immediately after,
so a failed move was permanent — `_warn_archive_move_failed` said "no auto-retry" in its own WARN
text. Coupled to an unconfirmed portal dropdown, it was a one-click, un-retryable, four-sheet
relocation whose only feedback was a WARN row. It never fired against live data; it was one
selection away.

This pass is the fifth instance of an established shape (`_mirror_hours_pass`,
`_mirror_equipment_pass`, `_mirror_material_list_pass`, `_mirror_material_incidents_pass`): its own
drained queue, its own gate, its own fences, and a commit point that reports back. Failures now
genuinely retry, because the queue keeps serving a job until its archive is terminal.

THE SIX CONTAINERS
------------------
A job owns a FOLDER per workstream, not a loose set of sheets — so this moves folders, and one
move relocates the whole subtree.

    Smartsheet  ITS — Archive / <Job> / {Safety, Progress, Purchase Orders, Subcontracts}/
    Box         ITS Archive   / <Job> / {Safety, Progress}/

Six, not eleven: `safety_reports.box.portal_root_folder_id` is the SHARED Box root for safety AND
purchase orders AND RFQs AND subcontracts, so moving `<safety root>/<Job>` carries
`Purchase Orders/`, `RFQs/`, `Vendor Quotes/` and the subcontract files with it.

THE TWO SYSTEMS FAIL DIFFERENTLY
--------------------------------
Smartsheet's move endpoint CANNOT rename (`newName` is a Copy-Folder parameter that `/move`
silently ignores), so each Smartsheet container is move-then-rename — two calls, non-atomic, with
a real crash window in between. Box's `Item.move(parent, name=)` does both in one PUT, so it has
no such window. That asymmetry is why the resume logic differs per system, and it is the single
most important thing to understand before editing this file.

RESUME KEYS OFF IDS, NEVER NAMES
--------------------------------
After a crash a container may have moved but not been renamed, so "did this finish?" cannot be
answered by looking for a name in the source: `week_sheet._ensure_job_folder`,
`hours_log._ensure_job_folder` and `job_sheet.ensure_job_sheet` all find-or-CREATE by job name, so
the live tree re-grows that name the moment anything is filed. The probe therefore reads the
RECORDED folder id's current name (`smartsheet_client.get_folder_name`) and compares it to the
expected label.

CAPABILITY GATED (Invariant 1): no AI, no send path. Enrolled in `tests/test_capability_gating.py`.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from progress_reports import equipment_status, hours_log, material_incidents, material_list
from safety_reports import safety_naming
from shared import error_log, sheet_ids, smartsheet_client
from shared.error_log import Severity

SCRIPT_NAME = "job_archive"

# The per-workstream labels the containers take inside the archive. These are the folder NAMES an
# operator will see, so they are deliberately plain English rather than internal keys.
LABEL_SAFETY = "Safety"
LABEL_PROGRESS = "Progress"
LABEL_PURCHASE_ORDERS = "Purchase Orders"
LABEL_SUBCONTRACTS = "Subcontracts"

# Stop auto-retrying a job whose archive keeps failing. Without a cap, a PERMANENT condition (the
# token lacking ADMIN on the Archive workspace, an operator-deleted destination) would re-fire the
# whole six-container sequence every cycle forever. The operator's "Try again" resets the counter.
MAX_ARCHIVE_ATTEMPTS = 20

# Access levels that satisfy the ADMIN_WORKSPACES requirement for a folder move.
_ADMIN_LEVELS = frozenset({"ADMIN", "OWNER"})


@dataclass(frozen=True)
class ArchiveSlot:
    """One relocatable container.

    A declarative tuple rather than four hand-written blocks: the fan-out across workspaces and
    roots is exactly the multi-surface shape that produced the old four-tracker tuple, and the
    reflex (HOUSE_REFLEXES §1) is to make the enumeration ONE datum you can grep.
    """

    system: str  # "smartsheet" | "box"
    key: str  # stable id in the D1 container report — never rendered to an operator
    label: str  # the folder name inside the archive, and the operator-facing name
    #: For Smartsheet: the WORKSPACE the per-job folder sits directly under, or None when it
    #: instead sits under a parent folder (`parent_folder`). Exactly one of the two is set.
    workspace: int | None = None
    parent_folder: int | None = None


SLOTS: tuple[ArchiveSlot, ...] = (
    # Safety + Progress per-job folders sit directly under their WORKSPACE root.
    ArchiveSlot("smartsheet", "smartsheet:safety", LABEL_SAFETY,
                workspace=sheet_ids.WORKSPACE_SAFETY_PORTAL),
    ArchiveSlot("smartsheet", "smartsheet:progress", LABEL_PROGRESS,
                workspace=sheet_ids.WORKSPACE_PROGRESS_REPORTING),
    # PO/RFQ and Subcontract per-job folders sit under a "Jobs" PARENT FOLDER.
    ArchiveSlot("smartsheet", "smartsheet:purchase_orders", LABEL_PURCHASE_ORDERS,
                parent_folder=sheet_ids.FOLDER_PO_JOBS),
    ArchiveSlot("smartsheet", "smartsheet:subcontracts", LABEL_SUBCONTRACTS,
                parent_folder=sheet_ids.FOLDER_SC_JOBS),
    # Box: TWO containers only — the safety root is shared by PO/RFQ/subcontracts (see the module
    # docstring), so its per-job folder carries them along.
    ArchiveSlot("box", "box:safety", LABEL_SAFETY),
    ArchiveSlot("box", "box:progress", LABEL_PROGRESS),
)

#: The four standing tracker sheets the OLD path moved individually. Retained only so the §43
#: runbook and any operator repair can name them; the folder move now carries them implicitly.
TRACKER_SHEET_NAMES = (
    hours_log.hours_log_sheet_name,
    equipment_status.equipment_sheet_name,
    material_list.material_list_sheet_name,
    material_incidents.material_incidents_sheet_name,
)


@dataclass
class ContainerResult:
    """One container's outcome, as reported back to D1 and rendered to the operator."""

    key: str
    label: str
    moved: bool
    note: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {"key": self.key, "label": self.label, "moved": self.moved, "note": self.note}


def verify_archive_capability(correlation_id: str | None = None) -> bool:
    """Pre-flight: does the token identity hold ADMIN on every workspace a move touches?

    Smartsheet's folder-move endpoint requires the `ADMIN_WORKSPACES` scope, and ITS authenticates
    with a PAT — which carries the acting user's own permissions. Without this probe the shortfall
    surfaces as a 403 only AFTER an operator pressed Archive, and only for whichever containers got
    that far: a half-archived job caused purely by a permissions gap.

    Returns False (and WARNs, naming the deficient workspace) rather than raising, so the caller
    skips the pass for the cycle instead of failing it. Never silent — a skipped pass with no log
    line would be indistinguishable from an empty queue.
    """
    required = (
        sheet_ids.WORKSPACE_ARCHIVE,
        sheet_ids.WORKSPACE_SAFETY_PORTAL,
        sheet_ids.WORKSPACE_PROGRESS_REPORTING,
        sheet_ids.WORKSPACE_PURCHASE_ORDERS,
        sheet_ids.WORKSPACE_SUBCONTRACTS,
    )
    for workspace_id in required:
        try:
            level = smartsheet_client.get_workspace_access_level(workspace_id)
        except Exception as exc:  # noqa: BLE001 — a probe failure must not fail the cycle
            error_log.log(
                Severity.WARN, SCRIPT_NAME,
                f"archive pre-flight could not read access level on workspace {workspace_id}; "
                f"skipping the archive pass this cycle: {type(exc).__name__}: {exc!r}",
                error_code="archive_preflight_unreadable",
                correlation_id=correlation_id,
            )
            return False
        if level.upper() not in _ADMIN_LEVELS:
            error_log.log(
                Severity.WARN, SCRIPT_NAME,
                f"archive pre-flight FAILED: the ITS Smartsheet identity holds {level!r} on "
                f"workspace {workspace_id}, but a folder move needs ADMIN_WORKSPACES. Every "
                f"archive would 403 partway through, leaving jobs half-relocated. Skipping the "
                f"archive pass until the share is fixed.",
                error_code="archive_preflight_not_admin",
                correlation_id=correlation_id,
            )
            return False
    return True


def ensure_archive_job_folder(folder_key: str) -> int:
    """Find-or-create `ITS — Archive / <folder_key>`, returning its folder id.

    Race-tolerant in the house pattern (`job_sheet.ensure_job_sheet`,
    `week_sheet._ensure_job_folder`): Smartsheet does not enforce folder-name uniqueness, so two
    creators can both pass the find step. We re-find after create, adopt the FIRST match, and WARN
    the duplicate for operator cleanup. Worst case is one empty orphan folder.

    Folder titles are NOT length-capped by Smartsheet (only sheet names are, at 50 — errorCode
    1041), so `folder_key` needs no truncation here.
    """
    existing = smartsheet_client.find_folder_by_name_in_workspace(
        sheet_ids.WORKSPACE_ARCHIVE, folder_key
    )
    if existing is not None:
        return existing

    created = smartsheet_client.create_folder_in_workspace(sheet_ids.WORKSPACE_ARCHIVE, folder_key)
    post_find = smartsheet_client.find_folder_by_name_in_workspace(
        sheet_ids.WORKSPACE_ARCHIVE, folder_key
    )
    if post_find is not None and post_find != created:
        error_log.log(
            Severity.WARN, SCRIPT_NAME,
            f"duplicate archive job folder {folder_key!r} under WORKSPACE_ARCHIVE "
            f"(using first match {post_find}; manual cleanup needed for {created})",
            error_code="archive_job_folder_duplicate",
        )
        return post_find
    return created


def resolve_source_container(slot: ArchiveSlot, folder_key: str) -> int | None:
    """Find the per-job folder in its LIVE location. Find-only — never creates.

    None means "nothing to move": either the job never produced anything in that workstream, or a
    previous cycle already relocated it. The caller distinguishes those two via the durable record,
    not by guessing here.
    """
    if slot.workspace is not None:
        return smartsheet_client.find_folder_by_name_in_workspace(slot.workspace, folder_key)
    assert slot.parent_folder is not None, "a smartsheet slot needs a workspace or a parent folder"
    return smartsheet_client.find_folder_by_name_in_folder(slot.parent_folder, folder_key)


def archive_smartsheet_container(
    slot: ArchiveSlot, folder_key: str, archive_job_folder: int
) -> ContainerResult:
    """Move one Smartsheet per-job folder into the archive and label it, or explain why not.

    MOVE-THEN-RENAME, and the order is load-bearing. Renaming first would make the folder invisible
    to the live find-or-create paths, which would then create a fresh empty folder under the job's
    name beside it — and the archive would go on to move the wrong tree. Moving first removes it
    from the source parent in a single call, so the source-side find immediately returns None and
    no live creator can be pointed at a half-archived container.

    The residual crash window is "moved but not renamed": a folder still called <Job> sitting
    inside Archive/<Job>/. Benign, detectable, and repaired by re-issuing the rename — which is
    idempotent, so the resume path just does it.
    """
    source = resolve_source_container(slot, folder_key)
    if source is None:
        return ContainerResult(slot.key, slot.label, moved=True, note="nothing to move")

    smartsheet_client.move_folder_to_folder(source, archive_job_folder)
    # Separate call — /move cannot rename. Idempotent, so a retry after a crash here is safe.
    if smartsheet_client.get_folder_name(source) != slot.label:
        smartsheet_client.rename_folder(source, slot.label)
    return ContainerResult(slot.key, slot.label, moved=True)


def _log_container_failure(
    job_id: str, slot: ArchiveSlot, exc: BaseException, correlation_id: str
) -> None:
    """WARN naming the JOB, the SYSTEM and the CONTAINER.

    The old `_warn_archive_move_failed` named only a sheet. With six containers across two systems
    that is not actionable — an operator reading `ITS_Errors` has to be able to tell which folder
    in which system is stuck without opening a runbook first.
    """
    error_log.log(
        Severity.WARN, SCRIPT_NAME,
        f"archive container FAILED for job_id={job_id!r}: {slot.system}/{slot.label} "
        f"({slot.key}): {type(exc).__name__}: {exc!r}. The job stays on the archive queue and "
        f"retries next cycle; see docs/runbooks/project_closure.md.",
        error_code="archive_container_failed",
        correlation_id=correlation_id,
    )


def archive_job(job: dict[str, Any], correlation_id: str | None = None) -> list[ContainerResult]:
    """Relocate every container for one job. Per-container fenced; never raises.

    Each container is attempted independently, so one failure never blocks the other five — a
    partial archive is a normal, resumable outcome rather than an all-or-nothing collapse. The
    caller turns these results into the D1 state the operator sees.
    """
    correlation_id = correlation_id or uuid.uuid4().hex[:12]
    job_id = str(job.get("job_id") or "")
    # The SNAPSHOT, not the live project_name — see the module docstring.
    folder_key = str(job.get("archive_folder_key") or "").strip()
    if not folder_key:
        # Defensive: the browser route always stamps this. An empty key would make every
        # find-by-name match nothing, which would look like a clean "nothing to move".
        error_log.log(
            Severity.WARN, SCRIPT_NAME,
            f"archive skipped for job_id={job_id!r}: archive_folder_key is empty, so no container "
            f"could be resolved. This should be impossible — the request route stamps it.",
            error_code="archive_folder_key_missing",
            correlation_id=correlation_id,
        )
        return [ContainerResult(s.key, s.label, moved=False, note="no folder key") for s in SLOTS]

    results: list[ContainerResult] = []
    archive_job_folder: int | None = None
    for slot in SLOTS:
        if slot.system != "smartsheet":
            # Box containers land in the PR that wires the Box root config; until then they are
            # reported as not-moved with an explicit note rather than silently omitted, so a
            # partial archive is honest about what remains.
            results.append(ContainerResult(slot.key, slot.label, moved=False, note="box leg pending"))
            continue
        try:
            if archive_job_folder is None:
                archive_job_folder = ensure_archive_job_folder(folder_key)
            results.append(archive_smartsheet_container(slot, folder_key, archive_job_folder))
        except Exception as exc:  # noqa: BLE001 — per-container fence; one failure never blocks the rest
            _log_container_failure(job_id, slot, exc, correlation_id)
            results.append(
                ContainerResult(slot.key, slot.label, moved=False, note=f"{type(exc).__name__}")
            )
    return results


def state_from_results(results: list[ContainerResult]) -> str:
    """Collapse per-container outcomes into the D1 archive_state the operator reads.

    `partial` is deliberately distinct from `failed`: an operator seeing "4 of 6 moved" needs to
    know something DID move, because the repair differs from "nothing happened".
    """
    moved = sum(1 for r in results if r.moved)
    if moved == len(results):
        return "complete"
    return "partial" if moved else "failed"


def folder_key_for(project_name: str) -> str:
    """The per-job folder key for a project name — the one naming rule, shared with Box.

    Thin delegate to `safety_naming.job_folder_name` so this module never grows a second copy;
    the Worker mirrors the SAME rule in TypeScript, with cross-language parity asserted in
    `tests/test_job_archive_guard.py`.
    """
    return safety_naming.job_folder_name(project_name)
