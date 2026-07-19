"""One-shot migration: seed the `po_materials.rfq_poll.*` ITS_Config rows
(ADR-0004 R2 — the outbound-RFQ generation daemon).

Companion to `po_materials/rfq_poll.py`. Run once at PR landing; safe to re-run
(per-row idempotency-guarded on Setting+Workstream — the seed_estimates_config.py
pattern).

Why the gate row exists even though the value ships FALSE — the dark-ship gate
reflex (HOUSE_REFLEXES §5): a boolean gate read via `_read_bool_setting(default=
False)` treats a MISSING row identically to `false`, so a capability that "ships
dark" without a seeded row has NO visible switch at all. Seeding the row `false`
in the same change that adds the gated code makes activation a visible cell-flip,
and the #336 `resolve_and_log` startup pass stops WARNing `config_row_missing`.

What it seeds (2 rows, workstream `po_materials`):

    po_materials.rfq_poll.polling_enabled       = false  (the ONE gate — dark)
    po_materials.rfq_poll.poll_interval_seconds = 120    (install-time cadence)

Auth: ITS_SMARTSHEET_TOKEN from macOS Keychain (same path the runtime SDK uses).

Run from `~/its` with the venv activated:

    python3 scripts/migrations/seed_rfq_config.py

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
        "Setting": "po_materials.rfq_poll.polling_enabled",
        "Workstream": WORKSTREAM,
        "Value": "false",
        "Description": (
            "Gate for the outbound-RFQ generation daemon (rfq_poll, ADR-0004 R2): "
            "pull composed RFQs from the Worker queue, rfq:v1 HMAC-verify "
            "(recompute-from-fields), render one PRICE-FREE RFQ PDF per vendor in "
            "the signed fan-out list, file to Box (<job>/Purchase Orders/RFQs/), "
            "write the RFQ_Log (rfq, vendor) row + the RFQ_Pending_Review row "
            "(Workstream 'po_materials_rfq'), mark-filed once per rfq, and mirror "
            "review-sheet SENT stamps back (status-sync). Ships FALSE (dark). Flip "
            "to 'true' ONLY after (a) the Worker is deployed with the RFQ routes + "
            "PORTAL_RFQ_API_TOKEN secret, (b) Keychain holds ITS_PORTAL_RFQ_TOKEN, "
            "(c) RFQ_Log + RFQ_Pending_Review are built and SHEET_RFQ_LOG / "
            "SHEET_RFQ_PENDING_REVIEW flipped (build_rfq_log_sheet.py + "
            "build_rfq_pending_review_sheet.py), and (d) the R2 partial live smoke "
            "has passed on the mirror. This daemon NEVER sends anything "
            "customer-facing (generation half of the External Send Gate; the send "
            "half is PR-D's rfq_send, its own gate, its own go-live)."
        ),
    },
    {
        "Setting": "po_materials.rfq_poll.poll_interval_seconds",
        "Workstream": WORKSTREAM,
        "Value": "120",
        "Description": (
            "Integer seconds between rfq_poll cycles. Read at INSTALL time by "
            "scripts/launchd/install.sh to substitute into the plist's StartInterval "
            "(the value is BAKED into the installed plist — changes take effect at "
            "the next `install.sh load org.solutionsmith.its.rfq-poll`, not hot). "
            "Default 120s: RFQ composition is an office trickle, staggered off "
            "portal-poll (60s), po-poll (90s), and estimate-poll (120s)."
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
    """Seed the rfq_poll rows. Idempotent per row.

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
    print(f"[info] Seeding {len(CONFIG_ROWS)} rows (rfq_poll: gate false + interval)")
    print()

    row_results = seed_config_rows()

    print()
    print("Summary:")
    for setting, status in row_results:
        print(f"  {setting}: {status}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
