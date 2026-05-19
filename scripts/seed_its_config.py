#!/usr/bin/env python3
"""Seed ITS_Config with the seven initial rows from Handover v5 §ITS_Config.

OPERATIONAL — makes REAL Smartsheet API calls and (on confirmation) writes
to ITS_Config. Sandbox-only: sheet ID comes from shared.sheet_ids.

Idempotent:
  - Existing keys with the same Value are SKIPPED.
  - Existing keys with a differing Value are flagged STALE and NOT
    overwritten. Surface them to the operator to resolve manually.
  - Missing keys are ADDED.
  - Setting-key match is case-sensitive, no whitespace trim.

A dry-run plan prints first. y/N before any write.

The reviewer_chain Value is pulled from shared.defaults.DEFAULT_REVIEWER_CHAINS
(the canonical source) and JSON-encoded — do not hand-copy the dict.

Re-run after:
  - Schema additions to the seed set (update SEED_ROWS below).
  - Reviewer-chain edits in shared.defaults (this script re-reads it).
"""
from __future__ import annotations

import json
import sys

from shared import sheet_ids, smartsheet_client
from shared.defaults import DEFAULT_REVIEWER_CHAINS


def _build_seed_rows() -> list[dict[str, str]]:
    """Construct the seven seed rows. Reviewer chain pulled live from shared.defaults."""
    reviewer_chain_json = json.dumps(
        DEFAULT_REVIEWER_CHAINS["safety_reports"], separators=(",", ":")
    )
    return [
        {
            "Setting": "system.state",
            "Value": "ACTIVE",
            "Workstream": "global",
            "Description": "Kill switch — ACTIVE | PAUSED | MAINTENANCE.",
        },
        {
            "Setting": "system.heartbeat_url",
            "Value": "PLACEHOLDER_uptimerobot_heartbeat_url",
            "Workstream": "global",
            "Description": "UptimeRobot heartbeat URL pinged by scripts/watchdog.py.",
        },
        {
            "Setting": "system.sentry_dsn_keychain_key",
            "Value": "ITS_SENTRY_DSN",
            "Workstream": "global",
            "Description": "Keychain entry name holding the Sentry DSN.",
        },
        {
            "Setting": "system.resend_api_keychain_key",
            "Value": "ITS_RESEND_API_KEY",
            "Workstream": "global",
            "Description": "Keychain entry name holding the Resend API key (out-of-band CRITICAL path).",
        },
        {
            "Setting": "system.operator_email",
            "Value": "seths@evergreenmirror.com",
            "Workstream": "global",
            "Description": "Operator email for CRITICAL alerts and oncall surfacing.",
        },
        {
            "Setting": "safety_reports.reviewer_chain",
            "Value": reviewer_chain_json,
            "Workstream": "safety_reports",
            "Description": (
                "JSON reviewer chain {primary, secondary, tertiary, "
                "delay_to_secondary_hours, delay_to_tertiary_hours}. "
                "Source of truth: shared.defaults.DEFAULT_REVIEWER_CHAINS."
            ),
        },
        {
            "Setting": "safety_reports.external_send_gate",
            "Value": "MANUAL",
            "Workstream": "safety_reports",
            "Description": (
                "External send gate mode. MANUAL = human approval required "
                "for every send (Foundation Mission v6 Invariant 1)."
            ),
        },
    ]


def classify(
    seed_rows: list[dict[str, str]],
    existing_rows: list[dict[str, object]],
) -> tuple[list[dict[str, str]], list[dict[str, str]], list[tuple[dict[str, str], object]]]:
    """Split seed rows into (added, skipped, stale) based on existing sheet state.

    Match key is (Setting, Workstream) — both case-sensitive, no whitespace trim.
    Stale entries carry the existing Value so the operator can see the divergence.
    """
    existing_by_key: dict[tuple[str, str], dict[str, object]] = {}
    for row in existing_rows:
        setting = row.get("Setting")
        workstream = row.get("Workstream")
        if isinstance(setting, str) and isinstance(workstream, str):
            existing_by_key[(setting, workstream)] = row

    added: list[dict[str, str]] = []
    skipped: list[dict[str, str]] = []
    stale: list[tuple[dict[str, str], object]] = []

    for seed in seed_rows:
        key = (seed["Setting"], seed["Workstream"])
        existing = existing_by_key.get(key)
        if existing is None:
            added.append(seed)
        elif existing.get("Value") == seed["Value"]:
            skipped.append(seed)
        else:
            stale.append((seed, existing.get("Value")))

    return added, skipped, stale


def _print_plan(
    added: list[dict[str, str]],
    skipped: list[dict[str, str]],
    stale: list[tuple[dict[str, str], object]],
) -> None:
    print("Dry-run plan:")
    print("-" * 60)
    for row in added:
        print(f"  ADDED    {row['Setting']} ({row['Workstream']})")
    for row in skipped:
        print(f"  SKIPPED  {row['Setting']} ({row['Workstream']}) — value matches")
    for row, existing_value in stale:
        print(
            f"  STALE    {row['Setting']} ({row['Workstream']}) — "
            f"existing={existing_value!r} seed={row['Value']!r} — NOT overwriting"
        )
    print("-" * 60)
    print(f"Totals: {len(added)} ADDED / {len(skipped)} SKIPPED / {len(stale)} STALE")


def main() -> None:
    print("ITS_Config seed")
    print("=" * 60)

    seed_rows = _build_seed_rows()
    existing = smartsheet_client.get_rows(sheet_ids.SHEET_CONFIG)
    added, skipped, stale = classify(seed_rows, existing)

    _print_plan(added, skipped, stale)

    if not added:
        print("\nNothing to write.")
        if stale:
            print("STALE rows above need operator attention.")
        return

    answer = input(f"\nWrite {len(added)} new row(s) to ITS_Config? [y/N] ").strip().lower()
    if answer != "y":
        print("Aborted; no writes.")
        sys.exit(1)

    smartsheet_client.add_rows(sheet_ids.SHEET_CONFIG, added)
    print(
        f"\nAdded {len(added)} / Skipped {len(skipped)} / Stale {len(stale)}"
    )


if __name__ == "__main__":
    main()
