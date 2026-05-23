"""Smartsheet workspace, folder, and sheet IDs for the ITS sandbox.

Bootstrap module. Holds the static IDs that shared/* modules need at startup
before they can read ITS_Config. Dynamic per-workstream config (allowlists,
reviewer chains, notification recipients, etc.) lives in ITS_Config itself
and is read via shared.smartsheet_client once wired.

Provisioned 2026-05-17 evening — see
docs/session_logs/2026-05-17_smartsheet_workspace_restructure.md for the
full narrative of what was created, moved, and deleted.

These are this customer's workspace/folder/sheet IDs. ITS is a white-glove
custom-development practice — each customer gets a private repo forked from
the blueprint and these values are replaced in-place for that customer. The
module shape (static workspace/folder/sheet identifiers) is the framework
default; the values are this deployment's reality.
"""
from __future__ import annotations

# ---- Workspaces ----------------------------------------------------------

WORKSPACE_DEMO         = 4129485730670468  # Forefront Portfolio — ITS Demo (customer-facing)
WORKSPACE_SYSTEM       = 680592632244100   # ITS — System (operator-only)
WORKSPACE_HUMAN_REVIEW = 8561891980142468  # ITS — Human Review (Evergreen-facing)

# Workspaces (added 2026-05-20)
WORKSPACE_OPERATIONS = 7217130472007556
WORKSPACE_ARCHIVE = 5528280611743620

# ---- Portfolio sub-folders ----------------------------------------------

FOLDER_ACTIVE_PROJECTS = 5819628569028484
FOLDER_PORTFOLIO_ROLLUPS = 8071428382713732
FOLDER_FIELD_REPORTS = 705799988242308

# ---- Operations + Archive sub-folders -----------------------------------

FOLDER_OPERATIONS_MASTER_DBS = 471604011526020
FOLDER_ARCHIVE_CLOSED_PROJECTS = 1034553964947332

# ---- Active project folders (Forefront Portfolio / 01 — Active Projects) -

# TODO(post-1111B): when 1111B replaces 1111A as canonical, regenerate
# FOLDER_PROJECT_* constants from the new clones. See
# docs/session_logs/2026-05-22_box_blueprint_1111b_design.md for blueprint.
FOLDER_PROJECT_BRADLEY_1 = 8025248894347140
FOLDER_PROJECT_BRADLEY_2 = 5210499127240580
FOLDER_PROJECT_BRIMFIELD_1 = 7462298940925828
FOLDER_PROJECT_BRIMFIELD_2 = 7180823964215172
FOLDER_PROJECT_HUNTLEY = 8306723871057796
FOLDER_PROJECT_ROCKFORD = 6828980243326852

# ---- Field Reports project subfolders -----------------------------------
# Forefront Portfolio / 03 — Field Reports / <project>

FOLDER_FIELD_REPORTS_BRADLEY_1 = 2957599801927556
FOLDER_FIELD_REPORTS_BRADLEY_2 = 4083499708770180
FOLDER_FIELD_REPORTS_BRIMFIELD_1 = 987274964952964
FOLDER_FIELD_REPORTS_BRIMFIELD_2 = 2113174871795588
FOLDER_FIELD_REPORTS_HUNTLEY = 4364974685480836
FOLDER_FIELD_REPORTS_ROCKFORD = 5139030871435140

# ---- ITS — System folders -----------------------------------------------

FOLDER_SYSTEM_CONFIG  = 164788727768964   # 01 — Config
FOLDER_SYSTEM_LOGS    = 5231338308560772  # 02 — Logs
FOLDER_SYSTEM_QUEUES  = 7201663145535364  # 03 — Queues
FOLDER_SYSTEM_DAEMONS = 2130046845511556  # 04 — Daemons

# ---- ITS — Human Review folders -----------------------------------------

FOLDER_HR_SAFETY_REPORTS              = 2486957285631876  # 01 — Safety Reports
FOLDER_HR_SUBCONTRACTS                = 1924007332210564  # 02 — Subcontracts
FOLDER_HR_PURCHASE_ORDERS_AND_MATERIALS = 2768432262342532  # 03 — Purchase Orders & Materials
FOLDER_HR_EMAIL_TRIAGE                = 8960881749976964  # 04 — Email Triage
FOLDER_HR_AI_EMPLOYEE                 = 1185135518345092  # 05 — AI Employee
FOLDER_HR_PERSONNEL                   = 7377585005979524  # 06 — Personnel

# ---- System sheets -------------------------------------------------------

SHEET_CONFIG              = 3072320166907780  # ITS — System / 01 — Config / ITS_Config
SHEET_PICKLIST_SYNC_CONFIG = 7486553185013636  # ITS — System / 01 — Config / Picklist_Sync_Config
SHEET_ERRORS              = 27291433258884    # ITS — System / 02 — Logs / ITS_Errors
SHEET_QUARANTINE          = 8687740798324612  # ITS — System / 02 — Logs / ITS_Quarantine
SHEET_REVIEW_QUEUE        = 7243317526876036  # ITS — System / 03 — Queues / ITS_Review_Queue
SHEET_DAEMON_HEALTH       = 4529351700729732  # ITS — System / 04 — Daemons / ITS_Daemon_Health

# ITS_Daemon_Health column IDs (PR #59.5). Operator-visible heartbeat sheet
# written per poll cycle by each daemon. Source IDs are stable across column
# renames, so heartbeat writes pin them here rather than going through
# title-based resolution. See safety_reports/intake_poll.py:_write_heartbeat_row
# for the canonical consumer and safety_reports/README.md for the operator
# read-side runbook. Schema brief (ITS_Daemon_Health_Schema_2026-05-21): 12
# columns capturing daemon identity, current run state, and last-error context.
DAEMON_HEALTH_COLUMNS: dict[str, int] = {
    "daemon_name":                  817803644145540,
    "workstream":                  5321403271516036,
    "enabled":                     3069603457830788,
    "interval_seconds":            7573203085201284,
    "source_id":                   1943703550988164,
    "last_heartbeat":              6447303178358660,
    "last_cycle_status":           4195503364673412,
    "last_cycle_items_processed":  8699102992043908,
    # `total_cycles` is the lifetime monotonic counter (PR #59.5 ARCH-3).
    # The Smartsheet column title is "Total Cycles Today" but the semantics
    # were changed to lifetime monotonic to avoid a read-before-write round
    # trip per cycle for an informational field. The column-title rename
    # is a separate UI-only cleanup; the ID below is stable across that.
    "total_cycles":                 536328667434884,
    "last_error_summary":          5039928294805380,
    "last_error_correlation_id":   2788128481120132,
    "notes":                       7291728108490628,
}

# ---- Human-review sheets -------------------------------------------------

SHEET_WPR_PENDING_REVIEW = 3096105695793028  # ITS — Human Review / 01 — Safety Reports / WPR_Pending_Review
SHEET_TIME_OFF           = 1506418040459140  # ITS — Human Review / 06 — Personnel / ITS_Time_Off

# ---- Master DB sheets (ITS — Operations / Master Databases) -------------
# Canonical sources for shared/picklist_sync.py. Vendor + Subcontractor
# stubs seeded from Bradley 1 FL parse 2026-05-17.

SHEET_VENDOR_DB        = 7278304330469252  # ITS — Operations / Master Databases / Vendor DB
SHEET_SUBCONTRACTOR_DB = 1230913068289924  # ITS — Operations / Master Databases / Subcontractor DB
SHEET_EQUIPMENT_MASTER = 4132885031243652  # ITS — Operations / Master Databases / Equipment Master


# ---- Reverse-lookup maps ------------------------------------------------

PROJECT_NAME_BY_FOLDER_ID: dict[int, str] = {
    FOLDER_PROJECT_BRADLEY_1: "Bradley 1",
    FOLDER_PROJECT_BRADLEY_2: "Bradley 2",
    FOLDER_PROJECT_BRIMFIELD_1: "Brimfield 1",
    FOLDER_PROJECT_BRIMFIELD_2: "Brimfield 2",
    FOLDER_PROJECT_HUNTLEY: "Huntley",
    FOLDER_PROJECT_ROCKFORD: "Rockford",
}

FIELD_REPORTS_FOLDER_BY_PROJECT: dict[str, int] = {
    "Bradley 1": FOLDER_FIELD_REPORTS_BRADLEY_1,
    "Bradley 2": FOLDER_FIELD_REPORTS_BRADLEY_2,
    "Brimfield 1": FOLDER_FIELD_REPORTS_BRIMFIELD_1,
    "Brimfield 2": FOLDER_FIELD_REPORTS_BRIMFIELD_2,
    "Huntley": FOLDER_FIELD_REPORTS_HUNTLEY,
    "Rockford": FOLDER_FIELD_REPORTS_ROCKFORD,
}
