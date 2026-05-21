#!/usr/bin/env python3
"""Picklist sync entry point — runs Picklist_Sync_Config-driven sync.

Modes:
    (default)               Run every enabled mapping in Picklist_Sync_Config.
    --dry                   Compute diffs + log proposed changes; no API writes.
    --mapping <mapping_id>  Run only the named mapping (regardless of enabled).
    --smoke-test            Bootstrap sandbox sheets + mapping, exercise full
                            add / remove-safe / remove-blocked-by-live-cells
                            flow end-to-end, tear down. NO touch to production
                            mappings.

Trigger:
    Default mode runs under launchd every 15 minutes
    (scripts/launchd/org.solutionsmith.its.picklist-sync.plist). One ITS_Errors
    INFO row per run summarizing mappings examined / applied / skipped /
    blocked / failed.

Failure handling:
    Per Op Stds v9 §27, single-mapping failures stay at ERROR (recorded in
    ITS_Errors, no operator wake-up). When >=3 mappings fail in one run,
    shared/picklist_sync.sync_all() escalates to CRITICAL via the
    triple-fire path (Sentry + Resend + ITS_Errors).
"""
from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shared import (  # noqa: E402
    picklist_sync,
    sheet_ids,
    smartsheet_client,
)
from shared.error_log import Severity, its_error_log, log  # noqa: E402

_SCRIPT = "scripts.run_picklist_sync"


def _print_stats(stats: picklist_sync.SyncStats) -> None:
    print(
        f"picklist sync — examined {stats.mappings_examined}: "
        f"applied={stats.mappings_applied}, "
        f"skipped_unchanged={stats.mappings_skipped_unchanged}, "
        f"dry_run={stats.mappings_dry_run}, "
        f"halted_oversize={stats.mappings_halted_oversize}, "
        f"failed={stats.mappings_failed}"
    )
    if stats.additions_total or stats.removals_applied_total or stats.removals_blocked_total:
        print(
            f"  options: +{stats.additions_total} additions, "
            f"-{stats.removals_applied_total} removals (applied), "
            f"-{stats.removals_blocked_total} removals (blocked by live cells)"
        )
    for r in stats.results:
        if r.status in ("failed", "halted_oversize"):
            print(f"  {r.mapping_id!r}: {r.status} — {r.error}")
        elif r.removals_blocked:
            print(
                f"  {r.mapping_id!r}: removal(s) blocked → Review Queue rows "
                f"{r.review_queue_rows}: {', '.join(repr(o) for o in r.removals_blocked)}"
            )


def _log_run_summary(stats: picklist_sync.SyncStats) -> None:
    """Single INFO row to ITS_Errors per run, per Op Stds 'observable' bar."""
    log(
        Severity.INFO,
        _SCRIPT,
        f"picklist sync — examined {stats.mappings_examined}, "
        f"applied {stats.mappings_applied}, "
        f"skipped_unchanged {stats.mappings_skipped_unchanged}, "
        f"halted_oversize {stats.mappings_halted_oversize}, "
        f"failed {stats.mappings_failed}, "
        f"+{stats.additions_total} adds, "
        f"-{stats.removals_applied_total} removed, "
        f"-{stats.removals_blocked_total} blocked",
        error_code="picklist_sync_run_summary",
    )


# ---- Smoke-test mode ----------------------------------------------------


def _smoke_test() -> int:
    """Bootstrap sandbox sheets + a Picklist_Sync_Config mapping, exercise
    the full flow, then tear everything down.

    Failure of any step prints the failure and attempts teardown of
    whatever was provisioned so we don't leave orphan sandbox sheets.

    Verifies:
      1. add — source has 3 rows, after sync target picklist has 3 options.
      2. add propagation — append 4th row to source, sync, target picklist has 4 options.
      3. remove-safe — drop a row whose value is not referenced; sync removes it.
      4. remove-blocked — drop a row whose value IS used in a live target cell;
         sync keeps the option + writes Review Queue row.
    """
    print("=" * 60)
    print("picklist sync smoke test — provisioning sandbox sheets")
    print("=" * 60)

    folder_id = sheet_ids.FOLDER_SYSTEM_CONFIG
    timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    source_name = f"_smoke_picklist_source_{timestamp}"
    target_name = f"_smoke_picklist_target_{timestamp}"
    mapping_id = f"_smoke_{timestamp}"

    # Track provisioned IDs as separate Optionals so teardown can skip
    # whichever slots never got assigned. Using distinct names (rather
    # than a dict[str, int | None]) lets mypy narrow the post-assignment
    # type to int.
    source_sheet_id: int | None = None
    target_sheet_id: int | None = None
    mapping_row_id: int | None = None

    try:
        # 1. Provision sandbox sheets
        source_sheet_id = smartsheet_client.create_sheet_in_folder(
            folder_id, source_name,
            [
                {"title": "vendor_name", "type": "TEXT_NUMBER", "primary": True},
            ],
        )
        print(f"[setup] source sandbox sheet id={source_sheet_id}")

        target_sheet_id = smartsheet_client.create_sheet_in_folder(
            folder_id, target_name,
            [
                {"title": "job_id", "type": "TEXT_NUMBER", "primary": True},
                {"title": "vendor", "type": "PICKLIST", "options": []},
            ],
        )
        print(f"[setup] target sandbox sheet id={target_sheet_id}")

        # Seed 3 source rows.
        smartsheet_client.add_rows(
            source_sheet_id,
            [{"vendor_name": v} for v in ("Acme Concrete", "Bravo Steel", "Charlie Lumber")],
        )

        # Insert mapping row in Picklist_Sync_Config.
        [mapping_row_id] = smartsheet_client.add_rows(
            sheet_ids.SHEET_PICKLIST_SYNC_CONFIG,
            [{
                "mapping_id": mapping_id,
                "source_sheet_id": str(source_sheet_id),
                "source_column": "vendor_name",
                "target_sheet_id": str(target_sheet_id),
                "target_column": "vendor",
                "enabled": True,
                "notes": "smoke test — auto-created + auto-deleted",
            }],
        )

        # --- Phase 1: initial add ---
        print()
        print("[phase 1] initial add — expect 3 options")
        stats = picklist_sync.sync_all(only=mapping_id)
        _print_stats(stats)
        assert stats.additions_total == 3, f"expected 3 additions, got {stats.additions_total}"
        cols = smartsheet_client.list_columns_with_options(target_sheet_id)
        vendor_col = next(c for c in cols if c["title"] == "vendor")
        assert sorted(vendor_col["options"]) == ["Acme Concrete", "Bravo Steel", "Charlie Lumber"], \
            f"options mismatch: {vendor_col['options']}"
        print(f"  target picklist options: {vendor_col['options']}")

        # --- Phase 2: add propagation ---
        print()
        print("[phase 2] add propagation — append 4th source row, expect 4 options")
        smartsheet_client.add_rows(
            source_sheet_id, [{"vendor_name": "Delta Roofing"}],
        )
        stats = picklist_sync.sync_all(only=mapping_id)
        _print_stats(stats)
        cols = smartsheet_client.list_columns_with_options(target_sheet_id)
        vendor_col = next(c for c in cols if c["title"] == "vendor")
        assert "Delta Roofing" in vendor_col["options"], f"missing add: {vendor_col['options']}"

        # --- Phase 3: remove-safe ---
        print()
        print("[phase 3] remove-safe — delete Charlie Lumber from source (unused), expect option gone")
        source_rows = smartsheet_client.get_rows(source_sheet_id)
        charlie_row_id = next(r["_row_id"] for r in source_rows if r.get("vendor_name") == "Charlie Lumber")
        smartsheet_client.delete_rows(source_sheet_id, [charlie_row_id])
        stats = picklist_sync.sync_all(only=mapping_id)
        _print_stats(stats)
        cols = smartsheet_client.list_columns_with_options(target_sheet_id)
        vendor_col = next(c for c in cols if c["title"] == "vendor")
        assert "Charlie Lumber" not in vendor_col["options"], \
            f"safe removal failed; option still present: {vendor_col['options']}"

        # --- Phase 4: remove-blocked-by-live-cell ---
        print()
        print("[phase 4] remove-blocked — target row uses 'Acme Concrete'; delete from source")
        print("           expect option retained + Review Queue row")
        smartsheet_client.add_rows(
            target_sheet_id,
            [{"job_id": "J-001", "vendor": "Acme Concrete"}],
        )
        source_rows = smartsheet_client.get_rows(source_sheet_id)
        acme_row_id = next(r["_row_id"] for r in source_rows if r.get("vendor_name") == "Acme Concrete")
        smartsheet_client.delete_rows(source_sheet_id, [acme_row_id])
        stats = picklist_sync.sync_all(only=mapping_id)
        _print_stats(stats)
        cols = smartsheet_client.list_columns_with_options(target_sheet_id)
        vendor_col = next(c for c in cols if c["title"] == "vendor")
        assert "Acme Concrete" in vendor_col["options"], \
            f"reference-blocked removal failed; option dropped: {vendor_col['options']}"
        # One Review Queue row should have been written.
        assert any(r.removals_blocked for r in stats.results), \
            "expected at least one removal_blocked result"

        print()
        print("All four phases passed.")
        return 0

    except Exception as e:
        print(f"[smoke FAILED] {e!r}")
        return 1

    finally:
        # Teardown order: mapping row → target sheet → source sheet.
        print()
        print("[teardown] tearing down sandbox state")
        if mapping_row_id is not None:
            try:
                smartsheet_client.delete_rows(
                    sheet_ids.SHEET_PICKLIST_SYNC_CONFIG,
                    [mapping_row_id],
                )
                print(f"  deleted Picklist_Sync_Config mapping row id={mapping_row_id}")
            except Exception as e:
                print(f"  WARN: failed to delete mapping row: {e!r}")
        for label, sheet_id in (
            ("target_sheet_id", target_sheet_id),
            ("source_sheet_id", source_sheet_id),
        ):
            if sheet_id is None:
                continue
            try:
                # SDK's delete_sheet via REST fallback (no direct wrapper today).
                import requests  # type: ignore[import-untyped]

                from shared import keychain
                token = keychain.get_secret("ITS_SMARTSHEET_TOKEN")
                r = requests.delete(
                    f"https://api.smartsheet.com/2.0/sheets/{sheet_id}",
                    headers={"Authorization": f"Bearer {token}"},
                )
                r.raise_for_status()
                print(f"  deleted sandbox sheet id={sheet_id} ({label})")
            except Exception as e:
                print(f"  WARN: failed to delete sandbox sheet id={sheet_id}: {e!r}")


# ---- Entrypoint ---------------------------------------------------------


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="ITS picklist sync — Picklist_Sync_Config-driven."
    )
    parser.add_argument(
        "--dry", action="store_true",
        help="compute diffs + log proposed changes; no API writes.",
    )
    parser.add_argument(
        "--mapping",
        help="run only the named mapping_id (overrides enabled filter).",
    )
    parser.add_argument(
        "--smoke-test", action="store_true",
        help="bootstrap sandbox sheets, run the full add/remove/blocked flow, tear down.",
    )
    return parser.parse_args(argv)


@its_error_log(_SCRIPT)
def main() -> None:
    args = _parse_args()

    if args.smoke_test:
        rc = _smoke_test()
        sys.exit(rc)

    stats = picklist_sync.sync_all(only=args.mapping, dry_run=args.dry)
    _print_stats(stats)
    _log_run_summary(stats)


if __name__ == "__main__":
    main()
