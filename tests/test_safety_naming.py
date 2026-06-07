"""Unit tests for safety_reports/safety_naming.py (PR-K shared naming)."""
from __future__ import annotations

from datetime import date

import pytest

from safety_reports import safety_naming


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Bradley 1", "Bradley 1"),
        ("  Bradley 1  ", "Bradley 1"),  # stripped
        ("A/B Site", "A-B Site"),  # slash → dash (path-like on both Box + Smartsheet)
        ("Job​1", "Job1"),  # non-printable dropped
    ],
)
def test_job_folder_name_sanitizes(raw, expected):
    assert safety_naming.job_folder_name(raw) == expected


def test_job_folder_name_empty_after_sanitize_falls_back_to_raw():
    # All-non-printable → cleaned is empty → fall back to the raw stripped name.
    assert safety_naming.job_folder_name("  ​  ") == "​"


@pytest.mark.parametrize(
    "any_day",
    [date(2026, 6, 6), date(2026, 6, 7), date(2026, 6, 12)],  # Sat / Sun / Fri of one week
)
def test_week_label_keys_on_saturday(any_day):
    # Every day Sat→Fri maps to the same Saturday-keyed label.
    assert safety_naming.week_label(any_day) == "week of 2026-06-06"


def test_week_label_distinct_weeks():
    assert safety_naming.week_label(date(2026, 6, 5)) == "week of 2026-05-30"  # prior week
    assert safety_naming.week_label(date(2026, 6, 6)) == "week of 2026-06-06"
