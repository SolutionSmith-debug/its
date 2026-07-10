"""One-shot migration: seed the `po_materials.config_actuator.*` ITS_Config row(s) (§50
config editor, slice 2).

Companion to the config actuator (po_materials/config_actuator.py). Run once at PR landing;
safe to re-run (per-row idempotency on Setting+Workstream — the seed_po_materials_config.py
pattern).

Why the gate row exists even though it ships FALSE — the dark-ship gate reflex
(HOUSE_REFLEXES §5): a boolean gate read via `_read_str_setting(..., "false")` treats a
MISSING row identically to `false`, so a capability that "ships dark" without a seeded row
has NO visible switch at all — the operator hunts for a cell that doesn't exist. Seeding the
row `false` in the same change that adds the gated code makes activation a visible cell-flip,
and the #336 `resolve_and_log` startup pass stops WARNing `config_row_missing`.

The Worker base-URL key (`safety_reports.portal.worker_base_url`) is NOT seeded here — the
config actuator SHARES the one Safety Portal Worker with portal_poll / po_poll, so that row
already exists (seeded by the portal config seeder). This migration seeds ONLY the actuator's
own polling gate.

What it seeds (1 row, workstream `po_materials`):

    po_materials.config_actuator.polling_enabled = false

Auth: ITS_SMARTSHEET_TOKEN from macOS Keychain (same path the runtime SDK uses).

Run from `~/its` with the venv activated:

    python3 scripts/migrations/seed_config_actuator_config.py

Exit code 0 on success or no-op; nonzero on any error.
"""
from __future__ import annotations

import sys
from typing import Any

import requests  # type: ignore[import-untyped]

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[2]))

from shared import keychain, sheet_ids  # noqa: E402

BASE = "https://api.smartsheet.com/2.0"

WORKSTREAM = "po_materials"

CONFIG_ROWS: list[dict[str, Any]] = [
    {
        "Setting": "po_materials.config_actuator.polling_enabled",
        "Workstream": WORKSTREAM,
        "Value": "false",
        "Description": (
            "Runtime gate for the config-editor actuator (config_actuator.py, §50 config "
            "editor slice 2): the privileged Mac daemon that drains config_requests, "
            "re-validates + writes a purchaser/tax/terms edit vs live git HEAD, commits + "
            "runs CI + merges, then deploys the Worker (re-bundling the config it imports at "
            "build time). Ships FALSE (dark) — HIGH-CAPABILITY (COMMITS + DEPLOYS code). Flip "
            "to 'true' ONLY after (a) the Worker is deployed with the /api/internal/config/* "
            "routes + the PORTAL_CONFIG_API_TOKEN secret, (b) Keychain holds "
            "ITS_PORTAL_CONFIG_TOKEN (the SEPARATE config-token tier), (c) the operator's git "
            "push + Cloudflare/wrangler auth are present on the Mac, and (d) the partial live "
            "smoke has passed on the mirror. This is a Developer-Operator-gated activation "
            "(git/deploy/secret is a FIXED high-capability class — never a Tier-2 flip)."
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
    """Seed the po_materials.config_actuator.* row(s). Idempotent per row.

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
    print(f"[info] Seeding {len(CONFIG_ROWS)} row (config_actuator polling gate, ships false)")
    print()

    row_results = seed_config_rows()

    print()
    print("Summary:")
    for setting, status in row_results:
        print(f"  {setting}: {status}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
