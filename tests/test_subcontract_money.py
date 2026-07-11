"""Exhaustive tests for subcontracts/money.py — the §2.1 Contract Price WORDS (legal money path)
+ the SOV-sums-to-price guard. A wrong price-in-words on a binding contract is unacceptable, so
cents_to_words is pinned against the real corpus specimens + edge cases."""
from __future__ import annotations

import pytest

from subcontracts import money
from subcontracts.money import MoneyError


@pytest.mark.parametrize("cents,expected", [
    (27401850, "Two hundred seventy-four thousand eighteen dollars and fifty cents"),  # the specimen
    (5300000, "Fifty-three thousand dollars"),                                          # .00 → no cents clause
    (968432, "Nine thousand six hundred eighty-four dollars and thirty-two cents"),
    (181270900, "One million eight hundred twelve thousand seven hundred nine dollars"),  # Bonacci Legacy
    (132000000, "One million three hundred twenty thousand dollars"),                   # Steger
    (1975081, "Nineteen thousand seven hundred fifty dollars and eighty-one cents"),     # Bonacci Seevers
    (100, "One dollar"),
    (200, "Two dollars"),
    (1, "Zero dollars and one cent"),
    (99, "Zero dollars and ninety-nine cents"),
    (0, "Zero dollars"),
    (101, "One dollar and one cent"),
    (1500000, "Fifteen thousand dollars"),
    (150000000000, "One billion five hundred million dollars"),
])
def test_cents_to_words(cents, expected):
    assert money.cents_to_words(cents) == expected


def test_cents_to_words_rejects_bad_input():
    for bad in (-1, True, 1.5, "100"):
        with pytest.raises(MoneyError):
            money.cents_to_words(bad)  # type: ignore[arg-type]


def test_int_to_words_us_style_no_and():
    assert money.int_to_words(274018) == "two hundred seventy-four thousand eighteen"
    assert money.int_to_words(0) == "zero"
    assert money.int_to_words(1000000) == "one million"
    assert money.int_to_words(21) == "twenty-one"
    assert money.int_to_words(100) == "one hundred"


def test_format_figure():
    assert money.format_figure(27401850) == "$274,018.50"
    assert money.format_figure(5300000) == "$53,000.00"
    assert money.format_figure(1) == "$0.01"


def test_contract_price_clause_words_and_figure_always_agree():
    # The whole point: words and figure both derive from the same cents → can NEVER disagree.
    c = money.contract_price_clause(27401850, "fixed")
    assert c == "Two hundred seventy-four thousand eighteen dollars and fifty cents ($274,018.50)"
    ntx = money.contract_price_clause(1975081, "not_to_exceed")
    assert ntx.startswith("NOT TO EXCEED Nineteen thousand seven hundred fifty dollars and eighty-one cents ($19,750.81)")
    with pytest.raises(MoneyError):
        money.contract_price_clause(100, "weird")


# ── SOV-sums-to-price guard ─────────────────────────────────────────────────


def test_sov_single_lump_line_matching_price_is_clean():
    lines = [{"description": "Work", "extended_cents": 27401850}]
    assert money.sov_mismatches(27401850, lines) == []


def test_sov_multi_line_sums_to_price_is_clean():
    lines = [
        {"qty": 1, "unit_price_cents": 10000000, "extended_cents": 10000000},
        {"qty": 2, "unit_price_cents": 5000000, "extended_cents": 10000000},
    ]
    assert money.sov_mismatches(20000000, lines) == []


def test_sov_total_mismatch_is_flagged():
    lines = [{"extended_cents": 9490000}]
    problems = money.sov_mismatches(27401850, lines)
    assert problems and "must sum to" in problems[0]


def test_sov_line_extended_recompute_mismatch_is_flagged():
    # client says extended=999 but qty×unit recomputes to 10_000_000 → flagged, never trusted.
    lines = [{"qty": 1, "unit_price_cents": 10000000, "extended_cents": 999}]
    problems = money.sov_mismatches(10000000, lines)
    assert any("!= recomputed" in p for p in problems)


def test_sov_rejects_bad_price_and_bad_unit():
    assert money.sov_mismatches(-1, [{"extended_cents": 0}])[0].startswith("contract_price_cents")
    problems = money.sov_mismatches(100, [{"qty": 1, "unit_price_cents": True}])
    assert any("unit_price_cents" in p for p in problems)
