"""One-shot migration: seed `safety_reports.intake.*` rows in ITS_Config.

Companion to the R3 session 1 PR (intake.py wiring). Run once during PR
creation; safe to re-run (per-row idempotency-guarded on Setting+Workstream).

What it does
------------

Seed 5 ITS_Config rows in workstream `safety_reports`:

    safety_reports.intake.allowed_senders         = ["seths@evergreenmirror.com"]
    safety_reports.intake.classification_model    = claude-sonnet-4-6
    safety_reports.intake.box_filing_enabled      = true
    safety_reports.intake.review_queue_on_low_confidence = true
    safety_reports.intake.confidence_threshold    = 0.75

Each row carries a Description that explains the value contract.

The allowed_senders list is the post-Mail.app-rule double-check used by
`safety_reports/intake.py`'s first pipeline stage. The Mail.app rule is
the primary defense (only allowlisted mail lands in the hot-folder);
this in-code check is defense-in-depth so any message that somehow
reaches the script from outside the list routes to ITS_Quarantine
without an Anthropic API call.

The other 4 rows are runtime knobs the operator can tune via sheet edit.

Auth: ITS_SMARTSHEET_TOKEN from macOS Keychain (same path the runtime
SDK uses).

Run from `~/its` with the venv activated:

    python3 scripts/migrations/seed_safety_intake_config.py

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
        "Setting": "safety_reports.intake.allowed_senders",
        "Workstream": WORKSTREAM,
        "Value": '["seths@evergreenmirror.com"]',
        "Description": (
            "JSON list of allowed sender emails (or '@domain.com' entries for "
            "domain match). The Mail.app rule is the primary defense; this is "
            "the in-code defense-in-depth check inside intake.py. "
            "Non-allowlisted senders route to ITS_Quarantine with category "
            "'untrusted_sender' and no Anthropic API call."
        ),
    },
    {
        "Setting": "safety_reports.intake.classification_model",
        "Workstream": WORKSTREAM,
        "Value": "claude-sonnet-4-6",
        "Description": (
            "Anthropic model ID used for the classify+extract call in "
            "intake.py. Default Sonnet 4.6. Override here if a workstream-"
            "specific tradeoff applies (e.g., 'claude-haiku-4-5-20251001' "
            "for cheaper bulk classification; not currently recommended "
            "for safety report extraction)."
        ),
    },
    {
        "Setting": "safety_reports.intake.box_filing_enabled",
        "Workstream": WORKSTREAM,
        "Value": "true",
        "Description": (
            "Capability flag. When 'true', intake.py uploads attachments to "
            "the project's Box subfolder after the Daily Reports row writes "
            "successfully. When 'false', intake.py writes the Daily Reports "
            "row but skips Box upload and tags Notes/Action Items with "
            "'[box_filing_disabled]'. Operator-tunable for testing / "
            "incident response."
        ),
    },
    {
        "Setting": "safety_reports.intake.review_queue_on_low_confidence",
        "Workstream": WORKSTREAM,
        "Value": "true",
        "Description": (
            "Behavior flag. When 'true' and classification confidence is below "
            "the threshold (see safety_reports.intake.confidence_threshold), "
            "the message routes to ITS_Review_Queue with "
            "Reason=low-confidence-extraction instead of writing a Daily "
            "Reports row directly. Default 'true' — Phase 1 conservatism."
        ),
    },
    {
        "Setting": "safety_reports.intake.confidence_threshold",
        "Workstream": WORKSTREAM,
        "Value": "0.75",
        "Description": (
            "Float threshold for the classification confidence gate (see "
            "safety_reports.intake.review_queue_on_low_confidence). Below "
            "this value, intake.py routes to Review Queue. Default 0.75. "
            "Tune down if too many real reports are getting flagged for "
            "review; up if low-quality classifications are slipping through."
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
    """Seed all 5 safety_reports.intake.* rows. Idempotent per row.

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
    print(f"[info] Seeding {len(CONFIG_ROWS)} rows (5 safety_reports.intake.* knobs)")
    print()

    row_results = seed_config_rows()

    print()
    print("Summary:")
    for setting, status in row_results:
        print(f"  {setting}: {status}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
