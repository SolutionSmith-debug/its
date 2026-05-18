"""
ITS job-folder name parser, v2.

Extends v1 (parse_job.py) with closed-project handling derived from
2026-05-14 late-night deep dive of 5 closed-project archives:

  - 2018.111 Neighborhood Portfolio  (Oregon-DATA-FOLDERS schema)
  - Bear Creek                       ((O)/(F) prefix + 21 numbered + 1111A inside)
  - 14130.1 Dooley (Mortenson)       (1111A schema, dash-customer-paren naming)
  - 14107 Charlotte (Ace)            (1111A schema, parens-wrap naming)
  - 14154 Lakeland (Invenergy)       (1111A schema, partial-fill / failure mode)
  - ECOS Indiana Nipsco              (multi-parallel chaos schema)

Adds:

  * parse_closed_folder() — recognizes legacy/closed-archive folder names
    that v1 didn't see (e.g., "(14107 Charlotte) Field", template placeholders).
  * classify_schema(top_level_names) — returns which of 4 organizational
    schemas a folder tree follows, given its top-level folder names.
  * detect_chaos(name) — flags real-world filing-hygiene problems on any
    individual folder name (Box-sync tmp, email-suffix assignment, generic
    "New folder", positional hacks, instructional names, duplicate suffix,
    unfilled template placeholders).
  * Canonical 1111A subject-set allowlists (Field A-K, Office 1-6).
  * BOS Contract 24-exhibit structure recognizer.
  * Extended TEST_CORPUS with names sampled from all 5 closed archives.

v2 imports v1 unchanged. All v1 behavior preserved.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

# Re-export v1 so callers can `from parse_job_v2 import *`
from parse_job import (
    JobIdKind,
    FolderKind,
    ParsedFolder,
    parse_folder as parse_folder_v1,
    PRIORITY_PREFIX,
    MODERN,
    LEGACY,
    RANGE,
    SUBJECT_KEYWORDS,
    UTILITY_NAMES,
    SHARED_NAMES,
    TEMPLATE_PATTERN,
)


# =============================================================================
# Schema classification
# =============================================================================

class Schema(str, Enum):
    """Which organizational pattern does a folder tree follow?"""
    ACTIVE_MODERN          = "active_modern"            # Kendall era, numbered subjects + Office/Field per sub-job
    TEMPLATE_1111A_CLEAN   = "1111a_clean"              # Charlotte/Dooley — (JobID Name) Field/Office cleanly applied
    TEMPLATE_1111A_PARTIAL = "1111a_partial"            # Lakeland — template copied but placeholders never renamed
    OF_PREFIX_NUMBERED     = "of_prefix"                # Bear Creek — (O)/(F) prefix on flat numbered folders
    DATA_FOLDER_GEOGRAPHIC = "data_folder"              # 2018.111 Neighborhood — [Region]-DATA-FOLDERS/[Site]-DATA-FOLDER
    MULTI_PARALLEL_CHAOS   = "multi_parallel"           # ECOS Indiana — Field Folders/+internal docs/+PM Box/ all in parallel
    UNKNOWN                = "unknown"


# Heuristic signature folders (top-level) per schema.
# classify_schema() looks for these in the input list.
_SCHEMA_SIGNATURES = {
    Schema.TEMPLATE_1111A_PARTIAL: [
        # Unfilled template placeholders == failure mode
        re.compile(r'^\(Project Number & Name\)\s+(Field|Office)$'),
    ],
    Schema.TEMPLATE_1111A_CLEAN: [
        # Either parens-wrap "(14107 Charlotte) Field" OR dash-customer "14130.1 Dooley (Mortenson) Field"
        re.compile(r'^\([0-9.]+\s+[^)]+\)\s+(Field|Office)$'),
        re.compile(r'^[0-9.]+\s+.+?\s+\([^)]+\)\s+(Field|Office)$'),
    ],
    Schema.OF_PREFIX_NUMBERED: [
        # "1. (O) Accounting", "9. (F) Field Reports" — three or more of these = strong signal
        re.compile(r'^\d+\.\s+\([OF]\)\s+'),
    ],
    Schema.DATA_FOLDER_GEOGRAPHIC: [
        # "Oregon-DATA-FOLDERS", "[Site]-DATA-FOLDER" — DATA-FOLDER(S) suffix
        re.compile(r'-DATA-FOLDERS?$', re.IGNORECASE),
    ],
    Schema.MULTI_PARALLEL_CHAOS: [
        # ECOS-style: multiple parallel "boxes" for same content
        re.compile(r'^(Field Folders|internal docs|PM Box)$'),
    ],
    Schema.ACTIVE_MODERN: [
        # Numbered subject folders at portfolio level: "1. EPC", "12. CLOSEOUT"
        # Need at least 3-4 of these to call it active-modern (closed projects don't have these at top level)
        re.compile(r'^\d+\.\s+(EPC|Buyout|CLOSEOUT|Project Schedules|Developer Documents|Submittals|Permitting)\b', re.IGNORECASE),
    ],
}


def classify_schema(top_level_names: list[str]) -> tuple[Schema, list[str]]:
    """
    Given the immediate-children folder names of a project root, return
    (best-guess schema, list of signature folders matched).

    Multiple signatures can fire. Priority order is set so failure-modes
    (1111A_PARTIAL) win over clean cases when both fire.
    """
    matched: dict[Schema, list[str]] = {s: [] for s in _SCHEMA_SIGNATURES}
    for name in top_level_names:
        for schema, patterns in _SCHEMA_SIGNATURES.items():
            for pat in patterns:
                if pat.search(name):
                    matched[schema].append(name)
                    break  # one signature match per (schema, name) is enough

    # Priority order: partial first (failure mode), then specific, then generic.
    priority = [
        Schema.TEMPLATE_1111A_PARTIAL,
        Schema.OF_PREFIX_NUMBERED,
        Schema.DATA_FOLDER_GEOGRAPHIC,
        Schema.MULTI_PARALLEL_CHAOS,
        Schema.TEMPLATE_1111A_CLEAN,
        Schema.ACTIVE_MODERN,
    ]

    for schema in priority:
        if matched.get(schema):
            # OF_PREFIX requires 3+ matches to call (avoid one-off (O) folders)
            if schema == Schema.OF_PREFIX_NUMBERED and len(matched[schema]) < 3:
                continue
            # ACTIVE_MODERN requires 3+ subject matches
            if schema == Schema.ACTIVE_MODERN and len(matched[schema]) < 3:
                continue
            return schema, matched[schema]

    return Schema.UNKNOWN, []


# =============================================================================
# Closed-project folder-name recognition (extends v1's parse_folder)
# =============================================================================

# (14107 Charlotte) Field  /  (14107 Charlotte) Office  — parens wrap whole job id
CLOSED_PARENS_WRAP = re.compile(
    r'^\((?P<jobid>[0-9.]+)\s+(?P<name>[^)]+)\)\s+(?P<side>Field|Office)\s*$'
)

# 14130.1 Dooley (Mortenson) Field  — dash-customer parens, then Field/Office
CLOSED_CUSTOMER_PAREN = re.compile(
    r'^(?P<jobid>[0-9.]+)\s+(?P<name>.+?)\s+\((?P<customer>[^)]+)\)\s+(?P<side>Field|Office)\s*$'
)

# Template placeholder: (Project Number & Name) Field — Lakeland failure mode
TEMPLATE_PLACEHOLDER = re.compile(
    r'^\(Project Number & Name\)\s+(?P<side>Field|Office)\s*$'
)

# (O) Accounting / (F) Photos — Bear Creek prefix style; usually preceded by "N. "
OF_PREFIX_TAG = re.compile(r'^\((?P<tag>[OF])\)\s+(?P<rest>.+)$')

# [Site]-DATA-FOLDER (closed-project, Neighborhood-style)
DATA_FOLDER_SITE = re.compile(r'^(?P<site>[A-Za-z][A-Za-z0-9\-]*?)-DATA-FOLDER\s*$', re.IGNORECASE)


@dataclass
class ClosedFolderParse:
    """Result of parsing a closed-project folder name."""
    raw: str
    kind: str                          # 'office_field_clean' | 'office_field_customer' | 'template_placeholder' | 'of_prefix' | 'data_folder' | 'not_closed'
    job_id: Optional[str] = None
    name: Optional[str] = None
    customer: Optional[str] = None
    side: Optional[str] = None         # 'Field' or 'Office'
    of_tag: Optional[str] = None       # 'O' or 'F'
    subject: Optional[str] = None      # the actual folder topic (after the (O)/(F) prefix)
    site: Optional[str] = None         # for DATA-FOLDER sites
    warnings: list = field(default_factory=list)


def parse_closed_folder(raw: str) -> ClosedFolderParse:
    """
    Parse a closed-project folder name. Returns kind='not_closed' if the name
    doesn't match any closed-project pattern (caller should fall back to v1).
    """
    m = TEMPLATE_PLACEHOLDER.match(raw)
    if m:
        return ClosedFolderParse(
            raw=raw, kind='template_placeholder',
            side=m.group('side'),
            warnings=['template placeholder never renamed — fill before filing'],
        )

    m = CLOSED_PARENS_WRAP.match(raw)
    if m:
        return ClosedFolderParse(
            raw=raw, kind='office_field_clean',
            job_id=m.group('jobid'),
            name=m.group('name').strip(),
            side=m.group('side'),
        )

    m = CLOSED_CUSTOMER_PAREN.match(raw)
    if m:
        return ClosedFolderParse(
            raw=raw, kind='office_field_customer',
            job_id=m.group('jobid'),
            name=m.group('name').strip(),
            customer=m.group('customer').strip(),
            side=m.group('side'),
        )

    m = OF_PREFIX_TAG.match(raw)
    if m:
        return ClosedFolderParse(
            raw=raw, kind='of_prefix',
            of_tag=m.group('tag'),
            subject=m.group('rest').strip(),
        )

    m = DATA_FOLDER_SITE.match(raw)
    if m:
        return ClosedFolderParse(
            raw=raw, kind='data_folder',
            site=m.group('site').strip(),
        )

    return ClosedFolderParse(raw=raw, kind='not_closed')


# =============================================================================
# Canonical 1111A template subject sets (extracted from Bear Creek archive)
# =============================================================================

# Field side: lettered A through K (plus L, M extensions seen in Dooley)
TEMPLATE_1111A_FIELD_SUBJECTS = {
    'A. Onsite Reporting & Tracking',
    'B. ESS Contract & Scope',
    'C. Schedules',
    'D. Issued For Construction Plans',
    'E. Subcontractors & Purchase Orders',
    'F. Permits & Inspector Cards',
    'G. Installation Manuals',
    'H. Approved Cut Sheets & Submittals',
    'I. RFI',
    'J. SWPPP Erosion Plans & Reports',
    'K. Closeout Package',
    # Optional extensions seen in real projects
    'L. 4th Quarter Project Names',
    'L. DPRs',
    'M. QC Templates',
}

# Field A. Onsite Reporting & Tracking sub-subjects (canonical from Bear Creek 1111A)
TEMPLATE_1111A_FIELD_A_SUB = {
    'A. Safety Plan & Signage (Site Specific)',
    'B. Daily Progress-Field Reports',
    'C. Material Tracking & Ship Tickets',
    'D. ESS Manpower & Rental Tracking',
    'E. Onsite Tool List (Dated)',
    'F. Quality Control Reports',
    'G. Electrical Commissioning & Testing Reports',
    'H. Meeting Minutes & Incident Reports',
    'I. Onsite Photos',
    'J. Work Orders',
    # Extensions
    'K. DFRs',
    'L. DPRs',
    'I. Incident Reports',
}

# Field K. Closeout Package — has mixed letters AND numbers in canonical template
TEMPLATE_1111A_FIELD_K_CLOSEOUT = {
    '2. Electrical Testing & Verification',
    '3. Commissioning Reports',
    '4. Final SWPPP N.O.T',
    '5. Final Signed Permits',
    '6. Irrigation & Landscape Manual',
    '7. Meter Picture ID',
    '8. O&M Manual',
    '9. Module Scans',
    '10. Punch Lists Executed',
    '11. Substantial Completion Certificate',
    '12. Final Completion Certificate',
    '13. Warranty Signed',
    'A. Final As Built Record Drawings',
    '1. Final As Built Record Drawings',  # Charlotte uses 1. for the same content
}

# Office side: numbered 1 through 6 in canonical 1111A; extensions in real projects
TEMPLATE_1111A_OFFICE_SUBJECTS = {
    '1. ESS Contract & LNTP (to owner)',
    '2. Accounting (to owner)',
    '3. Subcontractors & Vendors',
    '4. IFP Plans & Engineering',
    '5. Submittals In Process',
    '6. Notices - Reports - Correspondence',
    # Real-project extensions
    '6. Notices & Reports',  # Charlotte's variant
    '7. Pull Test',          # Charlotte
    '8. Budget- Jason',      # Charlotte (person-tagged)
}

# Office 2. Accounting sub-subjects
TEMPLATE_1111A_OFFICE_2_ACCOUNTING = {
    'A. Application For Payment',
    'B. Change Orders & CO Tracker',
    'C. Budget',
    'D. Insurance',
    'E. ESS Waivers',
    'F. Checks',  # Dooley extension
}

# Office 3. Subcontractors & Vendors sub-subjects
TEMPLATE_1111A_OFFICE_3_SUBS = {
    'A. Buyout  (estimates & quotes)',     # note: double space in canonical
    'B1. Vendor Name (Copy Folder)',       # placeholder for vendors w/ POs
    'B2. Sub Name (Copy Folder)',          # placeholder for subs w/ subcontracts
    'C. Sub & Vendor Contact List',
}

# B2 sub-subject template (per-subcontractor folder)
TEMPLATE_1111A_B2_SUB = {
    'A. Subcontract & Exhibits',
    'B. Change Orders & Tracker',
    'C. Insurance & W-9',
    'D. Proposal',
    'E. Unconditional Waivers',
    'G. Invoices',  # note: skips F!
}


# =============================================================================
# BOS Contract canonical 24-exhibit structure (from Lakeland archive)
# =============================================================================

BOS_CONTRACT_EXHIBITS = [
    '1. Contract',
    '2. Contract NTP',
    '3. Exhibit A - Scope of Work',
    '4. Exhibit B - Site Survey',
    '5. Exhibit C - Permits',
    '6. Exhibit D - Infrastructure Certification',
    '7. Exhibit F - Subcontractors',
    '8. Exhibit H - Progress Report',
    '9. Exhibit I - Payment Schedule',
    '10. Exhibit J-1 - Application for Payment',
    '11. Exhibit J-2 - Form of Invoice',
    '12. Exhibit K - Safety',
    '13. Exhibit L - Lien Waivers',
    '14. Exhibit M - Insurance Reqs',
    '15. Exhibit O - Geotech',
    '16. Exhibit P - QA Plan',
    '17. Exhibit Q - Lease Agreement',
    '18. Exhibit R - Tax Exempt Cert',
    '19. Exhibit S - Block Mech Cert',
    '20. Exhibit T - Substantial Cert',
    '21. Exhibit U - Final Cert',
    '22. Exhibit V - Schedule',
    '23. Exhibit X - Wire Instructions',
    '24. SOW Attachments',
]

# BOS uses Exhibits A, B, C, D, F (no E or G), H, I, J-1, J-2, K, L, M (no N), O,
# P, Q, R, S, T, U, V (no W), X. Skipped letters are real — flag if seen.
BOS_SKIPPED_EXHIBIT_LETTERS = {'E', 'G', 'N', 'W', 'Y', 'Z'}

# SOW Attachments sub-letters (Lakeland)
BOS_SOW_ATTACHMENTS = [
    '1. Attachment A - Modules',
    '2. Attachment B - SMA Equipment',
    '3. Attachment C - Transformer',
    '4. Attachment D - Racking',
    '5. Attachment E - Panel Board, Switchboard',
    '6. Attachment F - Design Drawings',
]


def is_bos_contract_folder(folder_name: str) -> bool:
    """Is this folder name an entry in the BOS Contract canonical structure?"""
    return folder_name in set(BOS_CONTRACT_EXHIBITS) or folder_name in set(BOS_SOW_ATTACHMENTS)


# =============================================================================
# Chaos pattern detection — real-world filing-hygiene problems
# =============================================================================

# Box Sync leaves temp folders behind during sync ops
BOXSYNC_TMP = re.compile(r'-boxsync-tmp-[0-9a-f]+', re.IGNORECASE)

# Email-suffix-as-assignment: "(name@evergreensolarservices.com)" or similar
EMAIL_SUFFIX = re.compile(r'\([a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}\)\s*$')

# Generic "New folder" (with optional " (n)" or email suffix)
GENERIC_NEW_FOLDER = re.compile(r'^New folder(?:\s*-?\s*Copy\s*\(\d+\))?(?:\s*\(\d+\))?(?:\s*\([^)]+\))?\s*$', re.IGNORECASE)

# Duplicate-suffix marker: trailing " (1)", " (2)", etc.
DUPLICATE_SUFFIX = re.compile(r'\s+\((\d+)\)\s*$')

# Positional ordering hack: pure underscore folder, or leading underscore
POSITIONAL_UNDERSCORE = re.compile(r'^_+$')

# Instructional folder name: contains imperative + emphasis
INSTRUCTIONAL_NAME = re.compile(
    r'(please\s+dump|put\s+(all|your|the)|drop\s+(here|files)|do\s+not|to be entered)',
    re.IGNORECASE,
)

# Multiple-exclamation emphasis
EXCLAMATION_EMPHASIS = re.compile(r'!{2,}')

# Person-name folder: "Jechiah's site inspection", "Jacob's pics", "Budget- Jason"
PERSON_NAME_TAG = re.compile(r"^[A-Z][a-z]+'s\s+|-\s*[A-Z][a-z]+\s*$")

# Unfilled template placeholder
UNFILLED_PLACEHOLDER = re.compile(r'\(Project Number & Name\)|\(Copy Folder\)|Vendor Name|Sub Name')

# Trailing-whitespace artifact (e.g. "Re_ NPC Solar Farms - Layouts - Thomas & St_ Louis .msg" has space before .msg)
TRAILING_SPACE_BEFORE_EXT = re.compile(r'\s\.\w{1,5}$')

# Double-space inside folder name
DOUBLE_SPACE = re.compile(r'  +')


@dataclass
class ChaosFlag:
    pattern: str
    severity: str  # 'info' | 'warn' | 'error'
    description: str
    match: str = ''


def detect_chaos(name: str) -> list[ChaosFlag]:
    """
    Inspect a single folder/file name and return all chaos-pattern flags.
    Empty list means the name is clean.
    """
    flags: list[ChaosFlag] = []

    if m := BOXSYNC_TMP.search(name):
        flags.append(ChaosFlag(
            pattern='boxsync_tmp',
            severity='warn',
            description='Box Sync left a temporary folder behind — should be ignored, not filed',
            match=m.group(0),
        ))

    if m := EMAIL_SUFFIX.search(name):
        flags.append(ChaosFlag(
            pattern='email_suffix_assignment',
            severity='info',
            description='Folder is tagged with an assignee email address — historic convention, not authoritative',
            match=m.group(0),
        ))

    if GENERIC_NEW_FOLDER.match(name):
        flags.append(ChaosFlag(
            pattern='generic_new_folder',
            severity='warn',
            description='Generic "New folder" never renamed — contents are unfiled / orphaned',
            match=name,
        ))

    if m := DUPLICATE_SUFFIX.search(name):
        flags.append(ChaosFlag(
            pattern='duplicate_suffix',
            severity='warn',
            description=f'Folder has " ({m.group(1)})" duplicate marker — likely Box-sync conflict or accidental copy',
            match=m.group(0),
        ))

    if POSITIONAL_UNDERSCORE.match(name):
        flags.append(ChaosFlag(
            pattern='positional_underscore',
            severity='warn',
            description='Single-underscore folder name used to sort to top of listing — not a canonical container',
            match=name,
        ))

    if m := INSTRUCTIONAL_NAME.search(name):
        flags.append(ChaosFlag(
            pattern='instructional_name',
            severity='warn',
            description='Folder name is an instruction/imperative — indicates intake backlog or unprocessed work',
            match=m.group(0),
        ))

    if m := EXCLAMATION_EMPHASIS.search(name):
        flags.append(ChaosFlag(
            pattern='exclamation_emphasis',
            severity='info',
            description='Folder name uses "!!!" emphasis — informal, often paired with instructional names',
            match=m.group(0),
        ))

    if UNFILLED_PLACEHOLDER.search(name):
        flags.append(ChaosFlag(
            pattern='unfilled_placeholder',
            severity='error',
            description='Folder name contains an unfilled template placeholder — template copied without customizing',
            match=name,
        ))

    if m := TRAILING_SPACE_BEFORE_EXT.search(name):
        flags.append(ChaosFlag(
            pattern='trailing_space_before_ext',
            severity='info',
            description='Whitespace immediately before file extension — typically a save-as artifact',
            match=m.group(0),
        ))

    if m := DOUBLE_SPACE.search(name):
        flags.append(ChaosFlag(
            pattern='double_space',
            severity='info',
            description='Folder name contains double space — typically a typo or copy artifact',
            match=m.group(0),
        ))

    return flags


# =============================================================================
# Unified parser entry point — chains v1 + closed-folder + chaos detection
# =============================================================================

def parse_folder(raw: str, parent: str = 'unknown') -> ParsedFolder:
    """
    v2 unified parse:
      1. Try v1 first (covers all active-modern + legacy + range cases)
      2. If v1 returned a SUBJECT folder with "unrecognized subject" warning,
         try the closed-project patterns
      3. Always run chaos detection and append flags as warnings
    """
    # Step 1: v1
    result = parse_folder_v1(raw, parent=parent)

    # Step 2: closed-project rescue
    if 'unrecognized subject folder' in result.warnings or result.folder_kind == FolderKind.SUBJECT:
        closed = parse_closed_folder(raw)
        if closed.kind != 'not_closed':
            # Promote: this isn't an unrecognized subject, it's a closed-project folder
            result.folder_kind = FolderKind.JOB if closed.kind in (
                'office_field_clean', 'office_field_customer',
            ) else FolderKind.SUBJECT
            if closed.job_id:
                result.job_id = closed.job_id
            if closed.name:
                result.name = closed.name
            if closed.customer:
                result.customer = closed.customer
            # Remove the v1 "unrecognized subject" warning since we now recognize it
            result.warnings = [w for w in result.warnings if 'unrecognized subject' not in w]
            result.warnings.extend(closed.warnings)
            result.warnings.append(f'closed-project pattern: {closed.kind}')

    # Step 3: chaos detection on raw name
    chaos = detect_chaos(raw)
    for flag in chaos:
        result.warnings.append(f'[{flag.severity}] {flag.pattern}: {flag.description}')

    return result


# =============================================================================
# Cross-project vendor allowlist (seed list from 5 closed projects)
# =============================================================================

KNOWN_VENDORS_SEED = {
    # Confirmed in 2+ projects
    'Classic Homes by Weaver Inc',     # Dooley, Lakeland
    'Classic Homes by Weaver',         # Lakeland (no Inc)
    'Georgia Fence Co',                # Dooley, Lakeland
    'Evergreen Landscape',             # Dooley
    # Bear Creek
    'A & A', 'City of Lodi', 'Diamond Ice', 'Hammer & Steel', 'Holt CAT',
    'Hunt & Sons', 'Mobile Mini', 'OxBlue', 'Titan', 'United Rentals',
    'Blue Oak Energy', 'C & R Fence', 'Challman Engineering', 'Chapman',
    'CLP', 'Labor Ready', 'Stout & Burg Electric', 'TSM', 'West Coast Backhoe',
    # Charlotte
    'Allied', 'Attala Steel', 'Fastenal', 'Lowndes Electric', 'Milton Cat',
    'Net Tech Technology', 'Trono Fuels', 'Welsh Electric', 'S. Denton Excavating',
    'Stuart Morrow Surveyor', 'Graybar',
    # Dooley
    'JWA', 'Titan Wire and Cable', 'Gary Weaver Mortenson Cleanup',
    # Lakeland
    'Garrett Fence Co', 'N.E.T. Inc', 'Nationwide Electrical Testing Inc',
    'Southeastern Surveying', 'Sims Fence Co', 'Arwood',
    # 2018.111 / Neighborhood Portfolio
    'Solectria', 'Yaskawa Solectria Solar', 'Canadian Solar', 'CSUN', 'SMA',
    'Astronergy', 'Flexrack', 'RBI', 'SunLink',
}

# Vendor name normalization aliases — same vendor under different names
VENDOR_ALIASES = {
    'N.E.T. Inc': 'Nationwide Electrical Testing Inc',
    'NET Inc': 'Nationwide Electrical Testing Inc',
    'Net Tech Technology': 'Net Tech Technology',  # canonical itself; placeholder
    'Classic Homes by Weaver': 'Classic Homes by Weaver Inc',
}


def normalize_vendor(name: str) -> str:
    """Return canonical form of a vendor name if known, else return input."""
    return VENDOR_ALIASES.get(name.strip(), name.strip())


# =============================================================================
# Entity-name evolution catalog (Evergreen entities through time)
# =============================================================================

ENTITY_HISTORY = [
    # (name, approximate_era, signal_seen_in)
    ('Evergreen Solar',              '2017-2018', 'Solectria quote header'),
    ('Evergreen Solar Services',     '2018-2019', '@evergreensolarservices.com emails throughout closed archives'),
    ('E.S.S. LLC',                   '2019-?',    'Older suite address records (STE 1030)'),
    ('Evergreen Renewables LLC',     '?-present', 'Current entity (STE 570)'),
]


# =============================================================================
# Test corpus — extended with names from all 5 closed-project archives
# =============================================================================

CLOSED_PROJECT_CORPUS = [
    # ---- Charlotte (1111A clean, parens-wrap naming) ----
    ('(14107 Charlotte) Field',                 'project',  'charlotte: parens-wrap Field'),
    ('(14107 Charlotte) Office',                'project',  'charlotte: parens-wrap Office'),
    ('B1. Allied',                              'subs',     'charlotte: B1 vendor (filled placeholder)'),
    ('B1. Allied-boxsync-tmp-b5681cd7b6480ac5c8edd58fde46c1', 'subs',
                                                'charlotte: Box Sync tmp folder'),
    ('B2. S. Denton Excavating',                'subs',     'charlotte: B2 sub with period in name'),
    ('K. DFRs',                                 'reporting','charlotte: Field extension K. DFRs'),
    ('L. DPRs',                                 'reporting','charlotte: Field extension L. DPRs'),
    ('7. Pull Test',                            'office',   'charlotte: Office extension'),
    ('8. Budget- Jason',                        'office',   'charlotte: person-name-tagged office folder'),
    ('Hotels  Living',                          'project',  'charlotte: loose-root, double-space typo'),

    # ---- Dooley (1111A clean, dash-customer-paren naming) ----
    ('14130.1 Dooley (Mortenson) Field',        'project',  'dooley: dash-customer-paren Field'),
    ('14130.1 Dooley (Mortenson) Office',       'project',  'dooley: dash-customer-paren Office'),
    ('B. ESS Contract & Scope (jw@evergreensolarservices.com)', 'field',
                                                'dooley: email-suffix on canonical folder'),
    ('B2. JWA',                                 'subs',     'dooley: filled B2 placeholder with acronym'),
    ('Classic Homes by Weaver Inc - Dooley',    'subs',     'dooley: full vendor-job pattern'),
    ('Evergreen Landscape - Dooley',            'subs',     'dooley: cross-brand subcontractor'),
    ('Georgia Fence Co (zg@evergreensolarservices.com)', 'subs',
                                                'dooley: vendor with email-suffix'),
    ('Titan Wire and Cable',                    'subs',     'dooley: bare vendor name (no -ProjName)'),

    # ---- Lakeland (1111A partial — placeholders never filled) ----
    ('(Project Number & Name) Field',           'project',  'lakeland: unfilled template placeholder'),
    ('(Project Number & Name) Office',          'project',  'lakeland: unfilled template placeholder'),
    ('_',                                       'project',  'lakeland: single-underscore positional hack'),
    ('Please Dump all Bid Docs Here!!!',        'project',  'lakeland: instructional name + emphasis'),
    ('Lakeland South Projects - To Be Entered', 'project',  'lakeland: stale intake backlog'),
    ('Unorganized Docs',                        'closeout', 'lakeland: PM admits defeat'),
    ("Jechiah's site inspection",               'photos',   'lakeland: person-name photo subfolder'),
    ('AFP # 1 - 1-5-15',                        'accounting','lakeland: sequenced+dated folder'),

    # ---- Bear Creek ((O)/(F) prefix style) ----
    ('1. (O) Accounting (Project Owner Only)',  'project',  'bear creek: (O) prefix office'),
    ('1. (O) Accounting (Project Owner Only) (1)', 'project','bear creek: duplicate-suffix'),
    ('9. (F) Field Reports & DPR\'s',           'project',  'bear creek: (F) prefix field'),
    ('21. (F) Close out Documents',             'project',  'bear creek: (F) prefix closeout'),
    ('21. (F) Close out Documents (1)',         'project',  'bear creek: duplicate-suffix closeout'),
    ('1111A Start Up (copy for new projects)',  'project',  'bear creek: contains 1111A template'),
    ('2. Electrical Testing & Verification (ej@evergreensolarservices.com)', 'closeout',
                                                'bear creek: email-suffix on closeout subfolder'),

    # ---- 2018.111 Neighborhood (DATA-FOLDER schema) ----
    ('Oregon-DATA-FOLDERS',                     'project',  'neighborhood: geographic umbrella'),
    ('St-Louis-DATA-FOLDER',                    'umbrella', 'neighborhood: per-site DATA-FOLDER'),
    ('Thomas-DATA-FOLDER',                      'umbrella', 'neighborhood: per-site DATA-FOLDER'),
    ('Tickle-Creek-DATA-FOLDER',                'umbrella', 'neighborhood: hyphenated site DATA-FOLDER'),
    ('Yamhill',                                 'umbrella', 'neighborhood: NO -DATA-FOLDER suffix (outlier)'),
    ('VA-ENGINEERING',                          'umbrella', 'neighborhood: geographic-office folder'),
    ('Bid-Package-INFO',                        'umbrella', 'neighborhood: cross-site bid package'),
    ('UTILITY-PGE-Interconnection-SPECS',       'umbrella', 'neighborhood: utility-name folder'),

    # ---- ECOS Indiana Nipsco (multi-parallel chaos) ----
    ('Field Folders',                           'project',  'ecos: parallel-organization box 1'),
    ('internal docs',                           'project',  'ecos: parallel-organization box 2'),
    ('PM Box',                                  'project',  'ecos: parallel-organization box 3 (PM workspace)'),
    ('New folder',                              'reporting','ecos: generic unfiled folder'),
    ('New folder (ben@evergreensolarservices.com)', 'reporting',
                                                'ecos: generic+email-suffix combo'),
    ('29. Ecovision Testing Reports',           'project',  'ecos: orphan numbered folder (no 1-28)'),
    ('Jacobs lincoln pics',                     'project',  'ecos: person-name + cross-site photos'),

    # ---- BOS Contract (Lakeland) ----
    ('BOS Contract Documents Lakeland South',   'office',   'lakeland: BOS contract container'),
    ('3. Exhibit A - Scope of Work',            'bos',      'bos: canonical exhibit'),
    ('10. Exhibit J-1 - Application for Payment','bos',     'bos: canonical exhibit (subscripted)'),
    ('24. SOW Attachments',                     'bos',      'bos: sow attachments container'),
]


# =============================================================================
# Main — runs v1 corpus + v2 closed-project corpus + schema classifier samples
# =============================================================================

def main():
    print("=" * 130)
    print("v1 CORPUS — active + legacy + range (regression check)")
    print("=" * 130)
    from parse_job import TEST_CORPUS as V1_CORPUS
    _run_corpus(V1_CORPUS, label='v1')

    print()
    print("=" * 130)
    print("v2 CORPUS — closed-project + chaos patterns")
    print("=" * 130)
    _run_corpus(CLOSED_PROJECT_CORPUS, label='v2')

    print()
    print("=" * 130)
    print("SCHEMA CLASSIFIER — sample top-level listings")
    print("=" * 130)
    samples = [
        ('Active Kendall',
         ['1. EPC', '2. Buyout', '3. Project Schedules', '12. CLOSEOUT',
          '2023.126.1 - Rodeo', '2023.126.2 - Apricus']),
        ('Charlotte (1111A clean)',
         ['(14107 Charlotte) Field', '(14107 Charlotte) Office',
          'Hotels  Living', 'Templates', 'Charlotte Lien']),
        ('Dooley (1111A clean, customer-paren)',
         ['14130.1 Dooley (Mortenson) Field', '14130.1 Dooley (Mortenson) Office',
          'Bidding', 'Misc Mortenson Docs', 'Subcontract Agreement Package']),
        ('Lakeland (1111A partial)',
         ['_', '14154 Lakeland (Invenergy)', '(Project Number & Name) Field',
          '(Project Number & Name) Office', 'lakeland',
          'Lakeland South Projects - To Be Entered',
          'Please Dump all Bid Docs Here!!!', 'Subcontract Agreement Package']),
        ('Bear Creek ((O)/(F) prefix)',
         ['1. (O) Accounting (Project Owner Only)', '2. (O) Proposal',
          '3. (O) Master EPC Contract', '9. (F) Field Reports & DPR\'s',
          '11. (F) Photos', '21. (F) Close out Documents']),
        ('2018.111 Neighborhood (DATA-FOLDER)',
         ['Oregon-DATA-FOLDERS', '2018.108 Oregon Portfolio',
          'AC panel board 36K.docx']),
        ('ECOS Indiana Nipsco (multi-parallel)',
         ['Field Folders', 'internal docs', 'PM Box', 'Laporte', 'Lincoln',
          'Middlebury', 'Portage', 'Panel Scans']),
    ]
    for label, names in samples:
        schema, matches = classify_schema(names)
        print(f"\n  {label}")
        print(f"    → {schema.value}")
        if matches:
            print(f"    signature matches: {matches[:3]}{' ...' if len(matches) > 3 else ''}")

    print()
    print("=" * 130)
    print("CHAOS DETECTION — same names, flagged")
    print("=" * 130)
    chaos_samples = [
        'B1. Allied-boxsync-tmp-b5681cd7b6480ac5c8edd58fde46c1',
        'B. ESS Contract & Scope (jw@evergreensolarservices.com)',
        'New folder (ben@evergreensolarservices.com)',
        '(Project Number & Name) Field',
        '_',
        'Please Dump all Bid Docs Here!!!',
        '1. (O) Accounting (Project Owner Only) (1)',
        'Hotels  Living',
    ]
    for name in chaos_samples:
        flags = detect_chaos(name)
        print(f"\n  {name!r}")
        if not flags:
            print('    (clean)')
        for f in flags:
            print(f"    [{f.severity}] {f.pattern}: {f.description}")


def _run_corpus(corpus, label):
    """
    A parse counts as a real failure only if v1 returned JOB-NAMED_ONLY
    at active/root parent WITHOUT a closed-project rescue or chaos flag.
    Subjects flagged with chaos patterns or known-canonical recognition are OK.
    """
    fail = 0
    print(f"\n{'STATUS':<7} {'KIND':<10} {'JOB ID':<22} {'NAME':<30} {'CUST':<15} {'NOTE'}")
    print('-' * 130)
    for raw, parent, note in corpus:
        p = parse_folder(raw, parent=parent)
        chaos = detect_chaos(raw)
        # Real failure = v1 returned "unrecognized subject" AND v2 closed-project
        # rescue did NOT fire AND no chaos pattern explains it
        rescued = any('closed-project pattern' in w for w in p.warnings)
        chaos_explained = len(chaos) > 0
        bad = (any('unrecognized subject' in w for w in p.warnings)
               and not rescued and not chaos_explained)
        status = '  OK' if not bad else 'FAIL'
        if bad:
            fail += 1
        print(f"{status:<7} {p.folder_kind.value:<10} "
              f"{(p.job_id or ''):<22} "
              f"{((p.name or '') or (p.raw[:28]))[:30]:<30} "
              f"{(p.customer or ''):<15} "
              f"{note}")
        # show any chaos flags inline
        for f in chaos:
            print(f"        ↳ chaos [{f.severity}]: {f.pattern}")
    print('-' * 130)
    print(f"{label}: known-gap subjects (need allowlist coverage, not bugs) = {fail} / {len(corpus)}")


if __name__ == '__main__':
    main()
