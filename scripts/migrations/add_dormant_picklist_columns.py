"""One-shot migration: add the two Phase 3a DORMANT picklist columns to their
live sheets (D1 = ADD).

The first `scripts/audit_picklist_drift.py` run surfaced two **dormant** findings
(`docs/audits/picklist_drift_2026-06-02_classification.md`): the
`picklist_validation.REGISTRY` declares a column the live sheet lacks AND no code
writes it yet —

  - ITS_Errors · `Workstream`     (registry: `_WORKSTREAM_VALUES_GLOBAL`)
  - ITS_Quarantine · `Disposition` (registry: RELEASE / DELETE / ESCALATE)

Phase 3a decision D1 = ADD: create the columns now (PICKLIST, seeded with the
registry's allowed set) so the weekly audit goes quiet and the sheets are ready
for the future writer. The *writers* (error_log `Workstream`, quarantine
`Disposition`) remain a separate, out-of-scope feature — an empty column is fine.

Single source of truth: the seeded option values are read from
`shared.picklist_validation.REGISTRY`, so this can never drift from the registry
the audit compares against.

Idempotent: a target whose column already exists live is skipped (never a
duplicate-titled column). Re-running after a successful add is a clean no-op.

Additive only: creates columns; never edits or removes an existing column. The
server-side "restrict to dropdown values only" toggle is the SEPARATE hardening
sweep (`docs/audits/picklist_hardening_audit.md`) and is intentionally left off
here, matching every other column on these sheets.

Auth: ITS_SMARTSHEET_TOKEN from macOS Keychain.

CLI:
    python3 scripts/migrations/add_dormant_picklist_columns.py            # PREVIEW (default)
    python3 scripts/migrations/add_dormant_picklist_columns.py --commit   # actually create

Preview is the default (no live write); `--commit` is required to mutate. Verify
afterwards with `python -m scripts.audit_picklist_drift --no-emit` — findings #2
and #3 should be gone.

Exit code 0 on success or no-op; nonzero on any error.
"""
from __future__ import annotations

import argparse
import sys

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[2]))

from shared import picklist_validation, sheet_ids, smartsheet_client  # noqa: E402

# The two Phase 3a dormant targets — (sheet_id, column_title). Each MUST be a
# column the audit classified "NOT PRESENT in live sheet"; the option values are
# pulled from REGISTRY below so they always match what the audit checks.
TARGETS: list[tuple[int, str]] = [
    (sheet_ids.SHEET_ERRORS, "Workstream"),
    (sheet_ids.SHEET_QUARANTINE, "Disposition"),
]


def _registry_options(sheet_id: int, column: str) -> list[str]:
    """The registry's allowed set for (sheet_id, column), sorted for stable display."""
    try:
        allowed = picklist_validation.REGISTRY[sheet_id][column]
    except KeyError as exc:
        raise RuntimeError(
            f"{column!r} on sheet {sheet_id} is not in picklist_validation.REGISTRY — "
            f"this migration only adds registered-but-absent columns."
        ) from exc
    return sorted(allowed)


def add_dormant_columns(*, commit: bool) -> tuple[int, int]:
    """Add the dormant columns. Returns (added, skipped).

    Preview (commit=False) reads live columns to decide add-vs-skip and prints
    the planned spec, but issues no write.
    """
    added = 0
    skipped = 0
    for sheet_id, column in TARGETS:
        options = _registry_options(sheet_id, column)
        live = smartsheet_client.list_columns_with_options(sheet_id)
        existing = next((c for c in live if c["title"] == column), None)
        if existing is not None:
            # Idempotency is title-AND-type: a column that exists with the wrong
            # type (e.g. someone added it as TEXT_NUMBER by hand) must NOT be
            # silently skipped — that would leave the schema wrong-typed with no
            # options and the audit still failing, a silent partial state.
            if existing["type"] != "PICKLIST":
                raise RuntimeError(
                    f"sheet={sheet_id} column={column!r} exists but type="
                    f"{existing['type']!r}, not PICKLIST — refusing to skip a "
                    f"wrong-typed column. Resolve the schema by hand (Tier-3)."
                )
            print(f"[skip] sheet={sheet_id} column={column!r} already present.")
            skipped += 1
            continue

        spec = f"PICKLIST options={options}"
        if not commit:
            print(
                f"[preview] sheet={sheet_id} would CREATE column={column!r} "
                f"({spec}) at index={len(live)}."
            )
            added += 1
            continue

        col_id = smartsheet_client.create_picklist_column(sheet_id, column, options)
        print(
            f"[ok] sheet={sheet_id} created column={column!r} "
            f"(column_id={col_id}, {spec})."
        )
        added += 1
    return added, skipped


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Add the two Phase 3a dormant picklist columns (D1=ADD).",
    )
    parser.add_argument(
        "--commit", action="store_true",
        help="Actually create the columns. Without it, PREVIEW only (no write).",
    )
    args = parser.parse_args()

    print(f"[info] Mode: {'LIVE WRITE (--commit)' if args.commit else 'PREVIEW (default)'}")
    print(f"[info] Targets: {[(s, c) for s, c in TARGETS]}")
    print()

    added, skipped = add_dormant_columns(commit=args.commit)

    print()
    print("Summary:")
    verb = "created" if args.commit else "would create"
    print(f"  Columns {verb}: {added}")
    print(f"  Skipped (already present): {skipped}")
    if not args.commit and added:
        print("  Re-run with --commit to apply.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
