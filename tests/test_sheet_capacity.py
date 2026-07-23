"""Tests for shared/sheet_capacity.py — the find-or-create sheet-count guard (A1)."""
from __future__ import annotations

from shared import defaults, sheet_capacity
from shared.smartsheet_client import SmartsheetNotFoundError


def test_headroom_ok_below_threshold(mocker) -> None:
    mocker.patch.object(
        sheet_capacity.smartsheet_client, "get_setting",
        side_effect=SmartsheetNotFoundError("no config row"),
    )
    h = sheet_capacity.check_create_headroom(1, now_count=10)
    assert h.ok is True
    assert h.current == 10
    assert h.ceiling == defaults.SHEET_COUNT_CEILING
    assert h.margin == defaults.SHEET_COUNT_MARGIN


def test_headroom_breach_near_ceiling(mocker) -> None:
    mocker.patch.object(
        sheet_capacity.smartsheet_client, "get_setting",
        side_effect=SmartsheetNotFoundError("no config row"),
    )
    # current + 1 crosses (ceiling - margin): one more would breach.
    at_edge = defaults.SHEET_COUNT_CEILING - defaults.SHEET_COUNT_MARGIN
    assert sheet_capacity.check_create_headroom(1, now_count=at_edge).ok is False
    # one below the edge is still ok.
    assert sheet_capacity.check_create_headroom(1, now_count=at_edge - 1).ok is True


def test_headroom_fail_open_on_read_error(mocker) -> None:
    mocker.patch.object(
        sheet_capacity.smartsheet_client, "get_setting",
        side_effect=SmartsheetNotFoundError("no config row"),
    )
    mocker.patch.object(
        sheet_capacity.smartsheet_client, "count_workspace_sheets",
        side_effect=RuntimeError("api down"),
    )
    h = sheet_capacity.check_create_headroom(1)  # no now_count → live read → fails
    assert h.ok is True  # fail-open: never block a create
    assert h.current == -1
    assert "fail-open" in h.note


def test_config_override(mocker) -> None:
    def fake_setting(key: str, *, workstream: str) -> str | None:
        return {
            sheet_capacity.CFG_CEILING: "100",
            sheet_capacity.CFG_MARGIN: "5",
        }.get(key)

    mocker.patch.object(sheet_capacity.smartsheet_client, "get_setting", side_effect=fake_setting)
    # ceiling-margin = 95; now_count=94 → 95 <= 95 ok; now_count=95 → 96 > 95 breach.
    ok = sheet_capacity.check_create_headroom(1, now_count=94)
    assert ok.ok is True and ok.ceiling == 100 and ok.margin == 5
    assert sheet_capacity.check_create_headroom(1, now_count=95).ok is False


def test_route_breach_enqueues_review_queue(mocker) -> None:
    add = mocker.patch.object(sheet_capacity.review_queue, "add")
    h = sheet_capacity.Headroom(ok=False, current=1460, ceiling=1500, margin=50)
    sheet_capacity.route_breach_to_review_queue(6820552519247748, h, workstream="global")
    add.assert_called_once()
    kwargs = add.call_args.kwargs
    assert kwargs["workstream"] == "global"
    assert kwargs["payload"]["current"] == 1460
