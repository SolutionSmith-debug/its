"""One-shot migration: extend ITS_Active_Jobs for Safety Portal Phase 3.

Additive schema change on the live ITS_Active_Jobs sheet (SHEET_ACTIVE_JOBS).
Idempotent + safe to re-run; pass --dry-run to preview without writing.

What it does (in this order — the order is load-bearing):
  1. Add four office-PM-maintained routing columns (TEXT), after `Address`:
       Stakeholder Name, Stakeholder Email, Stakeholder Phone,
       Safety Reports Contact Email   (the weekly-rollup TO recipient)
  2. RENAME the existing kebab `Job ID` column → `Job Slug` (human-readable
     secondary key; e.g. "bradley-1"). Done in step 2 so the title `Job ID` is
     free for the portal-written key column.
  3. Create the plain TEXT `Job ID` column (right after `Project Name`) and the
     plain TEXT `Portal Job Key` column (before the system columns). BOTH are
     API-creatable — no manual UI step.

Job-ID model (P2.5 Slice 6, 2026-06-30 — supersedes the Phase-3 AUTO_NUMBER
decision of 2026-06-05): THE PORTAL ASSIGNS the canonical JOB-###### from the
Worker's `job_counter` (migration 0022), and `shared/active_jobs_writer.py`
WRITES it into the `Job ID` cell on every mirror upsert (Job ID == Portal Job
Key == D1 job_id). The column must therefore be a plain writable TEXT_NUMBER —
a Smartsheet AUTO_NUMBER would REJECT every mirror write and assign its own
conflicting sequence. (This script's original AUTO_NUMBER manual-UI instruction
was the pre-Slice-6 design; it survived here unexercised until the 2026-07-23
tenant stand-up rehearsal first re-ran the script on a fresh tenant, where the
operator caught it — verified against the live pre-wipe dump: TEXT_NUMBER,
portal-allocated values JOB-000017/18/27/28.) `shared/active_jobs.py` reads the
`Job ID` column as the join key, exactly as before.

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
JOB_ID_TITLE = "Job ID"       # plain TEXT — portal-assigned JOB-###### (Slice 6)
PORTAL_JOB_KEY_TITLE = "Portal Job Key"  # plain TEXT — the mirror's find-or-create key


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
        # A legacy pre-Slice-6 AUTO_NUMBER key — do not rename it into Job Slug;
        # the 0022 cutover retype (AUTO_NUMBER -> TEXT, UI-only) applies instead.
        print(f"[skip] {KEBAB_OLD_TITLE!r} is AUTO_NUMBER (legacy pre-Slice-6) — "
              "see ensure_job_id_column for the retype instruction.")
        return "exists"
    if dry_run:
        print(f"[dry-run] would rename {KEBAB_OLD_TITLE!r} (id={job_id_col['id']}) "
              f"→ {KEBAB_NEW_TITLE!r}.")
        return "renamed"
    _put(f"/sheets/{SHEET}/columns/{job_id_col['id']}", {"title": KEBAB_NEW_TITLE})
    print(f"[ok] renamed {KEBAB_OLD_TITLE!r} → {KEBAB_NEW_TITLE!r} "
          f"(id={job_id_col['id']}).")
    return "renamed"


def ensure_job_id_column(*, dry_run: bool) -> str:
    """Create the plain TEXT `Job ID` column (portal-written key). Idempotent.

    Slice-6 model: the portal assigns the canonical JOB-###### (Worker
    `job_counter`, migration 0022) and `active_jobs_writer.upsert_job` WRITES it
    into this cell on every mirror pass — so the column is a plain TEXT_NUMBER
    the API can create directly. A surviving legacy AUTO_NUMBER column gets the
    0022 cutover instruction instead (retype is UI-only): an AUTO_NUMBER here
    would REJECT every mirror write. Returns "exists", "created", or
    "retype_required".
    """
    columns = _get_columns()
    existing = next((c for c in columns if c["title"] == JOB_ID_TITLE), None)
    if existing is not None:
        if existing.get("type") == "AUTO_NUMBER":
            print(f"[WARN] {JOB_ID_TITLE!r} is a legacy AUTO_NUMBER column — the "
                  "mirror's writes will be rejected until it is retyped. In the "
                  "Smartsheet UI: edit the column, change type to Text/Number "
                  "(existing values persist as text — migration 0022 cutover step 2).")
            return "retype_required"
        print(f"[skip] {JOB_ID_TITLE!r} already present as {existing.get('type')} "
              f"(id={existing['id']}).")
        return "exists"
    if dry_run:
        print(f"[dry-run] would create TEXT column {JOB_ID_TITLE!r} at index 1 "
              "(right after 'Project Name').")
        return "created"
    result = _post(f"/sheets/{SHEET}/columns",
                   [{"title": JOB_ID_TITLE, "type": "TEXT_NUMBER", "index": 1}])
    created = result.get("result", [])
    if not created:
        raise RuntimeError(f"Unexpected column-create response: {result!r}")
    print(f"[ok] created TEXT column {JOB_ID_TITLE!r} "
          f"(id={created[0]['id']}, index={created[0]['index']}) — the portal-"
          "assigned JOB-###### key; the fieldops mirror writes it on every upsert.")
    return "created"


def ensure_portal_job_key_column(*, dry_run: bool) -> str:
    """Create the plain TEXT `Portal Job Key` column. Idempotent.

    The mirror's find-or-create join key (holds the same JOB-###### value as
    `Job ID` for portal-origin rows). Previously a documented manual step in the
    progress-sheet builder's docstring; API-creatable all along.
    """
    columns = _get_columns()
    existing = next((c for c in columns if c["title"] == PORTAL_JOB_KEY_TITLE), None)
    if existing is not None:
        print(f"[skip] {PORTAL_JOB_KEY_TITLE!r} already present "
              f"(id={existing['id']}, type={existing.get('type')}).")
        return "exists"
    if dry_run:
        print(f"[dry-run] would create TEXT column {PORTAL_JOB_KEY_TITLE!r} "
              "before the system columns.")
        return "created"
    result = _post(
        f"/sheets/{SHEET}/columns",
        [{"title": PORTAL_JOB_KEY_TITLE, "type": "TEXT_NUMBER",
          "index": _pre_system_index(_get_columns())}])
    created = result.get("result", [])
    if not created:
        raise RuntimeError(f"Unexpected column-create response: {result!r}")
    print(f"[ok] created TEXT column {PORTAL_JOB_KEY_TITLE!r} "
          f"(id={created[0]['id']}, index={created[0]['index']}).")
    return "created"


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
    job_id_status = ensure_job_id_column(dry_run=args.dry_run)
    ensure_portal_job_key_column(dry_run=args.dry_run)

    if not args.dry_run:
        verify()
    if job_id_status == "retype_required":
        print("\n[done] Schema landed, but the legacy AUTO_NUMBER 'Job ID' still needs "
              "its UI retype to Text/Number (see WARN above) before the mirror can write.")
        return 1
    print("\n[done]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
