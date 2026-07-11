"""Tests for subcontracts/governing_law.py — job-site-state → jurisdiction tokens (operator decision)."""
from __future__ import annotations

import pytest

from subcontracts import governing_law
from subcontracts.governing_law import GoverningLawError


def test_virginia_keeps_the_corpus_home_venue():
    assert governing_law.state_name("VA") == "the Commonwealth of Virginia"
    assert governing_law.venue("VA") == "Fairfax County, Virginia"


def test_other_states_derive_state_of_and_state_courts():
    assert governing_law.state_name("OR") == "the State of Oregon"
    assert governing_law.venue("OR") == "the State of Oregon"
    assert governing_law.state_name("IL") == "the State of Illinois"
    assert governing_law.state_name("MD") == "the State of Maryland"


def test_commonwealths_render_commonwealth_of():
    for s, name in (("KY", "Kentucky"), ("MA", "Massachusetts"), ("PA", "Pennsylvania")):
        assert governing_law.state_name(s) == f"the Commonwealth of {name}"


def test_normalizes_case_and_whitespace():
    assert governing_law.state_name(" or ") == "the State of Oregon"


def test_unknown_state_fails_closed():
    for bad in ("", "ZZ", "Oregon", None):
        with pytest.raises(GoverningLawError):
            governing_law.state_name(bad)  # type: ignore[arg-type]


def test_resolve_returns_both_tokens_and_override_wins_on_venue():
    t = governing_law.resolve("OR")
    assert t == {"governing_law_state_name": "the State of Oregon", "governing_law_venue": "the State of Oregon"}
    t2 = governing_law.resolve("OR", venue_override="Marion County, Oregon")
    assert t2["governing_law_venue"] == "Marion County, Oregon"
    assert t2["governing_law_state_name"] == "the State of Oregon"  # state_name is always derived
