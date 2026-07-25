"""Unit tests for shared/approval_verification.py (F22).

The Smartsheet cell-history boundary (`smartsheet_client.get_cell_history`)
is mocked; these tests exercise the verdict logic + fail-closed posture.
The live-API counterpart is `tests/test_approval_verification_integration.py`
(Op Stds v14 §30, `-m integration`).
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from shared import approval_verification
from shared.approval_verification import VerdictReason, verify_approval
from shared.smartsheet_client import CellHistoryEvent, SmartsheetError

SHEET = 7264675665235844
ROW = 4242
COLUMN = "Approved for Send"
ALLOW = frozenset({"daniels@evergreenmirror.com", "seths@evergreenmirror.com"})


def _event(
    *,
    value: Any = True,
    email: str | None = "seths@evergreenmirror.com",
    when: datetime | None = None,
    user_id: int | None = None,
    name: str | None = "Seth Smith",
) -> CellHistoryEvent:
    return CellHistoryEvent(
        value=value,
        actor_email=email,
        actor_name=name,
        actor_user_id=user_id,
        modified_at=when or datetime(2026, 5, 29, 12, 0, tzinfo=UTC),
    )


def _patch_history(mocker, events: list[CellHistoryEvent]):
    return mocker.patch(
        "shared.approval_verification.smartsheet_client.get_cell_history",
        return_value=events,
    )


# ---- happy path ----------------------------------------------------------


def test_verified_when_authorized_actor_set_approved(mocker):
    _patch_history(mocker, [_event(value=True, email="daniels@evergreenmirror.com")])
    v = verify_approval(SHEET, ROW, COLUMN, authorized_actors=ALLOW)
    assert v.verified is True
    assert v.reason is VerdictReason.AUTHORIZED
    assert v.actor == "daniels@evergreenmirror.com"
    assert v.modified_at is not None


def test_case_insensitive_email_match(mocker):
    """Config / history casing differences must not fail-close a real approver."""
    _patch_history(mocker, [_event(email="daniels@evergreenmirror.com")])
    allow = frozenset({"Daniels@EverGreenMirror.Com"})
    v = verify_approval(SHEET, ROW, COLUMN, authorized_actors=allow)
    assert v.verified is True


# ---- the unverified reasons (all fail-closed) ---------------------------


def test_unverified_when_unauthorized_actor(mocker):
    _patch_history(mocker, [_event(value=True, email="attacker@evil.com")])
    v = verify_approval(SHEET, ROW, COLUMN, authorized_actors=ALLOW)
    assert v.verified is False
    assert v.reason is VerdictReason.UNAUTHORIZED_ACTOR
    assert v.actor == "attacker@evil.com"


def test_unverified_when_history_read_raises(mocker):
    """Any read failure → UNVERIFIED (fail-closed); the function never raises."""
    mocker.patch(
        "shared.approval_verification.smartsheet_client.get_cell_history",
        side_effect=SmartsheetError("HTTP 500"),
    )
    v = verify_approval(SHEET, ROW, COLUMN, authorized_actors=ALLOW)
    assert v.verified is False
    assert v.reason is VerdictReason.HISTORY_READ_FAILED
    assert "HTTP 500" in v.detail


def test_unverified_when_history_read_raises_keyerror(mocker):
    """A renamed/unknown column raises KeyError in the wrapper → fail-closed."""
    mocker.patch(
        "shared.approval_verification.smartsheet_client.get_cell_history",
        side_effect=KeyError("Approved for Send"),
    )
    v = verify_approval(SHEET, ROW, COLUMN, authorized_actors=ALLOW)
    assert v.verified is False
    assert v.reason is VerdictReason.HISTORY_READ_FAILED


def test_unverified_when_no_history(mocker):
    _patch_history(mocker, [])
    v = verify_approval(SHEET, ROW, COLUMN, authorized_actors=ALLOW)
    assert v.verified is False
    assert v.reason is VerdictReason.NO_HISTORY


def test_unverified_when_not_currently_approved(mocker):
    """Latest value is unchecked → cell un-approved since the poller's filter."""
    _patch_history(
        mocker,
        [_event(value=False, email="seths@evergreenmirror.com")],
    )
    v = verify_approval(SHEET, ROW, COLUMN, authorized_actors=ALLOW)
    assert v.verified is False
    assert v.reason is VerdictReason.NOT_CURRENTLY_APPROVED


def test_unverified_when_actor_email_missing(mocker):
    """An approving event with no actor email cannot be attributed → blocked."""
    _patch_history(mocker, [_event(value=True, email=None)])
    v = verify_approval(SHEET, ROW, COLUMN, authorized_actors=ALLOW)
    assert v.verified is False
    assert v.reason is VerdictReason.UNAUTHORIZED_ACTOR
    assert v.actor is None


def test_empty_allowlist_blocks_without_reading_history(mocker):
    """Empty authorized set → fail-closed, and history is not even read."""
    spy = _patch_history(mocker, [_event()])
    v = verify_approval(SHEET, ROW, COLUMN, authorized_actors=frozenset())
    assert v.verified is False
    assert v.reason is VerdictReason.EMPTY_ALLOWLIST
    spy.assert_not_called()


# ---- ordering robustness -------------------------------------------------


def test_latest_event_chosen_by_timestamp_not_list_position(mocker):
    """The deciding event is the newest by timestamp, regardless of list order.

    Older authorized approval is listed FIRST; a newer event by an
    unauthorized actor must win → UNVERIFIED. Proves we sort on modified_at,
    not trust API position.
    """
    older_authorized = _event(
        value=True,
        email="daniels@evergreenmirror.com",
        when=datetime(2026, 5, 20, 9, 0, tzinfo=UTC),
    )
    newer_unauthorized = _event(
        value=True,
        email="attacker@evil.com",
        when=datetime(2026, 5, 28, 9, 0, tzinfo=UTC),
    )
    _patch_history(mocker, [older_authorized, newer_unauthorized])
    v = verify_approval(SHEET, ROW, COLUMN, authorized_actors=ALLOW)
    assert v.verified is False
    assert v.reason is VerdictReason.UNAUTHORIZED_ACTOR
    assert v.actor == "attacker@evil.com"


def test_actor_user_id_carried_into_verdict(mocker):
    """Opportunistic user-id (None in prod) is threaded through for forensics."""
    _patch_history(
        mocker,
        [_event(email="seths@evergreenmirror.com", user_id=12345)],
    )
    v = verify_approval(SHEET, ROW, COLUMN, authorized_actors=ALLOW)
    assert v.actor_user_id == 12345


def test_latest_event_falls_back_to_first_when_no_timestamps(mocker):
    """When NO event carries a timestamp, _latest_event uses API order (events[0]).

    Exercises the untimestamped-fallback branch — defensive guard against an
    API that omits all timestamps; the live API always populates them.
    """
    events = [
        CellHistoryEvent(
            value=True,
            actor_email="daniels@evergreenmirror.com",
            actor_name="Daniel",
            actor_user_id=None,
            modified_at=None,
        ),
        CellHistoryEvent(
            value=True,
            actor_email="attacker@evil.com",
            actor_name="X",
            actor_user_id=None,
            modified_at=None,
        ),
    ]
    mocker.patch(
        "shared.approval_verification.smartsheet_client.get_cell_history",
        return_value=events,
    )
    v = verify_approval(SHEET, ROW, COLUMN, authorized_actors=ALLOW)
    # events[0] (the authorized actor) is the deciding event; None ts tolerated.
    assert v.verified is True
    assert v.actor == "daniels@evergreenmirror.com"
    assert v.modified_at is None


# ---- parse_authorized_actors --------------------------------------------


def test_parse_authorized_actors_basic():
    raw = "daniels@evergreenmirror.com,seths@evergreenmirror.com"
    assert approval_verification.parse_authorized_actors(raw) == frozenset(
        {"daniels@evergreenmirror.com", "seths@evergreenmirror.com"}
    )


def test_parse_authorized_actors_normalizes_and_drops_blanks():
    raw = "  Daniels@EvergreenMirror.com , ,SETHS@evergreenmirror.com,"
    assert approval_verification.parse_authorized_actors(raw) == frozenset(
        {"daniels@evergreenmirror.com", "seths@evergreenmirror.com"}
    )


@pytest.mark.parametrize("raw", [None, "", "   ", ",,,"])
def test_parse_authorized_actors_empty(raw):
    assert approval_verification.parse_authorized_actors(raw) == frozenset()
