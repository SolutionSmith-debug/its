"""Lint fence: every raw-``COLUMN_SCHEMA`` builder's column descriptions must fit
the Smartsheet 250-char column-description limit (HTTP 400 errorCode 1041).

Builders under ``scripts/migrations/build_*_sheet.py`` pass ``COLUMN_SCHEMA``
straight to ``create_sheet_in_folder`` with no runtime truncation, so a single
over-limit description 400s the WHOLE sheet create on a fresh tenant — and every
prior run adopted an existing sheet, so the create path was never exercised
(the mocks-pass-live-fails class). It has now bitten twice: build_system_sheets
(2026-07-22, fixed via its own ``_cap_descriptions`` payload cap) and
build_its_trusted_contacts (2026-07-24, 303-char Project Scope). This test stops
the third occurrence.

Scope note: ``build_system_sheets.py`` (plural "sheets") is deliberately excluded
by the glob — it assembles multiple per-sheet schemas and DEFENDS itself by
truncating over-limit descriptions in ``_cap_descriptions`` at payload time; that
path is fenced separately in ``tests/test_gap_builders.py``.
"""
from __future__ import annotations

import importlib.util
import pathlib

import pytest

# Canonical limit lives at build_system_sheets.SMARTSHEET_COLUMN_DESCRIPTION_MAX
# (== 250, the Smartsheet column-description API cap; over it → 400 errorCode 1041).
MAX_DESCRIPTION_CHARS = 250

_MIGRATIONS = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "migrations"
_BUILDERS = sorted(_MIGRATIONS.glob("build_*_sheet.py"))


def _load_module(path: pathlib.Path):
    spec = importlib.util.spec_from_file_location(f"_builder_{path.stem}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_builder_glob_is_non_empty() -> None:
    """Guard against the glob silently matching nothing (which would make every
    per-builder assertion vacuously pass)."""
    assert _BUILDERS, f"no build_*_sheet.py found under {_MIGRATIONS}"


@pytest.mark.parametrize("builder", _BUILDERS, ids=lambda p: p.stem)
def test_column_descriptions_within_smartsheet_cap(builder: pathlib.Path) -> None:
    module = _load_module(builder)
    schema = getattr(module, "COLUMN_SCHEMA", None)
    assert schema is not None, f"{builder.name} has no module-level COLUMN_SCHEMA"
    for col in schema:
        desc = col.get("description")
        if desc is None:
            continue
        assert len(desc) <= MAX_DESCRIPTION_CHARS, (
            f"{builder.name} column {col.get('title')!r} has a "
            f"{len(desc)}-char description (> {MAX_DESCRIPTION_CHARS}; Smartsheet "
            f"errorCode 1041 400s the whole sheet create). Trim the operator-facing "
            f"prose; move developer rationale to a code comment."
        )
