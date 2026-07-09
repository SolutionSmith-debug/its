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

# ITS –– Safety Portal — standalone workspace (2026-06-05). The "Safety Portal"
# folder (ITS_Active_Jobs + ITS_Forms_Catalog + WSR_human_review) was MOVED here
# from ITS — Operations with IDs preserved (amendment b). Workspace access =
# approval authority — sharing it is the send gate.
WORKSPACE_SAFETY_PORTAL = 194283417429892

# ITS — Progress Reporting — standalone workspace (2026-06-30, P2). The structural
# twin of the Safety Portal: an ITS-OWNED Smartsheet system-of-record ITS creates
# and writes (Op Stds v19 §51 — ITS-owned structured-SoR write-back). Like the
# Safety Portal it sits OUTSIDE the §23 audience-separation model and is governed by
# §46 — workspace membership = approval authority: the safety approvers are re-shared
# here so they may approve WPR_human_review rows (P5-blocking operator prereq).
# FLIP precedes SEED — flip the real ID after scripts/migrations/build_progress_reporting_workspace.py prints it.
WORKSPACE_PROGRESS_REPORTING = 5988851429730180  # ITS — Progress Reporting (created 2026-06-29 by build_progress_reporting_workspace.py)

# ---- Portfolio sub-folders ----------------------------------------------

FOLDER_ACTIVE_PROJECTS = 5819628569028484
FOLDER_PORTFOLIO_ROLLUPS = 8071428382713732
FOLDER_FIELD_REPORTS = 705799988242308

# ---- Operations + Archive sub-folders -----------------------------------

FOLDER_OPERATIONS_MASTER_DBS = 471604011526020
# Safety Portal folder — MOVED 2026-06-05 to the standalone ITS –– Safety Portal
# workspace (WORKSPACE_SAFETY_PORTAL); folder ID preserved (amendment b).
FOLDER_SAFETY_PORTAL = 6663869084002180  # ITS –– Safety Portal / Safety Portal (ITS_Active_Jobs, ITS_Forms_Catalog, WSR_human_review)
FOLDER_OPERATIONS_SAFETY_PORTAL = FOLDER_SAFETY_PORTAL  # back-compat alias (name retains the pre-move location)
# "Closed Projects" folder lives in the ITS — Archive workspace (WORKSPACE_ARCHIVE),
# verified live 2026-07-04; the §51 archive-on-closure path moves closed-job tracker
# sheets here. (Earlier comment placed it in WORKSPACE_SAFETY_PORTAL — that was wrong.)
FOLDER_ARCHIVE_CLOSED_PROJECTS = 1034553964947332

# ---- Active project folders (Forefront Portfolio / 01 — Active Projects) -
#
# These are Smartsheet folder IDs (NOT Box). Smartsheet folder structure
# is independent of Box's 1111A→1111B cutover; these constants stay
# unchanged across the canonical-blueprint flip. The 1111B-affected Box
# folder IDs live in `shared/defaults.py BOX_PROJECT_FOLDERS`.
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
SHEET_TRUSTED_CONTACTS    = 0                 # ITS — System / 01 — Config / ITS_Trusted_Contacts (OPERATOR: fill in after running scripts/migrations/build_its_trusted_contacts_sheet.py)
SHEET_PROJECT_ROUTING     = 3500842291253124  # ITS — System / 01 — Config / ITS_Project_Routing (E1 cutover 2026-06-03; built by scripts/migrations/build_its_project_routing_sheet.py, seeded from BOX_PROJECT_FOLDERS)
SHEET_ERRORS              = 27291433258884    # ITS — System / 02 — Logs / ITS_Errors
SHEET_QUARANTINE          = 8687740798324612  # ITS — System / 02 — Logs / ITS_Quarantine
SHEET_REVIEW_QUEUE        = 7243317526876036  # ITS — System / 03 — Queues / ITS_Review_Queue
SHEET_DAEMON_HEALTH       = 4529351700729732  # ITS — System / 04 — Daemons / ITS_Daemon_Health

# ITS_Daemon_Health column IDs (PR #59.5). Operator-visible heartbeat sheet
# written per poll cycle by each daemon. Source IDs are stable across column
# renames, so heartbeat writes pin them here rather than going through
# title-based resolution. See shared/heartbeat.py (HeartbeatReporter)
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

# DECOMMISSIONED 2026-06-05, fully repointed 2026-06-05 (PR4) — superseded by
# WSR_human_review (SHEET_WSR_HUMAN_REVIEW) for the portal flow. NO live RUNTIME code
# references this any more: weekly_generate (PR3), weekly_send + weekly_send_poll +
# watchdog Check I (PR4) all read/write WSR_human_review now. The only remaining refs
# are operator smoke scripts (smoke_test_watchdog_catchup), the picklist_validation
# registry entry, and a couple of tests — kept until the operator deletes the WPR
# SHEET, after which this constant + the picklist entry are a trivial follow-up
# removal. See docs/tech_debt.md.
SHEET_WPR_PENDING_REVIEW = 3096105695793028  # ITS — Human Review / 01 — Safety Reports / WPR_Pending_Review (decommissioned)
SHEET_TIME_OFF           = 1506418040459140  # ITS — Human Review / 06 — Personnel / ITS_Time_Off

# ---- Master DB sheets (ITS — Operations / Master Databases) -------------
# Canonical sources for shared/picklist_sync.py. Vendor + Subcontractor
# stubs seeded from Bradley 1 FL parse 2026-05-17.

# DECOMMISSIONED 2026-07-09 (PO S1): ITS_Vendors (SHEET_ITS_VENDORS, ITS — Purchase
# Orders / Control) is the SOLE vendor source of record. This old Operations stub
# sheet is retired-in-place — rows one-time-copied by scripts/migrations/
# seed_its_vendors.py; ZERO Picklist_Sync_Config mappings referenced it (verified
# live 2026-07-09), so nothing re-points. Constant retained ONLY for the seed's
# one-time copy — do not add new readers or writers.
SHEET_VENDOR_DB        = 7278304330469252  # ITS — Operations / Master Databases / Vendor DB (DECOMMISSIONED — see above)
SHEET_SUBCONTRACTOR_DB = 1230913068289924  # ITS — Operations / Master Databases / Subcontractor DB
SHEET_EQUIPMENT_MASTER = 4132885031243652  # ITS — Operations / Master Databases / Equipment Master

# ---- Safety Portal sheets (ITS –– Safety Portal / Safety Portal) ---------
# The Smartsheet inputs + the Phase-5 review surface for the Safety Portal flow.
# The folder MOVED to the standalone ITS –– Safety Portal workspace 2026-06-05
# (amendment b; IDs preserved). OPERATOR: flip a 0 placeholder after the matching
# build migration prints the real ID (FLIP precedes SEED).
SHEET_ACTIVE_JOBS   = 6223950341164932  # ITS_Active_Jobs   (built 2026-06-03 by build_its_active_jobs_sheet.py)
SHEET_FORMS_CATALOG = 423274885369732   # ITS_Forms_Catalog (built 2026-06-03 by build_its_forms_catalog_sheet.py)
SHEET_WSR_HUMAN_REVIEW = 5035670127988612  # WSR_human_review — Phase-5 weekly review/approve/send surface (amendment b; built 2026-06-05 by build_wsr_human_review_sheet.py). Supersedes WPR_Pending_Review for the portal flow.
SHEET_ORPHANED_REPORTS = 2577084374273924  # Orphaned Reports (Part C; built 2026-06-09 by build_orphaned_reports_sheet.py) — job_not_found/job_inactive portal submissions route here (ON) once ~/its is redeployed to this commit. ITS –– Safety Portal folder.

# ---- Progress Reporting sheets (ITS — Progress Reporting / Control) ------
# The cross-job control surfaces for the Progress Reporting flow — the structural
# twin of the Safety Portal sheets above. The "Control" folder holds the only two
# cross-job sheets; per-<Job> folders + per-week sheets are RUNTIME find-or-create
# (A1 margin-checked), never pre-wired here (same dynamic-discovery model as the
# safety week sheets). OPERATOR: flip each 0 after build_progress_reporting_workspace.py
# prints the real ID (FLIP precedes SEED).
FOLDER_PROGRESS_CONTROL = 2747740519196548  # ITS — Progress Reporting / Control (holds WPR_human_review + ITS_Active_Jobs_Progress)
SHEET_WPR_HUMAN_REVIEW = 2798573438586756       # WPR_human_review — weekly progress review/approve/send surface (mirrors WSR_human_review; created 2026-06-29). NOTE: distinct from the decommissioned SHEET_WPR_PENDING_REVIEW above.
SHEET_ACTIVE_JOBS_PROGRESS = 3079764947455876   # ITS_Active_Jobs_Progress — the progress workspace's own physical Active-Jobs sheet (job-tracker pivot, P2.5 Slice 4; created 2026-06-29). Carries Progress Reports Contact/CC + a Portal Job Key bridge column.

# ---- Purchase Orders sheets (ITS — Purchase Orders / Control) ------------
# WS1 S1 (Aug-7 delivery program). The EIGHTH standalone workspace — outside the
# §23 audience-separation model, governed by §46: workspace membership = approval
# authority (the share list IS the approver set the F22 gate verifies before
# po_send dispatches; decision D11). The "Control" folder holds the three
# cross-job PO sheets; PO PDFs live in Box. ITS_Vendors is the SOLE vendor SoR
# (supersedes SHEET_VENDOR_DB above); PO_Log MIRRORS the authoritative D1 PO
# store; PO_Pending_Review is a WSR schema twin (S5 engine binds by title).
# OPERATOR: flip each 0 after the matching builder prints the real ID (FLIP
# precedes SEED — seed_its_vendors.py refuses to run while SHEET_ITS_VENDORS=0).
WORKSPACE_PURCHASE_ORDERS = 6191118619568004  # ITS — Purchase Orders (created 2026-07-09 by build_purchase_orders_workspace.py)
FOLDER_PO_CONTROL = 6619259473291140          # ITS — Purchase Orders / Control
SHEET_ITS_VENDORS = 5404286845407108        # ITS_Vendors — vendor SoR (created 2026-07-09 by build_its_vendors_sheet.py)
SHEET_PO_LOG = 3152487031721860             # PO_Log — operator-visible ledger mirror of D1 (created 2026-07-09 by build_po_log_sheet.py)
SHEET_PO_PENDING_REVIEW = 1816168087113604  # PO_Pending_Review — PO review/approve/send surface (created 2026-07-09 by build_po_pending_review_sheet.py)


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
