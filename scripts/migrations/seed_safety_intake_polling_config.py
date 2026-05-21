"""One-shot migration: seed `safety_reports.intake.*` polling-daemon rows.

Companion to PR #59 (polling-daemon trigger for safety_reports.intake_poll).
Run once during PR creation; safe to re-run (per-row idempotency-guarded on
Setting+Workstream — same pattern as scripts/migrations/seed_safety_intake_config.py).

What it does
------------

Seed 3 ITS_Config rows in workstream `safety_reports`:

    safety_reports.intake.poll_interval_seconds   = 60
    safety_reports.intake.mailbox                 = safety@evergreenmirror.com
    safety_reports.intake.polling_enabled         = true

These are read at runtime by safety_reports/intake_poll.py (poll cadence + mailbox)
and by scripts/install_safety_intake_daemon.sh (poll cadence at install time).

`polling_enabled=false` is the operator-facing kill switch for the safety-intake
poller specifically — distinct from the global `system.state` kill switch. Use it
when you want every OTHER ITS workstream to keep running while temporarily halting
just safety intake (e.g., during a sandbox/prod cutover or mailbox migration).

Auth: ITS_SMARTSHEET_TOKEN from macOS Keychain (same path the runtime SDK uses).

Run from `~/its` with the venv activated:

    python3 scripts/migrations/seed_safety_intake_polling_config.py

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

CONFIG_ROWS: list[dict[str, Any]] = [
    {
        "Setting": "safety_reports.intake.poll_interval_seconds",
        "Workstream": WORKSTREAM,
        "Value": "60",
        "Description": (
            "Integer seconds between safety-intake poll cycles. Read at "
            "install time by scripts/install_safety_intake_daemon.sh to "
            "substitute into the launchd plist's StartInterval. Changes to "
            "this row take effect at the next install run (re-run the "
            "installer); the running daemon does not hot-reload this value "
            "because launchd holds the interval. Default 60s — matches "
            "the prior Mail.app rule's perceived responsiveness without "
            "hammering Graph."
        ),
    },
    {
        "Setting": "safety_reports.intake.mailbox",
        "Workstream": WORKSTREAM,
        "Value": "safety@evergreenmirror.com",
        "Description": (
            "Microsoft Graph mailbox address polled by "
            "safety_reports/intake_poll.py. Sandbox value is "
            "safety@evergreenmirror.com; cutover to "
            "safety@evergreenrenewables.com happens at Phase 1.5 alongside "
            "the rest of the sandbox-to-production tenant swap. The mailbox "
            "MUST be covered by the Entra app registration's Application "
            "Access Policy (Mail.ReadWrite on the resource)."
        ),
    },
    {
        "Setting": "safety_reports.intake.polling_enabled",
        "Workstream": WORKSTREAM,
        "Value": "true",
        "Description": (
            "Per-workstream kill switch for safety-intake polling. When "
            "'false', poll_once() returns early without touching Graph or "
            "the pipeline (and the launchd job exits cleanly each cycle). "
            "Distinct from the global system.state kill switch in two ways: "
            "(a) scope — only halts safety intake, not all of ITS; and "
            "(b) layer — gates the read side independently of the rest of "
            "the workstream. Use during cutover, mailbox migration, or "
            "while debugging a stuck poll cycle without taking down the "
            "rest of the system."
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
    """Seed all 3 safety_reports.intake.* polling rows. Idempotent per row.

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
    print(f"[info] Workstream = {WORKSTREAM!r}")
    print(f"[info] Seeding {len(CONFIG_ROWS)} rows (3 safety_reports.intake.* polling knobs)")
    print()

    row_results = seed_config_rows()

    print()
    print("Summary:")
    for setting, status in row_results:
        print(f"  {setting}: {status}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
