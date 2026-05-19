"""Tests for box_migration/parse_job_v3.parse_date_prefix.

Covers the three direction discriminators:
- R. M.D.YY <topic>  → direction='R'   (Received hypothesis)
- S. M.D.YY <topic>  → direction='S'   (Sent hypothesis)
- YYYY-MM-DD <topic> → direction='ISO' (no direction tag, added 2026-05-19)

Also covers the lowercase r./s. chaos-flagged path (direction is
upper-cased; a warning is appended).

Run with: pytest -q tests/test_parse_date_prefix.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

BOX_MIGRATION_DIR = Path(__file__).resolve().parent.parent / "box_migration"
if str(BOX_MIGRATION_DIR) not in sys.path:
    sys.path.insert(0, str(BOX_MIGRATION_DIR))

import parse_job_v3 as p  # noqa: E402

# ---- ISO 8601 form (new) -------------------------------------------------


@pytest.mark.parametrize(
    "raw,date,topic",
    [
        # Tech_debt entry's documented examples
        ("2024-12-04 Brimfield 1 IFC CAD",       "2024-12-04", "Brimfield 1 IFC CAD"),
        ("2024-12-13 Brimfield 1 IFC CAD - V2",  "2024-12-13", "Brimfield 1 IFC CAD - V2"),
        ("2025-09-15 BBCHS PBASE",               "2025-09-15", "BBCHS PBASE"),
        ("2024-08-13 - Bonacci Solar - Base Map - Standard",
         "2024-08-13", "- Bonacci Solar - Base Map - Standard"),
        ("2025-08-26 Roxbury IFC CAD Files",     "2025-08-26", "Roxbury IFC CAD Files"),
        # Trailing whitespace trim
        ("2024-12-04 Brimfield 1 IFC CAD   ",    "2024-12-04", "Brimfield 1 IFC CAD"),
    ],
)
def test_iso_positive_matches(raw, date, topic):
    r = p.parse_date_prefix(raw)
    assert r is not None
    assert r.direction == "ISO"
    assert r.date_raw == date
    assert r.topic == topic
    # ISO form should NOT carry the lowercase-R./S. warning.
    assert r.warnings == []


@pytest.mark.parametrize(
    "raw",
    [
        # Partial dates
        "2025 Some Project",                # no MM-DD
        "2024-12 Partial Date",             # no DD
        "2024- Bonacci",                    # garbage
        # Bad separators
        "2024-12-04Brimfield",              # missing space
        "2024.12.04 Brimfield",             # dots not dashes (that's R./S.-shape but no prefix)
        "2024/12/04 Brimfield",             # slashes
        # Out-of-range numeric parts that nonetheless match \d{4}-\d{2}-\d{2} ARE allowed
        # because we don't validate calendar validity — just shape. So those aren't
        # negative cases. But the no-topic case IS:
        "2024-12-04",                       # no topic
        "2024-12-04 ",                      # only whitespace as topic
        # Empty
        "",
    ],
)
def test_iso_non_matches(raw):
    r = p.parse_date_prefix(raw)
    # Some of these may match the R./S. patterns instead — verify they
    # don't return ISO.
    assert r is None or r.direction != "ISO"


# ---- R. M.D.YY form (regression — must not break) -----------------------


@pytest.mark.parametrize(
    "raw,direction,date,topic",
    [
        ("R. 5.6.25 Permit response",  "R", "5.6.25", "Permit response"),
        ("R. 12.3.2025 long-year",     "R", "12.3.2025", "long-year"),
        ("S. 11.22.24 to Luminace",    "S", "11.22.24", "to Luminace"),
        ("S. 3.18.26 demarcation",     "S", "3.18.26", "demarcation"),
        # No-topic form
        ("R. 5.6.25",                  "R", "5.6.25", None),
    ],
)
def test_rs_positive_matches_unchanged(raw, direction, date, topic):
    r = p.parse_date_prefix(raw)
    assert r is not None
    assert r.direction == direction
    assert r.date_raw == date
    assert r.topic == topic
    assert r.warnings == []  # no lowercase warning for uppercase forms


# ---- Lowercase r./s. (chaos-flagged; warning appended) ------------------


@pytest.mark.parametrize(
    "raw,upper_direction",
    [
        ("r. 4.17.26 AS-SURVEYED cad",  "R"),
        ("s. 4.17.25 RESPONSE",         "S"),
    ],
)
def test_lowercase_rs_uppercased_with_warning(raw, upper_direction):
    r = p.parse_date_prefix(raw)
    assert r is not None
    assert r.direction == upper_direction
    assert any("lowercase" in w for w in r.warnings)


# ---- Direction discriminator is the public surface ----------------------


def test_direction_iso_is_distinct_from_rs():
    iso = p.parse_date_prefix("2024-12-04 CAD")
    r = p.parse_date_prefix("R. 5.6.25 thing")
    s = p.parse_date_prefix("S. 5.6.25 thing")
    assert iso.direction == "ISO"
    assert r.direction == "R"
    assert s.direction == "S"
    assert len({iso.direction, r.direction, s.direction}) == 3


def test_none_returned_for_truly_non_date_input():
    for raw in ["random folder", "1. EPC", "V12. EPEC", "2025.201 KSI 4 IL"]:
        assert p.parse_date_prefix(raw) is None
