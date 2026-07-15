"""Idempotent migration: create + seed the ``ITS_Documentation_Index`` sheet.

The operator-visible index of the documentation corpus, one row per manifest doc. Mirrors the
existing sheet-builder pattern (``build_its_trusted_contacts_sheet.py`` etc.): find-or-create by
name (never destructive), then seed one row per doc from ``docs/enablement/manifest.yaml``.
After creating, records the sheet id in ITS_Config (``system.docs_index_sheet_id``) so nothing
hardcodes it.

Columns:
  Doc Key       TEXT_NUMBER (primary — the manifest key; the upsert key for Box-link fill)
  Title         TEXT_NUMBER
  Audience      TEXT_NUMBER (manifest `audience`)
  Scope         TEXT_NUMBER (one-line purpose — operator/future fill; blank at seed)
  Version       TEXT_NUMBER (source sha8 — the doc-currency baseline)
  Last Updated  DATE        (blank at seed)
  Box Link      TEXT_NUMBER (blank at seed; the `--upload` leg fills it on operator activation)
  Source Path   TEXT_NUMBER (repo-root-relative markdown source)

THE ONE LIVE SMARTSHEET WRITE of the documentation-corpus program (additive; no other live
writes). Operator-run:

    python3 scripts/migrations/build_docs_index_sheet.py            # create + seed (verify-after)
    python3 scripts/migrations/build_docs_index_sheet.py --dry-run  # print the rows, no write

Auth: ITS_SMARTSHEET_TOKEN from macOS Keychain. Exit 0 on success/no-op; nonzero on error.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from docs_pdf import manifest as _manifest  # noqa: E402
from shared import sheet_ids, smartsheet_client  # noqa: E402

SHEET_NAME = "ITS_Documentation_Index"
PARENT_FOLDER = sheet_ids.FOLDER_SYSTEM_CONFIG  # ITS — System / 01 — Config
CFG_SHEET_ID_KEY = "system.docs_index_sheet_id"
CFG_WORKSTREAM = "infrastructure"

COLUMN_SCHEMA: list[dict[str, Any]] = [
    {"title": "Doc Key", "type": "TEXT_NUMBER", "primary": True},
    {"title": "Title", "type": "TEXT_NUMBER"},
    {"title": "Audience", "type": "TEXT_NUMBER"},
    {"title": "Scope", "type": "TEXT_NUMBER"},
    {"title": "Version", "type": "TEXT_NUMBER"},
    {"title": "Last Updated", "type": "DATE"},
    {"title": "Box Link", "type": "TEXT_NUMBER"},
    {"title": "Source Path", "type": "TEXT_NUMBER"},
]


def _publish_order(man: _manifest.Manifest) -> list[_manifest.ManifestEntry]:
    """Manifest entries with the corpus INDEX (documentation_index) first, then manifest order."""
    index = man.by_key("documentation_index")
    rest = [e for e in man.entries if e.key != "documentation_index"]
    return ([index] if index is not None else []) + rest


def index_rows(man: _manifest.Manifest) -> list[dict[str, str]]:
    """One seed row per manifest doc (Box Link / Scope / Last Updated blank). Pure — no I/O
    beyond hashing the source bytes. INDEX (documentation_index) first."""
    rows: list[dict[str, str]] = []
    for e in _publish_order(man):
        try:
            sha8 = _manifest.compute_sha256(e.source_path())[:8]
        except OSError:
            sha8 = ""
        rows.append({
            "Doc Key": e.key,
            "Title": e.title,
            "Audience": e.audience or "",
            "Scope": "",
            "Version": sha8,
            "Box Link": "",
            "Source Path": e.source,
        })
    return rows


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Create + seed ITS_Documentation_Index (idempotent)")
    ap.add_argument("--dry-run", action="store_true", help="print the seed rows; make no write")
    args = ap.parse_args(argv)

    man = _manifest.load_manifest()
    rows = index_rows(man)

    if args.dry_run:
        print(f"[dry-run] {SHEET_NAME}: would ensure sheet in folder {PARENT_FOLDER} and seed "
              f"{len(rows)} row(s):")
        for r in rows:
            print(f"  {r['Doc Key']:24s} {r['Version']:9s} {r['Audience']:24s} {r['Source Path']}")
        print(f"[dry-run] would record {CFG_SHEET_ID_KEY} in ITS_Config (ws {CFG_WORKSTREAM}).")
        return 0

    existing = smartsheet_client.find_sheet_by_name_in_folder(PARENT_FOLDER, SHEET_NAME)
    if existing is not None:
        print(f"[skip] {SHEET_NAME!r} already present (sheet_id={existing}); not re-seeding.")
        return 0

    sheet_id = smartsheet_client.create_sheet_in_folder(PARENT_FOLDER, SHEET_NAME, COLUMN_SCHEMA)
    print(f"[ok] Created {SHEET_NAME!r} (sheet_id={sheet_id}).")
    smartsheet_client.add_rows(sheet_id, rows)
    print(f"[ok] Seeded {len(rows)} row(s).")
    # verify-after (read back the row count)
    back = smartsheet_client.get_rows(sheet_id)
    print(f"[verify] read back {len(back)} row(s).")
    # record the id so nothing hardcodes it (idempotent — add the ITS_Config row only if absent)
    _record_sheet_id(sheet_id)
    print(f"[bootstrap] Optionally add to shared/sheet_ids.py: SHEET_DOCS_INDEX = {sheet_id}")
    return 0


def _record_sheet_id(sheet_id: int) -> None:
    """Record the sheet id in ITS_Config (``system.docs_index_sheet_id``), idempotent."""
    existing = smartsheet_client.get_setting(CFG_SHEET_ID_KEY, workstream=CFG_WORKSTREAM)
    if existing is not None and str(existing).strip():
        print(f"[skip] {CFG_SHEET_ID_KEY} already set ({existing}); not overwriting.")
        return
    smartsheet_client.add_rows(sheet_ids.SHEET_CONFIG, [{
        "Setting": CFG_SHEET_ID_KEY,
        "Workstream": CFG_WORKSTREAM,
        "Value": str(sheet_id),
        "Description": "Sheet id of ITS_Documentation_Index (the corpus index). Set by "
                       "scripts/migrations/build_docs_index_sheet.py so nothing hardcodes it.",
    }])
    print(f"[ok] Recorded {CFG_SHEET_ID_KEY}={sheet_id} in ITS_Config.")


if __name__ == "__main__":
    sys.exit(main())
