"""
ITS job-folder name parser.

Given a Box/filesystem folder name and (optional) parent context, decide whether
it represents a JOB (or PORTFOLIO/SITE), and if so extract the structured
identity: priority#, job ID, sub-division, name, customer.

Source observations: sandbox zips + Active screenshots (Daniel, 2026-05-14).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Optional


class JobIdKind(str, Enum):
    MODERN = "modern"                     # 2025.201
    MODERN_SUBDIVIDED = "modern_sub"      # 2023.126.3
    LEGACY_5 = "legacy_5"                 # 18157
    LEGACY_SUBDIVIDED = "legacy_sub"      # 14154.3
    RANGE = "range"                       # 20171-20176 or 19115-19121
    BARE_ID_ONLY = "bare_id"              # "2023.333" (id, no name)
    NAMED_ONLY = "named_only"             # "Bear Creek"
    TEMPLATE = "template"                 # 1111A
    DATA_ROOM = "data_room"               # "99. QE Solar Data Room"


class FolderKind(str, Enum):
    JOB = "job"                  # any project / portfolio / site
    SUBJECT = "subject"          # 1. EPC, 12. CLOSEOUT — discipline folder
    UTILITY = "utility"          # 2021 Buyout Trackers, AutoCAD
    SHARED = "shared"            # 00. SHARED FOLDER, 1111A template


@dataclass
class ParsedFolder:
    raw: str
    folder_kind: FolderKind
    # job-specific
    priority: Optional[int] = None        # the "[N]. " prefix
    job_id_kind: Optional[JobIdKind] = None
    job_id: Optional[str] = None          # canonical "2023.126.3" / "14154.3" / "19115-19121"
    year: Optional[int] = None
    sequence: Optional[str] = None
    subdivision: Optional[str] = None     # "3" for .3
    range_start: Optional[str] = None
    range_end: Optional[str] = None
    name: Optional[str] = None
    customer: Optional[str] = None
    customer_separator: Optional[str] = None   # "(", " - ", "  (", None
    warnings: list = field(default_factory=list)


# ---------- pattern primitives ----------

# Priority prefix: "1. ", "12. ", "00. ", "99. " — followed by space
PRIORITY_PREFIX = re.compile(r'^(\d{1,3})\.\s+(.+)$')

# Modern: YYYY.NNN where YYYY is 19xx-21xx, NNN is 1-4 digits
MODERN = re.compile(r'^(?P<year>(?:19|20|21)\d{2})\.(?P<seq>\d{1,4})(?:\.(?P<sub>\d+))?(?:\s+(?P<rest>.+))?$')

# Legacy 5-digit: NNNNN[.S] (no year prefix, 4-6 digits to allow 19122, 14154.3, etc.)
LEGACY = re.compile(r'^(?P<id>\d{4,6})(?:\.(?P<sub>\d+))?(?:\s+(?P<rest>.+))?$')

# Range: NNNNN-NNNNN or NNNNN - NNNNN. Second half can be short-form (1-3 digits) sharing prefix.
RANGE = re.compile(r'^(?P<start>\d{4,6})\s*-\s*(?P<end>\d{1,6})(?:\s+(?P<rest>.+))?$')

# Customer trailing patterns. Tried in order, most specific first.
CUSTOMER_PATTERNS = [
    # "(Customer)" or "  (Customer)" at end — most reliable signal
    (re.compile(r'^(?P<name>.+?)\s{1,2}\((?P<customer>[^)]+)\)\s*$'), 'paren'),
    # " - Customer" at end, where Customer is single token (no spaces / commas)
    (re.compile(r'^(?P<name>.+?)\s+-\s*(?P<customer>[A-Z][A-Za-z0-9]+)\s*$'), 'dash'),
    # "Name- Customer" no space before dash (observed: "Dolphin and Shoestring- Kendall")
    (re.compile(r'^(?P<name>.+?)-\s+(?P<customer>[A-Z][A-Za-z0-9]+)\s*$'), 'dash_tight'),
]

# Known subject-folder names (at portfolio level)
SUBJECT_KEYWORDS = {
    'EPC', 'Buyout', 'Project Schedules', 'Developer Documents', 'Developer Docs',
    'Engineering General', 'Engineering Linc', 'Correspondence', 'Financials',
    'Change Management', 'Utility-Documents', 'Submittals', 'Permitting',
    'CLOSEOUT', 'ESS Contract & LNTP', 'Accounting', 'Subcontractors & Ve',
    'Submittal Logs',
}

# Known utility / non-job folder names at root or Active level
UTILITY_NAMES = {
    '2021 Buyout General', '2021 Buyout Trackers', 'Hydro Generator',
    'AutoCAD', 'Safety', 'New Hire Packet', 'Crawford Site compare files',
    '2. Bidding', '2. Portfolio Financials', '3. Misc', '5. Warranty Claims',
    '6. Website', '9. ProCore Back_Ups', '10. Statement of Qualifications',
}

# Special / shared folders
SHARED_NAMES = {'00. SHARED FOLDER', '99. QE Solar Data Room'}
TEMPLATE_PATTERN = re.compile(r'^1111A')


def parse_folder(raw: str, parent: str = 'unknown') -> ParsedFolder:
    """
    parent hints:
      'root'      — top-level, has Active/, Bidding/, etc.
      'active'    — inside 1. Active/, expect portfolios + sub-jobs + utilities
      'portfolio' — inside a portfolio, expect subject folders + sub-job folders
      'site'      — inside a site, expect Office/Field/loose files
      'unknown'   — make best effort
    """
    p = ParsedFolder(raw=raw, folder_kind=FolderKind.JOB)  # tentative

    # --- early classifiers ---
    if raw in SHARED_NAMES:
        p.folder_kind = FolderKind.SHARED
        return p

    if TEMPLATE_PATTERN.match(raw):
        p.folder_kind = FolderKind.SHARED
        p.job_id_kind = JobIdKind.TEMPLATE
        p.name = '1111A template'
        return p

    if raw in UTILITY_NAMES:
        p.folder_kind = FolderKind.UTILITY
        p.name = raw
        return p

    # --- strip priority prefix if present ---
    m = PRIORITY_PREFIX.match(raw)
    if m:
        p.priority = int(m.group(1))
        body = m.group(2)
    else:
        body = raw

    # --- if priority is present AND no job-ID pattern after it → SUBJECT folder ---
    looks_like_id = (
        MODERN.match(body) or
        RANGE.match(body) or
        (LEGACY.match(body) and re.match(r'^\d{4,6}', body))
    )

    if p.priority is not None and not looks_like_id:
        # something like "1. EPC", "12. CLOSEOUT"
        p.folder_kind = FolderKind.SUBJECT
        p.name = body
        return p

    # --- try RANGE pattern first (range overlaps with bare-id) ---
    m = RANGE.match(body)
    if m:
        p.job_id_kind = JobIdKind.RANGE
        p.range_start = m.group('start')
        p.range_end = m.group('end')
        p.job_id = f"{m.group('start')}-{m.group('end')}"
        rest = (m.group('rest') or '').strip()
        if rest:
            p.name, p.customer, p.customer_separator = _split_name_customer(rest)
        return p

    # --- try MODERN pattern ---
    m = MODERN.match(body)
    if m:
        p.year = int(m.group('year'))
        p.sequence = m.group('seq')
        p.subdivision = m.group('sub')
        if p.subdivision:
            p.job_id_kind = JobIdKind.MODERN_SUBDIVIDED
            p.job_id = f"{p.year}.{p.sequence}.{p.subdivision}"
        else:
            p.job_id_kind = JobIdKind.MODERN
            p.job_id = f"{p.year}.{p.sequence}"
        rest = (m.group('rest') or '').strip()
        if not rest:
            p.job_id_kind = JobIdKind.BARE_ID_ONLY
            p.warnings.append('no name component')
        else:
            p.name, p.customer, p.customer_separator = _split_name_customer(rest)
        return p

    # --- try LEGACY pattern ---
    m = LEGACY.match(body)
    if m:
        legacy_id = m.group('id')
        p.subdivision = m.group('sub')
        if p.subdivision:
            p.job_id_kind = JobIdKind.LEGACY_SUBDIVIDED
            p.job_id = f"{legacy_id}.{p.subdivision}"
        else:
            p.job_id_kind = JobIdKind.LEGACY_5
            p.job_id = legacy_id
        rest = (m.group('rest') or '').strip()
        if rest:
            p.name, p.customer, p.customer_separator = _split_name_customer(rest)
        else:
            p.warnings.append('legacy id with no name')
        return p

    # --- fallback: NAMED-ONLY ---
    # Could be "Bear Creek" / "ECOS Indiana Nipsco" / or a utility we don't know
    if parent in ('active', 'root', 'unknown'):
        # at root/active/unknown, untagged folders may be jobs OR utilities — flag
        p.job_id_kind = JobIdKind.NAMED_ONLY
        p.name, p.customer, p.customer_separator = _split_name_customer(body)
        p.warnings.append(f'named-only at {parent} — could be job or utility, no ID present')
    else:
        # at portfolio/site level, unknown name = subject we don't recognize
        p.folder_kind = FolderKind.SUBJECT
        p.name = body
        p.warnings.append('unrecognized subject folder')

    return p


def _split_name_customer(rest: str) -> tuple[str, Optional[str], Optional[str]]:
    """Try each customer-extraction pattern. Return (name, customer, separator)."""
    for pattern, kind in CUSTOMER_PATTERNS:
        m = pattern.match(rest)
        if m:
            return _clean_name(m.group('name')), m.group('customer').strip(), kind
    return _clean_name(rest), None, None


def _clean_name(s: str) -> str:
    """Strip leading dashes/whitespace that leak from prefix-split."""
    return re.sub(r'^[\s\-]+', '', s).strip()


# --------- test corpus ---------
TEST_CORPUS = [
    # (raw, parent_hint, label)
    # Sandbox zips (closed projects)
    ('14107 - Charlotte (Ace)',                'unknown',   'sandbox: legacy + customer paren'),
    ('14130.1 Dooley (Mortenson)',             'unknown',   'sandbox: legacy subdivided'),
    ('14130.3 Plum (Mortenson)',               'unknown',   'sandbox: legacy subdivided'),
    ('14150 Fort Picket (Schnieder)',          'unknown',   'sandbox: legacy + customer paren'),
    ('14154 Lakeland (Invenergy)',             'unknown',   'sandbox: legacy + customer paren'),
    ('2018.111 Neighborhood Portfolio',        'unknown',   'sandbox: modern, no customer'),
    ('Bear Creek',                             'unknown',   'sandbox: named only'),
    ('ECOS Indiana Nipsco',                    'unknown',   'sandbox: named only with state'),

    # Active screenshots
    ('00. SHARED FOLDER',                      'active',    'special: shared'),
    ('1. 2025.201 KSI 4 IL',                   'active',    'active: modern single-site'),
    ('2. 2024.335 Forefront - Luminace',       'active',    'active: dash customer'),
    ('3. 2023.126 Oregon - Kendall',           'active',    'active: dash customer'),
    ('4. 2025.358 Keystone  (Coast)',          'active',    'active: double-space + paren'),
    ('5. 2025.108 Bonacci 1&2 (Generate)',     'active',    'active: ampersand + paren'),
    ('6. 2025.364 Steger & Roxbury',           'active',    'active: ampersand, no customer'),
    ('7. 20171 - 20176 OR Portfolio (SPI)',    'active',    'active: range + paren'),
    ('8. 2022.123 GK3 Portfolio',              'active',    'active: portfolio, no customer'),
    ('9. 2021.122 CSP 2&3 Portfolio (Luminace)','active',   'active: portfolio + paren'),
    ('10. 2022.1126 Zena (UGE)',               'active',    'active: 4-digit seq'),
    ('11. 2022.7 Coast Energy (Oregon)',       'active',    'active: 1-digit seq + state-as-customer'),
    ('12. 2024.112 Almon, Lomaside, Perrydale (Ha...)','active','active: multi-name + truncated customer'),
    ('13. 2025.112 Kendall CSP Portfolio 5',   'active',    'active: implicit customer in name'),
    ('15. 2025.127 Dolphin and Shoestring- Kendall', 'active','active: dash_tight (no space before dash)'),
    ('16. 2023.109 PAC2 Blackwell & Wood River','active',   'active: no customer'),
    ('20. 2021.121 TX RGEC (Luminace)',        'active',    'active: state-prefix + paren'),
    ('23. 2021.125 Greenkey PAC 4',            'active',    'active: no customer'),
    ('25. 19122 Danville (CDA)',               'active',    'active: legacy 5-digit'),
    ('28. 16101-115 NC Portfolio (CapDyn)',    'active',    'active: short-range + paren'),
    ('33. 19115-19121 OR Portfolio (CDA)',     'active',    'active: range + paren'),
    ('34. 14154.3 Unadilla',                   'active',    'active: legacy subdivided'),
    ('35. 18157 Lookout',                      'active',    'active: legacy, no customer'),
    ('99. QE Solar Data Room',                 'active',    'special: data room'),
    ('1111A (Copy for new projects)',          'active',    'special: template'),
    ('2021 Buyout General',                    'active',    'utility'),
    ('2021 Buyout Trackers',                   'active',    'utility'),
    ('2023.333',                               'active',    'bare id'),
    ('Hydro Generator',                        'active',    'utility (named-only ambiguous)'),

    # Portfolio interior (Kendall)
    ('1. EPC',                                 'portfolio', 'subject'),
    ('2. Buyout',                              'portfolio', 'subject'),
    ('3. Project Schedules',                   'portfolio', 'subject'),
    ('5. Engineering General',                 'portfolio', 'subject (priority duplicate)'),
    ('5. Rodeo Entrance Draw...',              'portfolio', 'subject (priority duplicate)'),
    ('12. CLOSEOUT',                           'portfolio', 'subject'),
    ('2023.126.1 - Rodeo',                     'portfolio', 'sub-job, no prefix'),
    ('2023.126.2 - Apricus',                   'portfolio', 'sub-job, no prefix'),
    ('2023.126.3 - Lincoln',                   'portfolio', 'sub-job, no prefix'),

    # Site interior (Lincoln Office)
    ('1. ESS Contract & LNTP ...',             'site',      'office subject'),
    ('2. Accounting (to owner)',               'site',      'office subject with (to ___)'),
    ('10. Lincoln (to share)',                 'site',      'office subject with (to share)'),
]


def main():
    print(f"{'STATUS':<7} {'KIND':<10} {'JOB ID':<22} {'NAME':<35} {'CUST':<15} {'NOTE'}")
    print('-' * 130)
    fail_count = 0
    for raw, parent, label in TEST_CORPUS:
        p = parse_folder(raw, parent=parent)
        # heuristic: a parse is "ok" if folder_kind isn't JOB-with-warning or it's an expected case
        bad = False
        if p.warnings and label != 'utility (named-only ambiguous)':
            # named-only at active is *expected* to flag, but other warnings = parser miss
            if 'named-only at active' not in ' '.join(p.warnings):
                bad = True
        status = '  OK' if not bad else 'WARN'
        if bad:
            fail_count += 1
        cust = p.customer or ''
        name = (p.name or '')[:33]
        job_id = p.job_id or ''
        print(f"{status:<7} {p.folder_kind.value:<10} {job_id:<22} {name:<35} {cust:<15} {label}")
        if p.warnings:
            for w in p.warnings:
                print(f"        \u21b3 warn: {w}")
    print('-' * 130)
    print(f"FAIL count (unexpected): {fail_count} / {len(TEST_CORPUS)}")


if __name__ == '__main__':
    main()
