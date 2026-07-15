"""One-shot migration: seed the `subcontracts.subcontract_send.*` ITS_Config rows (SC-S4).

Companion to the subcontract SEND lane (subcontracts/subcontract_send.py +
subcontract_send_poll.py). Run once at PR landing; safe to re-run (per-row idempotency-guarded
on Setting+Workstream — the seed_subcontracts_config.py / seed_po_materials_config.py pattern),
so a re-run seeds only rows not already present.

Why the gate row exists even though it ships FALSE — the dark-ship gate reflex
(HOUSE_REFLEXES §5): a boolean gate read via `_read_bool_setting(default=False)` treats a
MISSING row identically to `false`, so a capability that "ships dark" without a seeded row has
NO visible switch at all — the operator hunts for a cell that doesn't exist. Seeding the row
`false` in the same change that adds the gated code makes activation a visible cell-flip, and
the #336 `resolve_and_log` startup pass stops WARNing `config_row_missing`. A SEND gate never
fails open (DEFAULT_POLLING_ENABLED=False), so the row-absent case is also SAFE.

What it seeds (4 rows, workstream `subcontracts`), mirroring `po_materials.po_send.*`:

    subcontracts.subcontract_send.polling_enabled      = false      (the SEND gate — dark)
    subcontracts.subcontract_send.poll_interval_seconds = 900       (install-time cadence)
    subcontracts.subcontract_send.scheduled_send_local  = MON 07:00 (scheduled batch window)
    subcontracts.subcontract_send.from_mailbox          = procurement@evergreenmirror.com

Auth: ITS_SMARTSHEET_TOKEN from macOS Keychain (same path the runtime SDK uses).

Run from `~/its` with the venv activated:

    python3 scripts/migrations/seed_subcontracts_send_config.py

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
        "Setting": "subcontracts.subcontract_send.polling_enabled",
        "Workstream": WORKSTREAM,
        "Value": "false",
        "Description": (
            "Gate for the subcontract SEND poller (subcontract_send_poll, SC-S4): dispatch "
            "approved Subcontract_Pending_Review rows (Send Now / Approve for Scheduled Send) "
            "to the SUBCONTRACTOR from procurement@, after the F22 approval gate against the "
            "ITS — Subcontracts workspace (§46 membership = approval authority). The email "
            "attaches the combined Subcontract Package.zip (body + Exhibit A + Annex C SoV). "
            "Ships FALSE (dark) — the External Send Gate: NO subcontractor email fires until "
            "this is flipped. Flip to 'true' ONLY after (a) the subcontract_send partial live "
            "smoke passed on the mirror, (b) procurement@ exists on the tenant + is in the "
            "app's Application Access Policy scope (or Graph 403s the send), and (c) the "
            "subcontract approvers are shared into the ITS — Subcontracts workspace (an empty "
            "share list fails closed — all sends HELD). Independent of the subcontract_poll "
            "generation gates. SC-S4 is a fixed high-capability-class (External Send Gate) "
            "activation — read this Description before flipping (HOUSE_REFLEXES §5); escalate "
            "to Seth."
        ),
    },
    {
        "Setting": "subcontracts.subcontract_send.poll_interval_seconds",
        "Workstream": WORKSTREAM,
        "Value": "900",
        "Description": (
            "Integer seconds between subcontract_send_poll cycles. Read at INSTALL time by "
            "scripts/launchd/install.sh to substitute into the plist's StartInterval (BAKED "
            "into the installed plist — changes take effect at the next `install.sh load "
            "org.solutionsmith.its.subcontract-send`, not hot). Default 900s (15 min) matches "
            "po-send / weekly-send / progress-send — an approval poller, not a fast puller."
        ),
    },
    {
        "Setting": "subcontracts.subcontract_send.scheduled_send_local",
        "Workstream": WORKSTREAM,
        "Value": "MON 07:00",
        "Description": (
            "The weekly scheduled-send window (local Pacific) for subcontract rows approved "
            "via 'Approve for Scheduled Send'. 'Send Now' dispatches immediately, out-of-band "
            "of this window. Format 'DDD HH:MM' (e.g. 'MON 07:00'). Read at runtime by "
            "subcontract_send_poll."
        ),
    },
    {
        "Setting": "subcontracts.subcontract_send.from_mailbox",
        "Workstream": WORKSTREAM,
        "Value": "procurement@evergreenmirror.com",
        "Description": (
            "The From mailbox for subcontract sends (2026-07-15 operator decision — reuses "
            "PO's procurement@ mailbox; no dedicated subcontracts mailbox). Read at RUNTIME by "
            "subcontract_send.send_one_row every dispatch (#336 REQUIRED_CONFIG). Mirror value "
            "here; the production cutover repoints it (cutover_checklist). The mailbox must "
            "exist + be in the app's Application Access Policy scope or Graph 403s the send."
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
    """Seed all 4 subcontracts.subcontract_send.* rows. Idempotent per row.

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
    print(f"[info] Seeding {len(CONFIG_ROWS)} rows (subcontract_send: gate false + interval + window + mailbox)")
    print()

    row_results = seed_config_rows()

    print()
    print("Summary:")
    for setting, status in row_results:
        print(f"  {setting}: {status}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
