"""Unit tests for the Saturday→Friday safety-week bucketing helper."""
from __future__ import annotations

from datetime import date

import pytest

from shared import safety_week

# The reference week: Sat 2026-05-30 → Fri 2026-06-05.
_WEEK_START = date(2026, 5, 30)
_WEEK_END = date(2026, 6, 5)


@pytest.mark.parametrize(
    "work_date",
    [
        date(2026, 5, 30),  # Saturday (opens the week → maps to itself)
        date(2026, 5, 31),  # Sunday — weekend attaches to the week ending the next Friday
        date(2026, 6, 1),   # Monday
        date(2026, 6, 2),   # Tuesday
        date(2026, 6, 3),   # Wednesday
        date(2026, 6, 4),   # Thursday
        date(2026, 6, 5),   # Friday (closes the week)
    ],
)
def test_every_day_of_the_week_maps_to_the_same_sat_fri_bounds(work_date: date) -> None:
    wk = safety_week.week_bounds(work_date)
    assert wk.start == _WEEK_START
    assert wk.end == _WEEK_END
    assert wk.start.weekday() == 5  # Saturday
    assert wk.end.weekday() == 4  # Friday
    assert wk.contains(work_date)


def test_saturday_is_idempotent_week_start() -> None:
    sat = date(2026, 6, 6)  # the *next* Saturday opens a *new* week
    wk = safety_week.week_bounds(sat)
    assert wk.start == sat
    assert wk.end == date(2026, 6, 12)


def test_adjacent_weeks_do_not_overlap() -> None:
    friday = safety_week.week_bounds(date(2026, 6, 5))
    next_saturday = safety_week.week_bounds(date(2026, 6, 6))
    assert friday.end == date(2026, 6, 5)
    assert next_saturday.start == date(2026, 6, 6)
    assert friday.end + (next_saturday.start - friday.end) == next_saturday.start  # contiguous, no gap


def test_dec_to_jan_year_boundary_week() -> None:
    """A week straddling New Year keys off its Saturday, spanning the year cleanly."""
    # Sat 2026-12-26 → Fri 2027-01-01.
    for d in (date(2026, 12, 26), date(2026, 12, 28), date(2026, 12, 31), date(2027, 1, 1)):
        wk = safety_week.week_bounds(d)
        assert wk.start == date(2026, 12, 26)
        assert wk.end == date(2027, 1, 1)
        assert wk.key == "2026-12-26"  # unambiguous across the year boundary


def test_key_is_saturday_iso_date() -> None:
    wk = safety_week.week_bounds(date(2026, 6, 3))
    assert wk.key == "2026-05-30"
    assert safety_week.week_key(date(2026, 6, 3)) == "2026-05-30"


def test_label_is_human_readable_span() -> None:
    wk = safety_week.week_bounds(date(2026, 6, 3))
    assert wk.label == "Sat 2026-05-30 → Fri 2026-06-05"


def test_keys_sort_chronologically_across_year_boundary() -> None:
    keys = [
        safety_week.week_key(date(2026, 12, 20)),  # earlier week
        safety_week.week_key(date(2026, 12, 26)),  # straddling week
        safety_week.week_key(date(2027, 1, 5)),    # next week in the new year
    ]
    assert keys == sorted(keys)
    assert len(set(keys)) == 3


def test_contains_rejects_out_of_week_dates() -> None:
    wk = safety_week.week_bounds(date(2026, 6, 3))
    assert not wk.contains(date(2026, 5, 29))  # the Friday before
    assert not wk.contains(date(2026, 6, 6))   # the Saturday after
