"""One-shot migration: seed the `subcontracts.subcontract_poll.*` ITS_Config rows
(SC-S3c).

Companion to the subcontract generation pipeline (subcontracts/subcontract_poll.py).
Run once at PR landing; safe to re-run (per-row idempotency-guarded on
Setting+Workstream — the seed_po_materials_config.py / seed_safety_intake_polling_config.py
pattern), so a re-run seeds only rows not already present.

Why the gate rows exist even though every value ships FALSE — the dark-ship gate
reflex (HOUSE_REFLEXES §5): a boolean gate read via `_read_bool_setting(default=
False)` treats a MISSING row identically to `false`, so a capability that "ships
dark" without a seeded row has NO visible switch at all — the operator hunts for a
cell that doesn't exist (bit the 2026-07-05 equipment/materials activation). Seeding
the rows `false` in the same change that adds the gated code makes activation a
visible cell-flip, and the #336 `resolve_and_log` startup pass stops WARNing
`config_row_missing`.

What it seeds (4 rows, workstream `subcontracts`):

    subcontracts.subcontract_poll.polling_enabled             = false   (drafts pass ①)
    subcontracts.subcontract_poll.subcontractors_sync_enabled = false   (subcontractor passes ② ③)
    subcontracts.subcontract_poll.status_sync_enabled         = false   (status pass ④)
    subcontracts.subcontract_poll.poll_interval_seconds       = 120     (install-time cadence)

Auth: ITS_SMARTSHEET_TOKEN from macOS Keychain (same path the runtime SDK uses).

Run from `~/its` with the venv activated:

    python3 scripts/migrations/seed_subcontracts_config.py

Exit code 0 on success or no-op; nonzero on any error.
"""
from __future__ import annotations

import sys
from typing import Any

import requests  # type: ignore[import-untyped]

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[2]))

from shared import keychain, sheet_ids  # noqa: E402

BASE = "https://api.smartsheet.com/2.0"

WORKSTREAM = "subcontracts"

CONFIG_ROWS: list[dict[str, Any]] = [
    {
        "Setting": "subcontracts.subcontract_poll.polling_enabled",
        "Workstream": WORKSTREAM,
        "Value": "false",
        "Description": (
            "Gate for subcontract_poll pass 1 (the DRAFTS pass): pull queued "
            "subcontracts from the Worker, HMAC-verify (sub:v1), render the "
            "deterministic package (Subcontract.docx + Annex C - Schedule of "
            "Values.xlsx), file BOTH to Box/Subcontract_Log/Subcontract_Pending_Review, "
            "receipt via mark-filed. Ships FALSE (dark). Flip to 'true' ONLY after "
            "(a) the Worker is deployed with the subcontract routes + "
            "PORTAL_SUB_API_TOKEN secret, (b) Keychain holds ITS_PORTAL_SUB_TOKEN and "
            "the shared ITS_PORTAL_HMAC_SECRET matches the Worker payload secret, and "
            "(c) the SC-S3c partial live smoke has passed on the mirror. Flipping this "
            "alone enables FILING only — the subcontractor SEND stays dark until SC-S4/S5 "
            "lands and its own gates flip."
        ),
    },
    {
        "Setting": "subcontracts.subcontract_poll.subcontractors_sync_enabled",
        "Workstream": WORKSTREAM,
        "Value": "false",
        "Description": (
            "Gate for subcontract_poll passes 2+3 (the §51 subcontractor sync): the "
            "ITS_Subcontractors full-replace down-sync into the Worker's D1 cache "
            "(dirty-row fence protects portal edits) AND the dirty-subcontractor "
            "up-sync back into ITS_Subcontractors (bridge-key find-or-create by Sub "
            "Key, column-scoped, never-delete, Archived non-clobber). Ships FALSE "
            "(dark). Flip to 'true' after the Worker subcontract routes are deployed "
            "and ITS_Subcontractors is seeded "
            "(scripts/migrations/seed_its_subcontractors.py) — safe to enable before "
            "the drafts pass; the passes are independent."
        ),
    },
    {
        "Setting": "subcontracts.subcontract_poll.status_sync_enabled",
        "Workstream": WORKSTREAM,
        "Value": "false",
        "Description": (
            "Gate for subcontract_poll pass 4 (the STATUS pass): mirror "
            "Subcontract_Pending_Review approve/SENT stamps to the Worker's "
            "status-sync route (D1 display cache; approved-then-sent ordering; "
            "superseded flip) and stamp Subcontract_Log (Status / Sent At / "
            "Superseded By). Ships FALSE (dark). Flip to 'true' together with "
            "polling_enabled — it is a no-op until review rows exist. The 'executed' "
            "terminal is operator-set on Subcontract_Log, not auto-synced here. F22 "
            "approval VERIFICATION stays with the SC-S4/S5 send poller; this pass "
            "reports state, it never authorizes a send."
        ),
    },
    {
        "Setting": "subcontracts.subcontract_poll.poll_interval_seconds",
        "Workstream": WORKSTREAM,
        "Value": "120",
        "Description": (
            "Integer seconds between subcontract_poll cycles. Read at INSTALL time by "
            "scripts/launchd/install.sh to substitute into the plist's StartInterval "
            "(the value is BAKED into the installed plist — changes take effect at the "
            "next `install.sh load org.solutionsmith.its.subcontract-poll`, not hot). "
            "Default 120s staggers off po-poll (90s) and portal-poll (60s) and suits "
            "the low subcontract volume."
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
    """Seed all 4 subcontracts.subcontract_poll.* rows. Idempotent per row.

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
    print(f"[info] Seeding {len(CONFIG_ROWS)} rows (subcontract_poll: 3 gates false + interval)")
    print()

    row_results = seed_config_rows()

    print()
    print("Summary:")
    for setting, status in row_results:
        print(f"  {setting}: {status}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
