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

WORKSPACE_DEMO         = 6153011522234244  # Forefront Portfolio — ITS Demo (customer-facing)
WORKSPACE_SYSTEM       = 2730369263921028   # ITS — System (operator-only)
WORKSPACE_HUMAN_REVIEW = 3056786778417028  # ITS — Human Review (Evergreen-facing)

# Workspaces (added 2026-05-20)
WORKSPACE_OPERATIONS = 5027111615391620
WORKSPACE_ARCHIVE = 1649411894863748

# ITS –– Safety Portal — standalone workspace (2026-06-05). The "Safety Portal"
# folder (ITS_Active_Jobs + ITS_Forms_Catalog + WSR_human_review) was MOVED here
# from ITS — Operations with IDs preserved (amendment b). Workspace access =
# approval authority — sharing it is the send gate.
WORKSPACE_SAFETY_PORTAL = 6820552519247748

# ITS — Progress Reporting — standalone workspace (2026-06-30, P2). The structural
# twin of the Safety Portal: an ITS-OWNED Smartsheet system-of-record ITS creates
# and writes (Op Stds v19 §51 — ITS-owned structured-SoR write-back). Like the
# Safety Portal it sits OUTSIDE the §23 audience-separation model and is governed by
# §46 — workspace membership = approval authority: the safety approvers are re-shared
# here so they may approve WPR_human_review rows (P5-blocking operator prereq).
# FLIP precedes SEED — flip the real ID after scripts/migrations/build_progress_reporting_workspace.py prints it.
WORKSPACE_PROGRESS_REPORTING = 171668267132804  # ITS — Progress Reporting (created 2026-06-29 by build_progress_reporting_workspace.py)

# ---- Portfolio sub-folders ----------------------------------------------

FOLDER_ACTIVE_PROJECTS = 6515532254996356
FOLDER_PORTFOLIO_ROLLUPS = 3841519976245124
FOLDER_FIELD_REPORTS = 1026770209138564

# ---- Operations + Archive sub-folders -----------------------------------

FOLDER_OPERATIONS_MASTER_DBS = 6550716627085188
# Safety Portal folder — MOVED 2026-06-05 to the standalone ITS –– Safety Portal
# workspace (WORKSPACE_SAFETY_PORTAL); folder ID preserved (amendment b).
FOLDER_SAFETY_PORTAL = 2261538947000196  # ITS –– Safety Portal / 00_Safety Portal (ITS_Active_Jobs, WSR_human_review, Orphaned Reports)
FOLDER_OPERATIONS_SAFETY_PORTAL = FOLDER_SAFETY_PORTAL  # back-compat alias (name retains the pre-move location)
# ITS_Forms_Catalog does NOT live beside the three sheets above — it has its own
# second folder in the same workspace (browsed live 2026-07-21; both folders are
# provisioned by scripts/migrations/build_safety_portal_workspace.py).
FOLDER_FORM_CATALOG = 6765138574370692  # ITS –– Safety Portal / 00_Form Catalog (ITS_Forms_Catalog)
# "Closed Projects" folder lives in the ITS — Archive workspace (WORKSPACE_ARCHIVE),
# verified live 2026-07-04; the §51 archive-on-closure path moves closed-job tracker
# sheets here. (Earlier comment placed it in WORKSPACE_SAFETY_PORTAL — that was wrong.)
FOLDER_ARCHIVE_CLOSED_PROJECTS = 4545207418021764

# ---- Active project folders (Forefront Portfolio / 01 — Active Projects) -
#
# These are Smartsheet folder IDs (NOT Box). Smartsheet folder structure
# is independent of Box's 1111A→1111B cutover; these constants stay
# unchanged across the canonical-blueprint flip. The 1111B-affected Box
# folder IDs live in `shared/defaults.py BOX_PROJECT_FOLDERS`.
FOLDER_PROJECT_BRADLEY_1 = 8767332068681604
FOLDER_PROJECT_BRADLEY_2 = 5811844813219716
FOLDER_PROJECT_BRIMFIELD_1 = 8063644626904964
FOLDER_PROJECT_BRIMFIELD_2 = 8626594580326276
FOLDER_PROJECT_HUNTLEY = 4967419883087748
FOLDER_PROJECT_ROCKFORD = 1589720162559876

# ---- Field Reports project subfolders -----------------------------------
# Forefront Portfolio / 03 — Field Reports / <project>

FOLDER_FIELD_REPORTS_BRADLEY_1 = 2152670115981188
FOLDER_FIELD_REPORTS_BRADLEY_2 = 6867375975884676
FOLDER_FIELD_REPORTS_BRIMFIELD_1 = 5741476069042052
FOLDER_FIELD_REPORTS_BRIMFIELD_2 = 7993275882727300
FOLDER_FIELD_REPORTS_HUNTLEY = 5178526115620740
FOLDER_FIELD_REPORTS_ROCKFORD = 6304426022463364

# ---- ITS — System folders -----------------------------------------------

FOLDER_SYSTEM_CONFIG  = 1775005051709316   # 01 — Config
FOLDER_SYSTEM_LOGS    = 6278604679079812  # 02 — Logs
FOLDER_SYSTEM_QUEUES  = 4026804865394564  # 03 — Queues
FOLDER_SYSTEM_DAEMONS = 4871229795526532  # 04 — Daemons

# ---- ITS — Human Review folders -----------------------------------------

FOLDER_HR_SAFETY_REPORTS              = 639742116161412  # 01 — Safety Reports
FOLDER_HR_SUBCONTRACTS                = 6269241650374532  # 02 — Subcontracts
FOLDER_HR_PURCHASE_ORDERS_AND_MATERIALS = 8521041464059780  # 03 — Purchase Orders & Materials
FOLDER_HR_EMAIL_TRIAGE                = 2610066953136004  # 04 — Email Triage
FOLDER_HR_AI_EMPLOYEE                 = 1484167046293380  # 05 — AI Employee
FOLDER_HR_PERSONNEL                   = 3735966859978628  # 06 — Personnel

# ---- System sheets -------------------------------------------------------

SHEET_CONFIG              = 8933909738770308  # ITS — System / 01 — Config / ITS_Config
SHEET_PICKLIST_SYNC_CONFIG = 8242420004114308  # ITS — System / 01 — Config / Picklist_Sync_Config
SHEET_TRUSTED_CONTACTS    = 0                 # ITS — System / 01 — Config / ITS_Trusted_Contacts (OPERATOR: fill in after running scripts/migrations/build_its_trusted_contacts_sheet.py)
SHEET_PROJECT_ROUTING     = 1807356403863428  # ITS — System / 01 — Config / ITS_Project_Routing (E1 cutover 2026-06-03; built by scripts/migrations/build_its_project_routing_sheet.py, seeded from BOX_PROJECT_FOLDERS)
SHEET_ERRORS              = 8015637140950916    # ITS — System / 02 — Logs / ITS_Errors
SHEET_QUARANTINE          = 137816716562308  # ITS — System / 02 — Logs / ITS_Quarantine
SHEET_REVIEW_QUEUE        = 7451476006752132  # ITS — System / 03 — Queues / ITS_Review_Queue
SHEET_DAEMON_HEALTH       = 697287746473860  # ITS — System / 04 — Daemons / ITS_Daemon_Health

# ITS_Daemon_Health column IDs (PR #59.5). Operator-visible heartbeat sheet
# written per poll cycle by each daemon. Source IDs are stable across column
# renames, so heartbeat writes pin them here rather than going through
# title-based resolution. See shared/heartbeat.py (HeartbeatReporter)
# for the canonical consumer and safety_reports/README.md for the operator
# read-side runbook. Schema brief (ITS_Daemon_Health_Schema_2026-05-21): 12
# columns capturing daemon identity, current run state, and last-error context.
DAEMON_HEALTH_COLUMNS: dict[str, int] = {
    "daemon_name":                  8245226804383620,
    "workstream":                  926877409906564,
    "enabled":                     5430477037277060,
    "interval_seconds":            3178677223591812,
    "source_id":                   7682276850962308,
    "last_heartbeat":              2052777316749188,
    "last_cycle_status":           6556376944119684,
    "last_cycle_items_processed":  4304577130434436,
    # `total_cycles` is the lifetime monotonic counter (PR #59.5 ARCH-3).
    # The Smartsheet column title is "Total Cycles Today" but the semantics
    # were changed to lifetime monotonic to avoid a read-before-write round
    # trip per cycle for an informational field. The column-title rename
    # is a separate UI-only cleanup; the ID below is stable across that.
    "total_cycles":                 8808176757804932,
    "last_error_summary":          223189968129924,
    "last_error_correlation_id":   4726789595500420,
    "notes":                       2474989781815172,
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
SHEET_WPR_PENDING_REVIEW = 8489603961933700  # ITS — Human Review / 01 — Safety Reports / WPR_Pending_Review (decommissioned)
SHEET_TIME_OFF           = 5992784853946244  # ITS — Human Review / 06 — Personnel / ITS_Time_Off

# ---- Master DB sheets (ITS — Operations / Master Databases) -------------
# Canonical sources for shared/picklist_sync.py. Vendor + Subcontractor
# stubs seeded from Bradley 1 FL parse 2026-05-17.

# DECOMMISSIONED 2026-07-09 (PO S1): ITS_Vendors (SHEET_ITS_VENDORS, ITS — Purchase
# Orders / Control) is the SOLE vendor source of record. This old Operations stub
# sheet is retired-in-place — rows one-time-copied by scripts/migrations/
# seed_its_vendors.py; ZERO Picklist_Sync_Config mappings referenced it (verified
# live 2026-07-09), so nothing re-points. Constant retained ONLY for the seed's
# one-time copy — do not add new readers or writers.
SHEET_VENDOR_DB        = 2933256310706052  # ITS — Operations / Master Databases / Vendor DB (DECOMMISSIONED — see above)
SHEET_SUBCONTRACTOR_DB = 326829637324676  # ITS — Operations / Master Databases / Subcontractor DB
SHEET_EQUIPMENT_MASTER = 7436855938076548  # ITS — Operations / Master Databases / Equipment Master

# ---- Safety Portal sheets (ITS –– Safety Portal / Safety Portal) ---------
# The Smartsheet inputs + the Phase-5 review surface for the Safety Portal flow.
# The folder MOVED to the standalone ITS –– Safety Portal workspace 2026-06-05
# (amendment b; IDs preserved). OPERATOR: flip a 0 placeholder after the matching
# build migration prints the real ID (FLIP precedes SEED).
SHEET_ACTIVE_JOBS   = 5623091248975748  # ITS_Active_Jobs   (built 2026-06-03 by build_its_active_jobs_sheet.py)
SHEET_FORMS_CATALOG = 5342578344939396   # ITS_Forms_Catalog (built 2026-06-03 by build_its_forms_catalog_sheet.py)
SHEET_WSR_HUMAN_REVIEW = 559548145291140  # WSR_human_review — Phase-5 weekly review/approve/send surface (amendment b; built 2026-06-05 by build_wsr_human_review_sheet.py). Supersedes WPR_Pending_Review for the portal flow.
SHEET_ORPHANED_REPORTS = 563087198343044  # Orphaned Reports (Part C; built 2026-06-09 by build_orphaned_reports_sheet.py) — job_not_found/job_inactive portal submissions route here (ON) once ~/its is redeployed to this commit. ITS –– Safety Portal folder.

# ---- Progress Reporting sheets (ITS — Progress Reporting / Control) ------
# The cross-job control surfaces for the Progress Reporting flow — the structural
# twin of the Safety Portal sheets above. The "Control" folder holds the only two
# cross-job sheets; per-<Job> folders + per-week sheets are RUNTIME find-or-create
# (A1 margin-checked), never pre-wired here (same dynamic-discovery model as the
# safety week sheets). OPERATOR: flip each 0 after build_progress_reporting_workspace.py
# prints the real ID (FLIP precedes SEED).
FOLDER_PROGRESS_CONTROL = 6352804534085508  # ITS — Progress Reporting / Control (holds WPR_human_review + ITS_Active_Jobs_Progress)
SHEET_WPR_HUMAN_REVIEW = 471776193630084       # WPR_human_review — weekly progress review/approve/send surface (mirrors WSR_human_review; created 2026-06-29). NOTE: distinct from the decommissioned SHEET_WPR_PENDING_REVIEW above.
SHEET_ACTIVE_JOBS_PROGRESS = 4975375821000580   # ITS_Active_Jobs_Progress — the progress workspace's own physical Active-Jobs sheet (job-tracker pivot, P2.5 Slice 4; created 2026-06-29). Carries Progress Reports Contact/CC + a Portal Job Key bridge column.

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
WORKSPACE_PURCHASE_ORDERS = 1860518127396740  # ITS — Purchase Orders (created 2026-07-09 by build_purchase_orders_workspace.py)
FOLDER_PO_CONTROL = 5262088999331716          # ITS — Purchase Orders / Control
FOLDER_PO_JOBS = 7184035324684164             # ITS — Purchase Orders / Jobs — parent of the DYNAMIC per-job tracking folders (shared/job_sheet.py find-or-creates "<job>/Purchase Orders" sheets under it; created 2026-07-13 by build_job_folders.py)
SHEET_ITS_VENDORS = 501553201893252        # ITS_Vendors — vendor SoR (created 2026-07-09 by build_its_vendors_sheet.py)
SHEET_PO_LOG = 8211994455789444             # PO_Log — operator-visible ledger mirror of D1 (created 2026-07-09 by build_po_log_sheet.py)
SHEET_PO_PENDING_REVIEW = 1734204520877956  # PO_Pending_Review — PO review/approve/send surface (created 2026-07-09 by build_po_pending_review_sheet.py)
SHEET_ESTIMATE_LOG = 7223859919933316       # Estimate_Log — vendor-estimate importer ledger (ADR-0004 E2; one row per uploaded estimate). Seeded 2026-07-19 (builder-precedes-seed complete; estimate_log.py refused writes while 0).
SHEET_RFQ_LOG = 223870681304964            # RFQ_Log — outbound-RFQ ledger, one row per (rfq, vendor) (ADR-0004 R2). Seeded 2026-07-19 (builder-precedes-seed complete; rfq_log.py refused writes while 0).
SHEET_RFQ_PENDING_REVIEW = 1176666226249604 # RFQ_Pending_Review — RFQ review/approve/send surface, one row per (rfq, vendor); PO_Pending_Review schema twin, Workstream tag 'po_materials_rfq' (ADR-0004 R2/decision 12). Seeded 2026-07-19 (builder-precedes-seed complete; rfq_review.py refused writes while 0).

# Subcontracts workstream (S1) — mirrors the PO trio in a standalone ITS — Subcontracts workspace
# (§46 workspace-membership = approval authority). ITS_Subcontractors is the party SoR (supersedes the
# legacy SHEET_SUBCONTRACTOR_DB two-column stub above, retired-in-place like the old Vendor DB);
# Subcontract_Log MIRRORS the authoritative D1 `subcontracts` store; Subcontract_Pending_Review is a
# WSR schema twin (the shared weekly_send engine binds by title). Placeholder 0 until the operator runs
# each builder (FLIP precedes SEED — the seeder refuses to run while the ID is 0; picklist_validation
# registers these sheets only when non-zero, the same guard as the PO/vendor sheets).
WORKSPACE_SUBCONTRACTS = 8545548824274820            # ITS — Subcontracts (build_subcontracts_workspace.py)
FOLDER_SC_CONTROL = 8138411417593732                 # ITS — Subcontracts / Control
FOLDER_SC_JOBS = 428635883628420                    # ITS — Subcontracts / Jobs — parent of the DYNAMIC per-job tracking folders (shared/job_sheet.py find-or-creates "<job>/Subcontracts" sheets under it; created 2026-07-13 by build_job_folders.py)
SHEET_ITS_SUBCONTRACTORS = 49013972750212          # ITS_Subcontractors — subcontractor SoR (build_its_subcontractors_sheet.py)
SHEET_SUBCONTRACT_LOG = 5005152829263748             # Subcontract_Log — operator-visible ledger mirror of D1 (build_subcontract_log_sheet.py)
SHEET_SUBCONTRACT_PENDING_REVIEW = 3986004334563204  # Subcontract_Pending_Review — review/approve/send surface (build_subcontract_pending_review_sheet.py)


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
