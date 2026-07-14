"""One-shot migration: seed the 11 remaining un-seeded ITS_Config rows — the 5
launchd interval rows + the 6 weekly-compile REQUIRED_CONFIG keys.

Companion to scripts/migrations/seed_daemon_gate_config.py (the 2026-07-13 config-WARN
storm fix). That migration seeded the 5 per-cycle GATE rows whose absence filled
ITS_Errors to the 20k cap. This one closes the remaining gap found in the 2026-07-14
ITS_Config audit (76 present vs 86 should-exist): the last 11 rows that SHOULD exist but
were never seeded. Every value below is the LIVE DEFAULT the code already resolves to, so
seeding changes NO behavior — it only (a) lets the dashboard edit-interval verb resolve the
interval rows instead of refusing, and (b) silences the low-volume weekly config_row_missing
WARN for the six REQUIRED_CONFIG generate keys (#336 observability).

Two buckets:

  BUCKET A — 5 launchd interval rows (`*.poll_interval_seconds`). Read by
  scripts/launchd/install.sh at (re)install to substitute the plist StartInterval, and by
  the operator dashboard's edit-interval verb (operator_dashboard/act/daemon_ops.py) as the
  persisted no-arg default. They are DELIBERATELY excluded from every daemon's REQUIRED_CONFIG
  (install-time, not hot-reloaded — launchd holds the live cadence), so their absence caused
  NO WARN storm; but the dashboard interval-edit verb REFUSES a daemon with no row ("seed it
  first (Developer-Operator)"). Seeded at the _DAEMONS / install.sh default = the cadence
  already installed → behaviorally inert. (po_poll=90, po_send=900, subcontract_poll=120, and
  intake=60 are already present; these are the remaining 5 of the 8 interval daemons.)

    safety_reports.weekly_send.poll_interval_seconds      / safety_reports   / 900
    safety_reports.portal_poll.poll_interval_seconds      / safety_reports   / 60
    safety_reports.compile_now_poll.poll_interval_seconds / safety_reports   / 90
    progress_reports.progress_send.poll_interval_seconds  / progress_reports / 900
    field_ops.fieldops_sync.poll_interval_seconds         / field_ops        / 90

  BUCKET B — 6 weekly-compile REQUIRED_CONFIG keys, once each under safety_reports and
  progress_reports. Declared in weekly_generate.py / progress_weekly_generate.py REQUIRED_CONFIG,
  so resolve_and_log WARNs config_row_missing whenever a compile runs while the row is absent —
  the same #336 class as the five gate rows, but on the WEEKLY (Friday / Compile-Now) cadence,
  so a low-volume WARN, not a storm driver. Values are the literal ConfigKey defaults.

    safety_reports.evergreen_contact_name                          / safety_reports   / the Evergreen Renewables office
    safety_reports.weekly_generate.job_timeout_seconds             / safety_reports   / 600
    safety_reports.weekly_generate.merge_memory_ceiling_bytes      / safety_reports   / 268435456
    progress_reports.evergreen_contact_name                        / progress_reports / the Evergreen Renewables office
    progress_reports.progress_weekly_generate.job_timeout_seconds  / progress_reports / 600
    progress_reports.progress_weekly_generate.merge_memory_ceiling_bytes / progress_reports / 268435456

No gate is among these 11 — every polling_enabled / *_enabled gate is already seeded, so this
migration cannot flip any capability on.

Idempotent per row (Setting+Workstream match → skip); get_setting is workstream-scoped, so
BOTH cells must match. Safe to re-run.

Auth: ITS_SMARTSHEET_TOKEN from macOS Keychain (same path the runtime SDK uses).

Run from `~/its` with the venv activated:

    python3 scripts/migrations/seed_generate_and_interval_config.py

Exit code 0 on success or no-op; nonzero on any error.
"""
from __future__ import annotations

import sys
from typing import Any

import requests  # type: ignore[import-untyped]

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[2]))

from shared import keychain, sheet_ids  # noqa: E402

BASE = "https://api.smartsheet.com/2.0"

_INTERVAL_NOTE = (
    "install-time poll cadence (seconds), read by scripts/launchd/install.sh to substitute "
    "the plist StartInterval and by the dashboard edit-interval verb as the persisted default. "
    "Seeded at the installed cadence — behaviorally inert (launchd already holds this value); "
    "NOT in any REQUIRED_CONFIG, so its absence caused no WARN, only made the dashboard "
    "interval-edit verb refuse. Changing it takes effect at the next install.sh re-install."
)
_GENERATE_NOTE = (
    "Declared in REQUIRED_CONFIG, so resolve_and_log WARNed config_row_missing on each weekly "
    "compile while absent (#336 observability). Seeded at the live ConfigKey default — no "
    "behavior change; only silences the per-compile WARN."
)

CONFIG_ROWS: list[dict[str, Any]] = [
    # --- Bucket A: 5 launchd interval rows (poll_interval_seconds) ---
    {
        "Setting": "safety_reports.weekly_send.poll_interval_seconds",
        "Workstream": "safety_reports",
        "Value": "900",
        "Description": "weekly-send poller (safety_reports/weekly_send_poll.py). " + _INTERVAL_NOTE,
    },
    {
        "Setting": "safety_reports.portal_poll.poll_interval_seconds",
        "Workstream": "safety_reports",
        "Value": "60",
        "Description": "portal PULL daemon (safety_reports/portal_poll.py). " + _INTERVAL_NOTE,
    },
    {
        "Setting": "safety_reports.compile_now_poll.poll_interval_seconds",
        "Workstream": "safety_reports",
        "Value": "90",
        "Description": "Compile-Now poller (safety_reports/compile_now_poll.py). " + _INTERVAL_NOTE,
    },
    {
        "Setting": "progress_reports.progress_send.poll_interval_seconds",
        "Workstream": "progress_reports",
        "Value": "900",
        "Description": "progress send poller (progress_reports/progress_send_poll.py). " + _INTERVAL_NOTE,
    },
    {
        "Setting": "field_ops.fieldops_sync.poll_interval_seconds",
        "Workstream": "field_ops",
        "Value": "90",
        "Description": "field-ops D1->Smartsheet up-sync daemon (field_ops/fieldops_sync.py). " + _INTERVAL_NOTE,
    },
    # --- Bucket B: 6 weekly-compile REQUIRED_CONFIG keys ---
    {
        "Setting": "safety_reports.evergreen_contact_name",
        "Workstream": "safety_reports",
        "Value": "the Evergreen Renewables office",
        "Description": (
            "Human contact-name phrase woven into the safety weekly-report email body "
            "(safety_reports/weekly_generate.py). " + _GENERATE_NOTE
        ),
    },
    {
        "Setting": "safety_reports.weekly_generate.job_timeout_seconds",
        "Workstream": "safety_reports",
        "Value": "600",
        "Description": (
            "Per-job wall-clock timeout (seconds) for the safety weekly compile — one hung "
            "Box/Smartsheet call can't block the whole run (safety_reports/weekly_generate.py). "
            + _GENERATE_NOTE
        ),
    },
    {
        "Setting": "safety_reports.weekly_generate.merge_memory_ceiling_bytes",
        "Workstream": "safety_reports",
        "Value": "268435456",
        "Description": (
            "Memory ceiling (bytes; 256 MiB) of gathered source PDFs before the safety packet "
            "merge (safety_reports/weekly_generate.py). " + _GENERATE_NOTE
        ),
    },
    {
        "Setting": "progress_reports.evergreen_contact_name",
        "Workstream": "progress_reports",
        "Value": "the Evergreen Renewables office",
        "Description": (
            "Human contact-name phrase woven into the progress weekly-report email body "
            "(progress_reports/progress_weekly_generate.py). " + _GENERATE_NOTE
        ),
    },
    {
        "Setting": "progress_reports.progress_weekly_generate.job_timeout_seconds",
        "Workstream": "progress_reports",
        "Value": "600",
        "Description": (
            "Per-job wall-clock timeout (seconds) for the progress weekly compile "
            "(progress_reports/progress_weekly_generate.py). " + _GENERATE_NOTE
        ),
    },
    {
        "Setting": "progress_reports.progress_weekly_generate.merge_memory_ceiling_bytes",
        "Workstream": "progress_reports",
        "Value": "268435456",
        "Description": (
            "Memory ceiling (bytes; 256 MiB) of gathered source PDFs before the progress packet "
            "merge (progress_reports/progress_weekly_generate.py). " + _GENERATE_NOTE
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
    """Seed the 11 rows. Idempotent per row (Setting+Workstream match → skip).

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
        f"[info] Seeding {len(CONFIG_ROWS)} rows (5 interval + 6 generate keys) at their live "
        "defaults — 2026-07-14 ITS_Config audit gap-fill; behaviorally inert"
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
