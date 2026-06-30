"""Build the "ITS — Progress Reporting" workspace + its "Control" folder.

P2 of the Progress Reporting program (the structural twin of the Safety Portal).
ITS OWNS this workspace and writes it as a structured system-of-record (Op Stds v19
§51). Like the Safety Portal workspace it sits OUTSIDE the §23 audience-separation
model and is governed by §46 — workspace membership = approval authority.

Why a migration script (not the Smartsheet MCP, which the original P2 brief named):
keeping the create in the operator-run idempotent migration family matches the rest
of the FLIP-precedes-SEED family, is safe to re-run, and avoids an irreversible MCP
create (the Smartsheet MCP cannot delete a workspace, so a mistaken MCP create is
stuck). The workspace REST create is the live-write step the operator runs; the code
itself is reversible (it lives on the feature branch until verified).

Creates (find-or-create, idempotent):
  1. The "ITS — Progress Reporting" WORKSPACE, if absent.
  2. The "Control" FOLDER inside it — the home of the two cross-job sheets
     (WPR_human_review + ITS_Active_Jobs_Progress). Per-<Job> folders + per-week
     sheets are RUNTIME find-or-create (A1 margin-checked), never built here.

Cutover sequence (FLIP precedes SEED):
  1. THIS script — note the printed WORKSPACE + FOLDER ids.
  2. Flip WORKSPACE_PROGRESS_REPORTING + FOLDER_PROGRESS_CONTROL in shared/sheet_ids.py.
  3. build_wpr_human_review_sheet.py + build_its_active_jobs_progress_sheet.py
     (each find-or-creates the SAME "Control" folder by name, so they are
     order-independent with this script and with each other).
  4. Flip SHEET_WPR_HUMAN_REVIEW + SHEET_ACTIVE_JOBS_PROGRESS.
  5. OPERATOR (P5-blocking): re-share every current safety-workspace approver into
     "ITS — Progress Reporting" (§46 — workspace membership = approval authority; an
     approver not shared here cannot approve a WPR_human_review row, and an empty
     resolved set fails closed / blocks all progress sends).

Convention: LIVE-write by default; pass --dry-run to preview (matches the
build_its_active_jobs_sheet.py / build_wsr_human_review_sheet.py family).

Auth: ITS_SMARTSHEET_TOKEN from macOS Keychain.

Run from ~/its (or a worktree):
    python3 scripts/migrations/build_progress_reporting_workspace.py --dry-run
    python3 scripts/migrations/build_progress_reporting_workspace.py

Exit 0 on success or no-op; nonzero on any error.
"""
from __future__ import annotations

import argparse
import sys

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[2]))

import requests  # type: ignore[import-untyped]  # noqa: E402

from shared import keychain, smartsheet_client  # noqa: E402

BASE = "https://api.smartsheet.com/2.0"
WORKSPACE_NAME = "ITS — Progress Reporting"
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
              "duplicate) before flipping WORKSPACE_PROGRESS_REPORTING.")
    print(f"[ok] created workspace {WORKSPACE_NAME!r} (workspace_id={new_id}).")
    print(f"[bootstrap] Update shared/sheet_ids.py:\n    WORKSPACE_PROGRESS_REPORTING = {new_id}")
    return new_id


def ensure_control_folder(workspace_id: int, *, dry_run: bool) -> int | None:
    """Find-or-create the "Control" folder in the workspace. Idempotent + order-independent
    with the two sheet-build scripts (they find-or-create the SAME folder by name)."""
    existing = smartsheet_client.find_folder_by_name_in_workspace(workspace_id, FOLDER_NAME)
    if existing is not None:
        print(f"[skip] folder {FOLDER_NAME!r} already present (folder_id={existing}).")
        return existing
    if dry_run:
        print(f"[dry-run] Would create folder {FOLDER_NAME!r} in workspace {workspace_id}.")
        return None
    new_id = smartsheet_client.create_folder_in_workspace(workspace_id, FOLDER_NAME)
    print(f"[ok] created folder {FOLDER_NAME!r} (folder_id={new_id}).")
    print(f"[bootstrap] Update shared/sheet_ids.py:\n    FOLDER_PROGRESS_CONTROL = {new_id}")
    return new_id


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build the ITS — Progress Reporting workspace + Control folder (P2)."
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
    print(f"  WORKSPACE_PROGRESS_REPORTING: id={workspace_id}")
    print(f"  FOLDER_PROGRESS_CONTROL:      id={folder_id}")
    print("\nNext: flip the two ids above, then run build_wpr_human_review_sheet.py "
          "+ build_its_active_jobs_progress_sheet.py.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
