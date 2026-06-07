#!/usr/bin/env python3
"""One-time cosmetic styling pass over the Safety Portal Smartsheet sheets (PR-I).

Purpose
    Bring the brand look (Evergreen green, approximated via the Smartsheet palette)
    to the sheets that predate the styled `week_sheet` creation path: the three
    static config sheets get their primary column branded; every EXISTING per-job
    week sheet under `WORKSPACE_SAFETY_PORTAL` gets the full `WEEK_SHEET_STYLES`.
    New week sheets inherit styling at creation (`week_sheet.ensure_week_sheet`), so
    this is a backfill, not an ongoing job.

Invariants
    Cosmetic ONLY — no row/data change, no external send (column width/format via
    `smartsheet_client.apply_column_styles`). Idempotent + re-runnable. Operator-run
    (not a daemon). Week sheets are detected by schema (a `Submission` primary +
    `Compile Now` column) so the static "Safety Portal" / "Form Catalog" folders are
    never mis-styled.

Failure modes
    A per-sheet styling failure prints a WARN line and continues (never aborts the
    whole pass). `--dry` lists what WOULD be styled without writing.

Consumers
    Operator-invoked: `python -m scripts.style_safety_portal_sheets [--dry]`.
"""
from __future__ import annotations

import argparse
import sys

from safety_reports import week_sheet
from shared import sheet_ids, smartsheet_client

STATIC_SHEETS = [
    (sheet_ids.SHEET_ACTIVE_JOBS, "ITS_Active_Jobs"),
    (sheet_ids.SHEET_FORMS_CATALOG, "ITS_Forms_Catalog"),
    (sheet_ids.SHEET_WSR_HUMAN_REVIEW, "WSR_human_review"),
]


def _style_primary(sheet_id: int, label: str, *, dry: bool) -> None:
    """Brand the primary column of a static sheet (bold + dark-green text + tint)."""
    cols = smartsheet_client.get_client().Sheets.get_columns(sheet_id, include_all=True).data
    primary = next((c for c in cols if getattr(c, "primary", False)), None)
    if primary is None:
        print(f"  {label}: no primary column — skipped")
        return
    print(f"  {label}: brand primary {primary.title!r}" + (" [dry]" if dry else ""))
    if not dry:
        smartsheet_client.apply_column_styles(
            sheet_id, [{"title": primary.title, "format": week_sheet.FMT_PRIMARY}]
        )


def _style_existing_week_sheets(*, dry: bool) -> int:
    """Apply WEEK_SHEET_STYLES to every existing per-job week sheet. Returns the count."""
    ws = smartsheet_client.get_client().Workspaces.get_workspace(
        sheet_ids.WORKSPACE_SAFETY_PORTAL, load_all=True
    )
    styled = 0
    for folder in ws.folders or []:
        for sheet in folder.sheets or []:
            cols = smartsheet_client.get_client().Sheets.get_columns(
                sheet.id, include_all=True
            ).data
            titles = {c.title for c in cols}
            # Detect a week sheet by schema, never by folder name.
            if week_sheet.COL_SUBMISSION not in titles or week_sheet.COL_COMPILE_NOW not in titles:
                continue
            print(f"  week sheet: {folder.name}/{sheet.name}" + (" [dry]" if dry else ""))
            if not dry:
                try:
                    smartsheet_client.apply_column_styles(sheet.id, week_sheet.WEEK_SHEET_STYLES)
                except smartsheet_client.SmartsheetError as exc:
                    print(f"    WARN: styling failed: {exc!r}", file=sys.stderr)
                    continue
            styled += 1
    return styled


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="style_safety_portal_sheets")
    parser.add_argument("--dry", action="store_true", help="list targets, write nothing")
    args = parser.parse_args(argv)

    print("Static sheets:")
    for sid, label in STATIC_SHEETS:
        _style_primary(sid, label, dry=args.dry)
    print("Existing week sheets:")
    count = _style_existing_week_sheets(dry=args.dry)
    print(f"Done — {count} week sheet(s) {'would be ' if args.dry else ''}styled.")


if __name__ == "__main__":
    main()
