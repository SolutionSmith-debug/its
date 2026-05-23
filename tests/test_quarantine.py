"""Tests for shared/quarantine.py — covers is_allowlisted AND
log_quarantined_message (wired to ITS_Quarantine 2026-05-18).

Run with: pytest -q tests/test_quarantine.py
"""
from __future__ import annotations

import pytest

from shared import quarantine, sheet_ids
from shared.quarantine import (
    VALID_WORKSTREAMS,
    QuarantineReason,
    is_allowlisted,
    log_quarantined_message,
)


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


# ---- log_quarantined_message --------------------------------------------


@pytest.fixture
def add_rows_mock(mocker):
    return mocker.patch(
        "shared.quarantine.smartsheet_client.add_rows",
        return_value=[5050],
    )


def test_valid_workstreams_match_live_picklist():
    # The live ITS_Quarantine.Workstream picklist (verified 2026-05-18)
    # uses `other` as the catch-all, NOT `global` like ITS_Review_Queue.
    expected = {
        "safety_reports", "po_materials", "subcontracts",
        "email_triage", "ai_employee", "other",
    }
    assert set(VALID_WORKSTREAMS) == expected


def test_log_quarantined_message_writes_correct_payload(add_rows_mock):
    row_id = log_quarantined_message(
        sender="suspicious@evil.example.com",
        subject="URGENT: invoice attached",
        timestamp="2026-05-18T12:34:56+00:00",
        summary="Generic phishing pretext — invoice/payment lure, no allowlist match.",
        workstream="safety_reports",
    )

    assert row_id == 5050
    add_rows_mock.assert_called_once()
    sheet_id, rows = add_rows_mock.call_args.args
    assert sheet_id == sheet_ids.SHEET_QUARANTINE
    assert len(rows) == 1

    row = rows[0]
    assert row["Quarantined Message"] == "quarantined: suspicious@evil.example.com"
    assert row["Received At"] == "2026-05-18T12:34:56+00:00"
    assert row["Sender"] == "suspicious@evil.example.com"
    assert row["Subject"] == "URGENT: invoice attached"
    assert row["Summary"] == (
        "Generic phishing pretext — invoice/payment lure, no allowlist match."
    )
    assert row["Workstream"] == "safety_reports"


@pytest.mark.parametrize("ws", sorted(VALID_WORKSTREAMS))
def test_log_quarantined_message_accepts_all_workstreams(add_rows_mock, ws):
    log_quarantined_message(
        sender="a@b.com", subject="s", timestamp="t", summary="x", workstream=ws,
    )
    assert add_rows_mock.call_args.args[1][0]["Workstream"] == ws


def test_log_quarantined_message_rejects_invalid_workstream(add_rows_mock):
    with pytest.raises(ValueError, match="not in"):
        log_quarantined_message(
            sender="a@b.com", subject="s", timestamp="t", summary="x",
            workstream="not_a_real_workstream",
        )
    add_rows_mock.assert_not_called()


def test_log_quarantined_message_rejects_global_workstream(add_rows_mock):
    # ITS_Review_Queue accepts "global"; ITS_Quarantine does NOT — picklist
    # differs. Lock this in so a copy-paste from review_queue doesn't break
    # quarantine writes.
    with pytest.raises(ValueError, match="not in"):
        log_quarantined_message(
            sender="a@b.com", subject="s", timestamp="t", summary="x",
            workstream="global",
        )


def test_log_quarantined_message_propagates_smartsheet_errors(add_rows_mock):
    # Failure-isolation note in the docstring is explicit: silent failure
    # of quarantine logging is a security-relevant audit-record loss.
    # Errors must propagate to the caller (quarantine-walk script) so it
    # can fire CRITICAL via error_log → triple-fire.
    from shared.smartsheet_client import SmartsheetError
    add_rows_mock.side_effect = SmartsheetError("HTTP 503: unavailable")

    with pytest.raises(SmartsheetError, match="503"):
        log_quarantined_message(
            sender="a@b.com", subject="s", timestamp="t", summary="x",
            workstream="safety_reports",
        )


def test_quarantined_message_label_format_uses_sender(add_rows_mock):
    log_quarantined_message(
        sender="phisher@bad.example.com", subject="x", timestamp="t",
        summary="x", workstream="email_triage",
    )
    label = add_rows_mock.call_args.args[1][0]["Quarantined Message"]
    assert label.startswith("quarantined:")
    assert "phisher@bad.example.com" in label


def test_quarantine_module_exports_valid_workstreams():
    # Public re-export check — workstream callers shouldn't have to grep
    # the source for the right constant name.
    assert hasattr(quarantine, "VALID_WORKSTREAMS")
    assert isinstance(quarantine.VALID_WORKSTREAMS, frozenset)


def test_log_quarantined_message_writes_reason_into_notes(add_rows_mock):
    """The Phase 1.4 reason taxonomy lands in Notes as `[reason: <code>]`.

    Live ITS_Quarantine has no dedicated Reason column; graceful-degrade
    into Notes preserves the audit trail without blocking on operator UI.
    """
    log_quarantined_message(
        sender="a@b.com", subject="s", timestamp="t", summary="x",
        workstream="safety_reports",
        reason=QuarantineReason.HEADER_FORGERY_SUSPECTED,
    )
    row = add_rows_mock.call_args.args[1][0]
    assert row["Notes"] == "[reason: header_forgery_suspected]"


def test_log_quarantined_message_omits_notes_when_no_reason(add_rows_mock):
    """Backward compat: legacy callers without reason → no Notes cell written."""
    log_quarantined_message(
        sender="a@b.com", subject="s", timestamp="t", summary="x",
        workstream="safety_reports",
    )
    row = add_rows_mock.call_args.args[1][0]
    assert "Notes" not in row


def test_quarantine_reason_values_documented():
    """Pin the disposition code surface so a rename surfaces in tests."""
    expected = {
        "unknown_sender", "sender_disabled", "workstream_out_of_scope",
        "header_forgery_suspected", "legacy_allowlist_miss",
    }
    assert {r.value for r in QuarantineReason} == expected
