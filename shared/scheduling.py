"""Date-shift, PTO, and reviewer-chain helpers shared across workstreams.

Three responsibilities, all foundation-level:

1. Federal-holiday-aware date shifts. Generation jobs roll back to the last business day on or
   before the target; send jobs roll forward to the next business day on or after the target.
   Recursive so two-holiday spans (e.g., Christmas Day + Boxing Day in customer calendars
   that adopt it) still land on a real business day.
2. PTO lookups against ITS_Time_Off. Stubbed today (returns nobody-is-out) until the sandbox
   sheet is provisioned. The fetcher is injected so tests and the real Smartsheet wiring can
   both plug in without changing call sites.
3. Three-tier reviewer-chain resolution per workstream. Reads chain composition from
   ITS_Config (also stubbed/injectable) and removes anyone currently out. Remaining members
   "shift up": surviving members take positional offsets [0, delay_to_secondary,
   delay_to_tertiary], so when the primary is out the secondary takes the 0-hour slot.

Identity references — emails, chain composition — live in `shared.defaults` and ITS_Config.
This module is identity-free by design.
"""
from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import date, timedelta
from functools import lru_cache

import holidays

from shared.defaults import DEFAULT_REVIEWER_CHAINS, ReviewerChainConfig

# ---- Holiday calendar ------------------------------------------------------

# `holidays.country_holidays('US')` returns the US federal observance calendar — which is
# what the customer's business runs on. State and bank holidays are intentionally excluded.
@lru_cache(maxsize=1)
def _federal_calendar() -> holidays.HolidayBase:
    # `years=None` lets the holidays library expand the calendar on demand as we query dates,
    # so we don't have to enumerate years up front.
    return holidays.country_holidays("US")


def is_federal_holiday(d: date) -> bool:
    """True if `d` is a US federal holiday (including observed dates)."""
    return d in _federal_calendar()


def _is_business_day(d: date) -> bool:
    # Mon=0..Fri=4 are weekdays; weekends and federal holidays are non-business.
    return d.weekday() < 5 and not is_federal_holiday(d)


def shift_gen_date(target: date) -> date:
    """Roll back to the most recent business day on or before `target`.

    Generation runs are scheduled the business day *before* the send. If that day is a
    federal holiday (or weekend), step back one day at a time until we land on a real
    business day. Recurses naturally for back-to-back holidays.
    """
    d = target
    while not _is_business_day(d):
        d -= timedelta(days=1)
    return d


def shift_send_date(target: date) -> date:
    """Roll forward to the next business day on or after `target`.

    Sends are externally visible — they must land on a day someone is actually at work.
    """
    d = target
    while not _is_business_day(d):
        d += timedelta(days=1)
    return d


# ---- Time-off lookup -------------------------------------------------------

@dataclass(frozen=True)
class TimeOffEntry:
    """One row of ITS_Time_Off. Date range is inclusive on both ends.

    `start_date == end_date` for single-day PTO. Retroactive entries — i.e., a row added
    today for a date already in the past — work the same way: they affect any lookup
    whose `on_date` falls in the range, regardless of when the entry was created.
    """
    person_email: str
    start_date: date
    end_date: date

    def covers(self, on_date: date) -> bool:
        return self.start_date <= on_date <= self.end_date


# Type alias for the function signature ITS_Time_Off fetchers must satisfy.
TimeOffFetcher = Callable[[], list[TimeOffEntry]]


def _empty_fetcher() -> list[TimeOffEntry]:
    """Stub fetcher used until ITS_Time_Off is provisioned. Returns no entries."""
    return []


@dataclass
class TimeOffClient:
    """PTO lookup wrapper.

    Production wiring will swap `fetcher` for a Smartsheet read against ITS_Time_Off.
    Until then the default fetcher returns []. Tests inject their own fetcher (or the
    `entries` shortcut) to exercise the lookup logic.

    No caching: each lookup calls the fetcher. That keeps retroactive PTO entries
    correct without an explicit refresh, at the cost of one Smartsheet read per query.
    Callers that need to query many people on the same day should pass a fetcher that
    closes over a pre-fetched list.
    """
    fetcher: TimeOffFetcher = field(default=_empty_fetcher)

    @classmethod
    def from_entries(cls, entries: list[TimeOffEntry]) -> TimeOffClient:
        """Build a client backed by a fixed list — convenience for tests."""
        return cls(fetcher=lambda: list(entries))

    def is_out(self, person_email: str, on_date: date) -> bool:
        """True if `person_email` has any PTO entry covering `on_date`."""
        return any(
            e.person_email == person_email and e.covers(on_date)
            for e in self.fetcher()
        )

    def who_is_out(self, on_date: date) -> list[str]:
        """All emails with a PTO entry covering `on_date`. Deduplicated; insertion-ordered."""
        seen: dict[str, None] = {}
        for e in self.fetcher():
            if e.covers(on_date):
                seen.setdefault(e.person_email, None)
        return list(seen)


# ---- Reviewer chain --------------------------------------------------------

@dataclass(frozen=True)
class ReviewerSlot:
    """One position in a resolved reviewer chain."""
    email: str
    joins_at_offset_hours: int


@dataclass(frozen=True)
class ReviewerChain:
    """An ordered chain of reviewers for a single workstream on a given date.

    Iteration yields slots in escalation order — primary first. `is_empty` is the signal
    callers use to trigger "hold week, alert via Resend" per the spec.
    """
    workstream: str
    on_date: date
    slots: tuple[ReviewerSlot, ...]

    @property
    def is_empty(self) -> bool:
        return not self.slots

    def __iter__(self) -> Iterator[ReviewerSlot]:
        return iter(self.slots)

    def __len__(self) -> int:
        return len(self.slots)


# Fetcher signature for ITS_Config chain reads. Returning None means "no override; use the
# default for this workstream" — distinct from returning an empty dict, which would be a
# misconfiguration we'd want to surface.
ChainConfigFetcher = Callable[[str], ReviewerChainConfig | None]


def _no_override(_workstream: str) -> ReviewerChainConfig | None:
    """Stub fetcher: ITS_Config is not yet provisioned, so always fall through to defaults."""
    return None


@dataclass
class ChainConfigLoader:
    """Reads reviewer-chain config from ITS_Config with a default fallback.

    `fetcher` returns the workstream's chain config or None if ITS_Config has no row for it.
    None falls through to `shared.defaults.DEFAULT_REVIEWER_CHAINS`. Tests inject a fetcher
    to override either side of that fallback.
    """
    fetcher: ChainConfigFetcher = field(default=_no_override)

    def load(self, workstream: str) -> ReviewerChainConfig:
        override = self.fetcher(workstream)
        if override is not None:
            return override
        try:
            return DEFAULT_REVIEWER_CHAINS[workstream]
        except KeyError as exc:
            raise KeyError(
                f"No reviewer chain configured for workstream {workstream!r}. "
                f"Add it to shared.defaults.DEFAULT_REVIEWER_CHAINS or to ITS_Config."
            ) from exc


def resolve_chain(
    workstream: str,
    on_date: date,
    *,
    time_off: TimeOffClient | None = None,
    config_loader: ChainConfigLoader | None = None,
) -> ReviewerChain:
    """Resolve the reviewer chain for `workstream` on `on_date`, with PTO applied.

    Surviving reviewers take positional offsets [0, delay_to_secondary, delay_to_tertiary].
    "Shifts up" semantic: if the primary is out, the secondary becomes the 0-hour slot and
    the tertiary becomes the secondary-offset slot.

    Returns an empty chain when every configured reviewer is out — callers treat this as
    "hold the week and alert via Resend" rather than auto-routing to anyone else.
    """
    time_off = time_off or TimeOffClient()
    config_loader = config_loader or ChainConfigLoader()
    cfg = config_loader.load(workstream)

    positional_emails = [cfg["primary"], cfg["secondary"], cfg["tertiary"]]
    positional_offsets = [0, cfg["delay_to_secondary_hours"], cfg["delay_to_tertiary_hours"]]
    available = [e for e in positional_emails if not time_off.is_out(e, on_date)]

    slots = tuple(
        ReviewerSlot(email=email, joins_at_offset_hours=positional_offsets[i])
        for i, email in enumerate(available)
    )
    return ReviewerChain(workstream=workstream, on_date=on_date, slots=slots)
