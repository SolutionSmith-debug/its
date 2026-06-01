"""Tests for shared/circuit_breaker.py.

Filesystem-only — no SDK. State lives in a per-test ``tmp_path`` file; the
clock is monkeypatched so cooldown transitions are deterministic. The conftest
autouse keychain + kill_switch mocks are no-ops here (this module imports
neither).

Run with: pytest -q tests/test_circuit_breaker.py
"""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

import shared.circuit_breaker as cb

# ---- test exception hierarchy (mirrors the smartsheet one) ----------------


class BaseError(Exception):
    """Stand-in for SmartsheetError (the ``count`` base)."""


class CircuitOpenError(BaseError):
    """Stand-in for SmartsheetCircuitOpenError (the ``open_exc``)."""


class AuthError(BaseError):
    """Stand-in for an ignored deterministic error (401/403/404)."""


class CountsError(BaseError):
    """Stand-in for a counting failure (429/5xx/transport)."""


# ---- fixtures -------------------------------------------------------------


class Clock:
    """Mutable monkeypatched clock for cooldown control."""

    def __init__(self, start: datetime) -> None:
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: int) -> None:
        self.now = self.now + timedelta(seconds=seconds)


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> Clock:
    c = Clock(datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC))
    monkeypatch.setattr(cb, "_now", c)
    return c


@pytest.fixture
def state_path(tmp_path: Path) -> Path:
    return tmp_path / "circuit_breaker.json"


def make_config(enabled: bool = True, threshold: int = 3, cooldown: int = 300):
    return lambda: cb.CircuitConfig(
        enabled=enabled, failure_threshold=threshold, cooldown_seconds=cooldown
    )


class _Guarded:
    """A guarded callable that also tracks how many times the inner fn ran.

    A small class rather than a function with a ``.calls`` attribute so mypy
    stays happy (you cannot attach attributes to a ``Callable``).
    """

    def __init__(self, state_path: Path, behavior, *, config=None) -> None:
        self.calls = 0
        cfg = config or make_config()

        @cb.guard(
            open_exc=CircuitOpenError,
            count=BaseError,
            ignore=(AuthError,),
            config_loader=cfg,
            state_path=state_path,
        )
        def _inner():
            self.calls += 1
            return behavior()

        self._guarded = _inner

    def __call__(self):
        return self._guarded()


def guarded(state_path: Path, behavior, *, config=None) -> _Guarded:
    """Build a guarded callable driven by ``behavior`` (returns a value or
    raises). Tracks invocation count on ``.calls``."""
    return _Guarded(state_path, behavior, config=config)


def read_state(state_path: Path) -> dict:
    return json.loads(state_path.read_text())


# ---- closed / hot path ----------------------------------------------------


def test_closed_passes_through_and_returns(state_path: Path) -> None:
    fn = guarded(state_path, lambda: "ok")
    assert fn() == "ok"
    assert fn.calls == 1


def test_hot_path_writes_nothing_on_clean_success(state_path: Path) -> None:
    """Healthy system → zero state writes (lock-free, no file created)."""
    fn = guarded(state_path, lambda: "ok")
    for _ in range(5):
        assert fn() == "ok"
    assert not state_path.exists()


# ---- tripping -------------------------------------------------------------


def _raise(exc: type[Exception]):
    def _b():
        raise exc("boom")

    return _b


def test_trips_open_after_threshold_consecutive_failures(state_path: Path, clock: Clock) -> None:
    fn = guarded(state_path, _raise(CountsError))  # threshold 3
    for _ in range(3):
        with pytest.raises(CountsError):
            fn()
    state = read_state(state_path)
    assert state["state"] == cb.OPEN
    assert state["consecutive_failures"] == 3
    assert state["opened_at"] == clock.now.isoformat()


def test_open_short_circuits_without_calling_fn(state_path: Path, clock: Clock) -> None:
    fn = guarded(state_path, _raise(CountsError))
    for _ in range(3):
        with pytest.raises(CountsError):
            fn()
    calls_before = fn.calls
    with pytest.raises(CircuitOpenError):
        fn()
    assert fn.calls == calls_before  # fn was NOT invoked


def test_any_success_resets_consecutive_count(state_path: Path, clock: Clock) -> None:
    box = {"mode": "fail"}

    def behavior():
        if box["mode"] == "fail":
            raise CountsError("boom")
        return "ok"

    fn = guarded(state_path, behavior)  # threshold 3
    for _ in range(2):  # 2 failures (< 3)
        with pytest.raises(CountsError):
            fn()
    box["mode"] = "ok"
    assert fn() == "ok"  # success → reset
    assert read_state(state_path)["consecutive_failures"] == 0
    box["mode"] = "fail"
    for _ in range(2):  # 2 more — still below threshold, must NOT trip
        with pytest.raises(CountsError):
            fn()
    assert read_state(state_path)["state"] == cb.CLOSED


def test_ignored_exception_never_counts(state_path: Path, clock: Clock) -> None:
    fn = guarded(state_path, _raise(AuthError))
    for _ in range(10):
        with pytest.raises(AuthError):
            fn()
    # No counting → no state file written at all.
    assert not state_path.exists()


def test_nested_open_exc_is_not_counted(state_path: Path, clock: Clock) -> None:
    """open_exc raised from WITHIN the call (nested short-circuit) must not
    count as a failure of the outer method."""
    fn = guarded(state_path, _raise(CircuitOpenError))
    for _ in range(5):
        with pytest.raises(CircuitOpenError):
            fn()
    assert not state_path.exists()


# ---- cooldown / half-open -------------------------------------------------


def _trip_open(state_path: Path, clock: Clock) -> None:
    fn = guarded(state_path, _raise(CountsError))
    for _ in range(3):
        with pytest.raises(CountsError):
            fn()
    assert read_state(state_path)["state"] == cb.OPEN


def test_cooldown_probe_success_closes(state_path: Path, clock: Clock) -> None:
    _trip_open(state_path, clock)
    clock.advance(301)  # past 300s cooldown
    fn = guarded(state_path, lambda: "ok")
    assert fn() == "ok"  # probe proceeds
    state = read_state(state_path)
    assert state["state"] == cb.CLOSED
    assert state["consecutive_failures"] == 0


def test_cooldown_probe_failure_reopens_with_fresh_opened_at(state_path: Path, clock: Clock) -> None:
    _trip_open(state_path, clock)
    first_opened = read_state(state_path)["opened_at"]
    clock.advance(301)
    fn = guarded(state_path, _raise(CountsError))
    with pytest.raises(CountsError):
        fn()  # probe fails
    state = read_state(state_path)
    assert state["state"] == cb.OPEN
    assert state["opened_at"] != first_opened
    assert datetime.fromisoformat(state["opened_at"]) > datetime.fromisoformat(first_opened)


def test_within_cooldown_still_short_circuits(state_path: Path, clock: Clock) -> None:
    _trip_open(state_path, clock)
    clock.advance(100)  # < 300s
    fn = guarded(state_path, lambda: "ok")
    with pytest.raises(CircuitOpenError):
        fn()
    assert fn.calls == 0


def test_half_open_concurrent_caller_short_circuits(state_path: Path, clock: Clock) -> None:
    """A second caller seeing HALF_OPEN (probe owned elsewhere) short-circuits."""
    cb._write_state(
        state_path,
        {"state": cb.HALF_OPEN, "consecutive_failures": 3, "opened_at": clock.now.isoformat()},
    )
    fn = guarded(state_path, lambda: "ok")
    with pytest.raises(CircuitOpenError):
        fn()
    assert fn.calls == 0


# ---- bypass ---------------------------------------------------------------


def test_bypass_calls_through_when_open(state_path: Path, clock: Clock) -> None:
    _trip_open(state_path, clock)
    fn = guarded(state_path, lambda: "ok")
    with cb.bypass():
        assert fn() == "ok"  # OPEN, but bypass calls straight through
    assert fn.calls == 1


def test_bypass_does_not_count_failures(state_path: Path, clock: Clock) -> None:
    fn = guarded(state_path, _raise(CountsError))
    with cb.bypass():
        for _ in range(10):
            with pytest.raises(CountsError):
                fn()
    assert not state_path.exists()  # nothing counted


# ---- enabled=false escape hatch -------------------------------------------


def test_disabled_passes_through_even_when_open(state_path: Path, clock: Clock) -> None:
    cb._write_state(
        state_path,
        {"state": cb.OPEN, "consecutive_failures": 9, "opened_at": clock.now.isoformat()},
    )
    fn = guarded(state_path, lambda: "ok", config=make_config(enabled=False))
    assert fn() == "ok"  # disabled → pass-through despite OPEN state
    assert fn.calls == 1


# ---- fail-open on bad state -----------------------------------------------


def test_missing_state_file_is_closed(state_path: Path) -> None:
    assert cb.is_open(state_path, make_config()) is False
    fn = guarded(state_path, lambda: "ok")
    assert fn() == "ok"


def test_corrupt_state_file_is_closed(state_path: Path) -> None:
    state_path.write_text("{ not valid json ::::")
    assert cb.is_open(state_path, make_config()) is False
    fn = guarded(state_path, lambda: "ok")
    assert fn() == "ok"


def test_unknown_state_value_is_closed(state_path: Path) -> None:
    state_path.write_text(json.dumps({"state": "GREMLIN", "consecutive_failures": 99}))
    assert cb.is_open(state_path, make_config()) is False


# ---- is_open semantics ----------------------------------------------------


def test_is_open_true_when_open_within_cooldown(state_path: Path, clock: Clock) -> None:
    _trip_open(state_path, clock)
    clock.advance(100)
    assert cb.is_open(state_path, make_config()) is True


def test_is_open_false_after_cooldown(state_path: Path, clock: Clock) -> None:
    _trip_open(state_path, clock)
    clock.advance(301)
    assert cb.is_open(state_path, make_config()) is False


def test_is_open_false_when_half_open(state_path: Path, clock: Clock) -> None:
    cb._write_state(
        state_path,
        {"state": cb.HALF_OPEN, "consecutive_failures": 3, "opened_at": clock.now.isoformat()},
    )
    assert cb.is_open(state_path, make_config()) is False
