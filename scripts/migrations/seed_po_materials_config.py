"""One-shot migration: seed the `po_materials.po_poll.*` ITS_Config rows (PO S4).

Companion to the S4 generation pipeline (po_materials/po_poll.py). Run once at PR
landing; safe to re-run (per-row idempotency-guarded on Setting+Workstream — the
seed_safety_intake_polling_config.py pattern).

Why the gate rows exist even though every value ships FALSE — the dark-ship gate
reflex (HOUSE_REFLEXES §5): a boolean gate read via `_read_bool_setting(default=
False)` treats a MISSING row identically to `false`, so a capability that "ships
dark" without a seeded row has NO visible switch at all — the operator hunts for a
cell that doesn't exist (bit the 2026-07-05 equipment/materials activation). Seeding
the rows `false` in the same change that adds the gated code makes activation a
visible cell-flip, and the #336 `resolve_and_log` startup pass stops WARNing
`config_row_missing`.

What it seeds (4 rows, workstream `po_materials`):

    po_materials.po_poll.polling_enabled        = false   (drafts pass ①)
    po_materials.po_poll.vendors_sync_enabled   = false   (vendor passes ② ③)
    po_materials.po_poll.status_sync_enabled    = false   (status pass ④)
    po_materials.po_poll.poll_interval_seconds  = 90      (install-time cadence)

Auth: ITS_SMARTSHEET_TOKEN from macOS Keychain (same path the runtime SDK uses).

Run from `~/its` with the venv activated:

    python3 scripts/migrations/seed_po_materials_config.py

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
        "Setting": "po_materials.po_poll.polling_enabled",
        "Workstream": WORKSTREAM,
        "Value": "false",
        "Description": (
            "Gate for po_poll pass 1 (the DRAFTS pass): pull queued POs from the "
            "Worker, HMAC-verify + totals-assert, render the PO PDF, file to "
            "Box/PO_Log/PO_Pending_Review, receipt via mark-filed. Ships FALSE "
            "(dark). Flip to 'true' ONLY after (a) the Worker is deployed with the "
            "PO routes + PORTAL_PO_API_TOKEN secret, (b) Keychain holds "
            "ITS_PORTAL_PO_TOKEN, and (c) the S4 partial live smoke has passed on "
            "the mirror. Flipping this alone enables FILING only — the vendor SEND "
            "stays dark until S5 lands and its own gates flip."
        ),
    },
    {
        "Setting": "po_materials.po_poll.vendors_sync_enabled",
        "Workstream": WORKSTREAM,
        "Value": "false",
        "Description": (
            "Gate for po_poll passes 2+3 (the §51 vendor sync): the ITS_Vendors "
            "full-replace down-sync into the Worker's D1 cache (dirty-row fence "
            "protects portal edits) AND the dirty-vendor up-sync back into "
            "ITS_Vendors (bridge-key find-or-create by Vendor Key, column-scoped, "
            "never-delete). Ships FALSE (dark). Flip to 'true' after the Worker PO "
            "routes are deployed and ITS_Vendors is seeded "
            "(scripts/migrations/seed_its_vendors.py) — safe to enable before the "
            "drafts pass; the passes are independent."
        ),
    },
    {
        "Setting": "po_materials.po_poll.status_sync_enabled",
        "Workstream": WORKSTREAM,
        "Value": "false",
        "Description": (
            "Gate for po_poll pass 4 (the STATUS pass): mirror PO_Pending_Review "
            "approve/SENT stamps to the Worker's status-sync route (D1 display "
            "cache; approved-then-sent ordering; superseded flip) and stamp PO_Log "
            "(Status / Sent At / Superseded By). Ships FALSE (dark). Flip to 'true' "
            "together with polling_enabled — it is a no-op until review rows exist. "
            "F22 approval VERIFICATION stays with the S5 send poller; this pass "
            "reports state, it never authorizes a send."
        ),
    },
    {
        "Setting": "po_materials.po_poll.poll_interval_seconds",
        "Workstream": WORKSTREAM,
        "Value": "90",
        "Description": (
            "Integer seconds between po_poll cycles. Read at INSTALL time by "
            "scripts/launchd/install.sh to substitute into the plist's "
            "StartInterval (the value is BAKED into the installed plist — changes "
            "take effect at the next `install.sh load org.solutionsmith.its.po-poll`, "
            "not hot). Default 90s keeps a small stagger off portal-poll (60s), "
            "matching fieldops-sync."
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
    """Seed all 4 po_materials.po_poll.* rows. Idempotent per row.

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
    print(f"[info] Seeding {len(CONFIG_ROWS)} rows (3 po_poll gates, ALL false + the poll interval)")
    print()

    row_results = seed_config_rows()

    print()
    print("Summary:")
    for setting, status in row_results:
        print(f"  {setting}: {status}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
