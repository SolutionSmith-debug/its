"""Subcontract money helpers (SC-S3a) — integer-cents arithmetic + the §2.1 Contract Price WORDS.

The value-add over the manual corpus (which shipped a real "nine cents / $…00" words↔figure
mismatch): the spelled-out §2.1 amount is DERIVED deterministically from the integer-cents figure, so
the words and the parenthesized figure can never disagree. Purpose-built (US legal phrasing, no "and"
inside the number, "and NN cents" only for the fractional part) rather than a library, so the exact
output is controlled + exhaustively tested. No floats touch the money path.

Also the SOV-sums-to-price guard (mirror of po_generate.totals_mismatches): the Schedule of Values
line values must sum to the §2.1 Contract Price; a mismatch fences to Review, never renders a contract.
"""
from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

_ONES = [
    "", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
    "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen",
    "eighteen", "nineteen",
]
_TENS = ["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]
# 1000^n scale names. Covers up to just under a quintillion — far beyond any subcontract.
_SCALES = ["", "thousand", "million", "billion", "trillion"]

_MAX_CENTS = 10 ** 15  # a $10-trillion ceiling; anything above is a data error, not a real contract.


class MoneyError(ValueError):
    """A money value is out of range or malformed for the contract money path."""


def _under_thousand(n: int) -> str:
    """0..999 → words (US style, no 'and'). '' for 0."""
    if n == 0:
        return ""
    if n < 20:
        return _ONES[n]
    if n < 100:
        tens, ones = divmod(n, 10)
        return _TENS[tens] + (f"-{_ONES[ones]}" if ones else "")
    hundreds, rest = divmod(n, 100)
    out = f"{_ONES[hundreds]} hundred"
    return f"{out} {_under_thousand(rest)}" if rest else out


def int_to_words(n: int) -> str:
    """A non-negative integer → US-English words. 0 → 'zero'."""
    if not isinstance(n, int) or isinstance(n, bool):
        raise MoneyError(f"int_to_words: expected a non-bool int, got {n!r}")
    if n < 0:
        raise MoneyError(f"int_to_words: negative value {n}")
    if n == 0:
        return "zero"
    # Split into 3-digit groups from the least-significant end.
    groups: list[int] = []
    while n > 0:
        n, rem = divmod(n, 1000)
        groups.append(rem)
    if len(groups) > len(_SCALES):
        raise MoneyError("int_to_words: value too large for the money path")
    parts: list[str] = []
    for scale_idx in range(len(groups) - 1, -1, -1):  # most-significant group first
        g = groups[scale_idx]
        if g == 0:
            continue
        words = _under_thousand(g)
        scale = _SCALES[scale_idx]
        parts.append(f"{words} {scale}".strip())
    return " ".join(parts)


def _cap_first(s: str) -> str:
    return s[:1].upper() + s[1:] if s else s


def cents_to_words(cents: int) -> str:
    """Integer cents → the spelled-out dollar amount, capitalized, for §2.1.

    e.g. 27401850 → 'Two hundred seventy-four thousand eighteen dollars and fifty cents'
         5300000  → 'Fifty-three thousand dollars'  (no cents clause when the fraction is .00)
         968432   → 'Nine thousand six hundred eighty-four dollars and thirty-two cents'
         100      → 'One dollar'
         1        → 'Zero dollars and one cent'
    """
    if not isinstance(cents, int) or isinstance(cents, bool):
        raise MoneyError(f"cents_to_words: expected a non-bool int, got {cents!r}")
    if cents < 0:
        raise MoneyError(f"cents_to_words: negative amount {cents}")
    if cents >= _MAX_CENTS:
        raise MoneyError(f"cents_to_words: amount {cents} exceeds the money-path ceiling")
    dollars, frac = divmod(cents, 100)
    dollar_word = int_to_words(dollars)
    dollar_unit = "dollar" if dollars == 1 else "dollars"
    out = f"{dollar_word} {dollar_unit}"
    if frac:
        cent_word = int_to_words(frac)
        cent_unit = "cent" if frac == 1 else "cents"
        out = f"{out} and {cent_word} {cent_unit}"
    return _cap_first(out)


def format_figure(cents: int) -> str:
    """Integer cents → the '$NN,NNN.NN' figure (thousands-separated, always 2 decimals)."""
    if not isinstance(cents, int) or isinstance(cents, bool) or cents < 0:
        raise MoneyError(f"format_figure: expected a non-negative non-bool int, got {cents!r}")
    dollars, frac = divmod(cents, 100)
    return f"${dollars:,}.{frac:02d}"


def contract_price_clause(cents: int, price_basis: str = "fixed") -> str:
    """The §2.1 amount clause: '<Words> (<$figure>)' for a fixed price, or 'NOT TO EXCEED <Words>
    (<$figure>)' for a not-to-exceed price. The single {{contract_price_clause}} token in the body —
    words + figure ALWAYS agree because both derive from the same integer cents."""
    if price_basis not in ("fixed", "not_to_exceed"):
        raise MoneyError(f"contract_price_clause: unknown price_basis {price_basis!r}")
    body = f"{cents_to_words(cents)} ({format_figure(cents)})"
    return f"NOT TO EXCEED {body}" if price_basis == "not_to_exceed" else body


def sov_extended_cents(qty: float, unit_price_cents: int) -> int:
    """A single SOV line's extended value = round(qty × unit_price_cents), ECMA half-up (never
    banker's round), mirroring po_generate._js_round for JS/Python HMAC agreement."""
    return int(math.floor(qty * unit_price_cents + 0.5))


def sov_mismatches(contract_price_cents: int, sov_lines: Sequence[Mapping[str, Any]]) -> list[str]:
    """RETURN (not raise) machine-readable mismatch strings when the SOV doesn't reconcile to the
    §2.1 Contract Price — mirror of po_generate.totals_mismatches. An empty list == clean. The caller
    (the render/generate) fences a non-empty result to the Review Queue and NEVER files a contract
    whose numbers don't re-derive. Each line's extended is server-recomputed (never client-trusted)."""
    problems: list[str] = []
    if not isinstance(contract_price_cents, int) or isinstance(contract_price_cents, bool) or contract_price_cents < 0:
        return [f"contract_price_cents must be a non-negative integer (got {contract_price_cents!r})"]
    total = 0
    for i, line in enumerate(sov_lines):
        qty = line.get("qty", 1)
        unit = line.get("unit_price_cents")
        stated = line.get("extended_cents")
        if unit is None:
            # a lump-sum line carries extended_cents directly (no unit price)
            recomputed = stated if isinstance(stated, int) and not isinstance(stated, bool) else None
            if recomputed is None:
                problems.append(f"sov line {i}: no unit_price_cents and no integer extended_cents")
                continue
        else:
            if not isinstance(unit, int) or isinstance(unit, bool) or unit < 0:
                problems.append(f"sov line {i}: unit_price_cents must be a non-negative integer (got {unit!r})")
                continue
            recomputed = sov_extended_cents(float(qty), unit)
            if isinstance(stated, int) and not isinstance(stated, bool) and stated != recomputed:
                problems.append(f"sov line {i}: extended_cents {stated} != recomputed {recomputed}")
        total += recomputed
    if not problems and total != contract_price_cents:
        problems.append(
            f"SOV total {total} != Contract Price {contract_price_cents} "
            f"(the Schedule of Values must sum to §2.1)"
        )
    return problems
