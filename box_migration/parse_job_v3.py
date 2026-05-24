"""
ITS job-folder name parser, v3.

Extends v2 (parse_job_v2.py) with live-production active-side handling derived
from 2026-05-16 deep-dive of 10 active Box projects:

  - 2023.126 Oregon - Kendall                 (canonical pre-Portfolio active)
  - 2024.112 Almon / Lomaside / Perrydale     (lower-letter sub-jobs)
  - 2024.335 Forefront - Luminace             (NNN.X 3-digit sub-jobs)
  - 2025.108 Bonacci 1&2 (Generate)           (single-project A./B. Office/Field)
  - 2025.112 Kendall CSP Portfolio 5          (lower-letter + z. archive)
  - 2025.127 Dolphin and Shoestring           (development-phase taxonomy)
  - 2025.201 KSI 4 IL                         (upper-letter+digit sub-jobs)
  - 2025.358 Keystone & Coast                 (Portfolio + pre-canonical 0.)
  - 2025.364 Steger & Roxbury                 (Portfolio + Box Drive copy)
  - 20171 - 20176 OR Portfolio (SPI)          (YYYY-NNNN dashed legacy sub-jobs)

Adds:

  * Schema.ACTIVE_PORTFOLIO_MODERN  — "N. Portfolio <Subject>" convention
  * Schema.ACTIVE_DEVELOPMENT       — pre-EPC 8-subject taxonomy
  * Schema.ACTIVE_SINGLE_PROJECT    — A./B. Office/Field at portfolio root
  * Schema.ACTIVE_HYBRID            — sub-jobs + portfolio subjects mixed

  * parse_active_subjob()           — recognizes the 5 sub-job ID formats
  * parse_active_subjob_side()      — recognizes the 5 Field/Office split styles
  * parse_portfolio_subject()       — recognizes "N. Portfolio <Subject>"
  * parse_development_subject()     — recognizes the 8 dev-phase subjects
  * parse_date_prefix()             — R. / S. + M.D.YY hypothesis (Received/Sent)

  * Canonical allowlists:
      PORTFOLIO_SUBJECTS            (8 cross-project canonical subjects)
      ACTIVE_FIELD_SUBJECTS_6       (consolidated active Field tree)
      ACTIVE_FIELD_SUBJECTS_BONACCI (5-subject variant, missing D. Schedules)
      DEVELOPMENT_SUBJECTS          (Dolphin/Shoestring 8-subject tree)

  * New chaos detectors:
      pre-canonical  "0. " prefix       (0. EEC Application)
      sub-decimal    "1.5." insert      (1.5. Funaro Landowner Claim)
      archive_letter "z. ARCHIVE..."    (Kendall CSP 5)
      box_drive_copy " - Copy" suffix   (Steger '- Copy' sibling)
      duplicate_number_at_level         (multi-folder caller-side check)

  * Extended TEST_CORPUS with names sampled from all 10 active projects.

v3 imports v2 unchanged. All v2 + v1 behavior preserved.

Authoring reference: ITS_Active_Project_Corpus_2026-05-16.docx (Sections
"Recommended parse_job_v3 deltas" and "Test corpus additions").
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

# Re-export v2 (which itself re-exports v1) so callers can
# `from parse_job_v3 import *` and get the full v1 + v2 + v3 surface.
from parse_job_v2 import (
    # v1 types
    JobIdKind, FolderKind, ParsedFolder,
    PRIORITY_PREFIX, MODERN, LEGACY, RANGE,
    SUBJECT_KEYWORDS, UTILITY_NAMES, SHARED_NAMES, TEMPLATE_PATTERN,
    # v2 closed-archive types
    ClosedFolderParse, parse_closed_folder,
    TEMPLATE_1111A_FIELD_SUBJECTS, TEMPLATE_1111A_FIELD_A_SUB,
    TEMPLATE_1111A_FIELD_K_CLOSEOUT,
    TEMPLATE_1111A_OFFICE_SUBJECTS, TEMPLATE_1111A_OFFICE_2_ACCOUNTING,
    TEMPLATE_1111A_OFFICE_3_SUBS, TEMPLATE_1111A_B2_SUB,
    BOS_CONTRACT_EXHIBITS, BOS_SKIPPED_EXHIBIT_LETTERS, BOS_SOW_ATTACHMENTS,
    is_bos_contract_folder,
    # v2 vendors / entity history
    KNOWN_VENDORS_SEED, VENDOR_ALIASES, normalize_vendor, ENTITY_HISTORY,
    # v2 chaos
    ChaosFlag,
    # v2 entry point — we wrap, not replace
    parse_folder as parse_folder_v2,
    detect_chaos as detect_chaos_v2,
    classify_schema as classify_schema_v2,
)


# =============================================================================
# Schema enum — re-declared with v2 values + v3 additions
# =============================================================================
# v2's Schema cannot be extended in place (Python Enum limitation), so v3
# re-declares the full set. v2's Schema remains valid for v2-only callers.

class Schema(str, Enum):
    """Which organizational pattern does a folder tree follow?"""
    # v2-era schemas (preserved)
    ACTIVE_MODERN           = "active_modern"             # Kendall 2023.126 era
    TEMPLATE_1111A_CLEAN    = "1111a_clean"               # Charlotte / Dooley
    TEMPLATE_1111A_PARTIAL  = "1111a_partial"             # Lakeland
    OF_PREFIX_NUMBERED      = "of_prefix"                 # Bear Creek (O)/(F)
    DATA_FOLDER_GEOGRAPHIC  = "data_folder"               # 2018.111 Neighborhood
    MULTI_PARALLEL_CHAOS    = "multi_parallel"            # ECOS Indiana
    UNKNOWN                 = "unknown"
    # v3 additions — active-side
    ACTIVE_PORTFOLIO_MODERN = "active_portfolio_modern"   # N. Portfolio <Subject>
    ACTIVE_DEVELOPMENT      = "active_development"        # Pre-EPC dev taxonomy
    ACTIVE_SINGLE_PROJECT   = "active_single_project"     # Bonacci A./B. at root
    ACTIVE_HYBRID           = "active_hybrid"             # Sub-jobs + portfolio subjects mixed


# =============================================================================
# Schema signature regexes (v3 set)
# =============================================================================
#
# Verified against the 1111B canonical paths in the post-1111B cutover PR
# (see docs/session_logs/2026-05-23_post_1111b_canonical_cutover.md).
# `\d+` matches both legacy single-digit (`1. Portfolio Client Docs`) and
# 1111B zero-padded (`01. Portfolio Client Docs`) prefixes — no regex
# extension needed.

# v2 ACTIVE_MODERN signature (Kendall 2023.126 — pre-Portfolio)
# v3 tightens "Permitting" with a negative lookahead so it doesn't collide
# with ACTIVE_DEVELOPMENT's "Permitting & Environmental".
ACTIVE_MODERN_SIG = re.compile(
    r'^\d+\.\s+(EPC|Buyout|CLOSEOUT|Project Schedules|Developer Documents|'
    r'Submittals|Permitting(?!\s*&))\b',
    re.IGNORECASE,
)

# v3 ACTIVE_PORTFOLIO_MODERN signature — broad-match "N. Portfolio <Subject>"
# Specific canonical match happens later in parse_portfolio_subject(). Here
# we just need to count signature hits for schema classification.
ACTIVE_PORTFOLIO_SIG = re.compile(
    r'^\d+\.\s+Portfolio\s+\S',
    re.IGNORECASE,
)

# v3 ACTIVE_DEVELOPMENT signature — any of the 8 dev-phase subjects
# (Interconnection variant "3. PGE SPQ0274" deliberately not matched here —
# it's recognized in parse_development_subject() instead.)
ACTIVE_DEVELOPMENT_SIG = re.compile(
    r'^\d+\.\s+(Corporate\s+Governance|GIS\s*&\s*Photos|Interconnection|'
    r'Permitting\s*&\s*Environmental|Production\s*&\s*Offtake|'
    r'Real\s+Estate|Regulatory|Eng\.)\b',
    re.IGNORECASE,
)

# v3 ACTIVE_SINGLE_PROJECT signature — A./B. at root with side word
# Exactly-two-match check enforced in classify_schema().
ACTIVE_SINGLE_PROJECT_SIG = re.compile(
    r'^[AB]\.\s+\S.*\s+(Office|Field)\s*$'
)


# =============================================================================
# Sub-job ID format recognizers (5 variants — v2 handled only YYYY.NNN.X)
# =============================================================================

# YYYY.NNN.X — modern standard (handled by v1 MODERN too; this is the relaxed form)
SUBJOB_FULL_DOT = re.compile(
    r'^(?P<jobid>\d{4}\.\d{2,3}\.\d+)\s*(?:-\s*|\s+)(?P<name>.+?)\s*$'
)

# NNN.X — Forefront's 3-digit portfolio prefix
SUBJOB_THREE_DIGIT = re.compile(
    r'^(?P<jobid>\d{3}\.\d+)\s+(?P<name>.+?)\s*$'
)

# YYYY-NNNN — SPI's dashed legacy format
SUBJOB_DASHED = re.compile(
    r'^(?P<jobid>\d{4}-\d{3,4})\s+(?P<name>.+?)\s*$'
)

# a. Almon — lower-letter sub-job prefix
SUBJOB_LETTER_LC = re.compile(
    r'^(?P<letter>[a-z])\.\s+(?P<name>.+?)\s*$'
)

# A1. Kiwi  or  A. Bonacci Office — upper-letter (with optional digit)
SUBJOB_LETTER_UC = re.compile(
    r'^(?P<letter>[A-Z]\d?)\.\s+(?P<name>.+?)\s*$'
)


# =============================================================================
# Active Field/Office split recognizer (5 variants)
# =============================================================================

ACTIVE_FIELD_OFFICE_VARIANTS = [
    # 335.1 Brimfield-1 Field  /  2023.126.1 - Rodeo Field
    ('full_id_name_side',
     re.compile(r'^(?P<id>\d{3,4}(?:\.\d+){1,2})\s+(?P<name>.+?)\s+(?P<side>Field|Office)\s*$')),
    # (Almon) Field  — same as closed Charlotte (14107 Charlotte) Field
    ('parens_wrap',
     re.compile(r'^\((?P<name>[^)]+)\)\s+(?P<side>Field|Office)\s*$')),
    # 1. Field  /  2. Office — Keystone Emmanuel numbered shorthand
    ('numbered_shorthand',
     re.compile(r'^(?P<num>[12])\.\s+(?P<side>Field|Office)\s*$')),
    # A. Kiwi Office  — KSI letter+name+side
    ('letter_name_side',
     re.compile(r'^(?P<letter>[A-Z])\.\s+(?P<name>.+?)\s+(?P<side>Field|Office)\s*$')),
    # Steger Field  — bare name + side. NOTE: keep this LAST; lowest specificity
    ('bare_name_side',
     re.compile(r'^(?P<name>\S.+?)\s+(?P<side>Field|Office)\s*$')),
]


# =============================================================================
# Portfolio / Development / Date-prefix / Archive recognizers
# =============================================================================

# "N. Portfolio <Subject>" — used by parse_portfolio_subject()
PORTFOLIO_SUBJECT = re.compile(
    r'^(?P<num>\d+)\.\s+Portfolio\s+(?P<subject>.+?)\s*$',
    re.IGNORECASE,
)

# "N. <Dev Subject>" — used by parse_development_subject()
DEVELOPMENT_SUBJECT = re.compile(
    r'^(?P<num>\d+)\.\s+(?P<subject>'
    r'Corporate\s+Governance|GIS\s*&\s*Photos|Interconnection|'
    r'Permitting\s*&\s*Environmental|Production\s*&\s*Offtake|'
    r'Real\s+Estate|Regulatory|Eng\.\s*\S+'
    r')\s*$',
    re.IGNORECASE,
)

# R. 5.6.25 Chint Quote  /  S. 3.18.26 demarcation signed
# Hypothesis: R = Received (from external), S = Sent (to external).
# Lowercase variant ("s. 4.17.25 RESPONSE") is treated as a chaos flag.
DATE_PREFIX_RS = re.compile(
    r'^(?P<dir>[RS])\.\s+(?P<date>\d{1,2}\.\d{1,2}\.\d{2,4})(?:\s+(?P<topic>.+))?\s*$'
)
DATE_PREFIX_RS_LOWER = re.compile(
    r'^(?P<dir>[rs])\.\s+(?P<date>\d{1,2}\.\d{1,2}\.\d{2,4})'
)

# ISO 8601 date prefix — YYYY-MM-DD <topic>. Added 2026-05-19 from the
# reconcile sanity check; observed in CAD-versioning workflows
# ("2024-12-04 Brimfield 1 IFC CAD", "2025-09-15 BBCHS PBASE"). No
# direction tag (unlike R./S.); we surface direction='ISO' from
# parse_date_prefix so callers can discriminate.
DATE_PREFIX_ISO = re.compile(
    r'^(?P<date>\d{4}-\d{2}-\d{2})\s+(?P<topic>.+?)\s*$'
)

# z. ARCHIVE PROJ  — cross-portfolio archive marker (Kendall CSP 5)
ARCHIVE_LETTER_Z = re.compile(
    r'^z\.\s+(?P<label>(ARCHIVE|RETIRED|OLD)\b.*?)\s*$',
    re.IGNORECASE,
)


# =============================================================================
# Sub-subject patterns (N.M, N.M.K, Na.) — added from 2026-05-18 reconcile
# =============================================================================
# Sub-subjects live one or two levels inside a canonical "N. Subject" folder
# and use the parent's number as a prefix. Three observed variants:
#
#   N.M  <Name>   — "7.1 Equipment", "7.10 IFC Redlines", "6.1 Zoning"
#   N.M.K <Name>  — "6.1.1 Land Use Approvals", "6.1.2 Other"
#   Na. <Name>    — "1a. Lum Review of IFC ELEC Drawings"  (digit-letter,
#                   parallel to v2's lowercase-letter sub-job but with a digit
#                   binding it to its parent subject)
#
# Bounded to \d{1,2} on each numeric segment so they cannot collide with
# job-ID patterns (which are \d{4}.\d{2,3} or \d{3,4}-\d{3,4}).

SUBSUBJECT_NUMERIC_2 = re.compile(
    r'^(?P<parent>\d{1,2})\.(?P<sub>\d{1,2})\s+(?P<name>.+?)\s*$'
)
SUBSUBJECT_NUMERIC_3 = re.compile(
    r'^(?P<parent>\d{1,2})\.(?P<mid>\d{1,2})\.(?P<sub>\d{1,2})\s+(?P<name>.+?)\s*$'
)
SUBSUBJECT_DIGIT_LETTER = re.compile(
    r'^(?P<parent>\d{1,2})(?P<letter>[a-z])\.\s+(?P<name>.+?)\s*$'
)


# =============================================================================
# Vendor / Sub enumeration patterns (V12., S10., etc.) — added from
# 2026-05-18 reconcile sanity check (see docs/tech_debt.md entry).
# =============================================================================
# Two-digit V/S enumeration falls through the existing SUBJOB_LETTER_UC
# pattern, which caps the post-letter digit at one (`[A-Z]\d?\.`). V1./S1.
# stay in LETTER_UC's domain; V12. / S10. need this new pattern.
#
#   V12. EPEC                 → kind='vendor', index='12'
#   S10. Well Demo            → kind='sub',    index='10'
#   V31. Cable Markers        → kind='vendor', index='31'
#
# Bounded to \d{1,2} so V100. doesn't accidentally match — three-digit
# vendor IDs aren't a real pattern in the observed corpus.

VENDOR_SUB = re.compile(
    r'^(?P<letter>[VS])(?P<index>\d{2})\.\s+(?P<name>.+?)\s*$'
)


# =============================================================================
# Canonical subject allowlists (parallel to TEMPLATE_1111A_*)
# =============================================================================

# 8 cross-project canonical "N. Portfolio <Subject>" entries.
# Variants in parens reflect the spelling drift observed in the corpus.
PORTFOLIO_SUBJECTS = {
    '1. Portfolio Client Docs',                       # 7/10 projects
    '2. Portfolio Buyout',                            # 6/10
    '3. Portfolio Schedules',                         # 7/10
    '4. Portfolio Dev Docs',                          # 6/10
    '6. Portfolio Owner Correspond',                  # 3/10 ("Correspond")
    '6. Portfolio Owner Correspondence',              # variant ("Correspondence")
    '6. Portfolio Owner Contract and Correspond',     # variant (Keystone)
    '7. Portfolio Financials',                        # 5/10
    '8. Portfolio Change Management',                 # 5/10
    '12. Portfolio Closeout',                         # 7/10 (mixed case)
    '12. PORTFOLIO CLOSEOUT',                         # caps variant (SPI)
    '11. PORTFOLIO CLOSEOUT',                         # at #11 (Forefront)
    '7. PORTFOLIO CLOSEOUT',                          # at #7 (Almon — gap-shifted)
}

# Subjects that frequently appear without the "Portfolio" prefix at
# portfolio root in active projects (Engineering, Utility, Submittals, etc.).
# These count as canonical for ACTIVE_PORTFOLIO_MODERN classification too.
ACTIVE_NON_PORTFOLIO_SUBJECTS = {
    '5. Engineering Gen',
    '5. Eng. Gen',                                    # Almon — abbreviated typo-ish
    '9. Utility-Documents-Tracking',
    '9. PGE-Documents-Tracking',                      # SPI — utility-name-specific
    '10. Submittals',
    '10. Submittal Logs',                             # KSI / Steger
    '11. De-Comm Bonds',
    '11. Permitting',                                 # Kendall 2023.126
    '0. EEC Application',                             # KSI — IL ABP-specific pre-canonical
}

# Consolidated 6-subject active Field tree.
# Confirmed identically across Forefront BRIMFIELD-1, Keystone Emmanuel,
# Steger, Almon. This is the working active canonical going forward.
ACTIVE_FIELD_SUBJECTS_6 = {
    'A. Onsite Reporting & Tracking',     # stable from closed 1111A
    'B. Approved Plans IFC',              # NEW (closed B was "ESS Contract & Scope")
    'C. Installation Manuals',            # NEW (closed C was "Schedules")
    'D. Schedules',                       # NEW (closed C content moved to D)
    'E. Permits & Inspector Cards',       # NEW (closed F content moved to E)
    'F. Project Closeout',                # NEW (closed K content moved to F)
}

# Bonacci variant — same 6 letters but D. Schedules absent.
ACTIVE_FIELD_SUBJECTS_BONACCI = ACTIVE_FIELD_SUBJECTS_6 - {'D. Schedules'}

# Development-phase 8-subject taxonomy (Dolphin / Shoestring).
# Entry 3 has a client-tagged variant: "3. PGE SPQ0274" (utility queue number)
# stands in for "3. Interconnection" when a specific utility application is filed.
# Entry 8 always carries a project tag: "8. Eng. Dolph", "8. Eng. ShoeS".
DEVELOPMENT_SUBJECTS = {
    '1. Corporate Governance',
    '2. GIS & Photos',
    '3. Interconnection',                 # canonical
    '4. Permitting & Environmental',
    '5. Production & Offtake',
    '6. Real Estate',
    '7. Regulatory',
    '8. Eng.',                            # always followed by project short-name
}


# =============================================================================
# Parse helpers — single-name recognizers (return dataclass results)
# =============================================================================

@dataclass
class ActiveSubjobParse:
    """Result of parsing a sub-job folder name at portfolio root."""
    raw: str
    kind: str                              # 'full_dot' | 'three_digit' | 'dashed' | 'letter_lc' | 'letter_uc' | 'not_subjob'
    job_id: Optional[str] = None
    letter: Optional[str] = None
    name: Optional[str] = None
    warnings: list = field(default_factory=list)


def parse_active_subjob(raw: str) -> ActiveSubjobParse:
    """
    Parse a sub-job folder name appearing at portfolio root in an active project.
    Returns kind='not_subjob' if no sub-job pattern matches.
    Order: most specific first (full dot > three-digit > dashed > letter).
    """
    # Reject portfolio-subject lookalikes BEFORE letter-prefix matchers fire.
    if PORTFOLIO_SUBJECT.match(raw):
        return ActiveSubjobParse(raw=raw, kind='not_subjob')

    m = SUBJOB_FULL_DOT.match(raw)
    if m:
        return ActiveSubjobParse(raw=raw, kind='full_dot',
                                 job_id=m.group('jobid'),
                                 name=m.group('name').strip())

    m = SUBJOB_THREE_DIGIT.match(raw)
    if m:
        return ActiveSubjobParse(raw=raw, kind='three_digit',
                                 job_id=m.group('jobid'),
                                 name=m.group('name').strip())

    m = SUBJOB_DASHED.match(raw)
    if m:
        return ActiveSubjobParse(raw=raw, kind='dashed',
                                 job_id=m.group('jobid'),
                                 name=m.group('name').strip())

    # Archive marker takes priority over generic letter-LC match.
    if ARCHIVE_LETTER_Z.match(raw):
        return ActiveSubjobParse(raw=raw, kind='not_subjob',
                                 warnings=['archive marker, not a live sub-job'])

    m = SUBJOB_LETTER_LC.match(raw)
    if m:
        return ActiveSubjobParse(raw=raw, kind='letter_lc',
                                 letter=m.group('letter'),
                                 name=m.group('name').strip())

    # Upper-letter LAST — overlaps with active Field/Office split forms.
    m = SUBJOB_LETTER_UC.match(raw)
    if m:
        # If name ends with " Office" or " Field", this is a single-project
        # Office/Field root (Bonacci pattern), not a sub-job.
        if re.search(r'\s+(Office|Field)\s*$', m.group('name')):
            return ActiveSubjobParse(raw=raw, kind='not_subjob',
                                     warnings=['matches active_single_project root'])
        return ActiveSubjobParse(raw=raw, kind='letter_uc',
                                 letter=m.group('letter'),
                                 name=m.group('name').strip())

    return ActiveSubjobParse(raw=raw, kind='not_subjob')


@dataclass
class ActiveSubjobSideParse:
    """Result of parsing a Field/Office split folder inside an active sub-job."""
    raw: str
    variant: str                           # one of ACTIVE_FIELD_OFFICE_VARIANTS keys
    name: Optional[str] = None
    side: Optional[str] = None             # 'Field' or 'Office'
    job_id: Optional[str] = None
    letter: Optional[str] = None
    num: Optional[str] = None


def parse_active_subjob_side(raw: str) -> Optional[ActiveSubjobSideParse]:
    """Parse the Field/Office split folder inside an active sub-job."""
    for variant, pattern in ACTIVE_FIELD_OFFICE_VARIANTS:
        m = pattern.match(raw)
        if m:
            d = m.groupdict()
            return ActiveSubjobSideParse(
                raw=raw, variant=variant,
                name=d.get('name'),
                side=d.get('side'),
                job_id=d.get('id'),
                letter=d.get('letter'),
                num=d.get('num'),
            )
    return None


@dataclass
class PortfolioSubjectParse:
    raw: str
    num: int
    subject: str
    canonical: bool                        # matches PORTFOLIO_SUBJECTS allowlist?


def parse_portfolio_subject(raw: str) -> Optional[PortfolioSubjectParse]:
    """Parse "N. Portfolio <Subject>" at portfolio root."""
    m = PORTFOLIO_SUBJECT.match(raw)
    if not m:
        return None
    canonical_form = f"{m.group('num')}. Portfolio {m.group('subject')}".strip()
    return PortfolioSubjectParse(
        raw=raw,
        num=int(m.group('num')),
        subject=m.group('subject').strip(),
        canonical=canonical_form in PORTFOLIO_SUBJECTS,
    )


@dataclass
class DevelopmentSubjectParse:
    raw: str
    num: int
    subject: str


def parse_development_subject(raw: str) -> Optional[DevelopmentSubjectParse]:
    """Parse "N. <Dev Subject>" inside an active-development sub-job."""
    m = DEVELOPMENT_SUBJECT.match(raw)
    if not m:
        return None
    return DevelopmentSubjectParse(
        raw=raw,
        num=int(m.group('num')),
        subject=m.group('subject').strip(),
    )


@dataclass
class DatePrefixParse:
    raw: str
    direction: str                         # 'R' / 'S' / 'ISO'
    date_raw: str                          # e.g. '5.6.25' (R./S.) or '2024-12-04' (ISO)
    topic: Optional[str] = None
    warnings: list = field(default_factory=list)


def parse_date_prefix(raw: str) -> Optional[DatePrefixParse]:
    """
    Parse one of three date-prefix forms:

      R. M.D.YY <topic>   — Received-from-external hypothesis (direction='R')
      S. M.D.YY <topic>   — Sent-to-external hypothesis      (direction='S')
      YYYY-MM-DD <topic>  — ISO 8601, no direction tag       (direction='ISO')

    Direction hypothesis for R./S. forms is pending owner confirmation.
    Lowercase R./S. variants are accepted but flagged as chaos.
    """
    m = DATE_PREFIX_RS.match(raw)
    if m:
        return DatePrefixParse(
            raw=raw, direction=m.group('dir'),
            date_raw=m.group('date'),
            topic=(m.group('topic') or '').strip() or None,
        )
    m = DATE_PREFIX_RS_LOWER.match(raw)
    if m:
        return DatePrefixParse(
            raw=raw, direction=m.group('dir').upper(),
            date_raw=m.group('date'),
            warnings=['lowercase R./S. prefix — convention is uppercase'],
        )
    m = DATE_PREFIX_ISO.match(raw)
    if m:
        return DatePrefixParse(
            raw=raw, direction='ISO',
            date_raw=m.group('date'),
            topic=m.group('topic').strip() or None,
        )
    return None


@dataclass
class SubsubjectParse:
    raw: str
    kind: str                              # 'numeric_two' | 'numeric_three' | 'digit_letter'
    parent: str                            # parent subject number/letter binding
    sub_index: str                         # sub-level index (e.g., "1", "10", "1.1")
    name: str


def parse_subsubject(raw: str) -> Optional[SubsubjectParse]:
    """
    Parse a sub-subject folder name living inside a canonical "N. Subject"
    parent. Three observed variants in the 2026-05-18 reconcile corpus:

      "7.1 Equipment"            → kind='numeric_two',   parent='7',  sub_index='1'
      "6.1.1 Land Use Approvals" → kind='numeric_three', parent='6',  sub_index='1.1'
      "1a. Lum Review ..."       → kind='digit_letter',  parent='1',  sub_index='a'

    Returns None if `raw` doesn't match any of the three. Try the 3-numeric
    form before the 2-numeric form so "6.1.1 Foo" doesn't half-match as
    "6.1 .1 Foo".
    """
    m = SUBSUBJECT_NUMERIC_3.match(raw)
    if m:
        return SubsubjectParse(
            raw=raw, kind='numeric_three',
            parent=m.group('parent'),
            sub_index=f"{m.group('mid')}.{m.group('sub')}",
            name=m.group('name').strip(),
        )
    m = SUBSUBJECT_NUMERIC_2.match(raw)
    if m:
        return SubsubjectParse(
            raw=raw, kind='numeric_two',
            parent=m.group('parent'),
            sub_index=m.group('sub'),
            name=m.group('name').strip(),
        )
    m = SUBSUBJECT_DIGIT_LETTER.match(raw)
    if m:
        return SubsubjectParse(
            raw=raw, kind='digit_letter',
            parent=m.group('parent'),
            sub_index=m.group('letter'),
            name=m.group('name').strip(),
        )
    return None


@dataclass
class VendorSubParse:
    raw: str
    kind: str                              # 'vendor' | 'sub'
    index: str                             # two-digit enumeration ("12", "31")
    name: str


def parse_vendor_sub(raw: str) -> Optional[VendorSubParse]:
    """
    Parse a V12. / S10. vendor-or-sub enumeration folder name.

      "V12. EPEC"              → kind='vendor', index='12', name='EPEC'
      "S10. Well Demo"         → kind='sub',    index='10', name='Well Demo'

    V/S single-digit forms (`V1.`, `S1.`) are owned by `SUBJOB_LETTER_UC`
    in v2 and intentionally NOT claimed here — this function caps at
    two digits via the regex and returns None on one-digit input so the
    LETTER_UC pattern keeps its existing domain.

    Returns None on no match. Mirrors the shape of `parse_subsubject`.
    """
    m = VENDOR_SUB.match(raw)
    if not m:
        return None
    letter = m.group('letter')
    kind = 'vendor' if letter == 'V' else 'sub'
    return VendorSubParse(
        raw=raw,
        kind=kind,
        index=m.group('index'),
        name=m.group('name').strip(),
    )


# =============================================================================
# v3 chaos pattern detectors
# =============================================================================

# Pre-canonical "0. " prefix — items filed before the 1-N canonical sequence
PRE_CANONICAL_ZERO = re.compile(r'^0\.\s+')

# Sub-decimal "1.5." inserts — between-canonical insertions
SUB_DECIMAL_INSERT = re.compile(r'^\d+\.\d+\.\s+\S')

# Box Drive copy duplicate (replaces the Box Sync "-boxsync-tmp-" pattern)
BOX_DRIVE_COPY = re.compile(r'\s+-\s+Copy\s*$')

# Person-name tag inside a subject folder at portfolio root.
# Catches "for ZACK" and "Teala Organize folder". The earlier trailing-
# capitalized-word alternation (`-\s*[A-Z][a-z]+\s*$`) was removed
# 2026-05-20 — see docs/audits/person_tag_audit_2026-05-19.md for the FP
# analysis (138 hits / ~95% noise) and tech_debt closure.
PERSON_TAG_IN_SUBJECT = re.compile(
    r'(\bfor\s+[A-Z]{3,}\b|'                            # "for ZACK"
    r'^[A-Z][a-z]+\s+(Organize|Cleanup|Notes|Files)\b)'  # "Teala Organize folder"
)


def detect_chaos(name: str) -> list:
    """
    v3 chaos detection. Calls v2 first to preserve all v2 flags, then adds
    v3-specific patterns. Returns ChaosFlag list.
    """
    flags = list(detect_chaos_v2(name))

    if PRE_CANONICAL_ZERO.match(name):
        flags.append(ChaosFlag(
            pattern='pre_canonical_zero',
            severity='info',
            description='Folder uses "0." prefix to file before the canonical 1-N sequence',
            match=name[:3],
        ))

    if SUB_DECIMAL_INSERT.match(name):
        flags.append(ChaosFlag(
            pattern='sub_decimal_insert',
            severity='info',
            description='Folder uses "N.M." sub-decimal to insert between canonical entries instead of renumbering',
            match=name.split()[0],
        ))

    if ARCHIVE_LETTER_Z.match(name):
        flags.append(ChaosFlag(
            pattern='archive_letter_z',
            severity='info',
            description='"z." prefix used as cross-portfolio archive marker — folder holds retired sub-jobs',
            match=name,
        ))

    if m := BOX_DRIVE_COPY.search(name):
        flags.append(ChaosFlag(
            pattern='box_drive_copy',
            severity='warn',
            description='Folder ends with " - Copy" — Box Drive duplicate (sibling of the original); usually safe to delete',
            match=m.group(0),
        ))

    if DATE_PREFIX_RS_LOWER.match(name) and not DATE_PREFIX_RS.match(name):
        flags.append(ChaosFlag(
            pattern='date_prefix_lowercase',
            severity='info',
            description='Lowercase R./S. date prefix — convention is uppercase',
            match=name[:2],
        ))

    if m := PERSON_TAG_IN_SUBJECT.search(name):
        # Skip false positives on canonical subjects (e.g. "Teala Paradise" should
        # never match here; the patterns are narrower than that).
        flags.append(ChaosFlag(
            pattern='person_tag_in_subject',
            severity='info',
            description='Folder name carries a person tag (assignee, owner, or todo target)',
            match=m.group(0),
        ))

    return flags


def detect_duplicate_numbers_at_level(names: list[str]) -> list:
    """
    Caller-side multi-folder chaos check. Scans a single directory level for
    multiple folders sharing the same numeric prefix (e.g. "3. Buyout" and
    "3. Portfolio Schedules"). Returns ChaosFlag list, one per duplicate group.
    """
    flags = []
    by_num: dict[str, list[str]] = {}
    for n in names:
        m = re.match(r'^(\d+)\.\s', n)
        if m:
            by_num.setdefault(m.group(1), []).append(n)
    for num, group in by_num.items():
        if len(group) > 1:
            flags.append(ChaosFlag(
                pattern='duplicate_number_at_level',
                severity='warn',
                description=f'Number "{num}." appears on {len(group)} sibling folders: {group}',
                match=f'{num}.',
            ))
    return flags


# =============================================================================
# v3 unified parse_folder() — chains v2 + active-production rescue
# =============================================================================

def parse_folder(raw: str, parent: str = 'unknown') -> ParsedFolder:
    """
    v3 parse_folder:
      1. Call v2 parse_folder (which itself chains v1 + closed-project rescue).
      2. If still unrecognized, try active-production rescues in priority order:
         portfolio subject → date prefix → active sub-job → development subject
         → active subjob Field/Office side.
      3. Run v3 chaos detection and append flags as warnings.
    """
    result = parse_folder_v2(raw, parent=parent)
    was_unrecognized = any('unrecognized subject' in w for w in result.warnings) \
                       or result.folder_kind == FolderKind.SUBJECT

    # Step 2a: Portfolio subject ("N. Portfolio <Subject>")
    if was_unrecognized:
        ps = parse_portfolio_subject(raw)
        if ps:
            result.folder_kind = FolderKind.SUBJECT
            result.warnings = [w for w in result.warnings if 'unrecognized subject' not in w]
            tag = 'canonical' if ps.canonical else 'non-canonical variant'
            result.warnings.append(f'portfolio-prefix subject: {ps.subject} ({tag})')
            was_unrecognized = False

    # Step 2b: Date-prefix R./S. loose item
    if was_unrecognized:
        dp = parse_date_prefix(raw)
        if dp:
            result.folder_kind = FolderKind.SUBJECT
            result.warnings = [w for w in result.warnings if 'unrecognized subject' not in w]
            note = f"date-prefix loose item: {dp.direction}={dp.date_raw}"
            if dp.topic:
                note += f" ({dp.topic})"
            result.warnings.append(note)
            result.warnings.extend(dp.warnings)
            was_unrecognized = False

    # Step 2c: Active sub-job (5 ID formats)
    if was_unrecognized:
        sj = parse_active_subjob(raw)
        if sj.kind != 'not_subjob':
            result.folder_kind = FolderKind.JOB
            if sj.job_id:
                result.job_id = sj.job_id
            if sj.name:
                result.name = sj.name
            result.warnings = [w for w in result.warnings if 'unrecognized subject' not in w]
            result.warnings.append(f'active sub-job format: {sj.kind}')
            result.warnings.extend(sj.warnings)
            was_unrecognized = False

    # Step 2d: Development-phase subject
    if was_unrecognized:
        ds = parse_development_subject(raw)
        if ds:
            result.folder_kind = FolderKind.SUBJECT
            result.warnings = [w for w in result.warnings if 'unrecognized subject' not in w]
            result.warnings.append(f'development-phase subject: {ds.subject}')
            was_unrecognized = False

    # Step 2e: Active sub-job Field/Office split (only relevant under a sub-job parent)
    if was_unrecognized:
        ss = parse_active_subjob_side(raw)
        if ss:
            result.folder_kind = FolderKind.SUBJECT
            result.warnings = [w for w in result.warnings if 'unrecognized subject' not in w]
            result.warnings.append(
                f'active sub-job side: {ss.side} ({ss.variant})'
            )
            was_unrecognized = False

    # Step 3: v3 chaos detection. detect_chaos already includes v2 flags, which
    # parse_folder_v2 may have appended via its own detect_chaos_v2 call.
    # De-dupe by full message string; v2 and v3 use the same format.
    chaos = detect_chaos(raw)
    for flag in chaos:
        msg = f'[{flag.severity}] {flag.pattern}: {flag.description}'
        if msg not in result.warnings:
            result.warnings.append(msg)

    return result


# =============================================================================
# v3 schema classifier
# =============================================================================

_V3_SIGNATURES = {
    Schema.ACTIVE_PORTFOLIO_MODERN: [ACTIVE_PORTFOLIO_SIG],
    Schema.ACTIVE_DEVELOPMENT:      [ACTIVE_DEVELOPMENT_SIG],
    Schema.ACTIVE_SINGLE_PROJECT:   [ACTIVE_SINGLE_PROJECT_SIG],
    Schema.ACTIVE_MODERN:           [ACTIVE_MODERN_SIG],
}


def classify_schema(top_level_names: list[str]) -> tuple[Schema, list[str]]:
    """
    v3 classifier. Examines top-level folder names of a project root and
    returns (best-guess schema, list of signature folders matched).

    Priority order (descending specificity):
      1. ACTIVE_SINGLE_PROJECT     — exactly 2 matches of A./B. + side, no sub-jobs
      2. ACTIVE_DEVELOPMENT        — 4+ dev-phase subjects in any sub-job depth-1 children
      3. ACTIVE_HYBRID             — Portfolio subjects AND sub-jobs at SAME root
      4. ACTIVE_PORTFOLIO_MODERN   — 3+ Portfolio-prefix subjects
      5. ACTIVE_MODERN             — 3+ pre-Portfolio canonical (Forefront, Kendall 2023.126)
      6. Delegate to v2 classifier for closed-archive schemas
    """
    matched: dict[Schema, list[str]] = {s: [] for s in _V3_SIGNATURES}
    for name in top_level_names:
        for schema, patterns in _V3_SIGNATURES.items():
            for pat in patterns:
                if pat.search(name):
                    matched[schema].append(name)
                    break

    # ACTIVE_SINGLE_PROJECT — strict: exactly 2 hits, both with same family letter,
    # and NO other sub-job-looking entries at the same root.
    single_hits = matched[Schema.ACTIVE_SINGLE_PROJECT]
    if len(single_hits) == 2:
        sj_count = sum(
            1 for n in top_level_names
            if parse_active_subjob(n).kind in ('full_dot', 'three_digit', 'dashed', 'letter_lc')
        )
        if sj_count == 0:
            return Schema.ACTIVE_SINGLE_PROJECT, single_hits

    # ACTIVE_DEVELOPMENT — 4+ dev-subject hits OR strong Dolphin-style structure
    dev_hits = matched[Schema.ACTIVE_DEVELOPMENT]
    if len(dev_hits) >= 4:
        return Schema.ACTIVE_DEVELOPMENT, dev_hits

    # ACTIVE_HYBRID — Portfolio subjects AND sub-jobs share the same root
    portfolio_hits = matched[Schema.ACTIVE_PORTFOLIO_MODERN]
    subjob_at_root = [
        n for n in top_level_names
        if parse_active_subjob(n).kind != 'not_subjob'
    ]
    if len(portfolio_hits) >= 2 and len(subjob_at_root) >= 1:
        # If sub-jobs include non-numbered IDs (Dolphin: "1. Dolphin", "2. Shoestring")
        # mixed with portfolio subjects, that's the hybrid signature.
        # Otherwise (just YYYY.NNN.X sub-jobs alongside portfolio subjects) it's
        # still ACTIVE_PORTFOLIO_MODERN — common case.
        # Heuristic: hybrid requires sub-jobs that look like "1. Dolphin" — i.e.,
        # numbered "N. <Name>" where N collides with a portfolio number.
        for sj in subjob_at_root:
            m = re.match(r'^(\d+)\.\s+(?!Portfolio\b)', sj)
            if m and any(p.startswith(f"{m.group(1)}.") for p in portfolio_hits):
                return Schema.ACTIVE_HYBRID, portfolio_hits + subjob_at_root

    # ACTIVE_PORTFOLIO_MODERN — 3+ Portfolio hits
    if len(portfolio_hits) >= 3:
        return Schema.ACTIVE_PORTFOLIO_MODERN, portfolio_hits

    # ACTIVE_MODERN — pre-Portfolio (v2 calibration)
    modern_hits = matched[Schema.ACTIVE_MODERN]
    if len(modern_hits) >= 3:
        return Schema.ACTIVE_MODERN, modern_hits

    # Fall through to v2 (handles closed-archive schemas + everything else)
    v2_schema, v2_matches = classify_schema_v2(top_level_names)
    # Re-cast v2's Schema member into v3's Schema (same string values).
    return Schema(v2_schema.value), v2_matches


# =============================================================================
# Test corpus — names sampled from all 10 active projects (2026-05-16)
# =============================================================================

ACTIVE_PRODUCTION_CORPUS = [
    # ---- Portfolio-prefix subjects (canonical active naming) ----
    ('1. Portfolio Client Docs',                'project', 'KSI: portfolio client docs (NEW canonical)'),
    ('2. Portfolio Buyout',                     'project', 'KSI: portfolio buyout'),
    ('3. Portfolio Schedules',                  'project', 'KSI: portfolio schedules'),
    ('4. Portfolio Dev Docs',                   'project', 'KSI: portfolio dev docs'),
    ('7. Portfolio Financials',                 'project', 'KSI: portfolio financials (NEW canonical)'),
    ('8. Portfolio Change Management',          'project', 'KSI: portfolio change mgmt (NEW canonical)'),
    ('12. Portfolio Closeout',                  'project', 'KSI: portfolio closeout (mixed case)'),
    ('12. PORTFOLIO CLOSEOUT',                  'project', 'SPI: portfolio closeout (caps variant)'),
    ('11. PORTFOLIO CLOSEOUT',                  'project', 'Forefront: portfolio closeout at #11'),
    ('6. Portfolio Owner Correspondence',       'project', 'Dolphin: long-form variant'),
    ('6. Portfolio Owner Contract and Correspond', 'project', 'Keystone: long-form variant 2'),
    ('0. Porfolio Permitting',                  'project', 'Keystone: TYPO chaos in canonical name'),

    # ---- Sub-job ID formats ----
    ('2023.126.1 - Rodeo',                      'project', 'Kendall: YYYY.NNN.X dash-separated'),
    ('2025.358.1 - Emmanuel Church',            'project', 'Keystone: YYYY.NNN.X dash-separated'),
    ('2025.364.1 Steger',                       'project', 'Steger: YYYY.NNN.X space-separated'),
    ('335.1 BRIMFIELD-1',                       'project', 'Forefront: NNN.X (3-digit portfolio prefix)'),
    ('2020-1071 Belvedere',                     'project', 'SPI: YYYY-NNNN dashed legacy format'),
    ('a. Almon',                                'project', 'Almon: lower-letter sub-job'),
    ('b. Lomaside',                             'project', 'Almon: lower-letter sub-job'),
    ('z. ARCHIVE PROJ',                         'project', 'Kendall CSP: lower-letter ARCHIVE marker'),
    ('A1. Kiwi',                                'project', 'KSI: upper-letter+digit sub-job'),
    ('A. Bonacci Office',                       'project', 'Bonacci: upper-letter Office/Field at root'),
    ('B. Bonacci Field',                        'project', 'Bonacci: paired with above'),

    # ---- Field/Office split variants inside sub-jobs ----
    ('335.1 Brimfield-1 Field',                 'subjob',  'Forefront: full repeat-of-id split'),
    ('335.1 Brimfield-1 Office',                'subjob',  'Forefront: full repeat-of-id split'),
    ('(Almon) Field',                           'subjob',  'Almon: parens-wrap (same as closed Charlotte)'),
    ('(Almon) Office',                          'subjob',  'Almon: parens-wrap office side'),
    ('Steger Field',                            'subjob',  'Steger: bare name + side'),
    ('Steger Office',                           'subjob',  'Steger: bare name + side'),
    ('1. Field',                                'subjob',  'Keystone Emmanuel: numbered shorthand'),
    ('2. Office',                               'subjob',  'Keystone Emmanuel: numbered shorthand'),
    ('A. Kiwi Office',                          'subjob',  'KSI Kiwi: letter prefix + name + side'),
    ('B. Kiwi Field',                           'subjob',  'KSI Kiwi: letter prefix + name + side'),

    # ---- Active 6-subject Field tree ----
    ('A. Onsite Reporting & Tracking',          'field',   'active: stable from closed 1111A'),
    ('B. Approved Plans IFC',                   'field',   'active: NEW Field B (was closed "ESS Contract & Scope")'),
    ('C. Installation Manuals',                 'field',   'active: NEW Field C (was closed G)'),
    ('D. Schedules',                            'field',   'active: NEW Field D (was closed C)'),
    ('E. Permits & Inspector Cards',            'field',   'active: NEW Field E (was closed F)'),
    ('F. Project Closeout',                     'field',   'active: NEW Field F (was closed K)'),

    # ---- Development-phase taxonomy (Dolphin/Shoestring) ----
    ('1. Corporate Governance',                 'devjob',  'Dolphin: dev-stage subject 1'),
    ('2. GIS & Photos',                         'devjob',  'Dolphin: dev-stage subject 2'),
    ('3. Interconnection',                      'devjob',  'Dolphin: dev-stage subject 3 (canonical)'),
    ('3. PGE SPQ0274',                          'devjob',  'Dolphin: client-tagged Interconnection variant'),
    ('4. Permitting & Environmental',           'devjob',  'Dolphin: dev-stage subject 4'),
    ('5. Production & Offtake',                 'devjob',  'Dolphin: dev-stage subject 5'),
    ('7. Regulatory',                           'devjob',  'Dolphin: dev-stage subject 7'),
    ('8. Eng. Dolph',                           'devjob',  'Dolphin: project-tagged engineering folder'),
    ('8. Eng. ShoeS',                           'devjob',  'Shoestring: project-tagged engineering folder'),

    # ---- Date-prefix R./S. convention ----
    ('R. 5.6.25 Chint Quote',                   'loose',   'Dolphin: Received from external, dated'),
    ('S. 3.18.26 demarcation signed',           'loose',   'KSI: Sent to external, dated'),
    ('R. 2.10.26 Landscape CADs',               'loose',   'KSI: Received CAD files, dated'),
    ('s. 4.17.25 RESPONSE',                    'loose',   'KSI: lowercase outlier — chaos flag'),

    # ---- New chaos patterns ----
    ('0. EEC Application',                      'project', 'KSI: pre-canonical 0. prefix'),
    ('0. Coast PA Contract Docs EJ',            'project', 'Keystone: pre-canonical with assignee initials'),
    ('1.5. Funaro Landowner Claim',             'project', 'Keystone: sub-decimal insert'),
    ('2025.364 CPG- Cook County- Steger - Copy','project', 'Steger: Box Drive copy chaos'),
    ('Teala Organize folder',                   'project', 'Keystone: assignee-named loose folder'),
    ('11. EPC Contract Redlines for ZACK',      'project', 'SPI: person-name tag in subject'),
    ('SHARED DONT STORE',                       'project', 'Forefront: instructional name + caps'),
    ('Hawthorne documents',                     'project', 'Almon: missing letter-prefix (orphan sub-job)'),
]


# Sample top-level listings — exercises classify_schema() against each of the
# 10 active projects from the 2026-05-16 corpus.
ACTIVE_PROJECT_TOP_LEVELS = [
    ('1. KSI 4 IL (2025.201)', [
        '0. EEC Application', '1. Portfolio Client Docs', '2. Portfolio Buyout',
        '3. Portfolio Schedules', '4. Portfolio Dev Docs', '5. Engineering Gen',
        '6. Portfolio Owner Correspond', '7. Portfolio Financials',
        '8. Portfolio Change Management', '9. Utility-Documents-Tracking',
        '10. Submittal Logs', '11. De-Comm Bonds', '12. Portfolio Closeout',
        'A1. Kiwi', 'A2. Deeplake', 'A3. Indian Creek', 'A4. North Pasture',
    ]),
    ('2. Forefront / Luminace (2024.335)', [
        '1. EPC documents', '2. Project Schedules', '3. Permitting',
        '4. Buyout', '5. Engineering Gen', '6. Change Management',
        '7. Portfolio Financials', '8. Correspondence',
        '9. Utility-Documents-Tracking', '10. Submittals',
        '11. PORTFOLIO CLOSEOUT', '12. PVsyst Exh Y Forefront Contract',
        '335.1 BRIMFIELD-1', '335.2 BRIMFIELD-2', '335.3 ROCKFORD',
        '335.4 BBCHS-1', '335.5 BBCHS-2', '335.6 HUNTLEY',
    ]),
    ('3. Oregon - Kendall (2023.126)', [
        '1. EPC', '2. Buyout', '3. Project Schedules', '4. Developer Documents',
        '5. Engineering General', '5. Rodeo Entrance Drawings',
        '6. Correspondence', '7. Financials', '8. Change Management',
        '9. Utility-Documents-Tracking', '10. Submittals', '11. Permitting',
        '12. CLOSEOUT',
        '2023.126.1 - Rodeo', '2023.126.2 - Apricus', '2023.126.3 - Lincoln',
    ]),
    ('4. Keystone & Coast (2025.358)', [
        '0. Coast PA Contract Docs EJ', '0. Porfolio Permitting',
        '1. Portfolio Client Docs', '1.5. Funaro Landowner Claim',
        '2. Portfolio Buyout', '3. Portfolio Schedules', '4. Dev Docs',
        '5. Engineering Gen', '6. Portfolio Owner Contract and Correspond',
        '7. Portfolio Financials', '8. Portfolio Change Management',
        '9. Utility-Documents-Tracking', '10. Submittal Logs',
        '11. De-Comm Bonds', '12. Portfolio Closeout',
        '2025.358.1 - Emmanuel Church', '2025.358.2 - Ridge Road',
        '2025.358.3 - Off Church', '2025.358.4 - Shamrock',
        'Funaro Pre-Delivery of Excavator', 'Teala Organize folder',
    ]),
    ('5. Bonacci (Generate) (2025.108)', [
        'A. Bonacci Office', 'B. Bonacci Field',
    ]),
    ('6. Steger & Roxbury (2025.364)', [
        '1. Portfolio Client Docs', '2. Portfolio Buyout',
        '3. Portfolio Schedules', '4. Portfolio Dev Docs', '5. Engineering Gen',
        '6. Portfolio Owner Correspond', '7. Portfolio Financials',
        '8. Portfolio Change Management', '9. Utility-Documents-Tracking',
        '10. Submittal Logs', '11. De-Comm Bonds', '12. Portfolio Closeout',
        '2025.364 CPG- Cook County- Steger',
        '2025.364 CPG- Cook County- Steger - Copy',
        '2025.364.1 Steger', '2025.364.2 Roxbury',
    ]),
    ('7. SPI OR Portfolio (20171-20176)', [
        '0. Portfolio Client Docs', '1. Portfolio Buyout',
        '2. Accounting (to owner)', '2. EPC Agreement',
        '3. Engineering Gen', '4. Portfolio Dev Docs',
        '5. Portfolio Schedules', '6. Correspondence - Notices',
        '7. Change Management', '9. PGE-Documents-Tracking',
        '10. Owner Docs', '11. EPC Contract Redlines for ZACK',
        '12. PORTFOLIO CLOSEOUT', '13. Submittals', '14. Lien Waivers',
        '2020-1071 Belvedere', '2020-1072 Dover', '2020-1073 Clayfield',
        '2020-1074 Waterford', '2020-1075 Manchester', '2020-1076 Cork',
        'Drone Flights', 'SPI Safety and Reporting',
    ]),
    ('12. Almon/Lomaside/Perrydale/Hawthorne (2024.112)', [
        '1. Portfolio Client Docs', '3. Buyout', '3. Portfolio Schedules',
        '4. Portfolio Dev Docs', '5. Eng. Gen', '6. Safe Harbor (Pads)',
        '7. PORTFOLIO CLOSEOUT', 'Common Energy Service agreements',
        'Dev Docs-Bidding', 'Hawthorne documents',
        'PUBLIC-Dev Docs-Bidding- Shared - Copy',
        'a. Almon', 'b. Lomaside', 'c. Perrydale',
    ]),
    ('13. Kendall CSP Portfolio 5 (2025.112)', [
        '1. Portfolio Client Docs', '2. Portfolio Buyout',
        '3. Portfolio Schedules', '4. Portfolio Dev Docs', '5. Engineering Gen',
        '6. Portfolio Owner Correspond', '7. Portfolio Financials',
        '8. Portfolio Change Management', '9. Utility-Documents-Tracking',
        '10. Permitting', '11. De-Comm Bonds', '12. Portfolio Closeout',
        'DEV DATAROOM - KSI- Hawthorne (OR) EPC',
        'a. Colfax Solar', 'b. Coker Solar', 'c. Crawford Solar',
        'd. Bradley Solar', 'z. ARCHIVE PROJ',
    ]),
    ('15. Dolphin and Shoestring (2025.127)', [
        '1. Dolphin', '2. Shoestring', '3. Portfolio Client Docs',
        '3. Portfolio Schedules', '4. Portfolio Buyout', '4. Portfolio Dev Docs',
        '5. Engineering Gen', '6. Portfolio Owner Correspondence',
        '7. Financials', '8. Portfolio Change Management',
        'R. 5.6.25 Chint Quote',
    ]),
]


# =============================================================================
# Main — runs v3 corpus + schema classifier against all 10 active samples
# =============================================================================

def _run_corpus(corpus, label):
    """Run a (raw, parent, note) tuple list through parse_folder and print."""
    print(f"\n{'STATUS':<7} {'KIND':<10} {'JOB ID':<22} {'NAME':<32} {'NOTE'}")
    print('-' * 130)
    fail = 0
    for raw, parent, note in corpus:
        p = parse_folder(raw, parent=parent)
        rescued = any(
            ('portfolio-prefix subject' in w) or ('active sub-job format' in w) or
            ('active sub-job side' in w) or ('development-phase subject' in w) or
            ('date-prefix loose item' in w) or ('closed-project pattern' in w)
            for w in p.warnings
        )
        chaos_seen = any(w.startswith('[') for w in p.warnings)
        bad = (
            any('unrecognized subject' in w for w in p.warnings)
            and not rescued and not chaos_seen
        )
        status = '  OK' if not bad else 'FAIL'
        if bad:
            fail += 1
        print(f"{status:<7} {p.folder_kind.value:<10} "
              f"{(p.job_id or ''):<22} "
              f"{((p.name or '') or p.raw[:30])[:32]:<32} "
              f"{note}")
    print('-' * 130)
    print(f"{label}: real failures = {fail} / {len(corpus)}")


def main():
    print("=" * 130)
    print("v3 CORPUS — active production (10 projects, 2026-05-16)")
    print("=" * 130)
    _run_corpus(ACTIVE_PRODUCTION_CORPUS, label='v3')

    print()
    print("=" * 130)
    print("SCHEMA CLASSIFIER — 10 active project top-level listings")
    print("=" * 130)
    for label, names in ACTIVE_PROJECT_TOP_LEVELS:
        schema, matches = classify_schema(names)
        dups = detect_duplicate_numbers_at_level(names)
        print(f"\n  [{label}]")
        print(f"    → {schema.value}")
        if matches:
            preview = matches[:3]
            print(f"    signature hits ({len(matches)}): "
                  f"{preview}{' ...' if len(matches) > 3 else ''}")
        for d in dups:
            print(f"    chaos: {d.pattern} — {d.description}")


if __name__ == '__main__':
    main()
