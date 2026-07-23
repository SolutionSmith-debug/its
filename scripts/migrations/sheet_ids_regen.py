"""Regenerate shared/sheet_ids.py (and its satellite ID surfaces) from the LIVE tenant.

The circle-closer for the builder family. Every scripts/migrations/build_*.py script
find-or-creates its workspace/folder/sheet and — until this tool existed — printed a
FLIP BLOCK for the operator to hand-paste into shared/sheet_ids.py. This script
replaces the hand-paste: it resolves every constant's live ID by NAME (the same
exact-name discipline the builders use), rewrites the constant values IN PLACE
(comments, alignment, and structure untouched), and remaps the satellite surfaces
that duplicate the sheet-ID datum (HOUSE_REFLEXES §1 multi-surface fan-out):

  - shared/sheet_ids.py            — every WORKSPACE_*/FOLDER_*/SHEET_* constant +
                                     the DAEMON_HEALTH_COLUMNS column-id dict
                                     (resolved by column TITLE; heartbeats are
                                     column-id-keyed and go silently dark on a
                                     stale dict).
  - (operator_dashboard/system_map.py reads shared.sheet_ids directly since
                                     2026-07-23 — no remap needed; the repo sweep still
                                     catches any reintroduced literal.)
  - safety_reports/week_folder.py  — TEMPLATE_DAILY_REPORTS_SHEET_ID /
                                     TEMPLATE_WEEKLY_ROLLUP_SHEET_ID (legacy
                                     email-path week-scaffold templates).
  - docs/doctrine_manifest.yaml    — the canonical_sheets ids compared against
                                     sheet_ids.py by check_doctrine_drift M4
                                     (CI-BLOCKING); in the integer-remap scope
                                     since 2026-07-23 (PR #670's proven miss).

After rewriting, a repo-wide sweep greps every remaining occurrence of a REPLACED
old id in *.py + *.yaml/*.yml/*.md files and prints a report — an old id
surviving anywhere else (a test pin, a runbook example, a doc) is surfaced,
never silent. Non-Python sweep hits are REPORT-ONLY, never auto-rewritten
(prose ids are sometimes deliberately historical).

Resolution model
    A declarative REGISTRY maps each constant to its canonical (workspace,
    folder-path, sheet-name) location — the names the BUILDERS create, byte-exact
    (em-dash discipline included). Resolution walks `GET /workspaces?includeAll=true`
    then `GET /workspaces/{id}?loadAll=true` per registry workspace. Fail-closed
    posture matches the builder family:
      - duplicate names at ANY path step -> the constant is AMBIGUOUS; its value is
        NEVER written (the FLIP-BLOCK-leak rule, applied to files instead of stdout).
      - a missing REQUIRED object -> the constant is MISSING; value left untouched.
        Missing/ambiguous constants make the run exit nonzero under --strict (and
        always under --check); without --strict the tool updates what it CAN
        resolve — the mode the standup orchestrator uses between builder stages,
        when later-stage objects deliberately do not exist yet.
      - OPTIONAL constants (dormant lanes: ITS_Trusted_Contacts) resolve to their
        live id when present and are left at their current value (0) when absent —
        absent-optional is not an error.

Modes
    default   — resolve + print the plan (what would change); writes nothing.
    --write   — apply the rewrite to the three files. Local file edits only; the
                tenant is never written. Review lands via `git diff` + PR.
    --check   — read-only parity probe: every required constant resolves, matches
                the file value, and every DAEMON_HEALTH_COLUMNS title maps to the
                live column id. Exit 0 = the file IS the tenant; nonzero otherwise.
                This is the post-stand-up gate (and is safe any time).

Auth: ITS_SMARTSHEET_TOKEN from macOS Keychain. Read-only against Smartsheet.

Run from ~/its (or a worktree):
    python3 scripts/migrations/sheet_ids_regen.py            # plan
    python3 scripts/migrations/sheet_ids_regen.py --write    # apply
    python3 scripts/migrations/sheet_ids_regen.py --check    # parity probe
"""
from __future__ import annotations

import argparse
import dataclasses
import pathlib
import re
import sys
import time
from typing import Any

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

import requests  # type: ignore[import-untyped]  # noqa: E402

from shared import keychain  # noqa: E402

BASE = "https://api.smartsheet.com/2.0"
REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]

SHEET_IDS_PATH = REPO_ROOT / "shared" / "sheet_ids.py"
WEEK_FOLDER_PATH = REPO_ROOT / "safety_reports" / "week_folder.py"
# docs/doctrine_manifest.yaml pins canonical sheet ids that check_doctrine_drift
# M4 (CI-BLOCKING --strict) compares against shared/sheet_ids.py — its own
# header says the ids mirror sheet_ids.py, so the remap enforces the stated
# contract. A rebuild that skips it RED-lights the landing PR until someone
# remembers the hand-fix (PR #670 had to; proven miss, 2026-07-23 review).
DOCTRINE_MANIFEST_PATH = REPO_ROOT / "docs" / "doctrine_manifest.yaml"

# Report-only sweep scope: *.py (as always) plus yaml/markdown, so a runbook or
# doc example pinning a replaced id is SURFACED in the same run instead of
# surviving silently. Non-Python files are never auto-rewritten by the sweep —
# prose ids are sometimes deliberately historical (session logs, audit records);
# the one yaml WRITE target is DOCTRINE_MANIFEST_PATH via the remap list above.
SWEEP_GLOBS: tuple[str, ...] = ("*.py", "*.yaml", "*.yml", "*.md")

# ---- canonical names (byte-exact; builder canon, em-dash discipline) ------

WS_SYSTEM = "ITS — System"
WS_SAFETY_PORTAL = "ITS –– Safety Portal"  # TWO EN DASHes — live-verified
WS_HUMAN_REVIEW = "ITS — Human Review"
WS_OPERATIONS = "ITS — Operations"
WS_ARCHIVE = "ITS — Archive"
WS_PROGRESS = "ITS — Progress Reporting"
WS_PURCHASE_ORDERS = "ITS — Purchase Orders"
WS_SUBCONTRACTS = "ITS — Subcontracts"
WS_DEMO = "Forefront Portfolio — ITS Demo"

_EM = "—"
for _name in (WS_SYSTEM, WS_HUMAN_REVIEW, WS_OPERATIONS, WS_ARCHIVE, WS_PROGRESS,
              WS_PURCHASE_ORDERS, WS_SUBCONTRACTS, WS_DEMO):
    if _name.count(_EM) != 1 or f" {_EM} " not in _name:
        raise ValueError(f"canonical_name_dash_corrupted: {_name!r}")
if WS_SAFETY_PORTAL != "ITS –– Safety Portal" or "—" in WS_SAFETY_PORTAL:
    raise ValueError("canonical_name_dash_corrupted: Safety Portal uses U+2013 U+2013")


@dataclasses.dataclass(frozen=True)
class Target:
    """Where a constant's object lives: workspace -> folder path -> optional sheet."""

    workspace: str
    folder_path: tuple[str, ...] = ()
    sheet: str | None = None
    optional: bool = False  # absent-optional keeps its current value (dormant lanes)

    @property
    def kind(self) -> str:
        if self.sheet is not None:
            return "sheet"
        return "folder" if self.folder_path else "workspace"

    def describe(self) -> str:
        parts = [self.workspace, *self.folder_path]
        if self.sheet is not None:
            parts.append(self.sheet)
        return " / ".join(parts)


_DEMO_PROJECTS: tuple[tuple[str, str], ...] = (
    # (constant suffix, live/builder folder name — Bradleys carry the BBCHS suffix)
    ("BRADLEY_1", "Bradley 1 (BBCHS 1)"),
    ("BRADLEY_2", "Bradley 2 (BBCHS 2)"),
    ("BRIMFIELD_1", "Brimfield 1"),
    ("BRIMFIELD_2", "Brimfield 2"),
    ("HUNTLEY", "Huntley"),
    ("ROCKFORD", "Rockford"),
)

FOLDER_ACTIVE_PROJECTS_NAME = "01 — Active Projects"
FOLDER_ROLLUPS_NAME = "02 — Portfolio Rollups"
FOLDER_FIELD_REPORTS_NAME = "03 — Field Reports (JHA/TBT)"
TEMPLATE_WEEK_FOLDER_NAME = "Week of 2026-03-09"

# The registry: shared/sheet_ids.py constant -> canonical live location.
# Names are the ones the BUILDERS create (build_* canon), not whatever a UI
# rename may have produced — after a rebuild the builder canon IS the live name.
REGISTRY: dict[str, Target] = {
    # Workspaces
    "WORKSPACE_DEMO": Target(WS_DEMO),
    "WORKSPACE_SYSTEM": Target(WS_SYSTEM),
    "WORKSPACE_HUMAN_REVIEW": Target(WS_HUMAN_REVIEW),
    "WORKSPACE_OPERATIONS": Target(WS_OPERATIONS),
    "WORKSPACE_ARCHIVE": Target(WS_ARCHIVE),
    "WORKSPACE_SAFETY_PORTAL": Target(WS_SAFETY_PORTAL),
    "WORKSPACE_PROGRESS_REPORTING": Target(WS_PROGRESS),
    "WORKSPACE_PURCHASE_ORDERS": Target(WS_PURCHASE_ORDERS),
    "WORKSPACE_SUBCONTRACTS": Target(WS_SUBCONTRACTS),
    # Demo / portfolio folders
    "FOLDER_ACTIVE_PROJECTS": Target(WS_DEMO, (FOLDER_ACTIVE_PROJECTS_NAME,)),
    "FOLDER_PORTFOLIO_ROLLUPS": Target(WS_DEMO, (FOLDER_ROLLUPS_NAME,)),
    "FOLDER_FIELD_REPORTS": Target(WS_DEMO, (FOLDER_FIELD_REPORTS_NAME,)),
    # System folders
    "FOLDER_SYSTEM_CONFIG": Target(WS_SYSTEM, ("01 — Config",)),
    "FOLDER_SYSTEM_LOGS": Target(WS_SYSTEM, ("02 — Logs",)),
    "FOLDER_SYSTEM_QUEUES": Target(WS_SYSTEM, ("03 — Queues",)),
    "FOLDER_SYSTEM_DAEMONS": Target(WS_SYSTEM, ("04 — Daemons",)),
    # Human Review folders
    "FOLDER_HR_SAFETY_REPORTS": Target(WS_HUMAN_REVIEW, ("01 — Safety Reports",)),
    "FOLDER_HR_SUBCONTRACTS": Target(WS_HUMAN_REVIEW, ("02 — Subcontracts",)),
    "FOLDER_HR_PURCHASE_ORDERS_AND_MATERIALS": Target(
        WS_HUMAN_REVIEW, ("03 — Purchase Orders & Materials",)),
    "FOLDER_HR_EMAIL_TRIAGE": Target(WS_HUMAN_REVIEW, ("04 — Email Triage",)),
    "FOLDER_HR_AI_EMPLOYEE": Target(WS_HUMAN_REVIEW, ("05 — AI Employee",)),
    "FOLDER_HR_PERSONNEL": Target(WS_HUMAN_REVIEW, ("06 — Personnel",)),
    # Operations / Archive / Safety-Portal folders
    "FOLDER_OPERATIONS_MASTER_DBS": Target(WS_OPERATIONS, ("Master Databases",)),
    "FOLDER_ARCHIVE_CLOSED_PROJECTS": Target(WS_ARCHIVE, ("Closed Projects",)),
    "FOLDER_SAFETY_PORTAL": Target(WS_SAFETY_PORTAL, ("00_Safety Portal",)),
    "FOLDER_FORM_CATALOG": Target(WS_SAFETY_PORTAL, ("00_Form Catalog",)),
    # Progress / PO / Subcontracts folders
    "FOLDER_PROGRESS_CONTROL": Target(WS_PROGRESS, ("Control",)),
    "FOLDER_PO_CONTROL": Target(WS_PURCHASE_ORDERS, ("Control",)),
    "FOLDER_PO_JOBS": Target(WS_PURCHASE_ORDERS, ("Jobs",)),
    "FOLDER_SC_CONTROL": Target(WS_SUBCONTRACTS, ("Control",)),
    "FOLDER_SC_JOBS": Target(WS_SUBCONTRACTS, ("Jobs",)),
    # System sheets
    "SHEET_CONFIG": Target(WS_SYSTEM, ("01 — Config",), "ITS_Config"),
    "SHEET_PICKLIST_SYNC_CONFIG": Target(
        WS_SYSTEM, ("01 — Config",), "Picklist_Sync_Config"),
    "SHEET_TRUSTED_CONTACTS": Target(
        WS_SYSTEM, ("01 — Config",), "ITS_Trusted_Contacts", optional=True),
    "SHEET_PROJECT_ROUTING": Target(
        WS_SYSTEM, ("01 — Config",), "ITS_Project_Routing"),
    "SHEET_ERRORS": Target(WS_SYSTEM, ("02 — Logs",), "ITS_Errors"),
    "SHEET_QUARANTINE": Target(WS_SYSTEM, ("02 — Logs",), "ITS_Quarantine"),
    "SHEET_REVIEW_QUEUE": Target(WS_SYSTEM, ("03 — Queues",), "ITS_Review_Queue"),
    "SHEET_DAEMON_HEALTH": Target(WS_SYSTEM, ("04 — Daemons",), "ITS_Daemon_Health"),
    # Human-review sheets (WPR_Pending_Review is decommissioned-but-present; the
    # legacy-workspace builder recreates it for constant parity)
    "SHEET_WPR_PENDING_REVIEW": Target(
        WS_HUMAN_REVIEW, ("01 — Safety Reports",), "WPR_Pending_Review",
        optional=True),
    "SHEET_TIME_OFF": Target(WS_HUMAN_REVIEW, ("06 — Personnel",), "ITS_Time_Off"),
    # Operations master DBs (Vendor DB decommissioned-but-present, same treatment)
    "SHEET_VENDOR_DB": Target(
        WS_OPERATIONS, ("Master Databases",), "Vendor DB", optional=True),
    "SHEET_SUBCONTRACTOR_DB": Target(
        WS_OPERATIONS, ("Master Databases",), "Subcontractor DB"),
    "SHEET_EQUIPMENT_MASTER": Target(
        WS_OPERATIONS, ("Master Databases",), "Equipment Master"),
    # Safety Portal sheets
    "SHEET_ACTIVE_JOBS": Target(
        WS_SAFETY_PORTAL, ("00_Safety Portal",), "ITS_Active_Jobs"),
    "SHEET_FORMS_CATALOG": Target(
        WS_SAFETY_PORTAL, ("00_Form Catalog",), "ITS_Forms_Catalog"),
    "SHEET_WSR_HUMAN_REVIEW": Target(
        WS_SAFETY_PORTAL, ("00_Safety Portal",), "WSR_human_review"),
    "SHEET_ORPHANED_REPORTS": Target(
        WS_SAFETY_PORTAL, ("00_Safety Portal",), "Orphaned Reports"),
    # Progress sheets
    "SHEET_WPR_HUMAN_REVIEW": Target(WS_PROGRESS, ("Control",), "WPR_human_review"),
    "SHEET_ACTIVE_JOBS_PROGRESS": Target(
        WS_PROGRESS, ("Control",), "ITS_Active_Jobs_Progress"),
    # Purchase Orders sheets
    "SHEET_ITS_VENDORS": Target(WS_PURCHASE_ORDERS, ("Control",), "ITS_Vendors"),
    "SHEET_PO_LOG": Target(WS_PURCHASE_ORDERS, ("Control",), "PO_Log"),
    "SHEET_PO_PENDING_REVIEW": Target(
        WS_PURCHASE_ORDERS, ("Control",), "PO_Pending_Review"),
    "SHEET_ESTIMATE_LOG": Target(WS_PURCHASE_ORDERS, ("Control",), "Estimate_Log"),
    "SHEET_RFQ_LOG": Target(WS_PURCHASE_ORDERS, ("Control",), "RFQ_Log"),
    "SHEET_RFQ_PENDING_REVIEW": Target(
        WS_PURCHASE_ORDERS, ("Control",), "RFQ_Pending_Review"),
    # Subcontracts sheets
    "SHEET_ITS_SUBCONTRACTORS": Target(
        WS_SUBCONTRACTS, ("Control",), "ITS_Subcontractors"),
    "SHEET_SUBCONTRACT_LOG": Target(WS_SUBCONTRACTS, ("Control",), "Subcontract_Log"),
    "SHEET_SUBCONTRACT_PENDING_REVIEW": Target(
        WS_SUBCONTRACTS, ("Control",), "Subcontract_Pending_Review"),
}

# Per-project demo folders (both trees), generated to keep the table readable.
for _suffix, _folder in _DEMO_PROJECTS:
    REGISTRY[f"FOLDER_PROJECT_{_suffix}"] = Target(
        WS_DEMO, (FOLDER_ACTIVE_PROJECTS_NAME, _folder))
    REGISTRY[f"FOLDER_FIELD_REPORTS_{_suffix}"] = Target(
        WS_DEMO, (FOLDER_FIELD_REPORTS_NAME, _folder))

# Constants living in OTHER files, resolved by the same registry mechanics.
# file path (repo-relative) -> {constant name -> Target}
EXTERNAL_CONSTANTS: dict[str, dict[str, Target]] = {
    "safety_reports/week_folder.py": {
        "TEMPLATE_DAILY_REPORTS_SHEET_ID": Target(
            WS_DEMO,
            (FOLDER_FIELD_REPORTS_NAME, "Bradley 1 (BBCHS 1)", TEMPLATE_WEEK_FOLDER_NAME),
            "Daily Reports — Week of 2026-03-09", optional=True),
        "TEMPLATE_WEEKLY_ROLLUP_SHEET_ID": Target(
            WS_DEMO,
            (FOLDER_FIELD_REPORTS_NAME, "Bradley 1 (BBCHS 1)", TEMPLATE_WEEK_FOLDER_NAME),
            "Weekly Rollup — Week of 2026-03-09", optional=True),
    },
}

# DAEMON_HEALTH_COLUMNS dict key -> live column TITLE on ITS_Daemon_Health.
# Titles are build_system_sheets.py canon ("Total Cycles Today" is deliberate —
# lifetime-monotonic semantics under the legacy title, see sheet_ids.py comment).
DAEMON_HEALTH_TITLE_BY_KEY: dict[str, str] = {
    "daemon_name": "Daemon Name",
    "workstream": "Workstream",
    "enabled": "Enabled",
    "interval_seconds": "Interval Seconds",
    "source_id": "Source ID",
    "last_heartbeat": "Last Heartbeat",
    "last_cycle_status": "Last Cycle Status",
    "last_cycle_items_processed": "Last Cycle Items Processed",
    "total_cycles": "Total Cycles Today",
    "last_error_summary": "Last Error Summary",
    "last_error_correlation_id": "Last Error Correlation ID",
    "notes": "Notes",
}

# ---- sentinels ------------------------------------------------------------

AMBIGUOUS = "AMBIGUOUS"
MISSING = "MISSING"
ABSENT_OPTIONAL = "ABSENT_OPTIONAL"

Resolution = int | str  # int = resolved id; str = one of the sentinels above


# ---- live-tenant fetch (read-only) ---------------------------------------


def _headers() -> dict[str, str]:
    token = keychain.get_secret("ITS_SMARTSHEET_TOKEN")
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def fetch_workspaces() -> list[dict[str, Any]]:
    r = requests.get(f"{BASE}/workspaces?includeAll=true", headers=_headers(), timeout=30)
    r.raise_for_status()
    return list(r.json().get("data", []))


def fetch_workspace_tree(workspace_id: int) -> dict[str, Any]:
    r = requests.get(f"{BASE}/workspaces/{workspace_id}?loadAll=true",
                     headers=_headers(), timeout=60)
    r.raise_for_status()
    return dict(r.json())


def fetch_sheet_columns(sheet_id: int) -> list[dict[str, Any]]:
    r = requests.get(f"{BASE}/sheets/{sheet_id}/columns?includeAll=true",
                     headers=_headers(), timeout=30)
    r.raise_for_status()
    return list(r.json().get("data", []))


# ---- resolution (pure, testable on fixture trees) ------------------------


def resolve_in_tree(tree: dict[str, Any], target: Target) -> Resolution:
    """Walk one workspace tree to the target. Duplicate names at ANY step -> AMBIGUOUS."""
    node = tree
    for name in target.folder_path:
        matches = [f for f in node.get("folders", []) or [] if f.get("name") == name]
        if len(matches) > 1:
            return AMBIGUOUS
        if not matches:
            return ABSENT_OPTIONAL if target.optional else MISSING
        node = matches[0]
    if target.sheet is None:
        if not target.folder_path:  # workspace itself — id from the tree root
            return int(tree["id"])
        return int(node["id"])
    sheets = [s for s in node.get("sheets", []) or [] if s.get("name") == target.sheet]
    if len(sheets) > 1:
        return AMBIGUOUS
    if not sheets:
        return ABSENT_OPTIONAL if target.optional else MISSING
    return int(sheets[0]["id"])


def resolve_all(
    registry: dict[str, Target],
    workspaces: list[dict[str, Any]],
    trees: dict[str, dict[str, Any]],
) -> dict[str, Resolution]:
    """Resolve every constant. `trees` is workspace-name -> loadAll tree (may be partial)."""
    by_name: dict[str, list[dict[str, Any]]] = {}
    for ws in workspaces:
        by_name.setdefault(str(ws.get("name")), []).append(ws)
    out: dict[str, Resolution] = {}
    for const, target in registry.items():
        candidates = by_name.get(target.workspace, [])
        if len(candidates) > 1:
            out[const] = AMBIGUOUS
            continue
        if not candidates:
            out[const] = ABSENT_OPTIONAL if target.optional else MISSING
            continue
        tree = trees.get(target.workspace)
        if tree is None:
            out[const] = ABSENT_OPTIONAL if target.optional else MISSING
            continue
        out[const] = resolve_in_tree(tree, target)
    return out


# ---- file rewrite (pure helpers, testable on fixture text) ---------------

_CONST_RE_TMPL = r"^(?P<prefix>{const}\s*=\s*)(?P<value>\d+)"


def read_current_values(text: str, constants: list[str]) -> dict[str, int]:
    """Current integer value of each constant present in `text` (missing = absent)."""
    out: dict[str, int] = {}
    for const in constants:
        m = re.search(_CONST_RE_TMPL.format(const=re.escape(const)), text, re.MULTILINE)
        if m:
            out[const] = int(m.group("value"))
    return out


def rewrite_constants(text: str, new_values: dict[str, int]) -> tuple[str, list[str]]:
    """Replace `CONST = <int>` values in place. Returns (new_text, constants_changed)."""
    changed: list[str] = []
    for const, value in new_values.items():
        pattern = _CONST_RE_TMPL.format(const=re.escape(const))

        def _sub(m: re.Match[str], _v: int = value, _c: str = const) -> str:
            if int(m.group("value")) != _v:
                changed.append(_c)
            return f"{m.group('prefix')}{_v}"

        text = re.sub(pattern, _sub, text, count=1, flags=re.MULTILINE)
    return text, changed


def rewrite_dict_values(text: str, dict_updates: dict[str, int]) -> tuple[str, list[str]]:
    """Replace `"key": <int>,` values (DAEMON_HEALTH_COLUMNS). Returns (text, changed)."""
    changed: list[str] = []
    for key, value in dict_updates.items():
        pattern = rf'(?P<prefix>"{re.escape(key)}":\s*)(?P<value>\d+)'

        def _sub(m: re.Match[str], _v: int = value, _k: str = key) -> str:
            if int(m.group("value")) != _v:
                changed.append(_k)
            return f"{m.group('prefix')}{_v}"

        text = re.sub(pattern, _sub, text, count=1)
    return text, changed


def rewrite_integer_remap(text: str, remap: dict[int, int]) -> tuple[str, int]:
    """Replace standalone old ids with new ids. Returns (text, replacements).

    Two-phase (old -> placeholder -> new) so a chained remap — some pair's NEW
    id textually equal to another pair's OLD id — can never double-replace.
    """
    n = 0
    placeholders: dict[str, int] = {}
    for i, (old, new) in enumerate(sorted(remap.items())):
        if old == new:
            continue
        token = f"\x00REMAP{i}\x00"
        text, count = re.subn(rf"(?<!\d){old}(?!\d)", token, text)
        n += count
        placeholders[token] = new
    for token, new in placeholders.items():
        text = text.replace(token, str(new))
    return text, n


def sweep_repo_for_old_ids(remap: dict[int, int], skip: set[pathlib.Path],
                           root: pathlib.Path | None = None) -> list[str]:
    """file:line hits of REPLACED old ids still present in tracked files.

    Report-only, across SWEEP_GLOBS (*.py + yaml/markdown — the doc scope added
    2026-07-23 after PR #670's hand-fixed manifest miss). `root` is a test seam;
    production sweeps always walk REPO_ROOT.
    """
    stale = {old for old, new in remap.items() if old != new}
    if not stale:
        return []
    root = root if root is not None else REPO_ROOT
    paths: set[pathlib.Path] = set()
    for pattern in SWEEP_GLOBS:
        paths.update(root.rglob(pattern))
    hits: list[str] = []
    for path in sorted(paths):
        if any(part in {".venv", ".venv-wt", "node_modules", ".git"} for part in path.parts):
            continue
        if path.resolve() in skip:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for i, line in enumerate(text.splitlines(), 1):
            for old in stale:
                if re.search(rf"(?<!\d){old}(?!\d)", line):
                    hits.append(f"{path.relative_to(root)}:{i}: {line.strip()[:100]}")
    return hits


# ---- orchestration --------------------------------------------------------


def remap_file_paths() -> list[pathlib.Path]:
    """Files whose integer literals are REWRITTEN by --write (the satellite remap).

    scripts/migrations + tests (*.py), plus docs/doctrine_manifest.yaml — the
    two-phase integer remap (`rewrite_integer_remap`) is plain-text and
    word-boundary-safe, so the format doesn't matter. The wipe tool + self are
    excluded at the call site (see the exemption comment in main()).
    test_remap_scope_includes_doctrine_manifest is the parity teeth.
    """
    paths: list[pathlib.Path] = []
    paths += sorted((REPO_ROOT / "scripts" / "migrations").glob("*.py"))
    paths += sorted((REPO_ROOT / "tests").glob("*.py"))
    paths.append(DOCTRINE_MANIFEST_PATH)
    return paths


def missing_required(
    resolutions: dict[str, Resolution],
    external: dict[str, dict[str, Resolution]],
) -> set[str]:
    """Names of REQUIRED constants that resolved MISSING (AMBIGUOUS excluded —
    duplicates are a real conflict, never a propagation artifact)."""
    miss = {c for c, r in resolutions.items() if r == MISSING}
    for path, res_map in external.items():
        miss |= {f"{path}:{c}" for c, r in res_map.items() if r == MISSING}
    return miss


def _resolve_live() -> tuple[dict[str, Resolution], dict[str, dict[str, Resolution]],
                             dict[str, int]]:
    """Resolve REGISTRY + EXTERNAL_CONSTANTS + DAEMON_HEALTH_COLUMNS against the tenant."""
    workspaces = fetch_workspaces()
    needed = {t.workspace for t in REGISTRY.values()}
    for consts in EXTERNAL_CONSTANTS.values():
        needed |= {t.workspace for t in consts.values()}
    by_name: dict[str, list[dict[str, Any]]] = {}
    for ws in workspaces:
        by_name.setdefault(str(ws.get("name")), []).append(ws)
    trees: dict[str, dict[str, Any]] = {}
    for name in sorted(needed):
        candidates = by_name.get(name, [])
        if len(candidates) == 1:
            trees[name] = fetch_workspace_tree(int(candidates[0]["id"]))

    resolutions = resolve_all(REGISTRY, workspaces, trees)
    external = {
        path: resolve_all(consts, workspaces, trees)
        for path, consts in EXTERNAL_CONSTANTS.items()
    }

    columns_by_key: dict[str, int] = {}
    dh = resolutions.get("SHEET_DAEMON_HEALTH")
    if isinstance(dh, int):
        cols = fetch_sheet_columns(dh)
        id_by_title = {str(c.get("title")): int(c["id"]) for c in cols}
        for key, title in DAEMON_HEALTH_TITLE_BY_KEY.items():
            if title in id_by_title:
                columns_by_key[key] = id_by_title[title]
    return resolutions, external, columns_by_key


def _print_resolution_report(
    resolutions: dict[str, Resolution],
    current: dict[str, int],
    columns_by_key: dict[str, int],
    current_columns: dict[str, int],
) -> tuple[int, int, list[str]]:
    """Print the plan. Returns (n_changes, n_unresolved_required, problem_lines)."""
    changes = 0
    problems: list[str] = []
    for const in sorted(resolutions):
        res = resolutions[const]
        cur = current.get(const)
        if isinstance(res, int):
            if cur != res:
                print(f"  [flip] {const}: {cur} -> {res}")
                changes += 1
        elif res == ABSENT_OPTIONAL:
            print(f"  [skip] {const}: absent (optional/dormant) — value untouched ({cur})")
        else:
            problems.append(f"{const}: {res} ({REGISTRY[const].describe()})")
    for key in sorted(columns_by_key):
        if current_columns.get(key) != columns_by_key[key]:
            print(f"  [flip] DAEMON_HEALTH_COLUMNS[{key!r}]: "
                  f"{current_columns.get(key)} -> {columns_by_key[key]}")
            changes += 1
    missing_cols = set(DAEMON_HEALTH_TITLE_BY_KEY) - set(columns_by_key)
    dh_resolved = isinstance(resolutions.get("SHEET_DAEMON_HEALTH"), int)
    if missing_cols and dh_resolved:
        problems.append(f"DAEMON_HEALTH_COLUMNS titles unresolved: {sorted(missing_cols)}")
    for p in problems:
        print(f"  [unresolved] {p}")
    return changes, len(problems), problems


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Regenerate shared/sheet_ids.py (+ satellites) from the live tenant.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--write", action="store_true", help="Apply the rewrite.")
    mode.add_argument("--check", action="store_true",
                      help="Read-only parity probe (strict).")
    parser.add_argument("--strict", action="store_true",
                        help="Fail on any unresolved REQUIRED constant (implied by --check).")
    parser.add_argument("--retry-missing", type=int, default=0, metavar="N",
                        help="Re-resolve up to N times while required constants are "
                             "MISSING, stopping early once the missing set stops "
                             "shrinking. Absorbs Smartsheet's create->read propagation "
                             "window (§45) when run right after a builder — objects "
                             "created seconds ago can be absent from the workspace "
                             "listing on the first read.")
    parser.add_argument("--retry-delay", type=float, default=3.0, metavar="SECS",
                        help="Delay between --retry-missing attempts (default 3s).")
    parser.add_argument("--expect", action="append", default=[], metavar="CONST",
                        help="A constant the caller's just-finished builder stage MUST "
                             "have created ('CONST', or 'path/file.py:CONST' for "
                             "external constants). Repeatable. Retries target ONLY "
                             "these (no convergence heuristic — propagation lag can "
                             "outlast any single retry, the 2026-07-23 failure), and "
                             "an expected constant still unresolved after the retry "
                             "budget FAILS the run nonzero — the deterministic "
                             "builder->flip contract for the standup interleave.")
    args = parser.parse_args()
    strict = args.strict or args.check

    known = set(REGISTRY) | {
        f"{path}:{c}" for path, consts in EXTERNAL_CONSTANTS.items() for c in consts}
    expect = set(args.expect)
    unknown = expect - known
    if unknown:
        print(f"[abort] unknown --expect name(s): {sorted(unknown)} — must be a "
              "REGISTRY constant or 'path:CONST' from EXTERNAL_CONSTANTS.")
        return 2

    def _resolved_ok(name: str) -> bool:
        if ":" in name:
            path, const = name.split(":", 1)
            return isinstance(external.get(path, {}).get(const), int)
        return isinstance(resolutions.get(name), int)

    print(f"[info] Mode: {'CHECK' if args.check else 'WRITE' if args.write else 'PLAN'}")
    resolutions, external, columns_by_key = _resolve_live()
    missing = missing_required(resolutions, external)
    attempt = 0
    while args.retry_missing and attempt < args.retry_missing:
        target = ({e for e in expect if not _resolved_ok(e)} if expect else missing)
        if not target:
            break
        attempt += 1
        print(f"[info] propagation_probe: {len(target)} "
              f"{'expected' if expect else 'required'} constant(s) unresolved — "
              f"re-resolving (attempt {attempt}/{args.retry_missing}, "
              f"{args.retry_delay:g}s delay)...")
        time.sleep(args.retry_delay)
        resolutions, external, columns_by_key = _resolve_live()
        new_missing = missing_required(resolutions, external)
        if not expect and new_missing == missing:
            print(f"[info] propagation_probe: missing set converged at "
                  f"{len(new_missing)} — treating as genuinely absent "
                  "(objects whose builders have not run yet).")
            break
        missing = new_missing

    still_expected = {e for e in expect if not _resolved_ok(e)}
    if still_expected:
        print(f"[abort] expected_unresolved: {len(still_expected)} constant(s) the "
              f"preceding builder stage should have created did not resolve after "
              f"{attempt} retr{'y' if attempt == 1 else 'ies'}: "
              f"{sorted(still_expected)}. Either propagation is exceptionally slow "
              "(re-run the stage) or the builder did not actually create the object "
              "(investigate before resuming). Nothing was written.")
        return 1

    sheet_ids_text = SHEET_IDS_PATH.read_text(encoding="utf-8")
    current = read_current_values(sheet_ids_text, list(REGISTRY))
    current_columns: dict[str, int] = {}
    for key in DAEMON_HEALTH_TITLE_BY_KEY:
        m = re.search(rf'"{re.escape(key)}":\s*(\d+)', sheet_ids_text)
        if m:
            current_columns[key] = int(m.group(1))

    print("\n[plan] shared/sheet_ids.py:")
    changes, unresolved, problems = _print_resolution_report(
        resolutions, current, columns_by_key, current_columns)

    ext_changes: dict[str, dict[str, int]] = {}
    for path, res_map in external.items():
        file_text = (REPO_ROOT / path).read_text(encoding="utf-8")
        cur_ext = read_current_values(file_text, list(res_map))
        print(f"\n[plan] {path}:")
        for const in sorted(res_map):
            res = res_map[const]
            if isinstance(res, int):
                if cur_ext.get(const) != res:
                    print(f"  [flip] {const}: {cur_ext.get(const)} -> {res}")
                    ext_changes.setdefault(path, {})[const] = res
            elif res == ABSENT_OPTIONAL:
                print(f"  [skip] {const}: absent (optional) — value untouched")
            else:
                problems.append(f"{path}:{const}: {res}")
                print(f"  [unresolved] {const}: {res}")
                unresolved += 1

    # Old->new integer remap for satellite literal rewrites + the repo sweep.
    remap: dict[int, int] = {}
    for const, res in resolutions.items():
        old = current.get(const)
        if isinstance(res, int) and old is not None and old != 0 and old != res:
            remap[old] = res
    for key, new in columns_by_key.items():
        old = current_columns.get(key)
        if old is not None and old != 0 and old != new:
            remap[old] = new

    if args.check:
        ok = changes == 0 and unresolved == 0 and not ext_changes
        print(f"\n[check] {'PARITY OK — files match the live tenant.' if ok else 'MISMATCH.'}")
        if not ok:
            print(f"[check] pending flips={changes + sum(len(v) for v in ext_changes.values())}"
                  f" unresolved={unresolved}")
        return 0 if ok else 1

    if strict and unresolved:
        print(f"\n[abort] {unresolved} unresolved required constant(s) under --strict; "
              "nothing written.")
        return 1

    if not args.write:
        print(f"\n[plan] {changes} sheet_ids flip(s), "
              f"{sum(len(v) for v in ext_changes.values())} satellite flip(s), "
              f"{unresolved} unresolved. Run with --write to apply.")
        return 0

    # ---- apply ----
    new_values = {c: r for c, r in resolutions.items() if isinstance(r, int)}
    sheet_ids_text, changed_consts = rewrite_constants(sheet_ids_text, new_values)
    sheet_ids_text, changed_cols = rewrite_dict_values(sheet_ids_text, columns_by_key)
    SHEET_IDS_PATH.write_text(sheet_ids_text, encoding="utf-8")
    print(f"\n[ok] shared/sheet_ids.py: {len(changed_consts)} constant(s) + "
          f"{len(changed_cols)} DAEMON_HEALTH column id(s) rewritten.")

    for path, res_map in external.items():
        target_path = REPO_ROOT / path
        file_text = target_path.read_text(encoding="utf-8")
        file_text, ch = rewrite_constants(
            file_text, {c: r for c, r in res_map.items() if isinstance(r, int)})
        target_path.write_text(file_text, encoding="utf-8")
        print(f"[ok] {path}: {len(ch)} constant(s) rewritten.")

    # Integer-literal remap across every surface that pins the same datum:
    # system_map MapNodes, builder-local pins (build_wsr_human_review_sheet.py
    # hard-pins FOLDER_SAFETY_PORTAL — the standup interleave depends on this
    # rewrite landing BEFORE that builder runs), test pins, and the doctrine
    # manifest's canonical_sheets ids (check_doctrine_drift M4 parity).
    # wipe_tenant.py is EXEMPT: its allowlist pins the wipe TARGETS (historical,
    # deleted ids) — remapping them to the rebuilt tenant's ids would arm a
    # re-run against the fresh workspaces; updating that allowlist must stay a
    # deliberate, reviewed code change (learned 2026-07-23, first rebuild).
    remap_files = remap_file_paths()
    self_path = pathlib.Path(__file__).resolve()
    wipe_tool = (REPO_ROOT / "scripts" / "migrations" / "wipe_tenant.py").resolve()
    for remap_path in remap_files:
        if remap_path.resolve() in (self_path, wipe_tool):
            continue  # self: registry/doc text; wipe tool: see exemption above
        remap_text = remap_path.read_text(encoding="utf-8")
        remap_text, n_hits = rewrite_integer_remap(remap_text, remap)
        if n_hits:
            remap_path.write_text(remap_text, encoding="utf-8")
            print(f"[ok] {remap_path.relative_to(REPO_ROOT)}: "
                  f"{n_hits} literal(s) remapped.")

    skip = {SHEET_IDS_PATH.resolve(), WEEK_FOLDER_PATH.resolve(), self_path}
    skip |= {p.resolve() for p in remap_files}
    hits = sweep_repo_for_old_ids(remap, skip)
    if hits:
        print(f"\n[WARN] stale_old_ids_survive: {len(hits)} line(s) elsewhere in the repo "
              "still carry a replaced old id — reconcile each (test pins, docs examples) "
              "in the same PR:")
        for h in hits[:60]:
            print(f"    {h}")
        if len(hits) > 60:
            print(f"    ... and {len(hits) - 60} more")
    else:
        print("\n[ok] repo sweep: no replaced old id survives anywhere in *.py.")
    if unresolved:
        print(f"\n[WARN] {unresolved} unresolved constant(s) left untouched (their objects "
              "do not exist yet, or names are ambiguous):")
        for p in problems:
            print(f"    {p}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
