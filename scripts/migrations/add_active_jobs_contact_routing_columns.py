"""One-shot migration: add the email-routing columns to ITS_Active_Jobs.

Safety Portal Phase 3 contacts amendment (2026-06-05). Additive + idempotent;
`--dry-run` previews. Adds, as **TEXT** columns (one per POST, before the trailing
system columns):

    Safety Reports Contact Name   — greeting target on the weekly email
    CC 1 … CC 5                   — CC recipients (one email per slot, or several
                                    comma-separated; weekly_send flattens + de-dups)

Why TEXT, not CONTACT_LIST (operator decision 2026-06-05): MULTI_CONTACT_LIST loses
external (non-org-member) emails on API read-back — both `value` and `objectValue`
collapse to the display names — so it cannot reliably yield CC emails. A single
CONTACT_LIST yields the email via `value`, but only one contact per slot. TEXT
stores the email string verbatim, is 100% reliable for retrieval, and needs no
contact-aware extraction. `Safety Reports Contact Email` is already TEXT (Phase 3).

Companion to scripts/migrations/extend_its_active_jobs_phase3.py. Uses the
Smartsheet REST API directly (add-column isn't surfaced through the SDK wrapper).
Auth: ITS_SMARTSHEET_TOKEN from macOS Keychain.

Run from ~/its:
    python3 scripts/migrations/add_active_jobs_contact_routing_columns.py --dry-run
    python3 scripts/migrations/add_active_jobs_contact_routing_columns.py
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

NEW_COLUMNS = ["Safety Reports Contact Name", "CC 1", "CC 2", "CC 3", "CC 4", "CC 5"]


def _headers() -> dict[str, str]:
    token = keychain.get_secret("ITS_SMARTSHEET_TOKEN")
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def _get_columns() -> list[dict[str, Any]]:
    r = requests.get(f"{BASE}/sheets/{SHEET}?include=columns", headers=_headers(), timeout=30)
    r.raise_for_status()
    return r.json().get("columns", [])


def _pre_system_index(columns: list[dict[str, Any]]) -> int:
    """Index just before the first system column — new data columns must land
    before the trailing MODIFIED_DATE/MODIFIED_BY (a batch that reorders them 400s)."""
    return min((c["index"] for c in columns if c.get("systemColumnType")), default=len(columns))


def add_columns(*, dry_run: bool) -> list[str]:
    """Add the routing columns one-at-a-time. Idempotent (skip-if-present)."""
    present = {c["title"] for c in _get_columns()}
    missing = [t for t in NEW_COLUMNS if t not in present]
    if not missing:
        print("[skip] all routing columns already present.")
        return []
    if dry_run:
        print(f"[dry-run] would add {missing} as TEXT, before the system columns.")
        return missing
    added: list[str] = []
    for title in missing:
        idx = _pre_system_index(_get_columns())  # re-fetch: the sheet grows each add
        r = requests.post(
            f"{BASE}/sheets/{SHEET}/columns", headers=_headers(),
            json=[{"title": title, "type": "TEXT_NUMBER", "index": idx}], timeout=30,
        )
        r.raise_for_status()
        col = r.json()["result"][0]
        print(f"[ok] added {title!r} (id={col['id']}, index={col['index']}).")
        added.append(title)
    return added


def verify() -> None:
    print("\n[verify] ITS_Active_Jobs columns now:")
    for c in sorted(_get_columns(), key=lambda c: c["index"]):
        print(f"    [{c['index']:>2}] {c['title']:<28} {c['type']}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Add ITS_Active_Jobs routing columns (Phase 3 amendment).")
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing.")
    args = parser.parse_args()
    print(f"[info] Sheet ITS_Active_Jobs = {SHEET}")
    print(f"[info] Mode: {'DRY-RUN' if args.dry_run else 'LIVE WRITE'}\n")
    add_columns(dry_run=args.dry_run)
    if not args.dry_run:
        verify()
    print("\n[done]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
