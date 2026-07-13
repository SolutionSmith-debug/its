"""One-shot migration: seed the 5 daemon-gate ITS_Config rows whose ABSENCE caused the
2026-07-13 config-WARN storm (the ITS_Errors row-cap incident).

Each of these keys was read every daemon cycle via the #336 `resolve_and_log` startup pass,
and the MISSING row made every cycle WARN `config_row_missing` — ~1,400–4,500 ITS_Errors
rows/day across the five keys — which filled ITS_Errors to the Smartsheet 20,000-row hard
cap on 2026-07-13 (errorCode 5634 on every add_rows; see the watchdog Check O STORM-MODE
rationale block, scripts/watchdog.py). The dark-ship gate reflex (HOUSE_REFLEXES §5)
already required seeding gate rows at merge time; these five predated / escaped it.

The rows were ALREADY applied to the live ITS_Config on 2026-07-13 via an ad-hoc idempotent
script during the incident response — this migration makes the seed DURABLE in the repo
(fresh installs, sandbox rebuilds, re-runs). Every value below is the LIVE DEFAULT the
daemons were already resolving to, so seeding changes no behavior; it only silences the
per-cycle missing-row WARN.

Companion pattern: scripts/migrations/seed_config_actuator_config.py (per-row idempotency
on Setting+Workstream — get_setting is workstream-scoped, so BOTH cells must match).

What it seeds (Setting / Workstream / Value):

    safety_reports.photo_screen.clamav_enabled        / safety_reports   / false
    safety_reports.compile_now_poll.polling_enabled   / safety_reports   / true
    progress_reports.compile_now_poll.polling_enabled / progress_reports / true
    progress_reports.progress_send.polling_enabled    / progress_reports / true
    progress_reports.progress_send.scheduled_send_local / progress_reports / MON 07:00

Auth: ITS_SMARTSHEET_TOKEN from macOS Keychain (same path the runtime SDK uses).

Run from `~/its` with the venv activated:

    python3 scripts/migrations/seed_daemon_gate_config.py

Exit code 0 on success or no-op; nonzero on any error.
"""
from __future__ import annotations

import sys
from typing import Any

import requests  # type: ignore[import-untyped]

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[2]))

from shared import keychain, sheet_ids  # noqa: E402

BASE = "https://api.smartsheet.com/2.0"

_SEEDED_NOTE = (
    "Seeded 2026-07-13 at the live default — the missing row made every daemon cycle WARN "
    "config_row_missing, which filled ITS_Errors to the 20k row cap (the Check O storm-mode "
    "incident)."
)

CONFIG_ROWS: list[dict[str, Any]] = [
    {
        "Setting": "safety_reports.photo_screen.clamav_enabled",
        "Workstream": "safety_reports",
        "Value": "false",
        "Description": (
            "Gates the L3 ClamAV leg of the §34 photo screen "
            "(safety_reports/photo_screen.py, read via intake.CFG_PHOTO_CLAMAV): 'true' scans "
            "each RAW uploaded photo with clamd before render/Box filing; 'false' (default) "
            "skips the scan leg — L1 magic/size and L2 Pillow verify/bomb-cap/re-encode still "
            "run. Leave false until ClamAV is installed on the Mac. " + _SEEDED_NOTE
        ),
    },
    {
        "Setting": "safety_reports.compile_now_poll.polling_enabled",
        "Workstream": "safety_reports",
        "Value": "true",
        "Description": (
            "Runtime gate for the SAFETY workstream pass of the Compile Now poller "
            "(safety_reports/compile_now_poll.py): 'true' polls each Active job's current "
            "week-sheet Rollup row for the operator Compile Now checkbox and fires the shared "
            "deterministic compile inline; 'false' skips the safety pass (the checkbox goes "
            "unserviced until the Friday calendar run). " + _SEEDED_NOTE
        ),
    },
    {
        "Setting": "progress_reports.compile_now_poll.polling_enabled",
        "Workstream": "progress_reports",
        "Value": "true",
        "Description": (
            "Runtime gate for the PROGRESS workstream pass of the Compile Now poller "
            "(safety_reports/compile_now_poll.py, workstream-scoped key): 'true' polls each "
            "Active progress job's current week-sheet Rollup row for the operator Compile Now "
            "checkbox and fires the progress compile inline; 'false' skips the progress pass "
            "(the safety pass keeps running). " + _SEEDED_NOTE
        ),
    },
    {
        "Setting": "progress_reports.progress_send.polling_enabled",
        "Workstream": "progress_reports",
        "Value": "true",
        "Description": (
            "Runtime gate for the progress send-dispatch poller "
            "(progress_reports/progress_send_poll.py): 'true' polls WPR_human_review for "
            "approved rows (Send Now / Approve for Scheduled Send, F22-verified) and "
            "dispatches progress_send.send_one_row; 'false' pauses dispatch (approved rows "
            "wait, nothing sends). " + _SEEDED_NOTE
        ),
    },
    {
        "Setting": "progress_reports.progress_send.scheduled_send_local",
        "Workstream": "progress_reports",
        "Value": "MON 07:00",
        "Description": (
            "Scheduled-send window for approved WPR rows "
            "(progress_reports/progress_send_poll.py): rows checked 'Approve for Scheduled "
            "Send' dispatch on/after this local wall-clock moment each week "
            "(America/Los_Angeles), format 'DDD HH:MM'. " + _SEEDED_NOTE
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
    json_body: dict[str, Any] = r.json()
    return json_body


def _get_json(path: str) -> dict[str, Any]:
    r = requests.get(BASE + path, headers=_headers())
    r.raise_for_status()
    json_body: dict[str, Any] = r.json()
    return json_body


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
    """Seed the 5 daemon-gate rows. Idempotent per row (Setting+Workstream match → skip).

    Returns: list of (setting, status) tuples — status is "created" or "exists".
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
    print(
        f"[info] Seeding {len(CONFIG_ROWS)} daemon-gate rows at their live defaults "
        "(2026-07-13 config-WARN-storm fix; already applied live — expect [skip] there)"
    )
    print()

    row_results = seed_config_rows()

    print()
    print("Summary:")
    for setting, status in row_results:
        print(f"  {setting}: {status}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
