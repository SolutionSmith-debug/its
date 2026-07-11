"""Governing-law + venue derivation (SC-S3a) — the operator's decision: the subcontract's governing
law is DERIVED from the job-site state, not hard-coded Virginia (the corpus specimen's default).

Fills the body's two jurisdiction tokens:
  * {{governing_law_state_name}} — §27.10 "governed by the laws of <...>"  (e.g. "the Commonwealth of
    Virginia", "the State of Oregon"). Commonwealths render "the Commonwealth of"; all others "the
    State of".
  * {{governing_law_venue}}      — §15.2 "venue and jurisdiction in the state courts of <...>".
    Virginia keeps Evergreen's home venue (Fairfax County) per the corpus; every other state falls back
    to a state-court venue. A per-subcontract override (a `governing_law_venue` field) always wins, so a
    specific county can be named when known — the derivation is only the default.

Pure, no I/O. An unknown 2-letter state fails CLOSED (raises) — a subcontract must never render an
empty/wrong jurisdiction clause.
"""
from __future__ import annotations

_STATE_NAMES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California",
    "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware", "FL": "Florida", "GA": "Georgia",
    "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois", "IN": "Indiana", "IA": "Iowa", "KS": "Kansas",
    "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine", "MD": "Maryland", "MA": "Massachusetts",
    "MI": "Michigan", "MN": "Minnesota", "MS": "Mississippi", "MO": "Missouri", "MT": "Montana",
    "NE": "Nebraska", "NV": "Nevada", "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico",
    "NY": "New York", "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma",
    "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina",
    "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas", "UT": "Utah", "VT": "Vermont",
    "VA": "Virginia", "WA": "Washington", "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming",
    "DC": "the District of Columbia",
}
# The four U.S. commonwealths render "the Commonwealth of <Name>".
_COMMONWEALTHS = {"VA", "KY", "MA", "PA"}


class GoverningLawError(ValueError):
    """The job-site state can't be resolved to a governing-law jurisdiction (fails closed)."""


def _normalize(state: str) -> str:
    s = (state or "").strip().upper()
    if s not in _STATE_NAMES:
        raise GoverningLawError(
            f"unknown job-site state {state!r} — a subcontract's governing law must resolve to a real "
            "state (set the site state, or provide governing_law_state_name/venue overrides)"
        )
    return s


def state_name(state: str) -> str:
    """§27.10 governing-law phrase: 'the Commonwealth of Virginia' / 'the State of Oregon'."""
    s = _normalize(state)
    name = _STATE_NAMES[s]
    if s == "DC":
        return name  # "the District of Columbia" reads on its own
    prefix = "the Commonwealth of" if s in _COMMONWEALTHS else "the State of"
    return f"{prefix} {name}"


def venue(state: str) -> str:
    """§15.2 venue phrase. Virginia keeps the corpus's Fairfax County home venue; every other state
    falls back to its own state courts (override with a per-subcontract governing_law_venue when a
    specific county is known)."""
    s = _normalize(state)
    if s == "VA":
        return "Fairfax County, Virginia"
    return f"the State of {_STATE_NAMES[s]}" if s != "DC" else "the District of Columbia"


def resolve(state: str, venue_override: str | None = None) -> dict[str, str]:
    """Both jurisdiction tokens for the body. venue_override (a per-subcontract field) wins over the
    derived venue so a specific county can be named; state_name is always derived from the site state."""
    tokens = {"governing_law_state_name": state_name(state), "governing_law_venue": venue(state)}
    if venue_override and venue_override.strip():
        tokens["governing_law_venue"] = venue_override.strip()
    return tokens
