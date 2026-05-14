"""Tests for shared/quarantine.py — covers is_allowlisted (the other helper is a stub).

Run with: pytest -q tests/test_quarantine.py
"""
from __future__ import annotations

from shared.quarantine import is_allowlisted


def test_exact_address_match():
    assert is_allowlisted("user@evergreenmirror.com", ["user@evergreenmirror.com"])


def test_domain_match():
    assert is_allowlisted("anyone@evergreenmirror.com", ["@evergreenmirror.com"])


def test_no_match():
    assert not is_allowlisted("attacker@evil.com", ["@evergreenmirror.com"])


def test_case_insensitive_sender():
    assert is_allowlisted("USER@EVERGREENMIRROR.COM", ["@evergreenmirror.com"])


def test_case_insensitive_allowlist_entry():
    assert is_allowlisted("user@evergreenmirror.com", ["@EVERGREENMIRROR.COM"])


def test_empty_allowlist_rejects_all():
    assert not is_allowlisted("user@evergreenmirror.com", [])


def test_whitespace_stripped_from_sender():
    assert is_allowlisted(" user@evergreenmirror.com ", ["user@evergreenmirror.com"])


def test_whitespace_stripped_from_allowlist():
    assert is_allowlisted("user@evergreenmirror.com", [" user@evergreenmirror.com "])


def test_empty_string_entry_ignored():
    # Defensive: a blank entry in the allowlist must not match anything.
    assert not is_allowlisted("user@evergreenmirror.com", ["", "  "])


def test_mixed_exact_and_domain_entries():
    allowlist = [
        "@evergreenmirror.com",
        "specific@external.com",
    ]
    assert is_allowlisted("anyone@evergreenmirror.com", allowlist)
    assert is_allowlisted("specific@external.com", allowlist)
    assert not is_allowlisted("other@external.com", allowlist)


def test_partial_domain_does_not_match():
    # "@mirror.com" must not match "@evergreenmirror.com" — exact suffix only.
    assert not is_allowlisted("user@mirror.com", ["@evergreenmirror.com"])
    assert not is_allowlisted("user@evilevergreenmirror.com", ["@evergreenmirror.com"])
