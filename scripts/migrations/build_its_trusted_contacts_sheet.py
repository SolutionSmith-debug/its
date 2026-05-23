"""One-shot migration: create ITS_Trusted_Contacts sheet under
ITS — System / 01 — Config / FOLDER_SYSTEM_CONFIG (164788727768964).

Idempotent — re-running checks for an existing sheet by name and skips
the create. Prints the resulting sheet ID for `SHEET_TRUSTED_CONTACTS`
bootstrap into `shared/sheet_ids.py`.

Schema (per Op Stds v11 §33 + docs/tech_debt.md):

  Email             TEXT_NUMBER (primary, exact-match key, case-normalized)
  Display Name      TEXT_NUMBER
  Role              PICKLIST (Field PM / Safety Officer / Subcontractor PM /
                              Site Supervisor / Operator / Other)
  Project Scope     TEXT_NUMBER (JSON list of project slugs; "*"=wildcard)
  Workstream Scope  TEXT_NUMBER (JSON list of workstream slugs; "*"=wildcard)
  Status            PICKLIST (ACTIVE / DISABLED / PENDING_VERIFICATION)
  Added By          TEXT_NUMBER
  Added Date        DATE
  Last Verified     DATE
  Notes             TEXT_NUMBER

Project Scope / Workstream Scope are JSON-list TEXT_NUMBER (not native
multi-PICKLIST) per the tech_debt entry — multi-PICKLIST SDK shape is
inconsistent and the cross-sheet picklist sync from PR #45-51 doesn't
cover multi-select reliably yet. Tech-debt note for graduation after
the Phase 1.4 picklist-hardening deliverable.

Auth: ITS_SMARTSHEET_TOKEN from macOS Keychain.

Run from `~/its` with the venv activated:

    python3 scripts/migrations/build_its_trusted_contacts_sheet.py

Exit code 0 on success or no-op; nonzero on any error.
"""
from __future__ import annotations

import sys
from typing import Any

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[2]))

from shared import sheet_ids, smartsheet_client  # noqa: E402

SHEET_NAME = "ITS_Trusted_Contacts"
PARENT_FOLDER = sheet_ids.FOLDER_SYSTEM_CONFIG  # 164788727768964

ROLE_OPTIONS = [
    "Field PM",
    "Safety Officer",
    "Subcontractor PM",
    "Site Supervisor",
    "Operator",
    "Other",
]

STATUS_OPTIONS = ["ACTIVE", "DISABLED", "PENDING_VERIFICATION"]

PROJECT_SCOPE_DESCRIPTION = (
    "JSON list of project slugs the contact is authorized for. Wildcard "
    '`["*"]` matches any project. Example: `["bradley_1", "huntley"]`. '
    "TEXT_NUMBER (not native multi-PICKLIST) — multi-PICKLIST SDK shape is "
    "inconsistent and cross-sheet sync doesn't cover multi-select yet. "
    "Tech-debt entry tracks graduation."
)

WORKSTREAM_SCOPE_DESCRIPTION = (
    "JSON list of workstream slugs the contact is authorized for. Wildcard "
    '`["*"]` matches any workstream. Example: `["safety_reports"]`. '
    "Same TEXT_NUMBER-vs-multi-PICKLIST rationale as Project Scope."
)

COLUMN_SCHEMA: list[dict[str, Any]] = [
    {"title": "Email", "type": "TEXT_NUMBER", "primary": True},
    {"title": "Display Name", "type": "TEXT_NUMBER"},
    {"title": "Role", "type": "PICKLIST", "options": ROLE_OPTIONS},
    {
        "title": "Project Scope",
        "type": "TEXT_NUMBER",
        "description": PROJECT_SCOPE_DESCRIPTION,
    },
    {
        "title": "Workstream Scope",
        "type": "TEXT_NUMBER",
        "description": WORKSTREAM_SCOPE_DESCRIPTION,
    },
    {"title": "Status", "type": "PICKLIST", "options": STATUS_OPTIONS},
    {"title": "Added By", "type": "TEXT_NUMBER"},
    {"title": "Added Date", "type": "DATE"},
    {"title": "Last Verified", "type": "DATE"},
    {"title": "Notes", "type": "TEXT_NUMBER"},
]


def build_trusted_contacts_sheet() -> tuple[str, int]:
    """Create ITS_Trusted_Contacts in FOLDER_SYSTEM_CONFIG. Idempotent.

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
        f"    SHEET_TRUSTED_CONTACTS = {new_sheet_id}"
    )
    return "created", new_sheet_id


def main() -> int:
    print(f"[info] Folder ITS — System / 01 — Config = {PARENT_FOLDER}")
    print(f"[info] Target sheet name = {SHEET_NAME!r}")
    print()

    status, sheet_id = build_trusted_contacts_sheet()

    print()
    print("Summary:")
    print(f"  {SHEET_NAME}: {status} (id={sheet_id})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
