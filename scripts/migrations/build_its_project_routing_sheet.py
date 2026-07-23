"""One-shot migration: create ITS_Project_Routing sheet under
ITS — System / 01 — Config / FOLDER_SYSTEM_CONFIG (1775005051709316).

E1 — migrates the hardcoded `shared.defaults.BOX_PROJECT_FOLDERS` dict to a
Smartsheet sheet so a non-developer can onboard a project (add a row) instead of
editing code + redeploying. Mirrors `build_its_trusted_contacts_sheet.py`.

Idempotent — re-running checks for an existing sheet by name and skips the
create. Prints the resulting sheet ID for `SHEET_PROJECT_ROUTING` bootstrap into
`shared/sheet_ids.py`.

Schema (one row per project):

  Project Name   TEXT_NUMBER (primary, exact-match key)
  Box Folder ID  TEXT_NUMBER (the project's Box folder ID under ITS DATA)
  Active         CHECKBOX    (false = retired; excluded from resolution)
  Notes          TEXT_NUMBER

Cutover sequence (FLIP precedes SEED — seed reads SHEET_PROJECT_ROUTING):
  1. THIS script (build the sheet); note the printed sheet id.
  2. Flip `SHEET_PROJECT_ROUTING` in `shared/sheet_ids.py` to that id.
  3. `seed_its_project_routing.py` (populate from BOX_PROJECT_FOLDERS). It reads
     the flipped constant, so seeding against the 0 placeholder raises — step 2
     MUST come first.
  4. Verify parity, then rely on the sheet.

Auth: ITS_SMARTSHEET_TOKEN from macOS Keychain.

Run from `~/its` with the venv:

    python3 scripts/migrations/build_its_project_routing_sheet.py

Exit code 0 on success or no-op; nonzero on any error.
"""
from __future__ import annotations

import sys
from typing import Any

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[2]))

from shared import sheet_ids, smartsheet_client  # noqa: E402

SHEET_NAME = "ITS_Project_Routing"
PARENT_FOLDER = sheet_ids.FOLDER_SYSTEM_CONFIG  # 1775005051709316

COLUMN_SCHEMA: list[dict[str, Any]] = [
    {"title": "Project Name", "type": "TEXT_NUMBER", "primary": True},
    {
        "title": "Box Folder ID",
        "type": "TEXT_NUMBER",
        "description": (
            "The project's Box folder ID under ITS DATA (a 1111B-derived clone). "
            "Opaque numeric string; do NOT reformat. Read at intake time by "
            "shared.project_routing.get_folder_id."
        ),
    },
    {
        "title": "Active",
        "type": "CHECKBOX",
        "description": "Uncheck to retire a project — it is then excluded from "
        "routing resolution (deny-by-default for a half-filled row).",
    },
    {"title": "Notes", "type": "TEXT_NUMBER"},
]


def build_project_routing_sheet() -> tuple[str, int]:
    """Create ITS_Project_Routing in FOLDER_SYSTEM_CONFIG. Idempotent.

    Returns: (status, sheet_id) where status is "created" or "exists".
    """
    existing_id = smartsheet_client.find_sheet_by_name_in_folder(
        PARENT_FOLDER, SHEET_NAME
    )
    if existing_id is not None:
        print(
            f"[skip] Sheet {SHEET_NAME!r} already present in folder {PARENT_FOLDER} "
            f"(sheet_id={existing_id})."
        )
        return "exists", existing_id

    new_sheet_id = smartsheet_client.create_sheet_in_folder(
        PARENT_FOLDER, SHEET_NAME, COLUMN_SCHEMA
    )
    print(
        f"[ok] Created sheet {SHEET_NAME!r} in folder {PARENT_FOLDER} "
        f"(sheet_id={new_sheet_id})."
    )
    print(
        f"[bootstrap] Update shared/sheet_ids.py:\n"
        f"    SHEET_PROJECT_ROUTING = {new_sheet_id}"
    )
    return "created", new_sheet_id


def main() -> int:
    print(f"[info] Folder ITS — System / 01 — Config = {PARENT_FOLDER}")
    print(f"[info] Target sheet name = {SHEET_NAME!r}")
    print()

    status, sheet_id = build_project_routing_sheet()

    print()
    print("Summary:")
    print(f"  {SHEET_NAME}: {status} (id={sheet_id})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
