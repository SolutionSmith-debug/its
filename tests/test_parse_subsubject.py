"""Tests for box_migration/parse_job_v3.parse_subsubject.

Added 2026-05-18 alongside the new entry point. Mirrors the path-manipulation
pattern from tests/test_seed_its_config.py — `box_migration/` isn't a package,
so we prepend the directory to sys.path before importing.

Run with: pytest -q tests/test_parse_subsubject.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

BOX_MIGRATION_DIR = Path(__file__).resolve().parent.parent / "box_migration"
if str(BOX_MIGRATION_DIR) not in sys.path:
    sys.path.insert(0, str(BOX_MIGRATION_DIR))

import parse_job_v3 as p  # noqa: E402

# ---- numeric_two: N.M Name -----------------------------------------------


@pytest.mark.parametrize(
    "raw,parent,sub,name",
    [
        ("7.1 Equipment",                "7",  "1",  "Equipment"),
        ("7.10 IFC Redlines",            "7",  "10", "IFC Redlines"),
        ("99.2 Vendor Name (Copy Folder)","99", "2",  "Vendor Name (Copy Folder)"),
        ("6.1 Zoning & Permitting",      "6",  "1",  "Zoning & Permitting"),
        ("8.3 Storm Water Permit",       "8",  "3",  "Storm Water Permit"),
        # Trailing whitespace is trimmed.
        ("7.1 Equipment   ",             "7",  "1",  "Equipment"),
    ],
)
def test_numeric_two_matches(raw, parent, sub, name):
    r = p.parse_subsubject(raw)
    assert r is not None
    assert r.kind == "numeric_two"
    assert r.parent == parent
    assert r.sub_index == sub
    assert r.name == name


# ---- numeric_three: N.M.K Name -------------------------------------------


@pytest.mark.parametrize(
    "raw,parent,sub_index,name",
    [
        ("6.1.1 Land Use Approvals", "6", "1.1", "Land Use Approvals"),
        ("6.1.2 Other",              "6", "1.2", "Other"),
        ("3.4.10 Late Submittals",   "3", "4.10","Late Submittals"),
    ],
)
def test_numeric_three_matches_and_takes_priority_over_two(raw, parent, sub_index, name):
    r = p.parse_subsubject(raw)
    assert r is not None
    assert r.kind == "numeric_three"
    assert r.parent == parent
    assert r.sub_index == sub_index
    assert r.name == name


# ---- digit_letter: Na. Name ----------------------------------------------


@pytest.mark.parametrize(
    "raw,parent,letter,name",
    [
        ("1a. Lum Review of IFC ELEC Drawings", "1", "a", "Lum Review of IFC ELEC Drawings"),
        ("2b. Some Other Folder",                "2", "b", "Some Other Folder"),
        ("9z. Trailing",                         "9", "z", "Trailing"),
    ],
)
def test_digit_letter_matches(raw, parent, letter, name):
    r = p.parse_subsubject(raw)
    assert r is not None
    assert r.kind == "digit_letter"
    assert r.parent == parent
    assert r.sub_index == letter
    assert r.name == name


# ---- Non-matches: must not collide with existing parsers -----------------


@pytest.mark.parametrize(
    "raw",
    [
        # Job IDs — 4-digit year prefix exceeds parent's \d{1,2} bound.
        "2025.201 KSI 4 IL",
        "2025.201.a Deep Lake",
        "2023.126.3 - Lincoln",
        # Canonical N. Subject — no second number, falls through.
        "1. EPC",
        "12. CLOSEOUT",
        # Free-text job folders — no leading digit.
        "From Evergreen EPC",
        "Submittals",
        # v3-known chaos: sub_decimal_insert. Has two numbers and a name but
        # the dot-then-space after the second number disqualifies it from
        # numeric_two (which requires \s after the digit, not \.\s).
        "1.5. Funaro Landowner Claim",
        # Uppercase digit-letter is owned by SUBJOB_LETTER_UC, not us.
        "A1. Kiwi",
        # Empty / single-segment / no name after digits.
        "",
        "7.",
        "7.1",        # missing whitespace + name
        "7.1 ",       # missing name (only whitespace after sub)
    ],
)
def test_non_matches_return_none(raw):
    assert p.parse_subsubject(raw) is None


# ---- Boundary: \d{1,2} cap on each numeric segment -----------------------


def test_three_digit_parent_is_rejected():
    # 100.1 would match a 3-digit parent; the regex caps at 2 digits to
    # avoid colliding with three-digit sub-job IDs (335.1 BRIMFIELD-1).
    assert p.parse_subsubject("100.1 Hypothetical") is None


def test_three_digit_sub_is_rejected():
    # 7.100 would match a 3-digit sub-index; capped to 2 digits because
    # no real folder has gone above .99 in the observed corpus.
    assert p.parse_subsubject("7.100 Hypothetical") is None
