"""Tests for shared/scheduling.py — holiday shifts, PTO, reviewer-chain resolution.

Coverage target: >= 95% on shared/scheduling.py and shared/defaults.py.

Run with: pytest -q tests/test_scheduling.py
       or: pytest -q tests/test_scheduling.py --cov=shared.scheduling --cov=shared.defaults
"""
from __future__ import annotations

from datetime import date

import pytest

from shared.defaults import DEFAULT_REVIEWER_CHAINS
from shared.scheduling import (
    ChainConfigLoader,
    ReviewerChain,
    ReviewerSlot,
    TimeOffClient,
    TimeOffEntry,
    is_federal_holiday,
    resolve_chain,
    shift_gen_date,
    shift_send_date,
)

# Hold the default emails once so we don't repeat them in every assertion. Tests reach into
# the defaults table on purpose — these are the canonical IDs the chain resolution must
# produce when no ITS_Config override is in place.
SAFETY = DEFAULT_REVIEWER_CHAINS["safety_reports"]
PRIMARY = SAFETY["primary"]
SECONDARY = SAFETY["secondary"]
TERTIARY = SAFETY["tertiary"]
DELAY_2 = SAFETY["delay_to_secondary_hours"]
DELAY_3 = SAFETY["delay_to_tertiary_hours"]


# ---- Federal holiday detection ---------------------------------------------

@pytest.mark.parametrize(
    "holiday",
    [
        date(2026, 1, 1),    # New Year's Day (Thu)
        date(2026, 5, 25),   # Memorial Day (Mon)
        date(2026, 7, 3),    # Independence Day observed (Fri — Jul 4 falls Sat)
        date(2026, 9, 7),    # Labor Day (Mon)
        date(2026, 10, 12),  # Columbus Day (Mon)
        date(2026, 11, 11),  # Veterans Day (Wed)
        date(2026, 11, 26),  # Thanksgiving (Thu)
        date(2026, 12, 25),  # Christmas Day (Fri)
    ],
)
def test_known_2026_federal_holiday_detected(holiday: date):
    assert is_federal_holiday(holiday)


def test_ordinary_weekday_is_not_holiday():
    # A run-of-the-mill Wednesday in May 2026.
    assert not is_federal_holiday(date(2026, 5, 13))


# ---- shift_gen_date: roll back ---------------------------------------------

@pytest.mark.parametrize(
    "target, expected",
    [
        # Each 2026 federal holiday rolls back to the immediately preceding business day.
        (date(2026, 5, 25),  date(2026, 5, 22)),   # Memorial Day Mon  -> Fri before
        (date(2026, 7, 3),   date(2026, 7, 2)),    # Independence obs. -> Thu Jul 2
        (date(2026, 7, 4),   date(2026, 7, 2)),    # Independence Day Sat -> Thu (skip weekend + obs.)
        (date(2026, 9, 7),   date(2026, 9, 4)),    # Labor Day Mon     -> Fri before
        (date(2026, 10, 12), date(2026, 10, 9)),   # Columbus Day Mon  -> Fri before
        (date(2026, 11, 11), date(2026, 11, 10)),  # Veterans Day Wed  -> Tue before
        (date(2026, 11, 26), date(2026, 11, 25)),  # Thanksgiving Thu  -> Wed before
        (date(2026, 12, 25), date(2026, 12, 24)),  # Christmas Day Fri -> Thu before
        # New Year's rollover: Jan 1 2026 is a Thursday holiday; gen rolls into prior year.
        (date(2026, 1, 1),   date(2025, 12, 31)),
    ],
)
def test_shift_gen_date_rolls_back_off_holiday(target: date, expected: date):
    assert shift_gen_date(target) == expected


def test_shift_gen_date_passes_through_normal_business_day():
    # Wednesday, May 13 2026 — no holiday, no weekend. Returned unchanged.
    d = date(2026, 5, 13)
    assert shift_gen_date(d) == d


# ---- shift_send_date: roll forward -----------------------------------------

@pytest.mark.parametrize(
    "target, expected",
    [
        (date(2026, 5, 25),  date(2026, 5, 26)),   # Memorial Day Mon  -> Tue after
        (date(2026, 7, 3),   date(2026, 7, 6)),    # Independence obs. Fri -> Mon (skip Sat/Sun + Jul 4)
        (date(2026, 7, 4),   date(2026, 7, 6)),    # Independence Day Sat -> Mon
        (date(2026, 9, 7),   date(2026, 9, 8)),    # Labor Day Mon     -> Tue after
        (date(2026, 10, 12), date(2026, 10, 13)),  # Columbus Day Mon  -> Tue after
        (date(2026, 11, 11), date(2026, 11, 12)),  # Veterans Day Wed  -> Thu after
        (date(2026, 11, 26), date(2026, 11, 27)),  # Thanksgiving Thu  -> Fri after
        (date(2026, 12, 25), date(2026, 12, 28)),  # Christmas Day Fri -> Mon (skip weekend)
        # New Year's rollover: Jan 1 2026 Thu holiday -> Fri Jan 2.
        (date(2026, 1, 1),   date(2026, 1, 2)),
    ],
)
def test_shift_send_date_rolls_forward_off_holiday(target: date, expected: date):
    assert shift_send_date(target) == expected


def test_shift_send_date_passes_through_normal_business_day():
    d = date(2026, 5, 13)
    assert shift_send_date(d) == d


# ---- Multi-holiday-in-a-row edge cases -------------------------------------

def test_shift_send_skips_holiday_then_weekend_then_holiday(mocker):
    """Three non-business days in a row: forces the loop to recurse twice past holidays."""
    # Synthetic scenario — patch is_federal_holiday to flag a run of weekdays as holiday.
    # Fri Jun 5 (real non-holiday) treated as holiday; Mon Jun 8 also treated as holiday.
    # Saturday Jun 6 and Sunday Jun 7 are real weekends. shift_send should land on Tue Jun 9.
    fake_holidays = {date(2026, 6, 5), date(2026, 6, 8)}
    mocker.patch(
        "shared.scheduling.is_federal_holiday",
        side_effect=lambda d: d in fake_holidays,
    )
    assert shift_send_date(date(2026, 6, 5)) == date(2026, 6, 9)


def test_shift_gen_skips_holiday_then_weekend_then_holiday(mocker):
    """Mirror of the send test: rolls back through holiday->weekend->holiday."""
    fake_holidays = {date(2026, 6, 5), date(2026, 6, 8)}
    mocker.patch(
        "shared.scheduling.is_federal_holiday",
        side_effect=lambda d: d in fake_holidays,
    )
    # Start at Mon Jun 8 (fake holiday). Roll back -> Sun 7 (weekend) -> Sat 6 (weekend)
    # -> Fri 5 (fake holiday) -> Thu Jun 4 (clean business day).
    assert shift_gen_date(date(2026, 6, 8)) == date(2026, 6, 4)


def test_christmas_2026_send_skips_weekend():
    """Real-world consecutive non-business days: Fri Christmas + Sat + Sun -> Mon Dec 28."""
    assert shift_send_date(date(2026, 12, 25)) == date(2026, 12, 28)


# ---- TimeOffEntry / TimeOffClient -----------------------------------------

def test_time_off_entry_covers_inclusive_range():
    entry = TimeOffEntry(
        person_email=PRIMARY,
        start_date=date(2026, 5, 11),
        end_date=date(2026, 5, 15),
    )
    assert entry.covers(date(2026, 5, 11))   # start boundary
    assert entry.covers(date(2026, 5, 13))   # middle
    assert entry.covers(date(2026, 5, 15))   # end boundary
    assert not entry.covers(date(2026, 5, 10))
    assert not entry.covers(date(2026, 5, 16))


def test_time_off_client_default_fetcher_returns_nobody_out():
    """The unwrapped, no-arg client is the production stub: nobody is ever out."""
    client = TimeOffClient()
    assert client.is_out(PRIMARY, date(2026, 5, 13)) is False
    assert client.who_is_out(date(2026, 5, 13)) == []


def test_time_off_client_is_out_true_when_entry_covers_date():
    client = TimeOffClient.from_entries(
        [TimeOffEntry(PRIMARY, date(2026, 5, 11), date(2026, 5, 15))]
    )
    assert client.is_out(PRIMARY, date(2026, 5, 13)) is True
    assert client.is_out(SECONDARY, date(2026, 5, 13)) is False
    # Date outside the range -> not out.
    assert client.is_out(PRIMARY, date(2026, 5, 20)) is False


def test_who_is_out_returns_unique_emails_in_insertion_order():
    client = TimeOffClient.from_entries([
        TimeOffEntry(SECONDARY, date(2026, 5, 13), date(2026, 5, 13)),
        TimeOffEntry(PRIMARY,   date(2026, 5, 13), date(2026, 5, 13)),
        # Same person, second overlapping entry — must not duplicate.
        TimeOffEntry(PRIMARY,   date(2026, 5, 12), date(2026, 5, 14)),
    ])
    assert client.who_is_out(date(2026, 5, 13)) == [SECONDARY, PRIMARY]


def test_retroactive_entry_affects_past_date_lookup():
    """A PTO row added today for a past date must affect who_is_out for that past date.

    Modeled as: build a client whose fetcher snapshot already contains the retroactive row.
    Since the fetcher is called each lookup, no cache flush is needed.
    """
    past = date(2026, 4, 1)
    entries: list[TimeOffEntry] = []
    client = TimeOffClient(fetcher=lambda: list(entries))

    # Before retroactive entry exists, nobody is out on the past date.
    assert client.who_is_out(past) == []

    # Someone enters PTO retroactively for that past date.
    entries.append(TimeOffEntry(PRIMARY, past, past))
    assert client.is_out(PRIMARY, past) is True
    assert client.who_is_out(past) == [PRIMARY]


# ---- ReviewerChain dataclass behavior --------------------------------------

def test_reviewer_chain_iterates_in_order():
    slots = (
        ReviewerSlot(email=PRIMARY,   joins_at_offset_hours=0),
        ReviewerSlot(email=SECONDARY, joins_at_offset_hours=DELAY_2),
    )
    chain = ReviewerChain(workstream="safety_reports", on_date=date(2026, 5, 13), slots=slots)
    assert list(chain) == list(slots)
    assert len(chain) == 2
    assert chain.is_empty is False


def test_empty_reviewer_chain_flagged_empty():
    chain = ReviewerChain(workstream="safety_reports", on_date=date(2026, 5, 13), slots=())
    assert chain.is_empty is True
    assert len(chain) == 0
    assert list(chain) == []


# ---- ChainConfigLoader -----------------------------------------------------

def test_chain_config_loader_falls_through_to_defaults():
    loader = ChainConfigLoader()  # stub fetcher always returns None
    cfg = loader.load("safety_reports")
    assert cfg["primary"] == PRIMARY
    assert cfg["delay_to_secondary_hours"] == DELAY_2


def test_chain_config_loader_uses_override_when_present():
    override = {
        "primary": "alt-primary@example.com",
        "secondary": "alt-secondary@example.com",
        "tertiary": "alt-tertiary@example.com",
        "delay_to_secondary_hours": 1,
        "delay_to_tertiary_hours": 2,
    }
    loader = ChainConfigLoader(fetcher=lambda ws: override if ws == "safety_reports" else None)
    cfg = loader.load("safety_reports")
    assert cfg["primary"] == "alt-primary@example.com"
    assert cfg["delay_to_tertiary_hours"] == 2


def test_chain_config_loader_raises_for_unknown_workstream():
    loader = ChainConfigLoader()
    with pytest.raises(KeyError, match="no_such_workstream"):
        loader.load("no_such_workstream")


# ---- resolve_chain ---------------------------------------------------------

def _client_with_out(*emails: str, on_date: date) -> TimeOffClient:
    return TimeOffClient.from_entries(
        [TimeOffEntry(e, on_date, on_date) for e in emails]
    )


def test_resolve_chain_no_one_out_yields_full_three_tier():
    on = date(2026, 5, 13)
    chain = resolve_chain("safety_reports", on, time_off=TimeOffClient())
    assert [s.email for s in chain] == [PRIMARY, SECONDARY, TERTIARY]
    assert [s.joins_at_offset_hours for s in chain] == [0, DELAY_2, DELAY_3]
    assert chain.workstream == "safety_reports"
    assert chain.on_date == on


def test_resolve_chain_primary_out_promotes_secondary_to_zero_hour():
    on = date(2026, 5, 13)
    chain = resolve_chain("safety_reports", on, time_off=_client_with_out(PRIMARY, on_date=on))
    assert [s.email for s in chain] == [SECONDARY, TERTIARY]
    assert [s.joins_at_offset_hours for s in chain] == [0, DELAY_2]


def test_resolve_chain_middle_out_preserves_ordering_and_shifts_jacob_up():
    """The named scenario in the brief: Teala stays primary, Sam removed, Jacob shifts up."""
    on = date(2026, 5, 13)
    chain = resolve_chain("safety_reports", on, time_off=_client_with_out(SECONDARY, on_date=on))
    assert [s.email for s in chain] == [PRIMARY, TERTIARY]
    # Jacob takes the 4h offset that was Sam's, not his original 18h.
    assert [s.joins_at_offset_hours for s in chain] == [0, DELAY_2]


def test_resolve_chain_tertiary_out_leaves_primary_and_secondary_in_place():
    on = date(2026, 5, 13)
    chain = resolve_chain("safety_reports", on, time_off=_client_with_out(TERTIARY, on_date=on))
    assert [s.email for s in chain] == [PRIMARY, SECONDARY]
    assert [s.joins_at_offset_hours for s in chain] == [0, DELAY_2]


def test_resolve_chain_primary_and_secondary_both_out_leaves_tertiary_at_zero():
    on = date(2026, 5, 13)
    chain = resolve_chain(
        "safety_reports", on, time_off=_client_with_out(PRIMARY, SECONDARY, on_date=on),
    )
    assert [s.email for s in chain] == [TERTIARY]
    assert [s.joins_at_offset_hours for s in chain] == [0]


def test_resolve_chain_all_three_out_returns_empty():
    on = date(2026, 5, 13)
    chain = resolve_chain(
        "safety_reports", on,
        time_off=_client_with_out(PRIMARY, SECONDARY, TERTIARY, on_date=on),
    )
    assert chain.is_empty
    # Empty chain is the documented signal for "hold the week, alert via Resend."
    assert list(chain) == []


def test_resolve_chain_uses_injected_config_loader():
    on = date(2026, 5, 13)
    override = {
        "primary": "p@x.com",
        "secondary": "s@x.com",
        "tertiary": "t@x.com",
        "delay_to_secondary_hours": 2,
        "delay_to_tertiary_hours": 6,
    }
    loader = ChainConfigLoader(fetcher=lambda ws: override)
    chain = resolve_chain("safety_reports", on, config_loader=loader)
    assert [s.email for s in chain] == ["p@x.com", "s@x.com", "t@x.com"]
    assert [s.joins_at_offset_hours for s in chain] == [0, 2, 6]


def test_resolve_chain_defaults_when_no_dependencies_injected():
    """Both stub deps used: production default state. Nobody is out; full chain returned."""
    chain = resolve_chain("safety_reports", date(2026, 5, 13))
    assert len(chain) == 3
    assert chain.slots[0].email == PRIMARY


# ---- Identity-free invariant ----------------------------------------------

def test_scheduling_module_contains_no_hardcoded_emails():
    """shared/scheduling.py must not contain any email literal — identities live in
    shared/defaults.py and ITS_Config. This guard catches accidental hardcoding in future
    edits. Decorators like `@lru_cache` are fine; only `name@domain.tld` patterns trip it."""
    import re
    from pathlib import Path

    src = (Path(__file__).resolve().parent.parent / "shared" / "scheduling.py").read_text()
    matches = re.findall(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", src)
    assert not matches, (
        f"shared/scheduling.py contains hardcoded email(s) {matches}; identity "
        "references must live in shared/defaults.py or ITS_Config."
    )
