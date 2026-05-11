"""Tests for shared/error_log.py.

LOG_DIR is monkeypatched to a pytest tmp_path in every test so these never
touch the real ~/its/logs/ directory.

Run with: pytest -q tests/test_error_log.py
"""
from __future__ import annotations

from datetime import datetime

import pytest

from shared.error_log import Severity, _local_log, its_error_log, log


@pytest.fixture
def log_dir(tmp_path, monkeypatch):
    """Redirect error_log.LOG_DIR to tmp_path for filesystem isolation."""
    monkeypatch.setattr("shared.error_log.LOG_DIR", tmp_path)
    return tmp_path


def _today_log(log_dir):
    """Path to today's log file in the redirected log_dir."""
    return log_dir / f"{datetime.now():%Y-%m-%d}.log"


# ---- Severity enum --------------------------------------------------------

def test_severity_enum_values():
    # The string values are part of the on-disk log format; locking them in.
    assert Severity.INFO.value == "INFO"
    assert Severity.WARN.value == "WARN"
    assert Severity.ERROR.value == "ERROR"
    assert Severity.CRITICAL.value == "CRITICAL"


# ---- _local_log -----------------------------------------------------------

def test_local_log_creates_log_dir_if_missing(tmp_path, monkeypatch):
    nested = tmp_path / "deep" / "nested" / "logs"
    monkeypatch.setattr("shared.error_log.LOG_DIR", nested)

    _local_log(Severity.INFO, "test.script", "hello")

    assert nested.exists()


def test_local_log_writes_tab_separated_dated_file(log_dir):
    _local_log(Severity.ERROR, "test.script", "msg")

    log_file = _today_log(log_dir)
    assert log_file.exists()

    # Format: timestamp \t severity \t script \t message
    line = log_file.read_text().rstrip("\n")
    parts = line.split("\t")
    assert len(parts) == 4
    assert parts[1] == "ERROR"
    assert parts[2] == "test.script"
    assert parts[3] == "msg"


def test_local_log_appends_across_calls(log_dir):
    _local_log(Severity.INFO, "s", "first")
    _local_log(Severity.WARN, "s", "second")

    contents = _today_log(log_dir).read_text()
    assert "first" in contents
    assert "second" in contents


def test_local_log_includes_exc_info_when_provided(log_dir):
    _local_log(
        Severity.CRITICAL,
        "s",
        "boom",
        exc_info="Traceback (most recent call last):\n  File ...",
    )

    contents = _today_log(log_dir).read_text()
    assert "Traceback" in contents


# ---- log() public helper --------------------------------------------------

def test_log_helper_writes_with_given_severity(log_dir):
    log(Severity.WARN, "test.script", "heads up")

    contents = _today_log(log_dir).read_text()
    assert "WARN" in contents
    assert "heads up" in contents


# ---- @its_error_log decorator --------------------------------------------

def test_decorator_returns_value_and_logs_start_complete(log_dir):
    @its_error_log("test.script")
    def work(x, y, *, scale=1):
        return (x + y) * scale

    # Args + kwargs pass through correctly.
    assert work(2, 3, scale=10) == 50

    contents = _today_log(log_dir).read_text()
    assert "started" in contents
    assert "completed" in contents


def test_decorator_logs_critical_with_traceback_and_reraises(log_dir):
    @its_error_log("test.script")
    def boom():
        raise ValueError("specific-message")

    # Re-raise: callers must still see the original exception.
    with pytest.raises(ValueError, match="specific-message"):
        boom()

    contents = _today_log(log_dir).read_text()
    assert "CRITICAL" in contents
    assert "specific-message" in contents
    assert "Traceback" in contents


def test_decorator_calls_alert_critical_on_exception(log_dir, mocker):
    alert = mocker.patch("shared.error_log._alert_critical")

    @its_error_log("test.script")
    def boom():
        raise RuntimeError("kaboom")

    with pytest.raises(RuntimeError):
        boom()

    alert.assert_called_once()
    script_arg, msg_arg, tb_arg = alert.call_args.args
    assert script_arg == "test.script"
    assert "kaboom" in msg_arg
    assert "Traceback" in tb_arg


def test_decorator_does_not_call_alert_on_success(log_dir, mocker):
    alert = mocker.patch("shared.error_log._alert_critical")

    @its_error_log("test.script")
    def work():
        return "ok"

    work()
    alert.assert_not_called()


def test_decorator_preserves_function_name():
    # functools.wraps must propagate __name__ so tracebacks and reprs stay useful.
    @its_error_log("test.script")
    def my_named_function():
        return None

    assert my_named_function.__name__ == "my_named_function"
