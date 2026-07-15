"""Tests for the ITS_Documentation_Index sheet builder (the corpus index).

Covers the pure row-building logic (mock-free) — the actual sheet create/seed is a live
Smartsheet write, operator-run. index_rows() must produce one seeded row per manifest doc with
the INDEX first, the source sha8 as Version, and Box Link blank (the --upload leg fills it).
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
_MIG_DIR = REPO_ROOT / "scripts" / "migrations"
if str(_MIG_DIR) not in sys.path:
    sys.path.insert(0, str(_MIG_DIR))

import build_docs_index_sheet as builder  # noqa: E402 — sys.path-driven import

from docs_pdf.manifest import load_manifest  # noqa: E402


def test_index_rows_one_per_manifest_doc() -> None:
    man = load_manifest()
    rows = builder.index_rows(man)
    assert len(rows) == len(man.entries)
    keys = {r["Doc Key"] for r in rows}
    assert keys == {e.key for e in man.entries}


def test_index_first() -> None:
    rows = builder.index_rows(load_manifest())
    assert rows[0]["Doc Key"] == "documentation_index"


def test_row_fields_seeded_and_blanks() -> None:
    man = load_manifest()
    rows = builder.index_rows(man)
    by_key = {r["Doc Key"]: r for r in rows}
    sysarch = by_key["system_architecture"]
    assert sysarch["Title"] == "ITS System Architecture"
    assert sysarch["Audience"] == "operator"
    assert sysarch["Source Path"] == "docs/references/system_architecture.md"
    assert len(sysarch["Version"]) == 8  # sha8
    # Box Link + Scope + Last Updated are blank at seed (Box Link filled by --upload; the row
    # dict omits Last Updated, which the sheet leaves empty).
    assert sysarch["Box Link"] == ""
    assert sysarch["Scope"] == ""


def test_column_schema_shape() -> None:
    titles = [c["title"] for c in builder.COLUMN_SCHEMA]
    assert titles == [
        "Doc Key", "Title", "Audience", "Scope", "Version", "Last Updated", "Box Link", "Source Path"
    ]
    assert builder.COLUMN_SCHEMA[0]["primary"] is True  # Doc Key is the primary key
