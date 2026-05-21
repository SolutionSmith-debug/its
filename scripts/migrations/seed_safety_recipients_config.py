"""One-shot migration: seed `safety_reports.recipients.*` rows in ITS_Config.

Companion to the R3 foundation PR (week_folder helper + sheet_ids deltas).
Run once during PR creation; safe to re-run (per-row idempotency-guarded on
Setting+Workstream).

What it does:

    Seed 7 ITS_Config rows in workstream `safety_reports`:

      safety_reports.recipients.bradley_1     = []
      safety_reports.recipients.bradley_2     = []
      safety_reports.recipients.brimfield_1   = []
      safety_reports.recipients.brimfield_2   = []
      safety_reports.recipients.huntley       = []
      safety_reports.recipients.rockford      = []
      safety_reports.recipients._default      = []

    Each per-job row carries a Description that explains the JSON-list
    contract; the `_default` row's Description documents the "missing →
    Review Queue with Reason=other" fallback semantics.

Empty `[]` Values are intentional. R3 session 1 (intake.py wiring) does
NOT auto-send anything until the operator fills these — via a one-time
sheet edit informed by a Teala email. The empty-list signal is read by
`weekly_generate.py` at draft time; a missing key (vs. blank value)
routes to ITS_Review_Queue per the resolution doc.

Auth: ITS_SMARTSHEET_TOKEN from macOS Keychain (same path the runtime
SDK uses).

Audit: committed to the repo so the provisioning event has a permanent
record. Run from `~/its` with the venv activated:

    python3 scripts/migrations/seed_safety_recipients_config.py

Exit code 0 on success or no-op; nonzero on any error.
"""
from __future__ import annotations

import sys
from typing import Any

import requests  # type: ignore[import-untyped]

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[2]))

from shared import keychain, sheet_ids  # noqa: E402

BASE = "https://api.smartsheet.com/2.0"

WORKSTREAM = "safety_reports"

PER_JOB_KEYS = [
    ("bradley_1", "Bradley 1"),
    ("bradley_2", "Bradley 2"),
    ("brimfield_1", "Brimfield 1"),
    ("brimfield_2", "Brimfield 2"),
    ("huntley", "Huntley"),
    ("rockford", "Rockford"),
]


def _per_job_description(job_label: str) -> str:
    return (
        f"JSON list of recipient emails for {job_label} WPRs. Set via "
        f"operator edit; weekly_generate.py reads at draft time."
    )


_DEFAULT_DESCRIPTION = (
    "Fallback list when a per-job entry is missing. Empty value never "
    "auto-sends; missing per-job key routes to ITS_Review_Queue with "
    "Reason=other."
)


CONFIG_ROWS: list[dict[str, Any]] = [
    {
        "Setting": f"safety_reports.recipients.{slug}",
        "Workstream": WORKSTREAM,
        "Value": "[]",
        "Description": _per_job_description(job_label),
    }
    for slug, job_label in PER_JOB_KEYS
] + [
    {
        "Setting": "safety_reports.recipients._default",
        "Workstream": WORKSTREAM,
        "Value": "[]",
        "Description": _DEFAULT_DESCRIPTION,
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


def _find_config_row(
    rows: list[dict[str, Any]],
    columns: list[dict[str, Any]],
    setting: str,
    workstream: str,
) -> dict[str, Any] | None:
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
    """Seed all 7 safety_reports.recipients.* rows. Idempotent per row.

    Returns: list of (setting, status) tuples — status is "created" or
    "exists".
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
    print(f"[info] ITS_Config sheet = {sheet_ids.SHEET_CONFIG}")
    print(f"[info] Workstream = {WORKSTREAM!r}")
    print(f"[info] Seeding {len(CONFIG_ROWS)} rows (6 per-job + 1 _default)")
    print()

    row_results = seed_config_rows()

    print()
    print("Summary:")
    for setting, status in row_results:
        print(f"  {setting}: {status}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
