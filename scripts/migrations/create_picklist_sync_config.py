"""One-shot migration: create Picklist_Sync_Config sheet + seed
`picklist_sync.size_warn_threshold` / `size_hard_halt_threshold` rows in
ITS_Config.

Companion to feat/picklist-sync. Run once during PR creation; safe to
re-run (both operations are idempotency-guarded — sheet by name, config
rows by Setting+Workstream).

What it does:

1. Look up Picklist_Sync_Config inside ITS — System / 01 — Config (folder
   1775005051709316). If absent, create it with the column schema below.
   Print the new sheet ID for `SHEET_PICKLIST_SYNC_CONFIG` bootstrap into
   `shared/sheet_ids.py`. If the sheet already exists, print the existing
   ID and skip the create.

2. Seed two ITS_Config rows in workstream `global`:
     - `picklist_sync.size_warn_threshold = 200`
     - `picklist_sync.size_hard_halt_threshold = 400`
   Both serve as operator-tunable overrides for the two-stage size
   guardrail in `shared/picklist_sync.py`. Fall-back constants live in
   `shared/defaults.py` (PICKLIST_SIZE_WARN_THRESHOLD,
   PICKLIST_SIZE_HARD_HALT_THRESHOLD). Skipped if already present.

Schema for Picklist_Sync_Config:

  mapping_id       TEXT_NUMBER (primary)
  source_sheet_id  TEXT_NUMBER
  source_column    TEXT_NUMBER
  target_sheet_id  TEXT_NUMBER
  target_column    TEXT_NUMBER
  enabled          CHECKBOX
  last_run_at      TEXT_NUMBER  (ISO 8601 string; DATE column would lose
                                  time-of-day precision at the script's
                                  sub-daily cadence — TEXT carries debuggable
                                  timestamps)
  last_run_hash    TEXT_NUMBER  (SHA-256 of sorted unique source values)
  notes            TEXT_NUMBER

last_run_at gets a column description noting the ISO 8601 convention so
future operators don't "fix" it to DATE and re-introduce the precision
loss.

Auth: ITS_SMARTSHEET_TOKEN from macOS Keychain (same path the runtime
SDK uses).

Audit: committed to the repo so the provisioning event has a permanent
record. Run from `~/its` with the venv activated:

    python3 scripts/migrations/create_picklist_sync_config.py

Exit code 0 on success or no-op; nonzero on any error.
"""
from __future__ import annotations

import sys
from typing import Any

import requests  # type: ignore[import-untyped]

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[2]))

from shared import keychain, sheet_ids, smartsheet_client  # noqa: E402

BASE = "https://api.smartsheet.com/2.0"

SHEET_NAME = "Picklist_Sync_Config"
PARENT_FOLDER = sheet_ids.FOLDER_SYSTEM_CONFIG  # 1775005051709316

LAST_RUN_AT_DESCRIPTION = (
    "ISO 8601 UTC timestamp of last successful sync. TEXT_NUMBER, not DATE — "
    "the sub-daily cron cadence needs time-of-day resolution for debugging "
    "('did the 7:15 run succeed?'). DATE would collapse to today-only. "
    "Do not 'fix' to DATE."
)

COLUMN_SCHEMA: list[dict[str, Any]] = [
    {"title": "mapping_id",      "type": "TEXT_NUMBER", "primary": True},
    {"title": "source_sheet_id", "type": "TEXT_NUMBER"},
    {"title": "source_column",   "type": "TEXT_NUMBER"},
    {"title": "target_sheet_id", "type": "TEXT_NUMBER"},
    {"title": "target_column",   "type": "TEXT_NUMBER"},
    {"title": "enabled",         "type": "CHECKBOX"},
    {"title": "last_run_at",     "type": "TEXT_NUMBER",
        "description": LAST_RUN_AT_DESCRIPTION},
    {"title": "last_run_hash",   "type": "TEXT_NUMBER"},
    {"title": "notes",           "type": "TEXT_NUMBER"},
]


CONFIG_ROWS = [
    {
        "Setting": "picklist_sync.size_warn_threshold",
        "Workstream": "global",
        "Value": "200",
        "Description": (
            "Picklist sync soft warning threshold (count of options). "
            "WARN to ITS_Errors when a target picklist would exceed this. "
            "Fallback in shared/defaults.py:PICKLIST_SIZE_WARN_THRESHOLD."
        ),
    },
    {
        "Setting": "picklist_sync.size_hard_halt_threshold",
        "Workstream": "global",
        "Value": "400",
        "Description": (
            "Picklist sync hard-halt threshold (count of options). Mapping "
            "is skipped (no API write) when proposed options exceed this, "
            "with ERROR to ITS_Errors. Auto-resumes when source returns to "
            "<= threshold. Fallback in "
            "shared/defaults.py:PICKLIST_SIZE_HARD_HALT_THRESHOLD."
        ),
    },
]


def _headers() -> dict[str, str]:
    token = keychain.get_secret("ITS_SMARTSHEET_TOKEN")
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }


def _post_json(path: str, body: Any) -> dict[str, Any]:
    r = requests.post(BASE + path, headers=_headers(), json=body)
    r.raise_for_status()
    return r.json()


def _get_json(path: str) -> dict[str, Any]:
    r = requests.get(BASE + path, headers=_headers())
    r.raise_for_status()
    return r.json()


# ---- 1) Picklist_Sync_Config sheet --------------------------------------


def create_picklist_sync_config_sheet() -> tuple[str, int]:
    """Create Picklist_Sync_Config inside FOLDER_SYSTEM_CONFIG. Idempotent.

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
        f"[bootstrap] Add to shared/sheet_ids.py:\n"
        f"    SHEET_PICKLIST_SYNC_CONFIG = {new_sheet_id}"
    )
    return "created", new_sheet_id


# ---- 2) ITS_Config size-threshold rows ----------------------------------


def _find_config_row(rows: list[dict[str, Any]], columns: list[dict[str, Any]],
                    setting: str, workstream: str) -> dict[str, Any] | None:
    col_id_by_title = {c["title"]: c["id"] for c in columns}
    setting_col = col_id_by_title["Setting"]
    workstream_col = col_id_by_title["Workstream"]
    for row in rows:
        s = w = None
        for cell in row.get("cells", []):
            if cell.get("columnId") == setting_col:
                s = cell.get("value")
            elif cell.get("columnId") == workstream_col:
                w = cell.get("value")
        if s == setting and w == workstream:
            return row
    return None


def seed_config_rows() -> list[tuple[str, str]]:
    """Seed both picklist_sync.size_* rows in ITS_Config. Idempotent per row.

    Returns: list of (setting, status) tuples.
    """
    sheet = _get_json(f"/sheets/{sheet_ids.SHEET_CONFIG}?include=columns")
    columns = sheet["columns"]
    rows = sheet["rows"]
    col_id_by_title = {c["title"]: c["id"] for c in columns}

    results: list[tuple[str, str]] = []
    for row_spec in CONFIG_ROWS:
        existing = _find_config_row(
            rows, columns, row_spec["Setting"], row_spec["Workstream"]
        )
        if existing is not None:
            print(
                f"[skip] ITS_Config row Setting={row_spec['Setting']!r} "
                f"Workstream={row_spec['Workstream']!r} already present."
            )
            results.append((row_spec["Setting"], "exists"))
            continue

        cells = []
        for title, value in row_spec.items():
            if title in col_id_by_title:
                cells.append({"columnId": col_id_by_title[title], "value": value})
        payload = [{"toBottom": True, "cells": cells}]
        result = _post_json(f"/sheets/{sheet_ids.SHEET_CONFIG}/rows", payload)
        new_id = result["result"][0]["id"]
        print(
            f"[ok] Seeded ITS_Config row id={new_id}: "
            f"Setting={row_spec['Setting']!r} Value={row_spec['Value']!r}"
        )
        results.append((row_spec["Setting"], "created"))
    return results


def main() -> int:
    print(f"[info] Folder ITS — System / 01 — Config = {PARENT_FOLDER}")
    print(f"[info] Target sheet name = {SHEET_NAME!r}")
    print(f"[info] ITS_Config sheet = {sheet_ids.SHEET_CONFIG}")
    print()

    sheet_status, sheet_id = create_picklist_sync_config_sheet()
    row_results = seed_config_rows()

    print()
    print("Summary:")
    print(f"  Picklist_Sync_Config sheet:        {sheet_status} (id={sheet_id})")
    for setting, status in row_results:
        print(f"  ITS_Config row {setting}: {status}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
