"""Tests for shared/header_forgery.py — SPF/DKIM/DMARC verdict + Return-Path.

Run with: pytest -q tests/test_header_forgery.py
"""
from __future__ import annotations

from shared.header_forgery import HeaderVerdict, analyze


def _hdr(name: str, value: str) -> dict[str, str]:
    return {"name": name, "value": value}


# Reference Authentication-Results values built from real M365 / Gmail
# samples (sender/domain anonymized). Pinned here so verdict-rule changes
# always have a concrete worked example.

AUTH_ALL_PASS = (
    "evergreenmirror.mail.protection.outlook.com; "
    "spf=pass (sender IP is 40.107.244.100) smtp.mailfrom=evergreenmirror.com; "
    "dkim=pass (signature was verified) header.d=evergreenmirror.com; "
    "dmarc=pass action=none header.from=evergreenmirror.com"
)
AUTH_SPF_FAIL = (
    "evergreenmirror.mail.protection.outlook.com; "
    "spf=fail (sender IP is 1.2.3.4); dkim=pass; dmarc=fail (p=none)"
)
AUTH_DKIM_FAIL = (
    "evergreenmirror.mail.protection.outlook.com; "
    "spf=pass; dkim=fail (body hash did not verify); dmarc=fail (p=none)"
)
AUTH_DMARC_FAIL_REJECT = (
    "evergreenmirror.mail.protection.outlook.com; "
    "spf=pass; dkim=pass; dmarc=fail (p=reject)"
)
AUTH_DMARC_FAIL_NONE = (
    "evergreenmirror.mail.protection.outlook.com; "
    "spf=pass; dkim=pass; dmarc=fail (p=none)"
)
AUTH_DMARC_FAIL_QUARANTINE = (
    "evergreenmirror.mail.protection.outlook.com; "
    "spf=pass; dkim=pass; dmarc=fail (p=quarantine)"
)
AUTH_SPF_SOFTFAIL = (
    "evergreenmirror.mail.protection.outlook.com; "
    "spf=softfail; dkim=pass; dmarc=pass"
)


def test_all_pass_returns_pass_verdict():
    headers = [
        _hdr("Authentication-Results", AUTH_ALL_PASS),
        _hdr("Return-Path", "<seths@evergreenmirror.com>"),
        _hdr("From", "Seth Smith <seths@evergreenmirror.com>"),
    ]
    analysis = analyze(headers)
    assert analysis.verdict is HeaderVerdict.PASS
    assert analysis.spf == "pass"
    assert analysis.dkim == "pass"
    assert analysis.dmarc == "pass"
    assert analysis.return_path_mismatch is False


def test_spf_fail_is_hard_fail():
    analysis = analyze([_hdr("Authentication-Results", AUTH_SPF_FAIL)])
    assert analysis.verdict is HeaderVerdict.HARD_FAIL
    assert analysis.spf == "fail"


def test_dkim_fail_is_hard_fail():
    analysis = analyze([_hdr("Authentication-Results", AUTH_DKIM_FAIL)])
    assert analysis.verdict is HeaderVerdict.HARD_FAIL
    assert analysis.dkim == "fail"


def test_dmarc_fail_with_p_reject_is_hard_fail():
    analysis = analyze([_hdr("Authentication-Results", AUTH_DMARC_FAIL_REJECT)])
    assert analysis.verdict is HeaderVerdict.HARD_FAIL
    assert analysis.dmarc == "fail"


def test_dmarc_fail_with_p_none_is_soft_fail():
    analysis = analyze([_hdr("Authentication-Results", AUTH_DMARC_FAIL_NONE)])
    assert analysis.verdict is HeaderVerdict.SOFT_FAIL
    assert analysis.dmarc == "fail"


def test_dmarc_fail_with_p_quarantine_is_soft_fail():
    analysis = analyze([_hdr("Authentication-Results", AUTH_DMARC_FAIL_QUARANTINE)])
    assert analysis.verdict is HeaderVerdict.SOFT_FAIL


def test_spf_softfail_is_soft_fail():
    analysis = analyze([_hdr("Authentication-Results", AUTH_SPF_SOFTFAIL)])
    assert analysis.verdict is HeaderVerdict.SOFT_FAIL
    assert analysis.spf == "softfail"


def test_missing_authentication_results_is_soft_fail():
    """No Authentication-Results header — caution wins, verdict is SOFT_FAIL."""
    analysis = analyze([_hdr("From", "x <a@b.c>")])
    assert analysis.verdict is HeaderVerdict.SOFT_FAIL
    assert analysis.spf == "missing"
    assert analysis.dkim == "missing"
    assert analysis.dmarc == "missing"


def test_multiple_authentication_results_uses_closest_to_receiver():
    """Graph emits headers in the order MTAs prepended them; first wins."""
    headers = [
        _hdr("Authentication-Results", AUTH_ALL_PASS),  # closest to receiver
        _hdr("Authentication-Results", AUTH_SPF_FAIL),  # older hop
    ]
    analysis = analyze(headers)
    assert analysis.verdict is HeaderVerdict.PASS


def test_return_path_matches_from_no_mismatch():
    headers = [
        _hdr("Authentication-Results", AUTH_ALL_PASS),
        _hdr("Return-Path", "<seths@evergreenmirror.com>"),
        _hdr("From", "Seth Smith <seths@evergreenmirror.com>"),
    ]
    analysis = analyze(headers)
    assert analysis.return_path_mismatch is False


def test_return_path_differs_from_from_sets_mismatch():
    headers = [
        _hdr("Authentication-Results", AUTH_ALL_PASS),
        _hdr("Return-Path", "<bounce@bounces.list.example>"),
        _hdr("From", "List Owner <list@example.com>"),
    ]
    analysis = analyze(headers)
    assert analysis.return_path_mismatch is True
    assert analysis.return_path_domain == "bounces.list.example"
    assert analysis.from_domain == "example.com"


def test_empty_return_path_no_mismatch():
    """A Return-Path with no addressable value (bounce-disabled) is NOT a mismatch."""
    headers = [
        _hdr("Authentication-Results", AUTH_ALL_PASS),
        _hdr("Return-Path", "<>"),
        _hdr("From", "Seth Smith <seths@evergreenmirror.com>"),
    ]
    analysis = analyze(headers)
    assert analysis.return_path_mismatch is False


def test_real_world_m365_sample_pass():
    """Worked example from a real M365 inbound message (anonymized).

    Verifies the parser handles the typical M365 verbose
    Authentication-Results form without choking.
    """
    auth = (
        "evergreenmirror.mail.protection.outlook.com (4.7.2); "
        "spf=pass (sender IP is 40.107.244.100) "
        "smtp.helo=NAM12-DM6-obe.outbound.protection.outlook.com "
        "smtp.mailfrom=evergreenmirror.com; "
        "dkim=pass (signature was verified) "
        "header.d=evergreenmirror.com header.s=selector1; "
        "dmarc=pass (p=none sp=none pct=100) action=none "
        "header.from=evergreenmirror.com; compauth=pass reason=100"
    )
    analysis = analyze([_hdr("Authentication-Results", auth)])
    assert analysis.verdict is HeaderVerdict.PASS


def test_real_world_gmail_relay_via_office365_multihop():
    """Multi-hop case: Gmail-originated message relayed through O365.

    Two Authentication-Results headers; the receiving server's verdict
    (first in the list) wins.
    """
    auth_o365 = (
        "evergreenmirror.mail.protection.outlook.com; "
        "spf=pass smtp.mailfrom=gmail.com; "
        "dkim=pass header.d=gmail.com; dmarc=pass"
    )
    auth_gmail = (
        "mx.google.com; "
        "spf=pass smtp.mailfrom=gmail.com; "
        "dkim=pass header.d=gmail.com; dmarc=pass"
    )
    headers = [
        _hdr("Authentication-Results", auth_o365),
        _hdr("Authentication-Results", auth_gmail),
    ]
    analysis = analyze(headers)
    assert analysis.verdict is HeaderVerdict.PASS
    # Raw Authentication-Results carried for diagnostics is the O365 one.
    assert analysis.raw_authentication_results == auth_o365
