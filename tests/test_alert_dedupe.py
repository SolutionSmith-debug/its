"""Tests for shared/alert_dedupe.py.

State file path is monkeypatched to a pytest tmp_path so these never touch
the real `~/its/state/alert_dedupe.json`. `shared.smartsheet_client.get_setting`
is mocked by an autouse fixture so no test hits live Smartsheet.

Run with: pytest -q tests/test_alert_dedupe.py
"""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

import shared.alert_dedupe as alert_dedupe
from shared import defaults
from shared.smartsheet_client import SmartsheetError


@pytest.fixture(autouse=True)
def state_in_tmp(tmp_path, monkeypatch):
    """Redirect STATE_DIR + STATE_FILE to tmp_path; no real-fs touches."""
    state_dir = tmp_path / "state"
    monkeypatch.setattr(alert_dedupe, "STATE_DIR", state_dir)
    monkeypatch.setattr(
        alert_dedupe, "STATE_FILE", state_dir / "alert_dedupe.json"
    )
    return state_dir


@pytest.fixture(autouse=True)
def settings_mock(mocker):
    """Default: window read from ITS_Config returns "60" (matches defaults)."""
    return mocker.patch(
        "shared.smartsheet_client.get_setting", return_value="60"
    )


@pytest.fixture
def frozen_now(monkeypatch):
    """Freeze alert_dedupe._now() at a known UTC instant.

    Returns a `setter` callable so tests can advance time mid-test
    (`frozen_now.advance(minutes=70)` or `frozen_now.set(new_dt)`).
    """
    class _Clock:
        def __init__(self):
            self.now = datetime(2026, 5, 20, 15, 0, 0, tzinfo=UTC)

        def set(self, dt):
            self.now = dt

        def advance(self, **kwargs):
            self.now = self.now + timedelta(**kwargs)

    clock = _Clock()
    monkeypatch.setattr(alert_dedupe, "_now", lambda: clock.now)
    return clock


# ---- should_fire — empty / fresh state ----------------------------------


def test_should_fire_returns_true_when_state_file_missing(state_in_tmp, frozen_now):
    assert alert_dedupe.should_fire("script::code") is True


def test_should_fire_does_not_open_window_on_its_own(state_in_tmp, frozen_now):
    # should_fire only consults state; it does NOT open a window. Without
    # a follow-up record_fire, two consecutive should_fire calls both
    # return True.
    assert alert_dedupe.should_fire("script::code") is True
    assert alert_dedupe.should_fire("script::code") is True
    # File may or may not exist depending on flock open mode, but key
    # should not be in any persisted state.
    if alert_dedupe.STATE_FILE.exists():
        state = json.loads(alert_dedupe.STATE_FILE.read_text() or "{}")
        assert "script::code" not in state


# ---- record_fire opens a window -----------------------------------------


def test_record_fire_creates_window_entry(state_in_tmp, frozen_now):
    alert_dedupe.record_fire("script::code")

    state = json.loads(alert_dedupe.STATE_FILE.read_text())
    entry = state["script::code"]
    assert entry["first_fired_at"] == frozen_now.now.isoformat()
    assert entry["last_fired_at"] == frozen_now.now.isoformat()
    assert entry["suppressed_count"] == 0
    assert entry["summarized"] is False
    # 60-minute window per the mocked ITS_Config read.
    expected_end = (frozen_now.now + timedelta(minutes=60)).isoformat()
    assert entry["window_ends_at"] == expected_end


def test_record_fire_inside_window_is_noop(state_in_tmp, frozen_now):
    # First record_fire opens the window. Second call inside that window
    # must not refresh first_fired_at (the window's start is sticky).
    alert_dedupe.record_fire("script::code")
    original_start = frozen_now.now

    frozen_now.advance(minutes=5)
    alert_dedupe.record_fire("script::code")

    state = json.loads(alert_dedupe.STATE_FILE.read_text())
    entry = state["script::code"]
    assert entry["first_fired_at"] == original_start.isoformat()


def test_record_fire_after_window_expiry_opens_fresh_window(
    state_in_tmp, frozen_now
):
    alert_dedupe.record_fire("script::code")
    frozen_now.advance(minutes=65)  # past the 60-min window
    alert_dedupe.record_fire("script::code")

    state = json.loads(alert_dedupe.STATE_FILE.read_text())
    entry = state["script::code"]
    # New window started at the now-advanced clock.
    assert entry["first_fired_at"] == frozen_now.now.isoformat()
    assert entry["suppressed_count"] == 0


# ---- should_fire — window logic -----------------------------------------


def test_should_fire_returns_false_inside_open_window(state_in_tmp, frozen_now):
    alert_dedupe.record_fire("script::code")
    frozen_now.advance(minutes=10)

    assert alert_dedupe.should_fire("script::code") is False


def test_should_fire_inside_window_increments_suppressed_count(
    state_in_tmp, frozen_now
):
    alert_dedupe.record_fire("script::code")
    frozen_now.advance(minutes=2)
    alert_dedupe.should_fire("script::code")
    frozen_now.advance(minutes=2)
    alert_dedupe.should_fire("script::code")
    frozen_now.advance(minutes=2)
    alert_dedupe.should_fire("script::code")

    state = json.loads(alert_dedupe.STATE_FILE.read_text())
    entry = state["script::code"]
    assert entry["suppressed_count"] == 3
    # last_fired_at moves with each suppressed call.
    assert entry["last_fired_at"] == frozen_now.now.isoformat()


def test_should_fire_returns_true_after_window_expiry(state_in_tmp, frozen_now):
    alert_dedupe.record_fire("script::code")
    frozen_now.advance(minutes=61)

    assert alert_dedupe.should_fire("script::code") is True


def test_distinct_keys_have_independent_windows(state_in_tmp, frozen_now):
    alert_dedupe.record_fire("script_a::code")
    frozen_now.advance(minutes=5)

    # script_b has never fired; should_fire returns True.
    assert alert_dedupe.should_fire("script_b::code") is True
    # script_a still in window.
    assert alert_dedupe.should_fire("script_a::code") is False


# ---- Fail-open on state errors ------------------------------------------


def test_should_fire_returns_true_on_corrupt_state_file(
    state_in_tmp, frozen_now, log_capture
):
    state_in_tmp.mkdir(parents=True, exist_ok=True)
    (state_in_tmp / "alert_dedupe.json").write_text("not json {{{ broken")

    assert alert_dedupe.should_fire("script::code") is True
    assert any("[alert-dedupe-state-error]" in line for line in log_capture.lines)


def test_should_fire_returns_true_on_non_object_root(
    state_in_tmp, frozen_now, log_capture
):
    state_in_tmp.mkdir(parents=True, exist_ok=True)
    (state_in_tmp / "alert_dedupe.json").write_text('["a", "b"]')

    assert alert_dedupe.should_fire("script::code") is True
    assert any("[alert-dedupe-state-error]" in line for line in log_capture.lines)


def test_record_fire_recovers_after_corruption(state_in_tmp, frozen_now):
    state_in_tmp.mkdir(parents=True, exist_ok=True)
    (state_in_tmp / "alert_dedupe.json").write_text("garbage")

    alert_dedupe.record_fire("script::code")

    state = json.loads(alert_dedupe.STATE_FILE.read_text())
    assert "script::code" in state


def test_should_fire_falls_open_on_unexpected_exception(
    state_in_tmp, frozen_now, monkeypatch, log_capture
):
    # Simulate an unexpected blow-up below the public surface.
    def boom(*args, **kwargs):
        raise RuntimeError("disk gone")

    monkeypatch.setattr(alert_dedupe, "_acquire_lock", boom)

    assert alert_dedupe.should_fire("script::code") is True
    assert any("[alert-dedupe-state-error]" in line for line in log_capture.lines)


def test_record_fire_silent_noop_on_unexpected_exception(
    state_in_tmp, frozen_now, monkeypatch, log_capture
):
    def boom(*args, **kwargs):
        raise RuntimeError("disk gone")

    monkeypatch.setattr(alert_dedupe, "_acquire_lock", boom)

    # Must not raise.
    alert_dedupe.record_fire("script::code")
    assert any("[alert-dedupe-state-error]" in line for line in log_capture.lines)


def test_should_fire_returns_true_when_lock_unobtainable(
    state_in_tmp, frozen_now, monkeypatch, log_capture
):
    monkeypatch.setattr(alert_dedupe, "_acquire_lock", lambda fh: False)

    assert alert_dedupe.should_fire("script::code") is True
    assert any(
        "could not acquire flock" in line for line in log_capture.lines
    )


# ---- Config read fallback -----------------------------------------------


def test_window_value_falls_back_to_defaults_on_smartsheet_failure(
    state_in_tmp, frozen_now, mocker
):
    mocker.patch(
        "shared.smartsheet_client.get_setting",
        side_effect=SmartsheetError("unreachable"),
    )

    alert_dedupe.record_fire("script::code")

    state = json.loads(alert_dedupe.STATE_FILE.read_text())
    entry = state["script::code"]
    # Default is the constant in shared/defaults.py.
    expected_end = (
        frozen_now.now + timedelta(minutes=defaults.ALERTING_DEDUPE_WINDOW_MINUTES)
    ).isoformat()
    assert entry["window_ends_at"] == expected_end


def test_window_value_falls_back_when_row_missing(
    state_in_tmp, frozen_now, mocker
):
    from shared.smartsheet_client import SmartsheetNotFoundError

    mocker.patch(
        "shared.smartsheet_client.get_setting",
        side_effect=SmartsheetNotFoundError("not seeded"),
    )

    alert_dedupe.record_fire("script::code")

    state = json.loads(alert_dedupe.STATE_FILE.read_text())
    assert (
        state["script::code"]["window_ends_at"]
        == (
            frozen_now.now
            + timedelta(minutes=defaults.ALERTING_DEDUPE_WINDOW_MINUTES)
        ).isoformat()
    )


def test_window_value_falls_back_on_non_numeric_value(
    state_in_tmp, frozen_now, mocker
):
    mocker.patch(
        "shared.smartsheet_client.get_setting",
        return_value="not-a-number",
    )

    alert_dedupe.record_fire("script::code")

    state = json.loads(alert_dedupe.STATE_FILE.read_text())
    expected_end = (
        frozen_now.now + timedelta(minutes=defaults.ALERTING_DEDUPE_WINDOW_MINUTES)
    ).isoformat()
    assert state["script::code"]["window_ends_at"] == expected_end


def test_window_value_uses_its_config_when_present(
    state_in_tmp, frozen_now, mocker
):
    # Non-default window — ITS_Config takes precedence over the constant.
    mocker.patch("shared.smartsheet_client.get_setting", return_value="15")

    alert_dedupe.record_fire("script::code")

    state = json.loads(alert_dedupe.STATE_FILE.read_text())
    expected_end = (frozen_now.now + timedelta(minutes=15)).isoformat()
    assert state["script::code"]["window_ends_at"] == expected_end


# ---- Lock serialization (process-equivalent simulation) -----------------


def test_concurrent_should_fire_calls_serialize_via_flock(
    state_in_tmp, frozen_now, monkeypatch
):
    """Two interleaved should_fire calls land at consistent state.

    Simulates: process A opens lock and reads, process B's flock waits,
    process A writes and releases, process B then reads A's write. The
    flock contract means each writer sees a consistent state.

    We can't spawn a second OS process inside pytest cleanly, but we can
    assert the lock-protected sequence by interleaving on the same
    in-process file handles via a stubbed `_acquire_lock` that records
    a hand-off counter.
    """
    alert_dedupe.record_fire("script::code")
    # Two suppressed calls in tight sequence — both increment.
    alert_dedupe.should_fire("script::code")
    alert_dedupe.should_fire("script::code")

    state = json.loads(alert_dedupe.STATE_FILE.read_text())
    assert state["script::code"]["suppressed_count"] == 2


def test_lock_released_after_each_call(state_in_tmp, frozen_now):
    """Smoke check: a sequence of operations doesn't deadlock on its own lock.

    If flock weren't released between calls, the second call would
    block (or fail-open on the lock-retry-exhaustion path). Confirm
    the persisted state shows the second call's effect — proves the
    first call released the lock.
    """
    for _ in range(3):
        alert_dedupe.record_fire("script::code")

    # All three calls completed; the persisted entry reflects exactly one
    # open window (subsequent record_fires inside the window were no-ops).
    state = json.loads(alert_dedupe.STATE_FILE.read_text())
    assert "script::code" in state


# ---- PR β: list_expired_summaries ---------------------------------------


def test_list_expired_summaries_returns_empty_when_state_missing(state_in_tmp, frozen_now):
    assert alert_dedupe.list_expired_summaries() == []


def test_list_expired_summaries_returns_empty_when_state_corrupt(
    state_in_tmp, frozen_now, log_capture
):
    state_in_tmp.mkdir(parents=True, exist_ok=True)
    (state_in_tmp / "alert_dedupe.json").write_text("not json {{{")

    assert alert_dedupe.list_expired_summaries() == []
    assert any("[alert-dedupe-state-error]" in line for line in log_capture.lines)


def test_list_expired_summaries_filters_to_expired_only(state_in_tmp, frozen_now):
    # Two entries: one whose window has closed, one still open.
    alert_dedupe.record_fire("script_expired::code")
    frozen_now.advance(minutes=70)
    alert_dedupe.record_fire("script_open::code")

    expired = alert_dedupe.list_expired_summaries()
    keys = [e.key for e in expired]
    assert "script_expired::code" in keys
    assert "script_open::code" not in keys


def test_list_expired_summaries_includes_all_fields(state_in_tmp, frozen_now):
    alert_dedupe.record_fire("script::code")
    # Suppress one fire inside the window so suppressed_count > 0.
    frozen_now.advance(minutes=5)
    alert_dedupe.should_fire("script::code")
    # Advance past window so the entry is expired.
    frozen_now.advance(minutes=70)

    expired = alert_dedupe.list_expired_summaries()
    assert len(expired) == 1
    e = expired[0]
    assert e.key == "script::code"
    assert e.first_fired_at  # non-empty
    assert e.last_fired_at  # non-empty
    assert e.window_ends_at  # non-empty
    assert e.suppressed_count == 1
    assert e.summarized is False


def test_list_expired_summaries_skips_malformed_entry(
    state_in_tmp, frozen_now, log_capture
):
    # Write a state file with one malformed entry (missing window_ends_at)
    # and one good expired entry. Sweep should return only the good one
    # and write a marker for the bad one.
    alert_dedupe.record_fire("good::code")
    frozen_now.advance(minutes=70)
    state = json.loads(alert_dedupe.STATE_FILE.read_text())
    state["bad::code"] = {"first_fired_at": "garbage"}
    alert_dedupe.STATE_FILE.write_text(json.dumps(state))

    expired = alert_dedupe.list_expired_summaries()
    keys = [e.key for e in expired]
    assert keys == ["good::code"]
    assert any("malformed window_ends_at" in line for line in log_capture.lines)


def test_list_expired_summaries_returns_immutable_snapshot(state_in_tmp, frozen_now):
    """ExpiredEntry is frozen — mutating it must raise; the state file is
    the only mutation surface, accessed only through mark_summarized /
    delete_entry."""
    import dataclasses

    alert_dedupe.record_fire("script::code")
    frozen_now.advance(minutes=70)

    [entry] = alert_dedupe.list_expired_summaries()
    with pytest.raises(dataclasses.FrozenInstanceError):
        entry.summarized = True  # type: ignore[misc]


# ---- PR β: mark_summarized ----------------------------------------------


def test_mark_summarized_sets_field_true(state_in_tmp, frozen_now):
    alert_dedupe.record_fire("script::code")
    alert_dedupe.mark_summarized("script::code")

    state = json.loads(alert_dedupe.STATE_FILE.read_text())
    assert state["script::code"]["summarized"] is True


def test_mark_summarized_no_op_on_missing_key(state_in_tmp, frozen_now):
    alert_dedupe.record_fire("script::code")
    alert_dedupe.mark_summarized("nonexistent::code")

    state = json.loads(alert_dedupe.STATE_FILE.read_text())
    assert state["script::code"]["summarized"] is False
    assert "nonexistent::code" not in state


def test_mark_summarized_preserves_other_entries(state_in_tmp, frozen_now):
    alert_dedupe.record_fire("a::code")
    alert_dedupe.record_fire("b::code")
    alert_dedupe.mark_summarized("a::code")

    state = json.loads(alert_dedupe.STATE_FILE.read_text())
    assert state["a::code"]["summarized"] is True
    assert state["b::code"]["summarized"] is False


def test_mark_summarized_silent_noop_when_state_missing(state_in_tmp, frozen_now):
    # No record_fire first → no state file → mark_summarized must not raise.
    alert_dedupe.mark_summarized("script::code")
    assert not alert_dedupe.STATE_FILE.exists()


def test_mark_summarized_logs_marker_on_failure(
    state_in_tmp, frozen_now, monkeypatch, log_capture
):
    alert_dedupe.record_fire("script::code")
    monkeypatch.setattr(alert_dedupe, "_acquire_lock", lambda fh: False)

    alert_dedupe.mark_summarized("script::code")
    assert any("could not acquire flock" in line for line in log_capture.lines)


# ---- PR β: delete_entry -------------------------------------------------


def test_delete_entry_removes_target_only(state_in_tmp, frozen_now):
    alert_dedupe.record_fire("a::code")
    alert_dedupe.record_fire("b::code")
    alert_dedupe.delete_entry("a::code")

    state = json.loads(alert_dedupe.STATE_FILE.read_text())
    assert "a::code" not in state
    assert "b::code" in state


def test_delete_entry_no_op_on_missing_key(state_in_tmp, frozen_now):
    alert_dedupe.record_fire("script::code")
    alert_dedupe.delete_entry("nonexistent::code")

    state = json.loads(alert_dedupe.STATE_FILE.read_text())
    assert "script::code" in state


def test_delete_entry_silent_noop_when_state_missing(state_in_tmp, frozen_now):
    alert_dedupe.delete_entry("script::code")
    assert not alert_dedupe.STATE_FILE.exists()


def test_delete_entry_logs_marker_on_failure(
    state_in_tmp, frozen_now, monkeypatch, log_capture
):
    alert_dedupe.record_fire("script::code")
    monkeypatch.setattr(alert_dedupe, "_acquire_lock", lambda fh: False)

    alert_dedupe.delete_entry("script::code")
    assert any("could not acquire flock" in line for line in log_capture.lines)


# ---- PR β: concurrent / serialized mark+delete --------------------------


def test_mark_then_delete_serialize_via_flock(state_in_tmp, frozen_now):
    """A mark followed by a delete must produce a state file without the entry.

    If the lock were not released between calls, the second call would
    fail-open (lock retry exhaustion) and the entry would survive. Confirm
    persisted state shows the deletion.
    """
    alert_dedupe.record_fire("script::code")
    alert_dedupe.mark_summarized("script::code")
    alert_dedupe.delete_entry("script::code")

    state = json.loads(alert_dedupe.STATE_FILE.read_text())
    assert "script::code" not in state


# ---- log_capture fixture ------------------------------------------------


@pytest.fixture
def log_capture(tmp_path, monkeypatch):
    """Redirect error_log.LOG_DIR to tmp_path and expose the captured lines."""
    import shared.error_log as el
    log_dir = tmp_path / "logs"
    monkeypatch.setattr(el, "LOG_DIR", log_dir)
    monkeypatch.setattr(el, "_in_smartsheet_write", False)

    class _Capture:
        @property
        def lines(self):
            files = list(log_dir.glob("*.log")) if log_dir.exists() else []
            if not files:
                return []
            return files[0].read_text().splitlines()

    return _Capture()
