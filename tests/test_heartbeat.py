"""Unit tests for shared/heartbeat.py — the consolidated HeartbeatReporter.

The 8 ITS_Daemon_Health helpers were extracted here from portal_poll +
weekly_send_poll (P0). These tests exercise the moved logic directly: the
liveness touch, find-or-create row resolution (A1 self-provision), the per-cycle
update, the lifetime-monotonic Total Cycles counter (ARCH-3), cache
invalidation on a 404, and the heartbeat-never-blocks failure isolation.

Real `state_io` / `circuit_breaker` / `sheet_ids` are used (tmp-path state file);
only the Smartsheet boundary and `error_log.log` are mocked.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from shared import sheet_ids
from shared.heartbeat import HeartbeatReporter
from shared.smartsheet_client import SmartsheetError, SmartsheetNotFoundError

COLS = sheet_ids.DAEMON_HEALTH_COLUMNS
DAEMON = "safety_reports.test_daemon"


@pytest.fixture
def reporter(tmp_path: Path) -> HeartbeatReporter:
    return HeartbeatReporter(
        script_name=DAEMON,
        daemon_name=DAEMON,
        workstream="safety_reports",
        liveness_path=tmp_path / "live.txt",
        interval_seconds=60,
        source_id="unit-test-source",
        row_state_path=tmp_path / "heartbeat_row_ids.json",
    )


@pytest.fixture
def ss(mocker):
    """Mock the Smartsheet boundary used by shared.heartbeat."""
    return {
        "find": mocker.patch("shared.heartbeat.smartsheet_client.find_row_by_primary"),
        "add": mocker.patch("shared.heartbeat.smartsheet_client.add_row_by_id"),
        "update": mocker.patch(
            "shared.heartbeat.smartsheet_client.update_row_cells_by_id",
            return_value=None,
        ),
        "log": mocker.patch("shared.heartbeat.error_log.log", return_value=None),
    }


# ---- liveness ------------------------------------------------------------


def test_write_liveness_writes_iso_utc_timestamp(reporter: HeartbeatReporter):
    reporter.write_liveness()
    text = reporter.liveness_path.read_text()
    parsed = datetime.fromisoformat(text)  # raises if not a valid ISO timestamp
    assert parsed.tzinfo is not None  # UTC-aware


# ---- write_row: find-or-create + persistence -----------------------------


def test_write_row_finds_existing_row_and_persists_state(reporter, ss):
    ss["find"].return_value = {"_row_id": 42}

    reporter.write_row(status="OK", items_processed=3)

    ss["find"].assert_called_once()
    ss["add"].assert_not_called()
    # update hit row 42 with the cycle cells
    sheet_arg, row_arg, cells = ss["update"].call_args.args
    assert sheet_arg == sheet_ids.SHEET_DAEMON_HEALTH
    assert row_arg == 42
    assert cells[COLS["last_cycle_status"]] == "OK"
    assert cells[COLS["last_cycle_items_processed"]] == 3
    assert cells[COLS["total_cycles"]] == 1
    # state file now caches row 42 with total_cycles=1
    state = json.loads(reporter.row_state_path.read_text())
    assert state[DAEMON] == {"row_id": 42, "total_cycles": 1}


def test_write_row_uses_cache_and_increments_total_monotonically(reporter, ss):
    # Pre-seed the shared state file (ARCH-2/ARCH-3): row 42, 5 prior cycles.
    reporter.row_state_path.write_text(
        json.dumps({DAEMON: {"row_id": 42, "total_cycles": 5}})
    )

    reporter.write_row(status="OK", items_processed=0)

    ss["find"].assert_not_called()  # cache hit — no lookup
    _, row_arg, cells = ss["update"].call_args.args
    assert row_arg == 42
    assert cells[COLS["total_cycles"]] == 6  # lifetime monotonic
    state = json.loads(reporter.row_state_path.read_text())
    assert state[DAEMON]["total_cycles"] == 6


def test_write_row_self_provisions_when_no_row_exists(reporter, ss):
    ss["find"].side_effect = [None, None]  # initial lookup + post-create re-find
    ss["add"].return_value = 99

    reporter.write_row(status="OK", items_processed=1)

    ss["add"].assert_called_once()
    # the self-provision payload carries the registration metadata
    _, payload = ss["add"].call_args.args
    assert payload[COLS["daemon_name"]] == DAEMON
    assert payload[COLS["enabled"]] is True
    assert payload[COLS["source_id"]] == "unit-test-source"
    # then the per-cycle update lands on the newly-created row
    _, row_arg, _ = ss["update"].call_args.args
    assert row_arg == 99
    assert json.loads(reporter.row_state_path.read_text())[DAEMON]["row_id"] == 99


def test_write_row_invalidates_cache_on_404(reporter, ss):
    reporter.row_state_path.write_text(
        json.dumps({DAEMON: {"row_id": 42, "total_cycles": 5}})
    )
    ss["update"].side_effect = SmartsheetNotFoundError("row gone")

    reporter.write_row(status="OK", items_processed=0)  # must not raise

    # cache entry evicted so next cycle re-resolves
    assert DAEMON not in json.loads(reporter.row_state_path.read_text())
    assert any(
        call.kwargs.get("error_code") == "daemon_health_write_failed"
        for call in ss["log"].call_args_list
    )


def test_write_row_logs_and_skips_when_self_provision_fails(reporter, ss):
    ss["find"].side_effect = [None]  # initial lookup misses
    ss["add"].side_effect = SmartsheetError("create blew up")

    reporter.write_row(status="OK", items_processed=0)  # must not raise

    ss["update"].assert_not_called()  # no row to write to
    assert any(
        call.kwargs.get("error_code") == "daemon_health_write_failed"
        for call in ss["log"].call_args_list
    )


def test_write_row_never_raises_on_unexpected_smartsheet_error(reporter, ss):
    ss["find"].return_value = {"_row_id": 42}
    ss["update"].side_effect = RuntimeError("transport exploded")

    # heartbeat-never-blocks: the daemon's primary work must not be interrupted
    reporter.write_row(status="OK", items_processed=0)

    assert any(
        call.kwargs.get("error_code") == "daemon_health_write_failed"
        for call in ss["log"].call_args_list
    )


def test_write_row_includes_optional_error_fields_when_present(reporter, ss):
    ss["find"].return_value = {"_row_id": 7}

    reporter.write_row(
        status="ERROR",
        items_processed=0,
        error_summary="boom",
        correlation_id="corr-1",
        notes="held",
    )

    _, _, cells = ss["update"].call_args.args
    assert cells[COLS["last_error_summary"]] == "boom"
    assert cells[COLS["last_error_correlation_id"]] == "corr-1"
    assert cells[COLS["notes"]] == "held"


def test_write_row_defaults_daemon_name_to_reporter(reporter, ss):
    ss["find"].return_value = {"_row_id": 1}

    reporter.write_row(status="OK", items_processed=0)  # no daemon_name passed

    # find lookup used the reporter's own daemon_name as the primary key value
    assert ss["find"].call_args.args[2] == DAEMON
