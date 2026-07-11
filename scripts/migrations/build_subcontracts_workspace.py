"""Build the "ITS — Subcontracts" workspace + its "Control" folder.

SC-S1 of the subcontracts workstream (Aug-7 delivery program,
`docs/2026-07-09_aug7_delivery_program.md`). The NINTH standalone workspace — like the
Safety Portal and Progress Reporting workspaces it sits OUTSIDE the §23 audience-separation
model and is governed by §46: **workspace membership = approval authority**. The share list
of THIS workspace is the approver set the F22 gate verifies before `sc_send` dispatches a
subcontract to a subcontractor (decision D11 — Evergreen limits who may approve by limiting
who is shared here).

ITS OWNS this workspace and writes it as a structured system-of-record (Op Stds v20 §51).

Why a migration script (not the Smartsheet MCP): keeping the create in the operator-run
idempotent migration family matches the FLIP-precedes-SEED convention, is safe to re-run,
and avoids an irreversible MCP create (the MCP cannot delete a workspace, so a mistaken
MCP create is stuck). Pattern: `build_progress_reporting_workspace.py`.

Creates (find-or-create, idempotent):
  1. The "ITS — Subcontracts" WORKSPACE, if absent.
  2. The "Control" FOLDER inside it — home of the three cross-job subcontract sheets
     (ITS_Subcontractors + Subcontract_Log + Subcontract_Pending_Review). Per-job artifacts
     (the subcontract PDFs) live in Box, not here.

Cutover sequence (FLIP precedes SEED):
  1. THIS script — note the printed WORKSPACE + FOLDER ids.
  2. Flip WORKSPACE_SUBCONTRACTS + FOLDER_SC_CONTROL in shared/sheet_ids.py.
  3. build_its_subcontractors_sheet.py + build_subcontract_log_sheet.py +
     build_subcontract_pending_review_sheet.py (each find-or-creates the SAME "Control"
     folder by name — order-independent with this script and with each other).
  4. Flip SHEET_ITS_SUBCONTRACTORS + SHEET_SUBCONTRACT_LOG + SHEET_SUBCONTRACT_PENDING_REVIEW.
  5. seed_its_subcontractors.py (refuses to run while SHEET_ITS_SUBCONTRACTORS is 0).
  6. OPERATOR (send-blocking): share every subcontract approver into "ITS — Subcontracts"
     (§46 — an approver not shared here cannot approve a Subcontract_Pending_Review row; an
     empty resolved set fails closed and blocks all subcontract sends).

Convention: LIVE-write by default; pass --dry-run to preview.

Auth: ITS_SMARTSHEET_TOKEN from macOS Keychain.

Run from ~/its (or a worktree):
    python3 scripts/migrations/build_subcontracts_workspace.py --dry-run
    python3 scripts/migrations/build_subcontracts_workspace.py

Exit 0 on success or no-op; nonzero on any error.
"""
from __future__ import annotations

import argparse
import sys

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[2]))

import requests  # type: ignore[import-untyped]  # noqa: E402

from shared import keychain, smartsheet_client  # noqa: E402

BASE = "https://api.smartsheet.com/2.0"
WORKSPACE_NAME = "ITS — Subcontracts"
FOLDER_NAME = "Control"


def _headers() -> dict[str, str]:
    token = keychain.get_secret("ITS_SMARTSHEET_TOKEN")
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def _find_workspace_id() -> int | None:
    """Return the id of a workspace named WORKSPACE_NAME, or None. Idempotency key."""
    r = requests.get(f"{BASE}/workspaces?includeAll=true", headers=_headers(), timeout=30)
    r.raise_for_status()
    for ws in r.json().get("data", []):
        if ws.get("name") == WORKSPACE_NAME:
            return int(ws["id"])
    return None


def ensure_workspace(*, dry_run: bool) -> int | None:
    """Find-or-create the workspace. Returns its id, or None on a dry-run create."""
    existing = _find_workspace_id()
    if existing is not None:
        print(f"[skip] workspace {WORKSPACE_NAME!r} already present (workspace_id={existing}).")
        return existing
    if dry_run:
        print(f"[dry-run] Would create workspace {WORKSPACE_NAME!r}.")
        return None
    r = requests.post(f"{BASE}/workspaces", headers=_headers(),
                      json={"name": WORKSPACE_NAME}, timeout=30)
    r.raise_for_status()
    new_id = int(r.json()["result"]["id"])
    # §45 re-find-after-create: surface a concurrent-create duplicate. _find_workspace_id
    # returns the FIRST name match — if it isn't the id the POST just returned, two
    # workspaces now share the name and the operator must reconcile before flipping the id.
    found = _find_workspace_id()
    if found is not None and found != new_id:
        print(f"[WARN] workspace_race_duplicate: created {new_id} but a name lookup returns "
              f"{found} — another {WORKSPACE_NAME!r} workspace exists; reconcile (delete the "
              "duplicate) before flipping WORKSPACE_SUBCONTRACTS.")
    print(f"[ok] created workspace {WORKSPACE_NAME!r} (workspace_id={new_id}).")
    print(f"[bootstrap] Update shared/sheet_ids.py:\n    WORKSPACE_SUBCONTRACTS = {new_id}")
    return new_id


def ensure_control_folder(workspace_id: int, *, dry_run: bool) -> int | None:
    """Find-or-create the "Control" folder in the workspace. Idempotent + order-independent
    with the three sheet-build scripts (they find-or-create the SAME folder by name)."""
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


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build the ITS — Subcontracts workspace + Control folder (SC-S1)."
    )
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing.")
    args = parser.parse_args()
    print(f"[info] Workspace = {WORKSPACE_NAME!r} | Folder = {FOLDER_NAME!r}")
    print(f"[info] Mode: {'DRY-RUN' if args.dry_run else 'LIVE WRITE'}\n")

    workspace_id = ensure_workspace(dry_run=args.dry_run)
    folder_id: int | None = None
    if workspace_id is not None:
        folder_id = ensure_control_folder(workspace_id, dry_run=args.dry_run)
    elif args.dry_run:
        print(f"[dry-run] Would then create folder {FOLDER_NAME!r} inside the new workspace.")

    print("\nSummary:")
    print(f"  WORKSPACE_SUBCONTRACTS: id={workspace_id}")
    print(f"  FOLDER_SC_CONTROL:      id={folder_id}")
    print("\nNext: flip the two ids above, then run build_its_subcontractors_sheet.py + "
          "build_subcontract_log_sheet.py + build_subcontract_pending_review_sheet.py.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
