"""Materialize the 1111B canonical Box blueprint in the mirror tenant.

Companion build script to `docs/session_logs/2026-05-22_box_blueprint_1111b_design.md`
(absorb landed in PR #67). The blueprint design adopts universal
zero-padded numeric prefixes (`01., 02., ...`) at every level with
restart-at-each-level, `99.NN` reserved for sort-to-end templates,
typo fixes, uniform `Portfolio` prefix on all 12 top-level Portfolio
folders, and hyphens only for structural compounds.

What this script does
---------------------

Three phases, all idempotent:

1. **Clone**: ensure `1111B (Copy for new projects)` exists under
   `ITS DATA` (Box folder ID `382010286207`). If missing, clone from
   `1111A (Copy for new projects)` (folder ID `382384021749`) via
   `copy_with_lock_retry` from PR #56 (Box source-folder-lock retry,
   30s × 40 attempts = 20-min budget).

2. **Walk-and-rename**: traverse the 1111B tree top-down and apply the
   ~88 entries in `RENAME_MAP`. Each entry is keyed by
   `(post_rename_parent_path, current_child_name)` → `target_child_name`.
   Re-runs are safe — if a folder is already at its target name, skip
   silently. Single-shot retry on transient `BoxNotFoundError` per the
   pattern PR #65 established for the Smartsheet side.

3. **Verify**: walk the resulting 1111B tree, count folders, assert
   each target name from `RENAME_MAP` is present at its expected path.
   Emit a compliance report to
   `~/its/logs/migrations/box_build_1111b_report.txt` with PASS / FAIL
   per folder. Non-zero exit on any FAIL.

Re-runnable
-----------

Safe to invoke repeatedly:
  - If 1111B already exists, the clone step is a no-op.
  - If folders are already renamed to their targets, the rename step
    is a no-op for those folders (target name present → skip silently).
  - If the source name is missing AND the target name is also missing,
    that's a structural drift signal — logged as a WARN with no rename.
  - Compliance report regenerates from the live state each run.

CLI
---

  python scripts/migrations/box_build_1111b_blueprint.py              # build + verify
  python scripts/migrations/box_build_1111b_blueprint.py --dry-run    # show planned renames; no writes
  python scripts/migrations/box_build_1111b_blueprint.py --verify-only # verify existing 1111B; no clone/renames

Scope discipline
----------------

- Mirror tenant only. 1111A and the 6 project clones are untouched.
- `shared/defaults.py BOX_PROJECT_FOLDERS` still references 1111A clones;
  no code-path migration in this PR.
- `(post-1111B)` TODO markers from PR #67 stay in place; they migrate
  in a future PR if/when 1111B becomes canonical.

The exact rename map lives in `RENAME_MAP` below — keyed by
post-rename parent path so top-down traversal works without churning
child lookups when a parent is renamed.
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from boxsdk.exception import BoxAPIException  # type: ignore[import-untyped]

from shared import box_client
from shared.error_log import its_error_log
from shared.kill_switch import require_active

SCRIPT_NAME = "scripts.migrations.box_build_1111b_blueprint"

# Box folder constants (mirror tenant). 1111A is the source template;
# 1111B will be created next to it as a side-by-side reference.
PARENT_FOLDER_ID = "382010286207"  # ITS DATA root in mirror tenant
SOURCE_1111A_ID = "382384021749"  # 1111A (Copy for new projects)
TARGET_1111B_NAME = "1111B (Copy for new projects)"

# Expected child count at 1111B root after clone (same as 1111A).
# Sourced from `box_clone_1111a_to_projects.EXPECTED_SUBFOLDER_COUNT`.
EXPECTED_TOPLEVEL_FOLDER_COUNT = 14

# Expected total folder count after the rename pass (1111B root + all
# descendants). Empirically measured at 267 via live inspection of
# 1111A on 2026-05-23 — the chat-session blueprint design captured in
# PR #67's session log estimated "131 folders" but that figure was the
# RENAME_MAP entry count, not the total tree footprint. Many leaf
# folders + already-properly-named folders carry forward unchanged
# from 1111A. 267 is the correct expected value going forward.
EXPECTED_TOTAL_FOLDER_COUNT = 267

# Single-shot retry budget for transient Box 404s on rename ops. Mirrors
# the PR #65 pattern for Smartsheet — bounded to one retry because the
# transient window is sub-second in practice; multi-retry loops would
# just delay non-transient errors without fixing the underlying staleness.
RENAME_RETRY_SLEEP_SECONDS = 0.5

# Lock-retry budget for Box deep-copy. Source-folder-lock failures
# surface during async copy; 30s × 40 attempts = 20-min budget matches
# the PR #56 evidence. Replicated from box_clone_1111a_to_projects per
# preservation-over-refactor — extracting to shared/box_helpers.py is
# the natural follow-on once a third consumer surfaces.
LOCK_RETRY_MAX_ATTEMPTS = 40
LOCK_RETRY_WAIT_SECONDS = 30

# Deep-copy polling budget. Box returns the new folder ID immediately
# but the child structure populates asynchronously.
DEEP_COPY_TIMEOUT_SECONDS = 600  # 10 minutes
DEEP_COPY_POLL_INTERVAL_SECONDS = 10

# Log destination. Same convention as the other migration scripts.
LOG_DIR = Path.home() / "its" / "logs" / "migrations"
LOG_PATH = LOG_DIR / "box_build_1111b.log"
REPORT_PATH = LOG_DIR / "box_build_1111b_report.txt"

log = logging.getLogger("box_build_1111b")


# ---- The rename map (88 entries) ---------------------------------------
#
# Keyed by (post-rename-parent-path, current_child_name) -> target_child_name.
# Apply top-down so child lookups use the post-rename parent name. Insertion
# order is the apply order; Python 3.7+ preserves dict insertion order so
# the top-level entries process first.
#
# Parent path uses "/" as separator. Empty string `""` = 1111B root.

RENAME_MAP: dict[tuple[str, str], str] = {
    # ===== Top level (1111B root) =====
    ("", "1. Portfolio Client Docs"): "01. Portfolio Client Docs",
    ("", "2. Portfolio Buyout"): "02. Portfolio Buyout",
    ("", "3. Portfolio Schedules"): "03. Portfolio Schedules",
    ("", "4. Portfolio Dev Docs"): "04. Portfolio Dev Docs",
    ("", "5. Engineering Gen"): "05. Portfolio Engineering Gen",
    ("", "6. Portfolio Owner Correspond"): "06. Portfolio Owner Correspondence",
    ("", "7. Portfolio Financials"): "07. Portfolio Financials",
    ("", "8. Portfolio Change Management"): "08. Portfolio Change Management",
    ("", "9. Utility-Documents-Tracking"): "09. Portfolio Utility Documents Tracking",
    ("", "10. Submittal Logs"): "10. Portfolio Submittal Logs",
    ("", "11. De-Comm Bonds"): "11. Portfolio De-Comm Bonds",
    ("", "12. Portfolio Closeout"): "12. Portfolio Closeout",
    # "(Project # & Name) Field" and "(Project # & Name) Office" — unchanged.

    # ===== Field tree =====
    ("(Project # & Name) Field", "A. Onsite Reporting & Tracking"): "01. Onsite Reporting & Tracking",
    ("(Project # & Name) Field", "B. Approved Plans IFC"): "02. Approved Plans IFC",
    ("(Project # & Name) Field", "C. Installation Manuals"): "03. Installation Manuals",
    ("(Project # & Name) Field", "D. Schedules"): "04. Schedules",
    ("(Project # & Name) Field", "E. Permits & Inspector Cards"): "05. Permits & Inspector Cards",
    ("(Project # & Name) Field", "F. Project Closeout"): "06. Project Closeout",

    # Field / 01. Onsite Reporting & Tracking
    ("(Project # & Name) Field/01. Onsite Reporting & Tracking", "A. Safety Plan & Reports"): "01. Safety Plan & Reports",
    ("(Project # & Name) Field/01. Onsite Reporting & Tracking", "B. Project Reports & Trackers"): "02. Project Reports & Trackers",
    ("(Project # & Name) Field/01. Onsite Reporting & Tracking", "C. Deliveries & Shipments"): "03. Deliveries & Shipments",
    ("(Project # & Name) Field/01. Onsite Reporting & Tracking", "D. Rental Quotes & Tracking"): "04. Rental Quotes & Tracking",
    ("(Project # & Name) Field/01. Onsite Reporting & Tracking", "E. Utility Info & Tracking"): "05. Utility Info & Tracking",
    ("(Project # & Name) Field/01. Onsite Reporting & Tracking", "F. SWPPP Plans & Reports"): "06. SWPPP Plans & Reports",
    ("(Project # & Name) Field/01. Onsite Reporting & Tracking", "G. QAQC & Punchlists"): "07. QAQC & Punchlists",
    ("(Project # & Name) Field/01. Onsite Reporting & Tracking", "H. Onsite Photos"): "08. Onsite Photos",
    ("(Project # & Name) Field/01. Onsite Reporting & Tracking", "I. Site Contacts"): "09. Site Contacts",

    # Field / 01.Onsite / 01.SafetyPlan
    ("(Project # & Name) Field/01. Onsite Reporting & Tracking/01. Safety Plan & Reports", "A. Site Info & Safety Templates"): "01. Site Info & Safety Templates",
    ("(Project # & Name) Field/01. Onsite Reporting & Tracking/01. Safety Plan & Reports", "B. Site Specific Safety Plan & Signage"): "02. Site Specific Safety Plan & Signage",
    ("(Project # & Name) Field/01. Onsite Reporting & Tracking/01. Safety Plan & Reports", "C. Employee Orientation"): "03. Employee Orientation",
    ("(Project # & Name) Field/01. Onsite Reporting & Tracking/01. Safety Plan & Reports", "D. JSA's"): "04. JSAs",
    ("(Project # & Name) Field/01. Onsite Reporting & Tracking/01. Safety Plan & Reports", "E. Tool Box Talks"): "05. Tool Box Talks",
    ("(Project # & Name) Field/01. Onsite Reporting & Tracking/01. Safety Plan & Reports", "F. Incident Reports"): "06. Incident Reports",

    # Field / 01.Onsite / 02.ProjectReports
    ("(Project # & Name) Field/01. Onsite Reporting & Tracking/02. Project Reports & Trackers", "A. DFR's"): "01. DFRs",
    ("(Project # & Name) Field/01. Onsite Reporting & Tracking/02. Project Reports & Trackers", "B. WPR's"): "02. WPRs",
    ("(Project # & Name) Field/01. Onsite Reporting & Tracking/02. Project Reports & Trackers", "C. Meeting Minutes"): "03. Meeting Minutes",
    ("(Project # & Name) Field/01. Onsite Reporting & Tracking/02. Project Reports & Trackers", "D. Inspection Reports"): "04. Inspection Reports",
    ("(Project # & Name) Field/01. Onsite Reporting & Tracking/02. Project Reports & Trackers", "E. Manpower Tracking"): "05. Manpower Tracking",
    ("(Project # & Name) Field/01. Onsite Reporting & Tracking/02. Project Reports & Trackers", "F. Other Project Trackers"): "06. Other Project Trackers",
    ("(Project # & Name) Field/01. Onsite Reporting & Tracking/02. Project Reports & Trackers", "G. Work Orders"): "07. Work Orders",

    # Field / 02.ApprovedPlans
    ("(Project # & Name) Field/02. Approved Plans IFC", "A. County Requirements"): "01. County Requirements",
    ("(Project # & Name) Field/02. Approved Plans IFC", "B. Fire Plan"): "02. Fire Plan",
    ("(Project # & Name) Field/02. Approved Plans IFC", "C. Solid Waste Plan"): "03. Solid Waste Plan",
    ("(Project # & Name) Field/02. Approved Plans IFC", "D. Approved Structual Calculations"): "04. Approved Structural Calculations",  # typo fix
    ("(Project # & Name) Field/02. Approved Plans IFC", "E. Civil"): "05. Civil",
    ("(Project # & Name) Field/02. Approved Plans IFC", "F. Mechanical"): "06. Mechanical",
    ("(Project # & Name) Field/02. Approved Plans IFC", "G. Electrical"): "07. Electrical",

    # Field / 04.Schedules — Templates → 99.01 Templates (sort-to-end)
    ("(Project # & Name) Field/04. Schedules", "Templates"): "99.01 Templates",

    # Field / 06.ProjectCloseout — zero-pad 1-12; hyphen on As-Built
    ("(Project # & Name) Field/06. Project Closeout", "1. Final As Built Record Drawings"): "01. Final As-Built Record Drawings",
    ("(Project # & Name) Field/06. Project Closeout", "2. ELEC Testing & CX"): "02. ELEC Testing & CX",
    ("(Project # & Name) Field/06. Project Closeout", "3. MECH Testing & CX"): "03. MECH Testing & CX",
    ("(Project # & Name) Field/06. Project Closeout", "4. Record Pictures"): "04. Record Pictures",
    ("(Project # & Name) Field/06. Project Closeout", "5. Special Inspection Reports"): "05. Special Inspection Reports",
    ("(Project # & Name) Field/06. Project Closeout", "6. Final Signed Permits"): "06. Final Signed Permits",
    ("(Project # & Name) Field/06. Project Closeout", "7. Meter Picture ID"): "07. Meter Picture ID",
    ("(Project # & Name) Field/06. Project Closeout", "8. Module Scans"): "08. Module Scans",
    ("(Project # & Name) Field/06. Project Closeout", "9. O&M Manual"): "09. O&M Manual",
    ("(Project # & Name) Field/06. Project Closeout", "10. Punch Lists Executed"): "10. Punch Lists Executed",
    ("(Project # & Name) Field/06. Project Closeout", "11. Completion Certificates"): "11. Completion Certificates",
    ("(Project # & Name) Field/06. Project Closeout", "12. Warranty Signed"): "12. Warranty Signed",

    # ===== Office tree =====
    ("(Project # & Name) Office", "1. ESS Contract & LNTP (to owner)"): "01. ESS Contract & LNTP (to owner)",
    ("(Project # & Name) Office", "2. Accounting (to owner)"): "02. Accounting (to owner)",
    ("(Project # & Name) Office", "3. Change Management"): "03. Change Management",
    ("(Project # & Name) Office", "4. Submittal Logs"): "04. Submittal Logs",
    ("(Project # & Name) Office", "5. Subcontractors & Vendors"): "05. Subcontractors & Vendors",
    ("(Project # & Name) Office", "6. Developer Docs"): "06. Developer Docs",
    ("(Project # & Name) Office", "7. Engineering"): "07. Engineering",
    ("(Project # & Name) Office", "8. Permitting"): "08. Permitting",
    ("(Project # & Name) Office", "9. Utility-Documents-Tracking"): "09. Utility Documents Tracking",

    # Office / 02.Accounting
    ("(Project # & Name) Office/02. Accounting (to owner)", "A. Application For Payment"): "01. Application For Payment",
    ("(Project # & Name) Office/02. Accounting (to owner)", "B. Change Orders & CO Tracker"): "02. Change Orders & CO Tracker",
    ("(Project # & Name) Office/02. Accounting (to owner)", "C. Budget"): "03. Budget",
    ("(Project # & Name) Office/02. Accounting (to owner)", "D. Insurance"): "04. Insurance",
    ("(Project # & Name) Office/02. Accounting (to owner)", "E. ESS Waivers"): "05. ESS Waivers",

    # Office / 03.ChangeManagement — 3.X restart
    ("(Project # & Name) Office/03. Change Management", "3.1 RFIs"): "01. RFIs",
    ("(Project # & Name) Office/03. Change Management", "3.2 OCOs"): "02. OCOs",
    ("(Project # & Name) Office/03. Change Management", "3.3 SCOs"): "03. SCOs",

    # Office / 04.SubmittalLogs
    ("(Project # & Name) Office/04. Submittal Logs", "4.1 Owner Submittals"): "01. Owner Submittals",

    # Office / 05.Subcontractors — 99.X stays, zero-pad sub-numbers, whitespace normalize
    ("(Project # & Name) Office/05. Subcontractors & Vendors", "99.1 Buyout  (estimates & quotes)"): "99.01 Buyout (estimates & quotes)",  # double-space fix
    ("(Project # & Name) Office/05. Subcontractors & Vendors", "99.2 Vendor Name (Copy Folder)"): "99.02 Vendor Name (Copy Folder)",
    ("(Project # & Name) Office/05. Subcontractors & Vendors", "99.3 Sub Name (Copy Folder)"): "99.03 Sub Name (Copy Folder)",
    ("(Project # & Name) Office/05. Subcontractors & Vendors", "99.4 PSA (Copy Folder)"): "99.04 PSA (Copy Folder)",

    # Office / 06.DeveloperDocs — 6.X restart
    ("(Project # & Name) Office/06. Developer Docs", "6.1 Zoning & Permitting"): "01. Zoning & Permitting",
    ("(Project # & Name) Office/06. Developer Docs", "6.2 Studies"): "02. Studies",
    ("(Project # & Name) Office/06. Developer Docs", "6.3 Interconnection"): "03. Interconnection",
    ("(Project # & Name) Office/06. Developer Docs", "6.4 Equipment"): "04. Equipment",
    ("(Project # & Name) Office/06. Developer Docs", "6.5 Title & Lease"): "05. Title & Lease",
    ("(Project # & Name) Office/06. Developer Docs", "6.6 Production Modeling"): "06. Production Modeling",

    # Office / 07.Engineering — 7.X restart, capitalize Geotech-Pile Test, hyphen
    ("(Project # & Name) Office/07. Engineering", "7.1 Equipment"): "01. Equipment",
    ("(Project # & Name) Office/07. Engineering", "7.2 Surveys"): "02. Surveys",
    ("(Project # & Name) Office/07. Engineering", "7.3 Geotech-pile test"): "03. Geotech-Pile Test",
    ("(Project # & Name) Office/07. Engineering", "7.4 Prelim Site Plans"): "04. Prelim Site Plans",
    ("(Project # & Name) Office/07. Engineering", "7.5 Production Profiles"): "05. Production Profiles",
    ("(Project # & Name) Office/07. Engineering", "7.6 10%"): "06. 10%",
    ("(Project # & Name) Office/07. Engineering", "7.7 60% IFP"): "07. 60% IFP",
    ("(Project # & Name) Office/07. Engineering", "7.8 90%"): "08. 90%",
    ("(Project # & Name) Office/07. Engineering", "7.9 IFC"): "09. IFC",
    ("(Project # & Name) Office/07. Engineering", "7.10 IFC Redlines"): "10. IFC Redlines",
    ("(Project # & Name) Office/07. Engineering", "7.11 As-Built"): "11. As-Built",

    # Office / 08.Permitting — 8.X restart
    ("(Project # & Name) Office/08. Permitting", "8.1 Planning & Zoning"): "01. Planning & Zoning",
    ("(Project # & Name) Office/08. Permitting", "8.2 Dept of Environment"): "02. Dept of Environment",
    ("(Project # & Name) Office/08. Permitting", "8.3 Storm Water Permit"): "03. Storm Water Permit",
    ("(Project # & Name) Office/08. Permitting", "8.4 Access & ROW Permit"): "04. Access & ROW Permit",
    ("(Project # & Name) Office/08. Permitting", "8.5 Building Permit"): "05. Building Permit",
    ("(Project # & Name) Office/08. Permitting", "8.6 Electrical Permit"): "06. Electrical Permit",
    ("(Project # & Name) Office/08. Permitting", "8.7 State DOT"): "07. State DOT",
    ("(Project # & Name) Office/08. Permitting", "8.8 Fire Dept Approval"): "08. Fire Dept Approval",

    # Office / 09.UtilityDocs — 9.X restart + typo + plurals
    ("(Project # & Name) Office/09. Utility Documents Tracking", "9.1 PPA's & IA's"): "01. PPAs & IAs",
    ("(Project # & Name) Office/09. Utility Documents Tracking", "9.2 Gear Approvals"): "02. Gear Approvals",
    ("(Project # & Name) Office/09. Utility Documents Tracking", "9.3 Job Sketches"): "03. Job Sketches",
    ("(Project # & Name) Office/09. Utility Documents Tracking", "9.4 Transfer Trip"): "04. Transfer Trip",
    ("(Project # & Name) Office/09. Utility Documents Tracking", "9.5 ROW Permits"): "05. ROW Permits",
    ("(Project # & Name) Office/09. Utility Documents Tracking", "9.6 Coorespondance"): "06. Correspondence",  # typo fix
    ("(Project # & Name) Office/09. Utility Documents Tracking", "9.7 Utility Notices"): "07. Utility Notices",
    ("(Project # & Name) Office/09. Utility Documents Tracking", "9.8 PTO's"): "08. PTOs",
    ("(Project # & Name) Office/09. Utility Documents Tracking", "9.9 COD's"): "09. CODs",

    # ===== Portfolio subtrees =====
    # Portfolio / 02.Buyout — 99. and Z. both fold to 99.NN
    ("02. Portfolio Buyout", "99. Templates"): "99.01 Templates",
    ("02. Portfolio Buyout", "Z. Example Specs"): "99.02 Example Specs",

    # Portfolio / 06.OwnerCorrespondence
    ("06. Portfolio Owner Correspondence", "1. Notices"): "01. Notices",
    ("06. Portfolio Owner Correspondence", "2. Weekly Progress Reports"): "02. Weekly Progress Reports",
    ("06. Portfolio Owner Correspondence", "3. Meeting Minutes"): "03. Meeting Minutes",

    # Portfolio / 07.Financials
    ("07. Portfolio Financials", "Sub Invoices"): "01. Sub Invoices",

    # Portfolio / 08.ChangeManagement — bare names get workflow-order prefix
    ("08. Portfolio Change Management", "RFIs"): "01. RFIs",
    ("08. Portfolio Change Management", "OCOs"): "02. OCOs",
    ("08. Portfolio Change Management", "SCOs"): "03. SCOs",

    # Portfolio / 12.Closeout
    ("12. Portfolio Closeout", "1. Mechanical Completion"): "01. Mechanical Completion",
    ("12. Portfolio Closeout", "2. SU&C Completion (K1)"): "02. SU&C Completion (K1)",
    ("12. Portfolio Closeout", "3. Substantial Completion"): "03. Substantial Completion",
    ("12. Portfolio Closeout", "4. Final Completion"): "04. Final Completion",
    ("12. Portfolio Closeout", "5. Owner Coorespondance"): "05. Owner Correspondence",  # typo fix

    # Portfolio / 12.Closeout / 01.MechCompletion
    ("12. Portfolio Closeout/01. Mechanical Completion", "1. MC Certificates"): "01. MC Certificates",
}


# ---- Logging setup ------------------------------------------------------


def _configure_logging() -> None:
    """Wire root logger to both stdout and the migrations log file."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log.setLevel(logging.INFO)
    log.handlers.clear()  # idempotent on re-import

    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S%z",
    )

    file_handler = logging.FileHandler(LOG_PATH, mode="a", encoding="utf-8")
    file_handler.setFormatter(formatter)
    log.addHandler(file_handler)

    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(formatter)
    log.addHandler(stream_handler)


# ---- Helpers (replicated from box_clone_1111a_to_projects per preservation) ----


def _is_lock_error(exc: BoxAPIException) -> bool:
    """Return True if `exc` looks like a Box source-folder-lock failure.

    Replicated from `box_clone_1111a_to_projects._is_lock_error`. Lock
    failures surface as HTTP 500 with a message containing 'locked' or
    'lock' (case-insensitive). Distinguishing from generic 500s matters —
    we retry locks but bail on real server errors.
    """
    if exc.status != 500:
        return False
    message = (exc.message or "").lower()
    return "lock" in message


def _count_child_folders(client: Any, folder_id: str) -> int:
    """Return the number of sub-folders directly inside `folder_id`."""
    items = client.folder(folder_id).get_items(
        limit=100, fields=["id", "name", "type"]
    )
    return sum(1 for item in items if item.type == "folder")


def _find_child(client: Any, parent_id: str, name: str) -> str | None:
    """Return the Box folder ID of `name` under `parent_id`, or None.

    Replicated from `box_clone_1111a_to_projects._find_project_folder_id`.
    Match by exact case-sensitive name. Box does not enforce uniqueness
    within a folder; first match wins.
    """
    items = client.folder(parent_id).get_items(
        limit=200, fields=["id", "name", "type"]
    )
    for item in items:
        if item.type == "folder" and item.name == name:
            return str(item.id)
    return None


def copy_with_lock_retry(
    client: Any,
    source_id: str,
    parent_id: str,
    name: str,
    *,
    max_attempts: int = LOCK_RETRY_MAX_ATTEMPTS,
    wait_seconds: int = LOCK_RETRY_WAIT_SECONDS,
) -> str:
    """Clone `source_id` into `parent_id` as `name`; retry on lock errors.

    Replicated from `box_clone_1111a_to_projects.copy_with_lock_retry`.
    Returns the new folder ID. Retries on HTTP 500 + 'lock' in message.
    Bails on any other error (4xx name conflicts, perm denials, etc.).
    Budget: max_attempts × wait_seconds (default 40 × 30s = 20 min).
    """
    for attempt in range(1, max_attempts + 1):
        try:
            new_folder = client.folder(source_id).copy(
                parent_folder=client.folder(parent_id),
                name=name,
            )
            return str(new_folder.id)
        except BoxAPIException as e:
            if not _is_lock_error(e):
                raise
            if attempt == max_attempts:
                raise RuntimeError(
                    f"Lock-retry budget exhausted after {max_attempts} attempts "
                    f"({max_attempts * wait_seconds}s) cloning {source_id} -> "
                    f"{parent_id} as {name!r}. Last error: HTTP {e.status}: {e.message}"
                ) from e
            log.info(
                "[lock] Attempt %s/%s: source locked, waiting %ss ...",
                attempt,
                max_attempts,
                wait_seconds,
            )
            time.sleep(wait_seconds)
    raise AssertionError("unreachable")  # pragma: no cover


def wait_for_deep_copy_complete(
    client: Any,
    folder_id: str,
    *,
    expected_count: int,
    timeout_seconds: int = DEEP_COPY_TIMEOUT_SECONDS,
    poll_interval: int = DEEP_COPY_POLL_INTERVAL_SECONDS,
) -> tuple[bool, int]:
    """Poll until `folder_id` has >= `expected_count` direct sub-folders.

    Replicated from `box_clone_1111a_to_projects.wait_for_deep_copy_complete`.
    Returns (completed, current_count). `completed=True` if the count
    reached `expected_count` within budget; `False` on timeout (folder
    still exists with whatever partial count we observed).
    """
    deadline = time.time() + timeout_seconds
    current = 0
    while time.time() < deadline:
        current = _count_child_folders(client, folder_id)
        if current >= expected_count:
            return True, current
        time.sleep(poll_interval)
    return False, current


def _resolve_path(
    client: Any, root_id: str, parent_path: str
) -> str | None:
    """Walk from `root_id` following the slash-separated `parent_path`.

    Empty `parent_path` returns `root_id` directly. Returns None if any
    intermediate segment is missing (caller treats as "path can't be
    resolved — skip the rename"). Each segment is the post-rename name
    of the parent, so traversal works correctly only when shallower
    renames have already been applied (which `RENAME_MAP` insertion
    order guarantees).
    """
    if parent_path == "":
        return root_id
    current = root_id
    for segment in parent_path.split("/"):
        child_id = _find_child(client, current, segment)
        if child_id is None:
            return None
        current = child_id
    return current


def _rename_folder(client: Any, folder_id: str, new_name: str) -> None:
    """Rename `folder_id` to `new_name`. Single-shot retry on transient 404.

    Per the PR #65 pattern for Smartsheet, the Box SDK can occasionally
    return 404 immediately after a creation/clone due to in-process
    caching. A 500 ms pause + one retry covers the staleness window.
    """
    try:
        client.folder(folder_id).rename(new_name)
    except BoxAPIException as first_exc:
        if first_exc.status != 404:
            raise
        log.info(
            "transient 404 renaming folder_id=%s to %r; retrying in %.1fs",
            folder_id,
            new_name,
            RENAME_RETRY_SLEEP_SECONDS,
        )
        time.sleep(RENAME_RETRY_SLEEP_SECONDS)
        client.folder(folder_id).rename(new_name)


# ---- Phase 1: clone ----------------------------------------------------


def ensure_1111b_clone(
    client: Any,
    *,
    parent_id: str = PARENT_FOLDER_ID,
    source_id: str = SOURCE_1111A_ID,
    dry_run: bool = False,
) -> str:
    """Return the folder ID of `1111B (Copy for new projects)`; clone if missing."""
    existing = _find_child(client, parent_id, TARGET_1111B_NAME)
    if existing is not None:
        log.info(
            "%s already exists under parent %s (folder_id=%s) — skipping clone",
            TARGET_1111B_NAME,
            parent_id,
            existing,
        )
        return existing

    if dry_run:
        log.info(
            "[dry-run] would clone source %s -> %s as %r",
            source_id,
            parent_id,
            TARGET_1111B_NAME,
        )
        return "(dry-run)"

    log.info(
        "cloning source %s -> %s as %r (Box deep-copy is async; "
        "lock-retry budget 20 min)",
        source_id,
        parent_id,
        TARGET_1111B_NAME,
    )
    new_id = copy_with_lock_retry(
        client=client,
        source_id=source_id,
        parent_id=parent_id,
        name=TARGET_1111B_NAME,
    )
    log.info("clone created folder_id=%s; waiting for deep-copy to populate", new_id)

    completed, child_count = wait_for_deep_copy_complete(
        client, new_id, expected_count=EXPECTED_TOPLEVEL_FOLDER_COUNT
    )
    if not completed:
        log.warning(
            "deep-copy timeout: only %s/%s top-level folders populated within "
            "the budget; continuing — the rename pass will surface specific gaps",
            child_count,
            EXPECTED_TOPLEVEL_FOLDER_COUNT,
        )
    else:
        log.info(
            "deep-copy completed: %s/%s top-level folders populated",
            child_count,
            EXPECTED_TOPLEVEL_FOLDER_COUNT,
        )
    return new_id


# ---- Phase 2: walk-and-rename ------------------------------------------


def apply_renames(
    client: Any, root_id: str, *, dry_run: bool = False
) -> dict[str, int]:
    """Apply every RENAME_MAP entry top-down. Idempotent.

    Returns a counter dict: renamed / already_renamed / source_missing /
    parent_unresolved. Logging is INFO per entry for action + WARN per
    unresolved.
    """
    counters = {
        "renamed": 0,
        "already_renamed": 0,
        "no_op_same_name": 0,
        "source_missing": 0,
        "parent_unresolved": 0,
    }
    for (parent_path, current_name), target_name in RENAME_MAP.items():
        # Same-name entries (e.g. "12. Portfolio Closeout" → "12. Portfolio Closeout")
        # are documentation-only: the blueprint records the expected name but no
        # rename is needed. Detect + skip without a Box API call.
        if current_name == target_name:
            counters["no_op_same_name"] += 1
            continue

        parent_id = _resolve_path(client, root_id, parent_path)
        if parent_id is None:
            log.warning(
                "parent path %r could not be resolved — skipping rename of %r",
                parent_path,
                current_name,
            )
            counters["parent_unresolved"] += 1
            continue
        if parent_id == "(dry-run)":
            # dry-run: 1111B doesn't exist yet, so post-clone traversal isn't possible.
            # Skip individual rename simulations; we log the count only.
            log.info(
                "[dry-run] would rename %r/%r -> %r (parent not yet cloned)",
                parent_path,
                current_name,
                target_name,
            )
            counters["renamed"] += 1
            continue

        child_id = _find_child(client, parent_id, current_name)
        if child_id is not None:
            if dry_run:
                log.info(
                    "[dry-run] would rename %r/%r -> %r",
                    parent_path,
                    current_name,
                    target_name,
                )
                counters["renamed"] += 1
                continue
            log.info(
                "renaming %r/%r -> %r (folder_id=%s)",
                parent_path,
                current_name,
                target_name,
                child_id,
            )
            _rename_folder(client, child_id, target_name)
            counters["renamed"] += 1
            continue

        # Source not present — check whether target is already there (idempotent re-run).
        existing_target_id = _find_child(client, parent_id, target_name)
        if existing_target_id is not None:
            log.info(
                "already renamed: %r/%r already exists (folder_id=%s) — skipping",
                parent_path,
                target_name,
                existing_target_id,
            )
            counters["already_renamed"] += 1
        else:
            log.warning(
                "structural drift: neither %r nor %r found under parent %r — skipping",
                current_name,
                target_name,
                parent_path,
            )
            counters["source_missing"] += 1
    return counters


# ---- Phase 3: verify + compliance report -------------------------------


def _count_all_descendants(client: Any, folder_id: str) -> int:
    """Recursively count all descendant folders (including `folder_id` itself)."""
    count = 1  # self
    items = client.folder(folder_id).get_items(
        limit=200, fields=["id", "name", "type"]
    )
    for item in items:
        if item.type == "folder":
            count += _count_all_descendants(client, str(item.id))
    return count


def verify_blueprint(
    client: Any, root_id: str
) -> tuple[bool, str, dict[str, int]]:
    """Verify 1111B conforms to the blueprint. Returns (passed, report, counters)."""
    report_lines: list[str] = []
    counters = {
        "expected_total": EXPECTED_TOTAL_FOLDER_COUNT,
        "actual_total": 0,
        "targets_present": 0,
        "targets_missing": 0,
        "sources_lingering": 0,
    }

    report_lines.append(
        f"1111B BLUEPRINT COMPLIANCE REPORT — generated {datetime.now(UTC).isoformat()}"
    )
    report_lines.append(f"Root folder_id: {root_id}")
    report_lines.append("")

    # Total descendant count.
    counters["actual_total"] = _count_all_descendants(client, root_id)
    total_pass = counters["actual_total"] == EXPECTED_TOTAL_FOLDER_COUNT
    report_lines.append(
        f"[{'PASS' if total_pass else 'FAIL'}] Total folder count: "
        f"{counters['actual_total']} (expected {EXPECTED_TOTAL_FOLDER_COUNT})"
    )
    report_lines.append("")

    # Each rename map entry's target should now be present.
    report_lines.append("Per-folder rename verification:")
    for (parent_path, current_name), target_name in RENAME_MAP.items():
        parent_id = _resolve_path(client, root_id, parent_path)
        if parent_id is None:
            report_lines.append(
                f"  [FAIL] parent path {parent_path!r} unresolved (was renamed "
                f"with source {current_name!r} -> target {target_name!r})"
            )
            counters["targets_missing"] += 1
            continue
        target_id = _find_child(client, parent_id, target_name)
        if target_id is None:
            # Maybe source is lingering (rename didn't happen)?
            source_id = _find_child(client, parent_id, current_name)
            if source_id is not None:
                report_lines.append(
                    f"  [FAIL] {parent_path!r} still has SOURCE name "
                    f"{current_name!r} — rename {target_name!r} did not happen"
                )
                counters["sources_lingering"] += 1
            else:
                report_lines.append(
                    f"  [FAIL] {parent_path!r} missing both source {current_name!r} "
                    f"and target {target_name!r}"
                )
                counters["targets_missing"] += 1
        else:
            report_lines.append(
                f"  [PASS] {parent_path!r}/{target_name!r} (folder_id={target_id})"
            )
            counters["targets_present"] += 1

    report_lines.append("")
    report_lines.append("Summary:")
    report_lines.append(f"  Targets present: {counters['targets_present']}")
    report_lines.append(f"  Targets missing: {counters['targets_missing']}")
    report_lines.append(f"  Source names lingering: {counters['sources_lingering']}")
    report_lines.append(
        f"  Total folders: {counters['actual_total']} "
        f"(expected {EXPECTED_TOTAL_FOLDER_COUNT})"
    )

    all_pass = (
        total_pass
        and counters["targets_missing"] == 0
        and counters["sources_lingering"] == 0
    )
    report_lines.append("")
    report_lines.append(f"OVERALL: {'PASS' if all_pass else 'FAIL'}")
    report = "\n".join(report_lines) + "\n"
    return all_pass, report, counters


def _write_report(report: str) -> None:
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(report)
    log.info("compliance report written to %s", REPORT_PATH)


# ---- CLI / main --------------------------------------------------------


@its_error_log(SCRIPT_NAME)
@require_active
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog=SCRIPT_NAME,
        description=(
            "Materialize the 1111B canonical Box blueprint in the mirror "
            "tenant. Idempotent: re-runs are safe — clone is skipped if "
            "1111B exists; renames are skipped if folders are already at "
            "their target names."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Log planned operations without making any writes.",
    )
    parser.add_argument(
        "--verify-only",
        action="store_true",
        help="Run only the compliance verification against an existing 1111B; "
             "no clone, no renames.",
    )
    args = parser.parse_args(argv)

    _configure_logging()

    client = box_client.get_client()

    if args.verify_only:
        log.info("--verify-only: skipping clone + rename phases")
        existing = _find_child(client, PARENT_FOLDER_ID, TARGET_1111B_NAME)
        if existing is None:
            log.error(
                "%s does not exist under parent %s — nothing to verify",
                TARGET_1111B_NAME,
                PARENT_FOLDER_ID,
            )
            return 1
        passed, report, _ = verify_blueprint(client, existing)
        print(report)
        _write_report(report)
        return 0 if passed else 1

    log.info(
        "starting 1111B materialization (dry_run=%s)",
        args.dry_run,
    )

    # Phase 1: clone (idempotent).
    root_id = ensure_1111b_clone(client, dry_run=args.dry_run)

    # Phase 2: walk-and-rename.
    if root_id == "(dry-run)":
        # Dry-run mode pre-clone — enumerate would-be renames from the map.
        log.info("[dry-run] would apply %s renames", len(RENAME_MAP))
        for (parent_path, current_name), target_name in RENAME_MAP.items():
            log.info(
                "[dry-run]  %r/%r -> %r",
                parent_path,
                current_name,
                target_name,
            )
        log.info("[dry-run] verification skipped (1111B not yet cloned)")
        return 0

    rename_counters = apply_renames(client, root_id, dry_run=args.dry_run)
    log.info(
        "rename pass: renamed=%s already_renamed=%s source_missing=%s parent_unresolved=%s",
        rename_counters["renamed"],
        rename_counters["already_renamed"],
        rename_counters["source_missing"],
        rename_counters["parent_unresolved"],
    )

    if args.dry_run:
        log.info("[dry-run] verification skipped (no live writes made)")
        return 0

    # Phase 3: verify.
    passed, report, _ = verify_blueprint(client, root_id)
    print(report)
    _write_report(report)
    if not passed:
        log.error("compliance verification FAILED — see %s", REPORT_PATH)
        return 1
    log.info("compliance verification PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
