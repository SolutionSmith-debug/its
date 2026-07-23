"""One-shot migration: add Correlation_ID column to ITS_Errors + seed
`alerting.dedupe_window_minutes` row in ITS_Config.

Companion to PR α (alert-routing dedupe core). Run once during PR α
landing; safe to re-run (both operations are idempotency-guarded).

What it does:
1. Inspect ITS_Errors (sheet 8015637140950916) columns. If `Correlation_ID`
   is absent, POST a new TEXT_NUMBER column at index 6 — between
   `Traceback` (last data column) and `Surfaced At` (first triage column).
   If the column is already present, skip silently.

2. Inspect ITS_Config (sheet 8933909738770308) for a row with
   `Setting = alerting.dedupe_window_minutes` and `Workstream = global`.
   If missing, POST a new row with `Value = 60` and a Description
   pointing at `shared/defaults.py:ALERTING_DEDUPE_WINDOW_MINUTES` as
   the fallback constant. If the row exists, skip silently.

Both operations use the Smartsheet REST API (not the SDK) for the schema
write — adding a column isn't surfaced cleanly through the SDK wrapper,
and using REST directly keeps the migration self-contained.

Auth: ITS_SMARTSHEET_TOKEN from macOS Keychain (same source the runtime
SDK uses). No env vars, no command-line args.

Audit: this script is committed to the repo so the schema change has a
permanent record. Run from `~/its` with the venv activated:

    python3 scripts/migrations/add_correlation_id_column.py

Exit code 0 on success or no-op; nonzero on any error.
"""
from __future__ import annotations

import sys
from typing import Any

import requests  # type: ignore[import-untyped]

# Allow running from repo root without installing the package.
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[2]))

from shared import keychain, sheet_ids  # noqa: E402

BASE = "https://api.smartsheet.com/2.0"
COLUMN_TITLE = "Correlation_ID"
COLUMN_INDEX = 6  # between Traceback (5) and Surfaced At (6, becomes 7 after insert)
COLUMN_TYPE = "TEXT_NUMBER"

CONFIG_SETTING = "alerting.dedupe_window_minutes"
CONFIG_WORKSTREAM = "global"
CONFIG_VALUE = "60"
CONFIG_DESCRIPTION = (
    "Resend-leg dedupe window for triple-fire CRITICAL alerts (PR α). "
    "Fallback constant in shared/defaults.py:ALERTING_DEDUPE_WINDOW_MINUTES."
)


def _headers() -> dict[str, str]:
    token = keychain.get_secret("ITS_SMARTSHEET_TOKEN")
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }


def _get_json(path: str) -> dict[str, Any]:
    r = requests.get(BASE + path, headers=_headers())
    r.raise_for_status()
    return r.json()


def _post_json(path: str, body: dict[str, Any] | list[dict[str, Any]]) -> dict[str, Any]:
    r = requests.post(BASE + path, headers=_headers(), json=body)
    r.raise_for_status()
    return r.json()


# ---- 1) ITS_Errors Correlation_ID column --------------------------------


def add_correlation_id_column() -> str:
    """Add Correlation_ID to ITS_Errors. Idempotent.

    Returns: "created", "exists", or raises on unexpected API failure.
    """
    sheet = _get_json(f"/sheets/{sheet_ids.SHEET_ERRORS}?include=columns")
    columns = sheet.get("columns", [])
    existing_titles = {c["title"] for c in columns}

    if COLUMN_TITLE in existing_titles:
        print(f"[skip] Column {COLUMN_TITLE!r} already present in ITS_Errors.")
        return "exists"

    payload = [{
        "title": COLUMN_TITLE,
        "type": COLUMN_TYPE,
        "index": COLUMN_INDEX,
    }]
    result = _post_json(f"/sheets/{sheet_ids.SHEET_ERRORS}/columns", payload)
    created = result.get("result", [])
    if not created:
        raise RuntimeError(f"Unexpected column-create response: {result!r}")
    col = created[0]
    print(
        f"[ok] Added column {col['title']!r} (id={col['id']}, type={col['type']}, "
        f"index={col['index']}) to ITS_Errors."
    )
    return "created"


# ---- 2) ITS_Config dedupe-window row ------------------------------------


def _find_config_row(rows: list[dict[str, Any]], columns: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Locate the existing row for (CONFIG_SETTING, CONFIG_WORKSTREAM), if any."""
    col_id_by_title = {c["title"]: c["id"] for c in columns}
    setting_col = col_id_by_title["Setting"]
    workstream_col = col_id_by_title["Workstream"]
    for row in rows:
        setting_val = None
        workstream_val = None
        for cell in row.get("cells", []):
            if cell.get("columnId") == setting_col:
                setting_val = cell.get("value")
            elif cell.get("columnId") == workstream_col:
                workstream_val = cell.get("value")
        if setting_val == CONFIG_SETTING and workstream_val == CONFIG_WORKSTREAM:
            return row
    return None


def seed_dedupe_window_row() -> str:
    """Seed alerting.dedupe_window_minutes / global in ITS_Config. Idempotent.

    Returns: "created" or "exists".
    """
    sheet = _get_json(f"/sheets/{sheet_ids.SHEET_CONFIG}?include=columns")
    columns = sheet.get("columns", [])
    rows = sheet.get("rows", [])

    if _find_config_row(rows, columns) is not None:
        print(
            f"[skip] Row Setting={CONFIG_SETTING!r} Workstream={CONFIG_WORKSTREAM!r} "
            "already present in ITS_Config."
        )
        return "exists"

    col_id_by_title = {c["title"]: c["id"] for c in columns}
    cells: list[dict[str, Any]] = [
        {"columnId": col_id_by_title["Setting"], "value": CONFIG_SETTING},
        {"columnId": col_id_by_title["Workstream"], "value": CONFIG_WORKSTREAM},
        {"columnId": col_id_by_title["Value"], "value": CONFIG_VALUE},
    ]
    if "Description" in col_id_by_title:
        cells.append({"columnId": col_id_by_title["Description"], "value": CONFIG_DESCRIPTION})

    payload = [{"toBottom": True, "cells": cells}]
    result = _post_json(f"/sheets/{sheet_ids.SHEET_CONFIG}/rows", payload)
    created = result.get("result", [])
    if not created:
        raise RuntimeError(f"Unexpected row-create response: {result!r}")
    print(
        f"[ok] Seeded ITS_Config row id={created[0]['id']} with "
        f"Setting={CONFIG_SETTING!r}, Workstream={CONFIG_WORKSTREAM!r}, "
        f"Value={CONFIG_VALUE!r}."
    )
    return "created"


# ---- Entrypoint ---------------------------------------------------------


def main() -> int:
    print(f"[info] Sheet ITS_Errors = {sheet_ids.SHEET_ERRORS}")
    print(f"[info] Sheet ITS_Config = {sheet_ids.SHEET_CONFIG}")
    print()

    col_status = add_correlation_id_column()
    row_status = seed_dedupe_window_row()

    print()
    print("Summary:")
    print(f"  Correlation_ID column: {col_status}")
    print(f"  dedupe-window row:     {row_status}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
