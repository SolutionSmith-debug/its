"""One-shot migration: add the `Workstream` cross-workstream send-guard column to
WSR_human_review + backfill every existing row to `safety` (P1b).

P1b adds a contamination guard to `safety_reports/weekly_send.send_one_row`: a row
whose `Workstream` tag != the sender's workstream is HARD-HELD (+ CRITICAL) rather
than transmitted. The guard READS the `Workstream` column; this migration creates it
on the live WSR sheet and seeds every existing (pre-P1b) row to `safety`, so the
guard's absent-WARN back-compat path is exercised only transiently. New rows are
seeded `safety` by `wsr_review.add_wsr_row`.

ORDER-CRITICAL — run BEFORE the P1b code goes live: once `add_wsr_row` writes the
`Workstream` cell, the column MUST exist or the write errors. The operator runs this
during the P1b live smoke, before merge/pull into `~/its`.

Column spec (PICKLIST, options read from `picklist_validation.REGISTRY` so it can
never drift): `["safety"]`. The server-side "restrict to dropdown" toggle is the
separate hardening sweep and is intentionally left off here (matching every other
column on this sheet).

Idempotent: a present-and-correctly-typed column is skipped; a present-but-wrong-type
column aborts (Tier-3 schema fix). The backfill writes only rows whose `Workstream`
is blank, so re-running after a successful apply is a clean no-op.

Auth: ITS_SMARTSHEET_TOKEN from macOS Keychain.

CLI:
    python3 scripts/migrations/add_wsr_workstream_column.py            # PREVIEW (default)
    python3 scripts/migrations/add_wsr_workstream_column.py --commit   # create + backfill

Exit 0 on success/no-op; nonzero on error.
"""
from __future__ import annotations

import argparse
import sys

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[2]))

from safety_reports import wsr_review  # noqa: E402
from shared import picklist_validation, sheet_ids, smartsheet_client  # noqa: E402

SHEET_ID = sheet_ids.SHEET_WSR_HUMAN_REVIEW
COLUMN = wsr_review.COL_WORKSTREAM            # "Workstream"
BACKFILL_VALUE = "safety"


def _registry_options() -> list[str]:
    """The registry's allowed set for the Workstream column (single source of truth)."""
    try:
        allowed = picklist_validation.REGISTRY[SHEET_ID][COLUMN]
    except KeyError as exc:
        raise RuntimeError(
            f"{COLUMN!r} on sheet {SHEET_ID} is not in picklist_validation.REGISTRY — "
            f"register it before running this migration."
        ) from exc
    return sorted(allowed)


def ensure_column(*, commit: bool) -> bool:
    """Create the Workstream PICKLIST column if absent.

    Returns True if the column exists after this call (present or just created),
    False only in PREVIEW when it WOULD be created.
    """
    options = _registry_options()
    live = smartsheet_client.list_columns_with_options(SHEET_ID)
    existing = next((c for c in live if c["title"] == COLUMN), None)
    if existing is not None:
        if existing["type"] != "PICKLIST":
            raise RuntimeError(
                f"sheet={SHEET_ID} column={COLUMN!r} exists but type={existing['type']!r}, "
                f"not PICKLIST — refusing to skip a wrong-typed column (Tier-3 schema fix)."
            )
        print(f"[skip] column {COLUMN!r} already present (PICKLIST).")
        return True
    spec = f"PICKLIST options={options}"
    if not commit:
        print(f"[preview] would CREATE column {COLUMN!r} ({spec}) at index={len(live)}.")
        return False
    col_id = smartsheet_client.create_picklist_column(SHEET_ID, COLUMN, options)
    print(f"[ok] created column {COLUMN!r} (column_id={col_id}, {spec}).")
    return True


def backfill(*, commit: bool, column_present: bool) -> tuple[int, int]:
    """Seed `Workstream=safety` on every row whose tag is blank.

    Returns (set_count, already_tagged). PREVIEW (or a not-yet-created column)
    reports the plan without writing.
    """
    rows = smartsheet_client.get_rows(SHEET_ID)
    if not column_present:
        # PREVIEW before the column exists: get_rows can't carry it, so every row reads
        # blank. Report the row count as the backfill scope.
        print(f"[preview] would backfill {COLUMN!r}={BACKFILL_VALUE!r} on up to {len(rows)} row(s) "
              "(column not yet created).")
        return len(rows), 0
    blank = [r for r in rows if not str(r.get(COLUMN) or "").strip()]
    already = len(rows) - len(blank)
    if not blank:
        print(f"[skip] all {len(rows)} row(s) already carry a Workstream tag.")
        return 0, already
    if not commit:
        print(f"[preview] would backfill {COLUMN!r}={BACKFILL_VALUE!r} on {len(blank)} blank row(s) "
              f"({already} already tagged).")
        return len(blank), already
    smartsheet_client.update_rows(
        SHEET_ID, [{"_row_id": r["_row_id"], COLUMN: BACKFILL_VALUE} for r in blank],
    )
    print(f"[ok] backfilled {COLUMN!r}={BACKFILL_VALUE!r} on {len(blank)} row(s) ({already} already tagged).")
    return len(blank), already


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Add the WSR_human_review Workstream send-guard column + backfill to 'safety' (P1b).",
    )
    parser.add_argument(
        "--commit", action="store_true",
        help="Actually create the column + backfill. Without it, PREVIEW only (no write).",
    )
    args = parser.parse_args()

    print(f"[info] Mode: {'LIVE WRITE (--commit)' if args.commit else 'PREVIEW (default)'}")
    print(f"[info] Target: sheet={SHEET_ID} column={COLUMN!r} backfill={BACKFILL_VALUE!r}")
    print()

    column_present = ensure_column(commit=args.commit)
    set_count, already = backfill(commit=args.commit, column_present=column_present)

    print()
    print("Summary:")
    print(f"  Column: {'present' if column_present else 'would create'}")
    verb = "backfilled" if args.commit else "would backfill"
    print(f"  Rows {verb}: {set_count} ({already} already tagged)")
    if not args.commit:
        print("  Re-run with --commit to apply.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
