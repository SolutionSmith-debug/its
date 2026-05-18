"""Tests for shared/error_log.py.

LOG_DIR is monkeypatched to a pytest tmp_path so these never touch the real
~/its/logs/ directory. `smartsheet_client.add_rows` is mocked by an autouse
fixture so no test hits live Smartsheet; tests that want to assert against
the mock fetch it as a fixture parameter.

Run with: pytest -q tests/test_error_log.py
"""
from __future__ import annotations

from datetime import date, datetime

import pytest

import shared.error_log as error_log_module
from shared.error_log import Severity, _local_log, its_error_log, log
from shared.smartsheet_client import SmartsheetError


@pytest.fixture
def log_dir(tmp_path, monkeypatch):
    """Redirect error_log.LOG_DIR to tmp_path for filesystem isolation."""
    monkeypatch.setattr("shared.error_log.LOG_DIR", tmp_path)
    return tmp_path


def _today_log(log_dir):
    """Path to today's log file in the redirected log_dir."""
    return log_dir / f"{datetime.now():%Y-%m-%d}.log"


@pytest.fixture(autouse=True)
def add_rows_mock(mocker, monkeypatch):
    """Autouse: mock smartsheet_client.add_rows and clear the INFO env-gate.

    No test ever hits live Smartsheet. Tests that want to assert against
    the mock take this fixture as a parameter (the same instance is reused).
    Also resets the module-level recursion guard between tests.
    """
    monkeypatch.delenv("ITS_ERROR_LOG_INFO", raising=False)
    error_log_module._in_smartsheet_write = False
    mock = mocker.patch("shared.error_log.smartsheet_client.add_rows")
    yield mock
    error_log_module._in_smartsheet_write = False


# ---- Severity enum --------------------------------------------------------

def test_severity_enum_values():
    # The string values are part of the on-disk log format AND the ITS_Errors
    # Severity picklist (verified live 2026-05-18); locking them in.
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


# ---- Smartsheet write path -----------------------------------------------


def _row_payload(add_rows_mock):
    """Helper: pull the single row dict out of the most recent add_rows call."""
    sheet_id, rows = add_rows_mock.call_args.args
    assert len(rows) == 1
    return sheet_id, rows[0]


def test_warn_writes_to_smartsheet_with_correct_cell_payload(log_dir, add_rows_mock):
    from shared import sheet_ids

    log(Severity.WARN, "test.script", "heads up")

    add_rows_mock.assert_called_once()
    sheet_id, row = _row_payload(add_rows_mock)
    assert sheet_id == sheet_ids.SHEET_ERRORS
    assert row == {
        "Error": "warn",  # default error_code = severity.lower()
        "Timestamp": date.today().isoformat(),
        "Severity": "WARN",
        "Script": "test.script",
        "Message": "heads up",
        "Traceback": "",
    }


def test_error_code_override_lands_in_error_column(log_dir, add_rows_mock):
    log(Severity.ERROR, "test.script", "boom", error_code="custom_code")

    _, row = _row_payload(add_rows_mock)
    assert row["Error"] == "custom_code"


def test_critical_with_exc_info_populates_traceback_column(log_dir, add_rows_mock):
    log(
        Severity.CRITICAL,
        "test.script",
        "kaboom",
        exc_info="Traceback (most recent call last):\n  File ...",
    )

    _, row = _row_payload(add_rows_mock)
    assert "Traceback" in row["Traceback"]
    assert row["Severity"] == "CRITICAL"


# ---- INFO env-gating -----------------------------------------------------


def test_info_not_written_to_smartsheet_by_default(log_dir, add_rows_mock):
    log(Severity.INFO, "test.script", "started")

    # Local file still gets it.
    assert "started" in _today_log(log_dir).read_text()
    # But Smartsheet write was skipped.
    add_rows_mock.assert_not_called()


def test_info_written_to_smartsheet_when_env_set(log_dir, add_rows_mock, monkeypatch):
    monkeypatch.setenv("ITS_ERROR_LOG_INFO", "1")

    log(Severity.INFO, "test.script", "started")

    add_rows_mock.assert_called_once()
    _, row = _row_payload(add_rows_mock)
    assert row["Severity"] == "INFO"


def test_info_not_written_when_env_set_to_other_value(log_dir, add_rows_mock, monkeypatch):
    # Defensive: only "1" enables. "true" / "yes" / "0" / empty all stay off.
    monkeypatch.setenv("ITS_ERROR_LOG_INFO", "true")
    log(Severity.INFO, "test.script", "started")
    add_rows_mock.assert_not_called()


def test_warn_error_critical_always_written_regardless_of_env(log_dir, add_rows_mock, monkeypatch):
    monkeypatch.delenv("ITS_ERROR_LOG_INFO", raising=False)

    log(Severity.WARN, "s", "w")
    log(Severity.ERROR, "s", "e")
    log(Severity.CRITICAL, "s", "c")

    assert add_rows_mock.call_count == 3


def test_decorator_started_completed_lines_gated_off_by_default(log_dir, add_rows_mock):
    @its_error_log("test.script")
    def work():
        return None

    work()

    # Both "started" and "completed" are INFO; default-off env gate suppresses
    # the Smartsheet round-trip. Local file still has both lines.
    add_rows_mock.assert_not_called()
    contents = _today_log(log_dir).read_text()
    assert "started" in contents
    assert "completed" in contents


def test_decorator_critical_writes_to_smartsheet_with_uncaught_exception_code(
    log_dir, add_rows_mock
):
    @its_error_log("test.script")
    def boom():
        raise ValueError("nope")

    with pytest.raises(ValueError):
        boom()

    add_rows_mock.assert_called_once()
    _, row = _row_payload(add_rows_mock)
    assert row["Error"] == "uncaught_exception"
    assert row["Severity"] == "CRITICAL"
    assert "nope" in row["Message"]
    assert "Traceback" in row["Traceback"]


# ---- Smartsheet write failure → local fallback ---------------------------


def test_smartsheet_failure_falls_back_to_local_marker(log_dir, add_rows_mock):
    add_rows_mock.side_effect = SmartsheetError("transient 429")

    # Must NOT raise — the marker fallback is the whole point of the guard.
    log(Severity.ERROR, "test.script", "the actual error")

    contents = _today_log(log_dir).read_text()
    # Original line preserved.
    assert "the actual error" in contents
    # Marker line written with the SmartsheetError repr.
    assert "[smartsheet-write-failed]" in contents
    assert "transient 429" in contents


def test_smartsheet_failure_only_catches_smartsheet_errors(log_dir, add_rows_mock):
    # If add_rows somehow raises a non-SmartsheetError (e.g., a bug in
    # _resolve_cells), the marker fallback does NOT swallow it — we want
    # programmer errors to surface, not silently drop log lines.
    add_rows_mock.side_effect = RuntimeError("unexpected")

    with pytest.raises(RuntimeError):
        log(Severity.ERROR, "test.script", "msg")


# ---- Recursion guard -----------------------------------------------------


def test_recursion_guard_blocks_reentrant_smartsheet_write(log_dir, add_rows_mock):
    # Simulate the failure mode: add_rows somehow ends up calling log() again
    # (e.g., a future smartsheet_client callback emits a log line during a
    # write). The inner call must NOT attempt a second Smartsheet write.
    inner_call_count = {"value": 0}

    def reentrant_add_rows(*args, **kwargs):
        inner_call_count["value"] += 1
        # Inner log() call should see the guard set and skip Smartsheet.
        log(Severity.WARN, "inner", "reentered")

    add_rows_mock.side_effect = reentrant_add_rows

    log(Severity.WARN, "outer", "first call")

    # Only the outer add_rows call should have run; the inner log() must
    # have seen the guard flag and returned without re-entering.
    assert inner_call_count["value"] == 1


def test_recursion_guard_reset_after_normal_completion(log_dir, add_rows_mock):
    log(Severity.WARN, "s", "first")
    log(Severity.WARN, "s", "second")
    # If the try/finally were broken, the guard would stick after first call
    # and add_rows would only fire once total.
    assert add_rows_mock.call_count == 2


def test_recursion_guard_reset_after_smartsheet_error(log_dir, add_rows_mock):
    add_rows_mock.side_effect = [SmartsheetError("first"), None]
    log(Severity.WARN, "s", "first")
    # Guard must have reset despite the exception — second call needs to
    # reach add_rows.
    log(Severity.WARN, "s", "second")
    assert add_rows_mock.call_count == 2
