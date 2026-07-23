"""Build the four hand-created legacy workspaces (Human Review / Operations / Archive / Demo).

Closes the last no-builder gap in the workspace family (survey 2026-07-22): these four
workspaces were provisioned by hand during the 2026-05-17 restructure and had NO
migration script, so a tenant wipe (or a fresh production tenant) had no scripted way
to recreate them. One script for the set — they are one coherent gap-class, and none
of them ever gets new members (the live workstreams all build their own workspaces).

What it creates (find-or-create, create-only, exact-name — the D1 family invariants):

  "ITS — Human Review"            6 numbered folders; WPR_Pending_Review (decommissioned
                                  shell, kept for constant/test parity) in 01 — Safety
                                  Reports; ITS_Time_Off in 06 — Personnel.
  "ITS — Operations"              "Master Databases" folder; Subcontractor DB +
                                  Equipment Master (live picklist-sync source) +
                                  Vendor DB (decommissioned shell).
  "ITS — Archive"                 "Closed Projects" folder (the §51 archive-on-closure
                                  target; the live tenant's nested duplicate inner
                                  folder was an artifact and is NOT recreated).
  "Forefront Portfolio — ITS Demo"  "01 — Active Projects" (+6 project folders),
                                  "02 — Portfolio Rollups", "03 — Field Reports
                                  (JHA/TBT)" (+6 project folders), and under
                                  Bradley 1's field reports the "Week of 2026-03-09"
                                  folder holding the TWO week_folder.py TEMPLATE
                                  sheets (Daily Reports / Weekly Rollup). The ~72
                                  per-project demo tracker sheets are CONTENT, not
                                  structure — they are not rebuilt (dumped to JSON by
                                  wipe_tenant.py; restoring them is a separate,
                                  deliberate operation if the demo is ever revived).

Sheet schemas are byte-copies of the live sandbox schemas captured 2026-07-22
(pre-wipe), embedded below — column titles, types, picklist options.

ID capture: NO hand-paste. Run scripts/migrations/sheet_ids_regen.py --write after
this (the standup orchestrator interleaves it automatically) — it resolves every
constant by name, including week_folder.py's two TEMPLATE ids.

Invariants: create-only (GET + create-POST, never PUT/DELETE); exact-name find,
adopt-don't-touch; accessLevel!=OWNER on an adopted workspace fails CLOSED;
duplicate-name PARENTS fail closed, duplicate terminal objects adopt-first with a
WARN; idempotent no-op on re-run; LIVE by default with ONE y/N gate (the prompt is
the control — no bypass flag); --dry-run previews.

Auth: ITS_SMARTSHEET_TOKEN from macOS Keychain.

Run from ~/its (or a worktree):
    python3 scripts/migrations/build_legacy_workspaces.py --dry-run
    python3 scripts/migrations/build_legacy_workspaces.py
"""
from __future__ import annotations

import argparse
import dataclasses
import os
import pathlib
import sys
from typing import Any

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

import requests  # type: ignore[import-untyped]  # noqa: E402

from shared import keychain, smartsheet_client  # noqa: E402

BASE = "https://api.smartsheet.com/2.0"
EM_DASH = "—"


# ---- schemas (live sandbox capture, 2026-07-22) ---------------------------

def _col(title: str, ctype: str = "TEXT_NUMBER", *, primary: bool = False,
         options: list[str] | None = None) -> dict[str, Any]:
    col: dict[str, Any] = {"title": title, "type": ctype}
    if primary:
        col["primary"] = True
    if options:
        col["options"] = options
    return col


TIME_OFF_COLUMNS = [
    _col("Entry", primary=True),
    _col("Person", "CONTACT_LIST"),
    _col("Start Date", "DATE"),
    _col("End Date", "DATE"),
    _col("Reason", "PICKLIST", options=["PTO", "Sick", "Holiday", "Personal", "Other"]),
    _col("Notes"),
]

WPR_PENDING_REVIEW_COLUMNS = [
    _col("Customer", primary=True),
    _col("Job"),
    _col("Week", "DATE"),
    _col("Draft Body"),
    _col("Recipients"),
    _col("Approved for Send", "CHECKBOX"),
    _col("Approved By", "CONTACT_LIST"),
    _col("Approved At", "DATE"),
    _col("Sent At", "DATE"),
    _col("Send Status", "PICKLIST", options=["PENDING", "SENT", "FAILED", "HELD"]),
    _col("Late Send", "CHECKBOX"),
    _col("Notes"),
]

SUBCONTRACTOR_DB_COLUMNS = [
    _col("Subcontractor", primary=True),
    _col("Primary Scope", "PICKLIST", options=[
        "Civil", "Electrical", "Mechanical", "Tree Clearing", "Erosion Control",
        "Entrance Install", "Concrete", "Trucking", "Pile Driving", "Module Install",
        "Other"]),
    _col("Secondary Scopes"),
    _col("Primary Contact"),
    _col("Phone"),
    _col("Email"),
    _col("Office Location"),
    _col("License & Insurance Status", "PICKLIST",
         options=["Current", "Lapsed", "Not Submitted", "N/A"]),
    _col("Past Performance", "PICKLIST",
         options=["Preferred", "Standard", "On Probation", "Do Not Use"]),
    _col("Last Project Worked"),
    _col("Notes"),
]

VENDOR_DB_COLUMNS = [
    _col("Vendor", primary=True),
    _col("Vendor Type", "PICKLIST",
         options=["Material", "Equipment", "Service", "Logistics", "Other"]),
    _col("Specialty / Products"),
    _col("Primary Contact"),
    _col("Phone"),
    _col("Email"),
    _col("Payment Terms", "PICKLIST",
         options=["Net 30", "Net 45", "Net 60", "Prepay", "COD", "Other"]),
    _col("Preferred Status", "PICKLIST",
         options=["Preferred", "Standard", "Backup", "Do Not Use"]),
    _col("Notes"),
]

EQUIPMENT_MASTER_COLUMNS = [
    _col("Equipment Type", primary=True),
    _col("Category", "PICKLIST", options=["Major Equipment", "BOS", "Hardware", "Other"]),
    _col("Manufacturer"),
    _col("Model"),
    _col("Spec Reference"),
    _col("Typical Lead Time"),
    _col("Preferred Vendor"),
    _col("Notes"),
]

TEMPLATE_DAILY_COLUMNS = [
    _col("Entry #", primary=True),
    _col("Report Date", "DATE"),
    _col("Report Category", "PICKLIST", options=[
        "Daily JHA", "Tool Box Talk", "Safe Work Observation",
        "Equipment Check Sheets", "Other"]),
    _col("Crew / Subcontractor"),
    _col("AHJ Inspection"),
    _col("Visitor Log"),
    _col("Safety Topic / Report Title"),
    _col("Summary of Events"),
    _col("Notes / Action Items"),
]

TEMPLATE_ROLLUP_COLUMNS = [
    _col("Section", primary=True),
    _col("Detail"),
    _col("Source Refs"),
    _col("Notes"),
]


# ---- declarative tree spec ------------------------------------------------


@dataclasses.dataclass(frozen=True)
class SheetSpec:
    name: str
    columns: list[dict[str, Any]]


@dataclasses.dataclass(frozen=True)
class FolderSpec:
    name: str
    folders: tuple[FolderSpec, ...] = ()
    sheets: tuple[SheetSpec, ...] = ()


@dataclasses.dataclass(frozen=True)
class WorkspaceSpec:
    name: str
    folders: tuple[FolderSpec, ...]


_DEMO_PROJECT_FOLDERS = (
    "Bradley 1 (BBCHS 1)", "Bradley 2 (BBCHS 2)", "Brimfield 1", "Brimfield 2",
    "Huntley", "Rockford",
)

WORKSPACES: tuple[WorkspaceSpec, ...] = (
    WorkspaceSpec("ITS — Human Review", (
        FolderSpec("01 — Safety Reports",
                   sheets=(SheetSpec("WPR_Pending_Review", WPR_PENDING_REVIEW_COLUMNS),)),
        FolderSpec("02 — Subcontracts"),
        FolderSpec("03 — Purchase Orders & Materials"),
        FolderSpec("04 — Email Triage"),
        FolderSpec("05 — AI Employee"),
        FolderSpec("06 — Personnel",
                   sheets=(SheetSpec("ITS_Time_Off", TIME_OFF_COLUMNS),)),
    )),
    WorkspaceSpec("ITS — Operations", (
        FolderSpec("Master Databases", sheets=(
            SheetSpec("Subcontractor DB", SUBCONTRACTOR_DB_COLUMNS),
            SheetSpec("Vendor DB", VENDOR_DB_COLUMNS),
            SheetSpec("Equipment Master", EQUIPMENT_MASTER_COLUMNS),
        )),
    )),
    WorkspaceSpec("ITS — Archive", (
        FolderSpec("Closed Projects"),
    )),
    WorkspaceSpec("Forefront Portfolio — ITS Demo", (
        FolderSpec("01 — Active Projects",
                   folders=tuple(FolderSpec(n) for n in _DEMO_PROJECT_FOLDERS)),
        FolderSpec("02 — Portfolio Rollups"),
        FolderSpec("03 — Field Reports (JHA/TBT)", folders=tuple(
            FolderSpec(n, folders=(
                FolderSpec("Week of 2026-03-09", sheets=(
                    SheetSpec("Daily Reports — Week of 2026-03-09",
                              TEMPLATE_DAILY_COLUMNS),
                    SheetSpec("Weekly Rollup — Week of 2026-03-09",
                              TEMPLATE_ROLLUP_COLUMNS),
                )),
            ) if n == "Bradley 1 (BBCHS 1)" else ())
            for n in _DEMO_PROJECT_FOLDERS)),
    )),
)


def _assert_canonical_dashes() -> None:
    """Every em-dash name must carry a real U+2014 (fail closed on normalization)."""
    def walk(folder: FolderSpec) -> None:
        for bad in ("–", "‐", "‑", "‒", "―", "−"):
            if bad in folder.name:
                raise ValueError(f"canonical_name_dash_corrupted: {folder.name!r}")
        for sub in folder.folders:
            walk(sub)

    for ws in WORKSPACES:
        if EM_DASH not in ws.name:
            raise ValueError(f"canonical_name_dash_corrupted: {ws.name!r} lost its em dash")
        for folder in ws.folders:
            walk(folder)


_assert_canonical_dashes()


# ---- engine ---------------------------------------------------------------


def _headers() -> dict[str, str]:
    token = keychain.get_secret("ITS_SMARTSHEET_TOKEN")
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def _confirm(prompt: str) -> bool:
    """One y/N gate for the whole run (tests monkeypatch).

    STANDUP_NONINTERACTIVE=1 (set ONLY by the standup orchestrator, whose master
    gate is the control) auto-approves without touching stdin — stdin is closed
    under the orchestrator, so an unexpected prompt fails loudly (EOFError)
    instead of being silently fed a 'y'. Standalone runs still prompt."""
    if os.environ.get("STANDUP_NONINTERACTIVE") == "1":
        print(f"{prompt} [auto-approved: STANDUP_NONINTERACTIVE]")
        return True
    return input(f"{prompt} [y/N] ").strip().lower() == "y"


class BuildRefusedError(RuntimeError):
    """A fail-closed guard (not-owned / duplicate parent) refused the build."""


def _find_workspaces(name: str) -> list[dict[str, Any]]:
    r = requests.get(f"{BASE}/workspaces?includeAll=true", headers=_headers(), timeout=30)
    r.raise_for_status()
    return [ws for ws in r.json().get("data", []) if ws.get("name") == name]


def _folder_children(parent_kind: str, parent_id: int) -> dict[str, Any]:
    url = (f"{BASE}/workspaces/{parent_id}" if parent_kind == "workspace"
           else f"{BASE}/folders/{parent_id}")
    r = requests.get(url, headers=_headers(), timeout=30)
    r.raise_for_status()
    return dict(r.json())


def _create_sheet_in_folder(folder_id: int, spec: SheetSpec) -> int:
    r = requests.post(f"{BASE}/folders/{folder_id}/sheets", headers=_headers(),
                      json={"name": spec.name, "columns": spec.columns}, timeout=60)
    r.raise_for_status()
    return int(r.json()["result"]["id"])


class Runner:
    def __init__(self, *, dry_run: bool) -> None:
        self.dry_run = dry_run
        self.confirmed: bool | None = None
        self.created = 0
        self.skipped = 0

    def allow(self, what: str) -> bool:
        if self.dry_run:
            print(f"[dry-run] Would create {what}.")
            return False
        if self.confirmed is None:
            self.confirmed = _confirm(
                "About to make the FIRST live create (legacy workspaces). Proceed?")
            if not self.confirmed:
                print("[skip] Operator declined; nothing was created.")
        return self.confirmed

    def ensure_workspace(self, spec: WorkspaceSpec) -> int | None:
        matches = _find_workspaces(spec.name)
        if len(matches) > 1:
            print(f"[WARN] duplicate_parent_ambiguity: {len(matches)} workspaces named "
                  f"{spec.name!r} — failing closed.")
            raise BuildRefusedError(f"duplicate workspace {spec.name!r}")
        if matches:
            ws = matches[0]
            access = ws.get("accessLevel")
            print(f"[skip] workspace {spec.name!r} already present "
                  f"(id={ws['id']}, accessLevel={access}).")
            if access != "OWNER":
                print("[WARN] adopted_workspace_not_owned — refusing to create inside it "
                      f"(accessLevel={access}, permalink={ws.get('permalink')}).")
                raise BuildRefusedError(f"workspace {spec.name!r} not OWNER")
            self.skipped += 1
            return int(ws["id"])
        if not self.allow(f"workspace {spec.name!r}"):
            return None
        r = requests.post(f"{BASE}/workspaces", headers=_headers(),
                          json={"name": spec.name}, timeout=30)
        r.raise_for_status()
        new_id = int(r.json()["result"]["id"])
        self.created += 1
        print(f"[ok] created workspace {spec.name!r} (id={new_id}).")
        return new_id

    def ensure_tree(self, parent_kind: str, parent_id: int,
                    folders: tuple[FolderSpec, ...],
                    sheets: tuple[SheetSpec, ...]) -> None:
        listing = _folder_children(parent_kind, parent_id)
        child_folders = listing.get("folders", []) or []
        child_sheets = listing.get("sheets", []) or []
        for sheet_spec in sheets:
            existing = [s for s in child_sheets if s.get("name") == sheet_spec.name]
            if existing:
                if len(existing) > 1:
                    print(f"[WARN] duplicate_name_ambiguity: {len(existing)} sheets named "
                          f"{sheet_spec.name!r} in {parent_kind} {parent_id}; adopting first.")
                print(f"[skip] sheet {sheet_spec.name!r} already present "
                      f"(id={existing[0]['id']}).")
                self.skipped += 1
            elif self.allow(f"sheet {sheet_spec.name!r} in folder {parent_id}"):
                new_id = _create_sheet_in_folder(parent_id, sheet_spec)
                self.created += 1
                print(f"[ok] created sheet {sheet_spec.name!r} (id={new_id}).")
        for folder_spec in folders:
            existing = [f for f in child_folders if f.get("name") == folder_spec.name]
            if len(existing) > 1:
                # This folder is a PARENT we may create inside — fail closed.
                print(f"[WARN] duplicate_parent_ambiguity: {len(existing)} folders named "
                      f"{folder_spec.name!r} under {parent_kind} {parent_id} — failing closed.")
                raise BuildRefusedError(f"duplicate folder {folder_spec.name!r}")
            if existing:
                folder_id = int(existing[0]["id"])
                print(f"[skip] folder {folder_spec.name!r} already present (id={folder_id}).")
                self.skipped += 1
            else:
                if not self.allow(
                        f"folder {folder_spec.name!r} under {parent_kind} {parent_id}"):
                    continue
                if parent_kind == "workspace":
                    folder_id = smartsheet_client.create_folder_in_workspace(
                        parent_id, folder_spec.name)
                else:
                    folder_id = smartsheet_client.create_folder_in_folder(
                        parent_id, folder_spec.name)
                self.created += 1
                print(f"[ok] created folder {folder_spec.name!r} (id={folder_id}).")
            if folder_spec.folders or folder_spec.sheets:
                self.ensure_tree("folder", folder_id, folder_spec.folders,
                                 folder_spec.sheets)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build the four legacy workspaces (Human Review / Operations / "
                    "Archive / Demo).")
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing.")
    args = parser.parse_args()

    print(f"[info] Mode: {'DRY-RUN' if args.dry_run else 'LIVE WRITE'}")
    print(f"[info] Workspaces: {', '.join(repr(w.name) for w in WORKSPACES)}\n")
    runner = Runner(dry_run=args.dry_run)
    try:
        for ws_spec in WORKSPACES:
            ws_id = runner.ensure_workspace(ws_spec)
            if ws_id is None:
                if runner.confirmed is False:
                    break
                continue
            runner.ensure_tree("workspace", ws_id, ws_spec.folders, ())
            # sheets never live at workspace top level in this family
            if runner.confirmed is False:
                break
    except BuildRefusedError as exc:
        print(f"\n[abort] {exc} — reconcile before re-running. Partial creates (if any) "
              "are safe: re-run adopts them.")
        return 1

    print(f"\nSummary: created={runner.created} adopted/skipped={runner.skipped}")
    print("[next] Run scripts/migrations/sheet_ids_regen.py --write to flip every "
          "constant (including week_folder.py TEMPLATE ids) — no hand-paste.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
