"""Build ITS_Subcontractors — the subcontractor source-of-record for the subcontracts workstream (SC-S1).

Creates the sheet in the "Control" folder of the "ITS — Subcontracts" workspace
(build_subcontracts_workspace.py; §46 workspace). ITS_Subcontractors is the SOLE
subcontractor SoR (decision D4 + the SC-S1 Subcontractor-DB decision): the old
Operations "Subcontractor DB" (shared/sheet_ids.SHEET_SUBCONTRACTOR_DB) is
retired-in-place like the old Vendor DB — seed_its_subcontractors.py performs the
one-time row copy. The legacy stub is a two-column seed; the constant is retained
ONLY for the seed's one-time copy — no new readers or writers point at it.

Sync posture (D4, the §51 bidirectional instance — SC-S2/SC-S4 build the machinery):
Smartsheet (THIS sheet) = SoR; D1 = portal cache. Down-sync full-replace with a
dirty-row fence; up-sync bridge-key find-or-create on **Sub Key** (SUB-######),
column-scoped, never-delete (deactivate via Active). The Sub Key is the immutable
join identity: `sc_send` resolves the subcontractor recipient (TO = Contact Email)
from this sheet BY KEY at send time — a blank Contact Email HOLDs the send
(`held_no_recipient`), never silently drops it.

Option-set parity: State / Trades / Default Terms Profile / Active option lists
here MUST stay set-equal to the matching frozensets in
shared/picklist_validation.py (REGISTRY gates every add_rows/update_rows write to
this sheet once SHEET_ITS_SUBCONTRACTORS is flipped). The State list additionally
MUST equal subcontracts.governing_law._STATE_NAMES so every value resolves to a
governing-law jurisdiction. tests/test_subcontract_s1.py pins the parity.

Trades is MULTI_PICKLIST (the subcontractor analog of ITS_Vendors' Supply
Categories). Creation via POST /sheets is expected to work on SDK 3.9
(MultiPicklistObjectValue verified); if the live create rejects the type (the
ABSTRACT_DATETIME errorCode-1142 class), fall back to creating TEXT_NUMBER and
retyping via update_column (the hours_log pattern), then update this comment with
the verified truth.

Idempotent: find-or-creates the "Control" folder by name and skips if a sheet named
ITS_Subcontractors already exists in it (order-independent with the other SC-S1
builders).

Prereq: build_subcontracts_workspace.py has been run and WORKSPACE_SUBCONTRACTS
flipped in shared/sheet_ids.py.

    python3 scripts/migrations/build_its_subcontractors_sheet.py --dry-run
    python3 scripts/migrations/build_its_subcontractors_sheet.py
"""
from __future__ import annotations

import argparse
import sys
from typing import Any

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[2]))

from shared import sheet_ids, smartsheet_client  # noqa: E402

WORKSPACE = sheet_ids.WORKSPACE_SUBCONTRACTS
FOLDER_NAME = "Control"
SHEET_NAME = "ITS_Subcontractors"

# Ordered for the dropdown UI; set-parity with shared/picklist_validation.py AND
# subcontracts.governing_law._STATE_NAMES is test-pinned (tests/test_sc_s1_sheets.py) —
# every State on the sheet MUST resolve to a governing-law jurisdiction, or the
# subcontract render fences. The 50 states + DC (2-letter USPS), the jurisdiction
# grouping/filter axis (replaces the coarse vendor West/Midwest/East region — a
# subcontract's governing law is per-state, so the registry groups by state).
STATE_OPTIONS = [
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "DC", "FL", "GA", "HI", "ID",
    "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN", "MS", "MO",
    "MT", "NE", "NV", "NH", "NJ", "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA",
    "RI", "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY",
]
TRADE_OPTIONS = [
    "Surveying", "Civil", "Fencing", "Post Installation", "Mechanical",
    "AC Electrical", "MV Electrical", "DC Electrical", "Specialty",
]
# Provisional profile vocabulary — the subcontract terms library (SC-S3, the
# deferred subcontract-generation workflow) has not landed yet, so these are the
# mechanically-derived analogs of ITS_Vendors' Default Terms Profile set:
# negotiated_msa == the direct rename of the vendor negotiated_gtc attach-not-
# generate role (0049: "negotiated Master Subcontract Agreement pointer"), and
# standard_subcontract == the default generated subcontract-body profile pin.
# Reconcile set-equal with shared/picklist_validation._SUBCONTRACTOR_TERMS_PROFILE_VALUES
# when the SC terms manifest ships.
TERMS_PROFILE_OPTIONS = ["standard_subcontract", "negotiated_msa"]
ACTIVE_OPTIONS = ["Active", "Inactive", "Archived"]  # lifecycle set shared with ITS_Active_Jobs

COLUMN_SCHEMA: list[dict[str, Any]] = [
    {"title": "Subcontractor Name", "type": "TEXT_NUMBER", "primary": True},
    {"title": "Sub Key", "type": "TEXT_NUMBER",
     "description": "SUB-###### — the immutable bridge key (D4). Allocated by seed_its_subcontractors.py / "
                    "the up-sync writer, NEVER edited by hand; the D1 cache joins on it and sc_send "
                    "resolves the subcontractor recipient by it at send time."},
    {"title": "Address", "type": "TEXT_NUMBER",
     "description": "Subcontractor address block as printed on the subcontract party block (1–4 lines, comma-separated)."},
    {"title": "Contact Name", "type": "TEXT_NUMBER"},
    {"title": "Contact Email", "type": "TEXT_NUMBER",
     "description": "THE send-time recipient (TO) — sc_send resolves it from this sheet by Sub Key at "
                    "dispatch. Blank → the send is HELD (held_no_recipient), never silently dropped."},
    {"title": "Contact Phone", "type": "TEXT_NUMBER"},
    {"title": "State", "type": "PICKLIST", "options": STATE_OPTIONS,
     "description": "Job-site state (2-letter USPS) — the SPA subcontractor-picker grouping/filter axis "
                    "and the subcontract's governing-law jurisdiction (corpus: OR, IL, MD/PA/VA). "
                    "Set-equal to subcontracts.governing_law._STATE_NAMES so every value resolves."},
    {"title": "Trades", "type": "MULTI_PICKLIST", "options": TRADE_OPTIONS,
     "description": "What the subcontractor performs — the second SPA filter axis. Multi-select."},
    {"title": "Default Terms Profile", "type": "PICKLIST", "options": TERMS_PROFILE_OPTIONS,
     "description": "The terms-library profile preselected for this subcontractor's subcontracts (D6; "
                    "subcontract-body manifest ids). Drafts pin id+version at generate time."},
    {"title": "MSA Reference", "type": "TEXT_NUMBER",
     "description": "Box link to the negotiated Master Subcontract Agreement (MSA) for negotiated_msa "
                    "subcontractors (attach-not-generate, D6). Blank for library-terms subcontractors."},
    {"title": "COI Reference", "type": "TEXT_NUMBER",
     "description": "Box link to the subcontractor's Certificate of Insurance (COI). POINTER only — no "
                    "coverage gate (the COI-validity SoR is unseen; parity with the D1 coi_reference column)."},
    {"title": "License #", "type": "TEXT_NUMBER",
     "description": "Subcontractor's contractor license number (state license board). Free text; no format gate."},
    {"title": "Active", "type": "PICKLIST", "options": ACTIVE_OPTIONS,
     "description": "Lifecycle (never-delete — deactivate; D4). Only Active subcontractors appear in the SPA "
                    "picker."},
    {"title": "Notes", "type": "TEXT_NUMBER"},
    {"title": "Last Modified", "type": "DATETIME", "systemColumnType": "MODIFIED_DATE"},
    {"title": "Modified By", "type": "CONTACT_LIST", "systemColumnType": "MODIFIED_BY"},
]


def _require_workspace() -> int:
    if not WORKSPACE:
        print("[error] WORKSPACE_SUBCONTRACTS is still 0 in shared/sheet_ids.py.\n"
              "        Run build_subcontracts_workspace.py first and flip the printed id.",
              file=sys.stderr)
        raise SystemExit(2)
    return WORKSPACE


def ensure_control_folder(workspace_id: int, *, dry_run: bool) -> int | None:
    """Find-or-create the "Control" folder. Idempotent + order-independent."""
    existing = smartsheet_client.find_folder_by_name_in_workspace(workspace_id, FOLDER_NAME)
    if existing is not None:
        print(f"[skip] folder {FOLDER_NAME!r} already present (folder_id={existing}).")
        return existing
    if dry_run:
        print(f"[dry-run] Would create folder {FOLDER_NAME!r} in workspace {workspace_id}.")
        return None
    new_id = smartsheet_client.create_folder_in_workspace(workspace_id, FOLDER_NAME)
    print(f"[ok] created folder {FOLDER_NAME!r} (folder_id={new_id}).")
    print(f"[bootstrap] Update shared/sheet_ids.py:\n    FOLDER_SC_CONTROL = {new_id}")
    return new_id


def build_sheet(*, dry_run: bool) -> tuple[str, int | None]:
    workspace_id = _require_workspace()
    folder_id = ensure_control_folder(workspace_id, dry_run=dry_run)
    if folder_id is None:
        print(f"[dry-run] Would create sheet {SHEET_NAME!r} with columns: "
              f"{[c['title'] for c in COLUMN_SCHEMA]}.")
        return "dry-run", None

    existing_id = smartsheet_client.find_sheet_by_name_in_folder(folder_id, SHEET_NAME)
    if existing_id is not None:
        print(f"[skip] sheet {SHEET_NAME!r} already present (sheet_id={existing_id}).")
        print(f"[bootstrap] shared/sheet_ids.py:\n    SHEET_ITS_SUBCONTRACTORS = {existing_id}")
        return "exists", existing_id

    if dry_run:
        print(f"[dry-run] Would create sheet {SHEET_NAME!r} in folder {folder_id} with "
              f"columns: {[c['title'] for c in COLUMN_SCHEMA]}.")
        return "dry-run", None

    new_id = smartsheet_client.create_sheet_in_folder(folder_id, SHEET_NAME, COLUMN_SCHEMA)
    print(f"[ok] created {SHEET_NAME!r} in folder {folder_id} (sheet_id={new_id}).")
    print(f"[bootstrap] Update shared/sheet_ids.py:\n    SHEET_ITS_SUBCONTRACTORS = {new_id}")
    return "created", new_id


def main() -> int:
    parser = argparse.ArgumentParser(description="Build ITS_Subcontractors (SC-S1 subcontractor SoR).")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    print(f"[info] Workspace ITS — Subcontracts = {WORKSPACE}")
    print(f"[info] Folder = {FOLDER_NAME!r} | Sheet = {SHEET_NAME!r}")
    print(f"[info] Mode: {'DRY-RUN' if args.dry_run else 'LIVE WRITE'}\n")

    status, sheet_id = build_sheet(dry_run=args.dry_run)
    print(f"\nSummary:\n  {SHEET_NAME}: {status} (id={sheet_id})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
