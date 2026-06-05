"""Saturday→Friday safety-week bucketing — the single load-bearing date rule.

Purpose
-------
Safety Portal weeks run Saturday through Friday: weekend work attaches to the week
that *ends* the following Friday, so a complete Sat–Fri packet is reviewable as a
unit (Phase 3+ decision, brief Part 0). Bucketing keys on the form **work-date**,
never the receipt date.

Invariants
----------
- The Saturday epoch anchor is load-bearing: `intake.py` (deciding which per-job
  week sheet a submission files into) and `weekly_generate.py` (deciding which
  submissions compile into one packet) MUST agree on week membership exactly — do
  not change the anchor without updating both consumers.
- Canonical week key = the Saturday start date in ISO format (e.g. `2026-05-30`),
  deliberately NOT an ISO week number: ISO weeks run Mon–Sun, so an ISO-week label
  would misrepresent a Sat–Fri week and break at Dec→Jan. The Saturday date is
  exact, sortable, and year-spanning (a New-Year-straddling week — Sat 2026-12-26 →
  Fri 2027-01-01 — keys unambiguously as `2026-12-26`).
- Pure: no I/O, no SDK, no implicit clock — the only date input is `work_date`.

Failure modes
-------------
Raises nothing on a valid `datetime.date`; a non-date argument raises `TypeError`
(intentional + unhandled — callers pass a bare calendar date).

Consumers
---------
- safety_reports/intake.py — which per-job week sheet a submission files into.
- safety_reports/weekly_generate.py (Phase 5) — which submissions compile together.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

# date.weekday(): Mon=0 … Sat=5, Sun=6.
_SATURDAY = 5


@dataclass(frozen=True)
class SafetyWeek:
    """The Saturday–Friday week a given work-date belongs to.

    `start` — the Saturday that opens the week (inclusive).
    `end`   — the Friday that closes the week (inclusive).
    `key`   — canonical machine key = `start.isoformat()` (sortable, year-spanning).
    `label` — human-readable span for folder names / operator surfaces.
    """

    start: date
    end: date

    @property
    def key(self) -> str:
        """Canonical, sortable week identifier (the Saturday ISO date)."""
        return self.start.isoformat()

    @property
    def label(self) -> str:
        """Human-readable span, e.g. 'Sat 2026-05-30 → Fri 2026-06-05'."""
        return f"Sat {self.start.isoformat()} → Fri {self.end.isoformat()}"

    def contains(self, work_date: date) -> bool:
        """True if `work_date` falls within this Saturday–Friday week (inclusive)."""
        return self.start <= work_date <= self.end


def week_bounds(work_date: date) -> SafetyWeek:
    """Return the Saturday–Friday `SafetyWeek` containing `work_date`.

    A Saturday maps to itself as the week start; every other day walks back to the
    most recent Saturday. The end is always start + 6 days (the Friday).
    """
    days_since_saturday = (work_date.weekday() - _SATURDAY) % 7
    start = work_date - timedelta(days=days_since_saturday)
    return SafetyWeek(start=start, end=start + timedelta(days=6))


def week_key(work_date: date) -> str:
    """Shorthand for `week_bounds(work_date).key` — the canonical week identifier."""
    return week_bounds(work_date).key
