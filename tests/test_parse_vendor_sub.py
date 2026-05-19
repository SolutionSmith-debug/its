"""Tests for box_migration/parse_job_v3.parse_vendor_sub.

Added 2026-05-19 alongside the new entry point. Mirrors the path-
manipulation pattern from tests/test_parse_subsubject.py — box_migration/
isn't a package, so we prepend the directory to sys.path before importing.

Run with: pytest -q tests/test_parse_vendor_sub.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

BOX_MIGRATION_DIR = Path(__file__).resolve().parent.parent / "box_migration"
if str(BOX_MIGRATION_DIR) not in sys.path:
    sys.path.insert(0, str(BOX_MIGRATION_DIR))

import parse_job_v3 as p  # noqa: E402

# ---- Positive matches ----------------------------------------------------


@pytest.mark.parametrize(
    "raw,kind,index,name",
    [
        # Tech_debt entry's documented examples
        ("V12. EPEC",                            "vendor", "12", "EPEC"),
        ("V31. Cable Markers",                   "vendor", "31", "Cable Markers"),
        ("S11. Erosion Control Consulting INC",  "sub",    "11", "Erosion Control Consulting INC"),
        ("S12. Helm",                            "sub",    "12", "Helm"),
        # Boundary: lower edge of two-digit range
        ("V10. Anything",                        "vendor", "10", "Anything"),
        ("S99. Edge Case",                       "sub",    "99", "Edge Case"),
        # Trailing whitespace trimmed
        ("V14. CAB   ",                          "vendor", "14", "CAB"),
    ],
)
def test_positive_matches(raw, kind, index, name):
    r = p.parse_vendor_sub(raw)
    assert r is not None
    assert r.kind == kind
    assert r.index == index
    assert r.name == name


# ---- Negative matches: must not steal from SUBJOB_LETTER_UC's domain -----


@pytest.mark.parametrize(
    "raw",
    [
        # Single-digit V/S — owned by SUBJOB_LETTER_UC (v2). Must not match
        # here, otherwise we'd double-claim and break the existing parser.
        "V1. Single Digit",
        "S1. Single Digit",
        "V0. Zero",
        # Not V or S
        "A1. Kiwi",
        "B2. Field",
        "K10. Kestrel",
        # Three-digit cap rejection
        "V100. Three Digit",
        "S100. Three Digit",
        # No name after the prefix
        "V12.",
        "V12. ",
        # Letters in the index position
        "Va. Lowercase",
        "VAB. Letters",
        # Lowercase v/s explicitly NOT matched (case-sensitive)
        "v12. lowercase",
        "s10. lowercase",
        # Wrong separator
        "V12: EPEC",
        "V12-EPEC",
        "V12 EPEC",   # missing dot
        # Empty / single-segment
        "",
        "V",
        "V.",
        # Adjacent-pattern collisions
        "1. EPC",                       # canonical subject
        "2025.201 KSI 4 IL",            # job ID
        "1a. Lum Review",               # digit-letter subsubject
        "7.1 Equipment",                # numeric subsubject
    ],
)
def test_non_matches_return_none(raw):
    assert p.parse_vendor_sub(raw) is None


# ---- Public surface ------------------------------------------------------


def test_vendor_sub_parse_fields():
    r = p.parse_vendor_sub("V12. EPEC")
    # Lock in the public dataclass shape so claim-chain consumers know
    # what to inspect.
    assert r is not None
    assert r.raw == "V12. EPEC"
    assert r.kind == "vendor"
    assert r.index == "12"
    assert r.name == "EPEC"


def test_kind_discriminator_maps_v_to_vendor_and_s_to_sub():
    assert p.parse_vendor_sub("V14. Anything").kind == "vendor"
    assert p.parse_vendor_sub("S14. Anything").kind == "sub"
