"""One-shot migration: extend ITS_Active_Jobs for Safety Portal Phase 3.

Additive schema change on the live ITS_Active_Jobs sheet (SHEET_ACTIVE_JOBS).
Idempotent + safe to re-run; pass --dry-run to preview without writing.

What it does (in this order — the order is load-bearing):
  1. Add four office-PM-maintained routing columns (TEXT), after `Address`:
       Stakeholder Name, Stakeholder Email, Stakeholder Phone,
       Safety Reports Contact Email   (the weekly-rollup TO recipient)
  2. RENAME the existing kebab `Job ID` column → `Job Slug` (human-readable
     secondary key; e.g. "bradley-1"). Done in step 2 so the title `Job ID` is
     free for the AUTO_NUMBER column.
  3. Confirm / instruct the AUTO_NUMBER `Job ID` column. NOTE: the Smartsheet
     REST API CANNOT create AUTO_NUMBER columns (POST /columns → errorCode 1008;
     it is a UI-only column type). This step DETECTS the column and, if absent,
     prints the exact one-time manual UI step for the operator. Once added in the
     UI (format JOB-0001…), existing rows backfill and shared/active_jobs.py reads
     it as the immutable join key the portal payload carries.

Phase-3 decision (operator, 2026-06-05): switch the immutable Job-ID key from
the seeded kebab string to a Smartsheet AUTO_NUMBER — collision-proof at
creation (obviates a Python uniqueness guard), and the kebab lives on as
`Job Slug`. `shared/active_jobs.py` reads the `Job ID` column as the join key.

Uses the Smartsheet REST API directly: add/rename column ops aren't surfaced
through `shared/smartsheet_client.py`, and REST keeps the migration
self-contained (mirrors scripts/migrations/add_correlation_id_column.py).

Auth: ITS_SMARTSHEET_TOKEN from macOS Keychain. No env vars.

Run from ~/its:
    python3 scripts/migrations/extend_its_active_jobs_phase3.py --dry-run
    python3 scripts/migrations/extend_its_active_jobs_phase3.py

Exit 0 on success or no-op; nonzero on any error.
"""
from __future__ import annotations

import argparse
import sys
from typing import Any

import requests  # type: ignore[import-untyped]

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[2]))

from shared import keychain, sheet_ids  # noqa: E402

BASE = "https://api.smartsheet.com/2.0"
SHEET = sheet_ids.SHEET_ACTIVE_JOBS

CONTACT_COLUMNS = [
    "Stakeholder Name",
    "Stakeholder Email",
    "Stakeholder Phone",
    "Safety Reports Contact Email",
]
KEBAB_OLD_TITLE = "Job ID"
KEBAB_NEW_TITLE = "Job Slug"
AUTONUM_TITLE = "Job ID"
AUTONUM_FORMAT = {"prefix": "JOB-", "fill": "0000", "startingNumber": 1}


def _headers() -> dict[str, str]:
    token = keychain.get_secret("ITS_SMARTSHEET_TOKEN")
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def _get_columns() -> list[dict[str, Any]]:
    r = requests.get(
        f"{BASE}/sheets/{SHEET}?include=columns&exclude=nonexistentCells",
        headers=_headers(), timeout=30,
    )
    r.raise_for_status()
    return r.json().get("columns", [])


def _post(path: str, body: Any) -> dict[str, Any]:
    r = requests.post(BASE + path, headers=_headers(), json=body, timeout=30)
    r.raise_for_status()
    return r.json()


def _put(path: str, body: Any) -> dict[str, Any]:
    r = requests.put(BASE + path, headers=_headers(), json=body, timeout=30)
    r.raise_for_status()
    return r.json()


def _pre_system_index(columns: list[dict[str, Any]]) -> int:
    """Index just before the first system column (MODIFIED_DATE/MODIFIED_BY).

    New data columns must land before the trailing system columns — Smartsheet
    400s a batch that would reorder them, which is why we insert one at a time
    at this computed boundary.
    """
    return min((c["index"] for c in columns if c.get("systemColumnType")), default=len(columns))


def add_contact_columns(columns: list[dict[str, Any]], *, dry_run: bool) -> list[str]:
    """Add the four routing TEXT columns before the system columns. Idempotent.

    Added one-per-POST: a single batch with explicit indices collides with the
    trailing system columns (Smartsheet 400). Re-fetching each iteration keeps
    the insert point correct as the sheet grows.
    """
    titles = {c["title"] for c in columns}
    missing = [t for t in CONTACT_COLUMNS if t not in titles]
    if not missing:
        print("[skip] all four routing columns already present.")
        return []
    if dry_run:
        print(f"[dry-run] would add columns {missing} just before the system columns.")
        return missing
    added: list[str] = []
    for title in missing:
        result = _post(
            f"/sheets/{SHEET}/columns",
            [{"title": title, "type": "TEXT_NUMBER", "index": _pre_system_index(_get_columns())}],
        )
        created = result.get("result", [])
        if not created:
            raise RuntimeError(f"Unexpected column-create response for {title!r}: {result!r}")
        print(f"[ok] added routing column {title!r} "
              f"(id={created[0]['id']}, index={created[0]['index']}).")
        added.append(title)
    return added


def rename_kebab_to_slug(columns: list[dict[str, Any]], *, dry_run: bool) -> str:
    """Rename the kebab `Job ID` → `Job Slug`. Idempotent.

    Returns "renamed", "exists" (already Job Slug / already migrated), or "absent".
    """
    by_title = {c["title"]: c for c in columns}
    if KEBAB_NEW_TITLE in by_title:
        print(f"[skip] {KEBAB_NEW_TITLE!r} already present (kebab rename done).")
        return "exists"
    job_id_col = by_title.get(KEBAB_OLD_TITLE)
    if job_id_col is None:
        print(f"[warn] no {KEBAB_OLD_TITLE!r} column found to rename.")
        return "absent"
    if job_id_col.get("type") == "AUTO_NUMBER":
        # Already the auto-number key; nothing to rename (shouldn't happen pre-slug).
        print(f"[skip] {KEBAB_OLD_TITLE!r} is already AUTO_NUMBER.")
        return "exists"
    if dry_run:
        print(f"[dry-run] would rename {KEBAB_OLD_TITLE!r} (id={job_id_col['id']}) "
              f"→ {KEBAB_NEW_TITLE!r}.")
        return "renamed"
    _put(f"/sheets/{SHEET}/columns/{job_id_col['id']}", {"title": KEBAB_NEW_TITLE})
    print(f"[ok] renamed {KEBAB_OLD_TITLE!r} → {KEBAB_NEW_TITLE!r} "
          f"(id={job_id_col['id']}).")
    return "renamed"


_MANUAL_INSTRUCTION = (
    "\n[MANUAL STEP REQUIRED] The Smartsheet API CANNOT create AUTO_NUMBER columns\n"
    "(POST /columns returns errorCode 1008 for type AUTO_NUMBER — it is a UI-only\n"
    "column type). In the Smartsheet UI, add a column to ITS_Active_Jobs:\n"
    f"    Name:     {AUTONUM_TITLE}\n"
    "    Type:     System Columns → Auto-Number\n"
    f"    Format:   prefix {AUTONUM_FORMAT['prefix']!r}, "
    f"{len(AUTONUM_FORMAT['fill'])}-digit fill, starting number "
    f"{AUTONUM_FORMAT['startingNumber']}  (→ JOB-0001, JOB-0002, …)\n"
    "    Position: right after 'Project Name'\n"
    "This is the immutable join key the portal payload carries; existing rows\n"
    "backfill JOB-0001…JOB-0006. shared/active_jobs.py reads this column as the key.\n"
)


def ensure_autonumber_job_id(*, dry_run: bool) -> str:
    """Confirm the AUTO_NUMBER `Job ID` column, or print the manual UI step.

    AUTO_NUMBER columns cannot be created through the REST API (verified: bare
    `type: AUTO_NUMBER` → 1008). The migration renames the kebab key out of the
    way (freeing the title) and hands the operator the one manual UI step. Never
    fails the run — returns "exists", "manual_required", or "conflict".
    """
    columns = _get_columns()
    by_title = {c["title"]: c for c in columns}
    existing = by_title.get(AUTONUM_TITLE)
    if existing is not None and existing.get("type") == "AUTO_NUMBER":
        print(f"[ok] AUTO_NUMBER {AUTONUM_TITLE!r} present (id={existing['id']}).")
        return "exists"
    if existing is not None:
        print(f"[warn] a non-AUTO_NUMBER column titled {AUTONUM_TITLE!r} exists "
              f"(type={existing.get('type')}); rename it before adding the "
              "AUTO_NUMBER column in the UI.")
        return "conflict"
    print(_MANUAL_INSTRUCTION)
    return "manual_required"


def verify() -> None:
    """Print the resulting column layout for an at-a-glance audit."""
    print("\n[verify] ITS_Active_Jobs columns now:")
    for c in sorted(_get_columns(), key=lambda c: c["index"]):
        kind = c["type"]
        extra = f" {c.get('autoNumberFormat')}" if kind == "AUTO_NUMBER" else ""
        print(f"    [{c['index']:>2}] {c['title']:<28} {kind}{extra}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Extend ITS_Active_Jobs (Phase 3).")
    parser.add_argument("--dry-run", action="store_true",
                        help="Preview the schema change without writing.")
    args = parser.parse_args()

    print(f"[info] Sheet ITS_Active_Jobs = {SHEET}")
    print(f"[info] Mode: {'DRY-RUN' if args.dry_run else 'LIVE WRITE'}\n")

    columns = _get_columns()
    add_contact_columns(columns, dry_run=args.dry_run)
    rename_kebab_to_slug(columns, dry_run=args.dry_run)
    status = ensure_autonumber_job_id(dry_run=args.dry_run)

    if not args.dry_run:
        verify()
    if status == "manual_required":
        print("\n[done] API-doable schema landed; complete the AUTO_NUMBER 'Job ID' "
              "column in the Smartsheet UI (see MANUAL STEP above).")
    else:
        print("\n[done]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
