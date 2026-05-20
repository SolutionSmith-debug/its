"""Tests for shared/scheduling.py — holiday shifts, PTO, reviewer-chain resolution.

Coverage target: >= 95% on shared/scheduling.py and shared/defaults.py.

Run with: pytest -q tests/test_scheduling.py
       or: pytest -q tests/test_scheduling.py --cov=shared.scheduling --cov=shared.defaults
"""
from __future__ import annotations

from datetime import date

import pytest

from shared.defaults import DEFAULT_REVIEWER_CHAINS
from shared.error_log import Severity
from shared.scheduling import (
    ChainConfigLoader,
    ReviewerChain,
    ReviewerSlot,
    TimeOffClient,
    TimeOffEntry,
    _coerce_date,
    _extract_email,
    _live_fetcher,
    is_federal_holiday,
    resolve_chain,
    shift_gen_date,
    shift_send_date,
)
from shared.smartsheet_client import (
    SmartsheetAuthError,
    SmartsheetError,
    SmartsheetNotFoundError,
)

# ---- Smartsheet isolation --------------------------------------------------

@pytest.fixture(autouse=True)
def _stub_pto_smartsheet_get_rows(mocker):
    """ITS_Time_Off appears empty by default — tests stay offline.

    Existing tests built before the live fetcher was wired assumed
    `TimeOffClient()`'s default fetcher returned []. Wiring `_live_fetcher`
    as the new default means an unmocked test run would attempt a real
    Smartsheet read. Autouse-stubbing `smartsheet_client.get_rows` to []
    preserves the historical behavior (default client = nobody is out)
    without rewriting every test. Per-test mocker.patch overrides this
    fixture's value when a specific behavior is needed.
    """
    return mocker.patch(
        "shared.scheduling.smartsheet_client.get_rows",
        return_value=[],
    )


@pytest.fixture(autouse=True)
def _silence_pto_warn_logging(mocker):
    """Silence `error_log.log` from leaking to ITS_Errors during tests.

    `_live_fetcher` fail-open paths call `log(WARN, ...)` which would otherwise
    try to write to ITS_Errors via `_smartsheet_log`. Tests that need to
    verify WARN content patch `shared.scheduling.log` directly to inspect args.
    """
    return mocker.patch("shared.scheduling.log")

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


def test_time_off_client_default_fetcher_is_live_fetcher():
    """The default fetcher is `_live_fetcher` (production wiring)."""
    assert TimeOffClient().fetcher is _live_fetcher


def test_time_off_client_default_returns_nobody_out_when_sheet_empty(_stub_pto_smartsheet_get_rows):
    """Autouse stub returns [] from get_rows — _live_fetcher returns no entries."""
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


def test_retroactive_entry_affects_new_client_instances():
    """A retroactive PTO row shows up for subsequent client instances.

    With per-instance caching (planning decision D-i.2a), the original client
    snapshots entries at first lookup. A fresh client picks up entries added
    later. The watchdog instantiates one client per run, so retroactive PTO
    is correctly visible on the next run.
    """
    past = date(2026, 4, 1)
    entries: list[TimeOffEntry] = []

    def fetcher() -> list[TimeOffEntry]:
        return list(entries)

    # First client: nobody is out yet.
    client_v1 = TimeOffClient(fetcher=fetcher)
    assert client_v1.who_is_out(past) == []

    # Someone enters PTO retroactively for that past date.
    entries.append(TimeOffEntry(PRIMARY, past, past))

    # client_v1 still sees the cached empty snapshot (per-instance cache).
    assert client_v1.who_is_out(past) == []

    # A fresh client picks up the new entry — retroactive PTO is preserved
    # across instance boundaries even though it's cached within an instance.
    client_v2 = TimeOffClient(fetcher=fetcher)
    assert client_v2.is_out(PRIMARY, past) is True
    assert client_v2.who_is_out(past) == [PRIMARY]


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


# ===========================================================================
# Group A — Live fetcher parsing (synthetic Smartsheet rows)
# ===========================================================================
# All cases inject `smartsheet_client.get_rows` return values rather than
# hitting the live sheet. The autouse fixture `_stub_pto_smartsheet_get_rows`
# provides the per-test override target.


SMOKE_EMAIL = "alex@evergreenmirror.com"


def _row(*, email_cell, start, end, reason="PTO", entry="ITS-SMOKE-row", row_id=1):
    """Build a get_rows-shaped dict for ITS_Time_Off."""
    return {
        "_row_id": row_id,
        "Entry": entry,
        "Person": email_cell,
        "Start Date": start,
        "End Date": end,
        "Reason": reason,
        "Notes": "",
    }


# ---- _extract_email helper -------------------------------------------------


@pytest.mark.parametrize(
    "cell,expected",
    [
        ("alex@evergreenmirror.com",                              "alex@evergreenmirror.com"),
        ({"email": "alex@evergreenmirror.com"},                   "alex@evergreenmirror.com"),
        ({"email": "alex@evergreenmirror.com", "name": "Alex P"}, "alex@evergreenmirror.com"),
    ],
)
def test_extract_email_recovers_email(cell, expected):
    assert _extract_email(cell) == expected


@pytest.mark.parametrize(
    "cell",
    [
        None,
        "",                       # blank string
        "Alex P",                 # display name, no '@'
        {"email": None},          # dict without usable email
        {"email": "Alex P"},      # email key but value not an email
        {"name": "Alex P"},       # dict with name but no email key
        12345,                    # wrong type entirely
    ],
)
def test_extract_email_returns_none_when_unrecoverable(cell):
    assert _extract_email(cell) is None


# ---- _coerce_date helper ---------------------------------------------------


def test_coerce_date_accepts_iso_string():
    assert _coerce_date("2026-05-20") == date(2026, 5, 20)


def test_coerce_date_accepts_date_object():
    d = date(2026, 5, 20)
    assert _coerce_date(d) is d


def test_coerce_date_drops_time_component_from_datetime():
    from datetime import datetime as dt
    assert _coerce_date(dt(2026, 5, 20, 13, 45)) == date(2026, 5, 20)


@pytest.mark.parametrize("raw", [None, "", "not-a-date", "2026/05/20", 12345])
def test_coerce_date_returns_none_when_unparseable(raw):
    assert _coerce_date(raw) is None


# ---- _live_fetcher parsing -------------------------------------------------


def test_live_fetcher_parses_single_day_pto(_stub_pto_smartsheet_get_rows):
    _stub_pto_smartsheet_get_rows.return_value = [
        _row(email_cell=SMOKE_EMAIL, start="2026-05-20", end="2026-05-20"),
    ]
    entries = _live_fetcher()
    assert entries == [
        TimeOffEntry(SMOKE_EMAIL, date(2026, 5, 20), date(2026, 5, 20)),
    ]


def test_live_fetcher_parses_multi_day_pto(_stub_pto_smartsheet_get_rows):
    _stub_pto_smartsheet_get_rows.return_value = [
        _row(email_cell=SMOKE_EMAIL, start="2026-05-20", end="2026-05-24"),
    ]
    entries = _live_fetcher()
    assert entries[0].start_date == date(2026, 5, 20)
    assert entries[0].end_date == date(2026, 5, 24)
    assert entries[0].covers(date(2026, 5, 22))


def test_live_fetcher_handles_overlapping_entries_for_same_person(_stub_pto_smartsheet_get_rows):
    """Overlapping rows are returned as two entries; client de-dups in who_is_out."""
    _stub_pto_smartsheet_get_rows.return_value = [
        _row(email_cell=SMOKE_EMAIL, start="2026-05-20", end="2026-05-22", row_id=1),
        _row(email_cell=SMOKE_EMAIL, start="2026-05-21", end="2026-05-25", row_id=2),
    ]
    entries = _live_fetcher()
    assert len(entries) == 2
    client = TimeOffClient(fetcher=lambda: entries)
    assert client.is_out(SMOKE_EMAIL, date(2026, 5, 21)) is True
    assert client.who_is_out(date(2026, 5, 21)) == [SMOKE_EMAIL]


def test_live_fetcher_far_future_entry_does_not_cover_today(_stub_pto_smartsheet_get_rows):
    """An entry months ahead is parsed correctly and covers() returns False for today."""
    _stub_pto_smartsheet_get_rows.return_value = [
        _row(email_cell=SMOKE_EMAIL, start="2027-01-15", end="2027-01-20"),
    ]
    entries = _live_fetcher()
    assert entries[0].covers(date(2026, 5, 20)) is False
    assert entries[0].covers(date(2027, 1, 17)) is True


def test_live_fetcher_ended_yesterday_entry_does_not_cover_today(_stub_pto_smartsheet_get_rows):
    """Yesterday-ended entry parses correctly and excludes today."""
    _stub_pto_smartsheet_get_rows.return_value = [
        _row(email_cell=SMOKE_EMAIL, start="2026-05-15", end="2026-05-19"),
    ]
    entries = _live_fetcher()
    assert entries[0].covers(date(2026, 5, 20)) is False
    assert entries[0].covers(date(2026, 5, 19)) is True


def test_live_fetcher_contact_dict_with_email_and_name(_stub_pto_smartsheet_get_rows):
    """CONTACT_LIST cell that arrives as {email, name} dict — email extracted."""
    _stub_pto_smartsheet_get_rows.return_value = [
        _row(
            email_cell={"email": SMOKE_EMAIL, "name": "Alex Park"},
            start="2026-05-20",
            end="2026-05-20",
        ),
    ]
    entries = _live_fetcher()
    assert entries == [TimeOffEntry(SMOKE_EMAIL, date(2026, 5, 20), date(2026, 5, 20))]


def test_live_fetcher_skips_row_with_missing_email_and_warns(
    _stub_pto_smartsheet_get_rows, _silence_pto_warn_logging,
):
    """Row whose Person cell has no recoverable email is skipped and a WARN fires.

    The fetch survives — other rows still parse.
    """
    _stub_pto_smartsheet_get_rows.return_value = [
        _row(email_cell="Alex Park", start="2026-05-20", end="2026-05-20", row_id=11),
        _row(email_cell=SMOKE_EMAIL, start="2026-05-21", end="2026-05-21", row_id=12),
    ]
    entries = _live_fetcher()
    # Only the valid row survives.
    assert entries == [TimeOffEntry(SMOKE_EMAIL, date(2026, 5, 21), date(2026, 5, 21))]
    # A WARN was emitted with the bad row's identity.
    assert _silence_pto_warn_logging.called
    warn_call = _silence_pto_warn_logging.call_args_list[0]
    assert warn_call.args[0] is Severity.WARN
    assert warn_call.args[1] == "shared.scheduling"
    assert "11" in warn_call.args[2]  # row_id in the message


def test_live_fetcher_accepts_all_canonical_reason_values(_stub_pto_smartsheet_get_rows):
    """Every canonical Reason value parses without surfacing in TimeOffEntry.

    `TimeOffEntry` deliberately does not carry Reason (out of brief scope);
    the fetcher must still tolerate every canonical value without skipping
    or warning. This locks in fixture realism — if Reason ever becomes
    consumed downstream, the picklist coverage is already documented.
    """
    _stub_pto_smartsheet_get_rows.return_value = [
        _row(email_cell=SMOKE_EMAIL, start="2026-05-20", end="2026-05-20",
             reason=r, entry=f"ITS-SMOKE-{r}", row_id=i)
        for i, r in enumerate(("PTO", "Sick", "Holiday", "Personal", "Other"), start=1)
    ]
    entries = _live_fetcher()
    assert len(entries) == 5
    assert all(e.person_email == SMOKE_EMAIL for e in entries)


def test_live_fetcher_skips_row_with_unparseable_dates(
    _stub_pto_smartsheet_get_rows, _silence_pto_warn_logging,
):
    """Row with bad Start Date is skipped; rest of fetch succeeds."""
    _stub_pto_smartsheet_get_rows.return_value = [
        _row(email_cell=SMOKE_EMAIL, start="not-a-date", end="2026-05-20", row_id=21),
        _row(email_cell=SMOKE_EMAIL, start="2026-05-22", end="2026-05-22", row_id=22),
    ]
    entries = _live_fetcher()
    assert entries == [TimeOffEntry(SMOKE_EMAIL, date(2026, 5, 22), date(2026, 5, 22))]
    assert _silence_pto_warn_logging.called


# ===========================================================================
# Group B — Per-instance caching behavior
# ===========================================================================


def test_caching_two_lookups_one_fetch():
    """Two lookups on the same client invoke the fetcher exactly once."""
    call_count = 0

    def fetcher() -> list[TimeOffEntry]:
        nonlocal call_count
        call_count += 1
        return [TimeOffEntry(PRIMARY, date(2026, 5, 20), date(2026, 5, 22))]

    client = TimeOffClient(fetcher=fetcher)
    assert client.is_out(PRIMARY, date(2026, 5, 21)) is True
    assert client.who_is_out(date(2026, 5, 21)) == [PRIMARY]
    assert call_count == 1


def test_caching_new_instance_refetches():
    """A fresh TimeOffClient instance invokes the fetcher again."""
    call_count = 0

    def fetcher() -> list[TimeOffEntry]:
        nonlocal call_count
        call_count += 1
        return []

    TimeOffClient(fetcher=fetcher).who_is_out(date(2026, 5, 20))
    TimeOffClient(fetcher=fetcher).who_is_out(date(2026, 5, 20))
    assert call_count == 2


def test_caching_scoped_per_instance_not_shared_across_instances():
    """Instance A's cache must not be visible to instance B even if B uses
    a fetcher that would return different data."""
    a_entries = [TimeOffEntry(PRIMARY, date(2026, 5, 20), date(2026, 5, 20))]
    b_entries = [TimeOffEntry(SECONDARY, date(2026, 5, 20), date(2026, 5, 20))]

    client_a = TimeOffClient(fetcher=lambda: a_entries)
    client_b = TimeOffClient(fetcher=lambda: b_entries)
    assert client_a.who_is_out(date(2026, 5, 20)) == [PRIMARY]
    assert client_b.who_is_out(date(2026, 5, 20)) == [SECONDARY]


# ===========================================================================
# Group C — Fail-open path
# ===========================================================================


def test_live_fetcher_fail_open_on_smartsheet_auth_error(
    _stub_pto_smartsheet_get_rows, _silence_pto_warn_logging,
):
    """SmartsheetAuthError → WARN logged, [] returned. Watchdog keeps running."""
    _stub_pto_smartsheet_get_rows.side_effect = SmartsheetAuthError(
        "HTTP 401 (code 1002): Your Access Token is invalid"
    )
    entries = _live_fetcher()
    assert entries == []
    assert _silence_pto_warn_logging.called
    warn_call = _silence_pto_warn_logging.call_args_list[0]
    assert warn_call.args[0] is Severity.WARN
    assert "Smartsheet" in warn_call.args[2]


def test_live_fetcher_fail_open_on_sheet_not_found(
    _stub_pto_smartsheet_get_rows, _silence_pto_warn_logging,
):
    """SmartsheetNotFoundError → WARN logged, [] returned. Distinguishable in logs."""
    _stub_pto_smartsheet_get_rows.side_effect = SmartsheetNotFoundError(
        "HTTP 404 (code 1006): Not Found"
    )
    assert _live_fetcher() == []
    assert _silence_pto_warn_logging.called


def test_live_fetcher_fail_open_on_unexpected_exception(
    _stub_pto_smartsheet_get_rows, _silence_pto_warn_logging,
):
    """A non-Smartsheet exception (e.g., network unreachable) still fails open."""
    _stub_pto_smartsheet_get_rows.side_effect = ConnectionError(
        "Network is unreachable"
    )
    assert _live_fetcher() == []
    assert _silence_pto_warn_logging.called
    # The "unexpected" path message is distinguishable from the typed-Smartsheet path.
    warn_call = _silence_pto_warn_logging.call_args_list[0]
    assert "unexpected" in warn_call.args[2]


def test_live_fetcher_typed_and_unexpected_paths_emit_distinguishable_messages(
    _stub_pto_smartsheet_get_rows, _silence_pto_warn_logging,
):
    """Both fail-open paths must be distinguishable in the morning log scan."""
    _stub_pto_smartsheet_get_rows.side_effect = SmartsheetError("typed")
    _live_fetcher()
    _stub_pto_smartsheet_get_rows.side_effect = RuntimeError("untyped")
    _live_fetcher()
    msgs = [call.args[2] for call in _silence_pto_warn_logging.call_args_list]
    typed_msg = next(m for m in msgs if "Smartsheet" in m and "unexpected" not in m)
    untyped_msg = next(m for m in msgs if "unexpected" in m)
    assert typed_msg != untyped_msg


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
