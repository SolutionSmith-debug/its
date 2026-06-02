"""Tests for shared/error_log.py.

LOG_DIR is monkeypatched to a pytest tmp_path so these never touch the real
~/its/logs/ directory. `smartsheet_client.add_rows` is mocked by an autouse
fixture so no test hits live Smartsheet; tests that want to assert against
the mock fetch it as a fixture parameter.

Run with: pytest -q tests/test_error_log.py
"""
from __future__ import annotations

import re
from datetime import date, datetime

import pytest

import shared.alert_dedupe as alert_dedupe_module
import shared.error_log as error_log_module
from shared.error_log import Severity, _local_log, its_error_log, log
from shared.smartsheet_client import SmartsheetError

# UUID4 pattern for correlation-ID assertions.
_UUID4_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)


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


@pytest.fixture(autouse=True)
def send_alert_mock(mocker):
    """Autouse: mock the lazy-imported `resend_client.send_alert` so no test
    triggers a live Resend POST. Mirrors the `add_rows_mock` pattern.

    The `_alert_critical` function lazy-imports `shared.resend_client` inside
    the function body — we patch `shared.resend_client.send_alert` at the
    module level so the import will resolve to the mocked attribute. Also
    resets the module-level `_in_resend_alert` recursion guard between tests.
    """
    error_log_module._in_resend_alert = False
    error_log_module._in_alert_critical = False
    # Patch at the source module so the lazy `from . import resend_client`
    # inside `_alert_critical` sees the mocked function.
    mock = mocker.patch("shared.resend_client.send_alert")
    yield mock
    error_log_module._in_resend_alert = False
    error_log_module._in_alert_critical = False


@pytest.fixture(autouse=True)
def alert_dedupe_state(tmp_path, monkeypatch):
    """Autouse: redirect alert_dedupe state file to tmp_path.

    Without this, the dedupe key `test.script::uncaught_exception` persists
    between tests in `~/its/state/alert_dedupe.json` and silently suppresses
    Resend calls in later tests. tmp_path isolation gives each test a clean
    window. Also fixes the `should_fire` clock to a real `datetime.now`.
    """
    state_dir = tmp_path / "state"
    monkeypatch.setattr(alert_dedupe_module, "STATE_DIR", state_dir)
    monkeypatch.setattr(
        alert_dedupe_module, "STATE_FILE", state_dir / "alert_dedupe.json"
    )
    return state_dir


@pytest.fixture(autouse=True)
def sentry_capture_mock(mocker):
    """Autouse: mock the lazy-imported `sentry_client.capture_exception` so no
    test triggers a live Sentry event. Mirrors the `send_alert_mock` pattern.

    Without this, the decorator-CRITICAL tests would fire real events into the
    operator's Sentry project — polluting the dashboard with test noise. Also
    resets the module-level `_in_sentry_capture` recursion guard between tests.
    """
    error_log_module._in_sentry_capture = False
    mock = mocker.patch("shared.sentry_client.capture_exception")
    yield mock
    error_log_module._in_sentry_capture = False


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
        "Correlation_ID": "",
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


# ---- _alert_critical → Resend wiring -------------------------------------


def test_alert_critical_called_on_critical_severity(log_dir, send_alert_mock):
    @its_error_log("test.script")
    def boom():
        raise ValueError("kaboom")

    with pytest.raises(ValueError):
        boom()

    send_alert_mock.assert_called_once()
    subject, body = send_alert_mock.call_args.args
    assert subject.startswith("[ITS CRITICAL] test.script:")
    assert "kaboom" in subject
    assert "Script:    test.script" in body
    assert "Timestamp:" in body
    assert "Message:   unhandled: kaboom" in body
    assert "Traceback:" in body
    assert "ValueError" in body


def test_log_critical_fires_alert_other_severities_do_not(
    log_dir, send_alert_mock, sentry_capture_mock
):
    # A3: log(CRITICAL) now PAGES directly (Resend + Sentry). INFO/WARN/ERROR
    # never page.
    log(Severity.INFO, "s", "info msg")
    log(Severity.WARN, "s", "warn msg")
    log(Severity.ERROR, "s", "error msg")
    send_alert_mock.assert_not_called()
    sentry_capture_mock.assert_not_called()

    log(Severity.CRITICAL, "s", "critical msg")  # via log() directly, NOT decorator
    send_alert_mock.assert_called_once()
    sentry_capture_mock.assert_called_once()


def test_log_critical_alert_false_records_but_does_not_page(
    log_dir, send_alert_mock, sentry_capture_mock, add_rows_mock
):
    # A3 opt-out: alert=False records the CRITICAL (local file + ITS_Errors
    # row) but withholds the operator page — the watchdog's MAINTENANCE-defer
    # path. The record legs still fire.
    log(Severity.CRITICAL, "s", "deferred crit", alert=False)
    send_alert_mock.assert_not_called()
    sentry_capture_mock.assert_not_called()
    add_rows_mock.assert_called_once()  # the ITS_Errors record still fired


def test_log_critical_threads_one_correlation_id_to_all_legs(
    log_dir, send_alert_mock, sentry_capture_mock, add_rows_mock
):
    # A3: a direct log(CRITICAL) with no correlation_id mints ONE id and
    # threads it to the ITS_Errors row, the Resend subject, and the Sentry tag.
    log(Severity.CRITICAL, "s", "crit msg")

    _, row = _row_payload(add_rows_mock)
    row_corr = row["Correlation_ID"]
    assert _UUID4_RE.match(row_corr)

    sentry_corr = sentry_capture_mock.call_args.kwargs.get("correlation_id")
    assert sentry_corr == row_corr

    subject, _ = send_alert_mock.call_args.args
    assert f"[corr: {row_corr[:8]}]" in subject


def test_alert_critical_truncates_long_message_in_subject(log_dir, send_alert_mock):
    long_msg = "x" * 200

    @its_error_log("test.script")
    def boom():
        raise ValueError(long_msg)

    with pytest.raises(ValueError):
        boom()

    subject, _ = send_alert_mock.call_args.args
    # 80-char limit applies to the message portion of the subject; the
    # prefix + script name don't count toward truncation. Subject should
    # contain an ellipsis indicating truncation happened.
    assert "…" in subject


def test_alert_critical_resend_failure_does_not_raise(log_dir, send_alert_mock):
    # Any exception from the Resend path must be swallowed — the underlying
    # CRITICAL event was already captured by _local_log + _smartsheet_log.
    from shared.resend_client import ResendError

    send_alert_mock.side_effect = ResendError("API down")

    @its_error_log("test.script")
    def boom():
        raise ValueError("the real bug")

    # Must re-raise the ORIGINAL ValueError, not the ResendError.
    with pytest.raises(ValueError, match="the real bug"):
        boom()

    # Marker line written to local log so operators can see the alert path failed.
    contents = _today_log(log_dir).read_text()
    assert "[resend-alert-failed]" in contents
    assert "API down" in contents


def test_alert_critical_swallows_non_resend_exceptions_too(log_dir, send_alert_mock):
    # The brief mandates "must NOT raise ... anything." If send_alert raises
    # something other than ResendError (e.g., KeychainError when the API key
    # isn't seeded), _alert_critical must still swallow.
    send_alert_mock.side_effect = RuntimeError("keychain missing")

    @its_error_log("test.script")
    def boom():
        raise ValueError("the real bug")

    with pytest.raises(ValueError, match="the real bug"):
        boom()

    contents = _today_log(log_dir).read_text()
    assert "[resend-alert-failed]" in contents
    assert "RuntimeError" in contents
    assert "keychain missing" in contents


def test_alert_critical_recursion_guard_blocks_reentry(
    log_dir, send_alert_mock, sentry_capture_mock
):
    # If send_alert somehow triggers another log() call that hits CRITICAL,
    # the inner _alert_critical must see the top-level guard and skip BOTH legs.
    inner_call_count = {"value": 0}

    def reentrant_send(subject, body, **kwargs):
        inner_call_count["value"] += 1
        # Inner CRITICAL log → would trigger _alert_critical → must be fully
        # skipped by the top-level reentrancy guard.
        log(Severity.CRITICAL, "inner", "reentered")

    send_alert_mock.side_effect = reentrant_send

    @its_error_log("test.script")
    def boom():
        raise ValueError("outer")

    with pytest.raises(ValueError):
        boom()

    # Exactly ONE Resend AND ONE Sentry. The inner reentrant log(CRITICAL) is
    # blocked by `_in_alert_critical`; the per-leg guards alone would have let
    # the Sentry leg double-fire (the A3 asymmetry this test now locks).
    assert send_alert_mock.call_count == 1
    assert sentry_capture_mock.call_count == 1
    assert inner_call_count["value"] == 1


# ---- _alert_critical → Sentry leg + dual-leg independence ----------------


def test_alert_critical_calls_both_resend_and_sentry(
    log_dir, send_alert_mock, sentry_capture_mock
):
    @its_error_log("test.script")
    def boom():
        raise ValueError("kaboom")

    with pytest.raises(ValueError):
        boom()

    send_alert_mock.assert_called_once()
    sentry_capture_mock.assert_called_once()
    # Sentry receives the structured fields (script, message, exc_info)
    # rather than the email subject + body.
    script, message, exc_info = sentry_capture_mock.call_args.args
    assert script == "test.script"
    assert "kaboom" in message
    assert "ValueError" in exc_info


def test_sentry_called_even_when_resend_fails(
    log_dir, send_alert_mock, sentry_capture_mock
):
    # Resend failing must not prevent Sentry from being called.
    send_alert_mock.side_effect = RuntimeError("resend down")

    @its_error_log("test.script")
    def boom():
        raise ValueError("kaboom")

    with pytest.raises(ValueError):
        boom()

    send_alert_mock.assert_called_once()
    sentry_capture_mock.assert_called_once()  # ← key assertion

    # Both the original CRITICAL and the resend-failure marker are
    # in the local log.
    contents = _today_log(log_dir).read_text()
    assert "kaboom" in contents
    assert "[resend-alert-failed]" in contents


def test_resend_called_even_when_sentry_fails(
    log_dir, send_alert_mock, sentry_capture_mock
):
    # Sentry failing must not prevent Resend from being called.
    sentry_capture_mock.side_effect = RuntimeError("sentry down")

    @its_error_log("test.script")
    def boom():
        raise ValueError("kaboom")

    with pytest.raises(ValueError):
        boom()

    send_alert_mock.assert_called_once()  # ← Resend still ran
    sentry_capture_mock.assert_called_once()

    contents = _today_log(log_dir).read_text()
    assert "[sentry-capture-failed]" in contents


def test_both_legs_failing_still_does_not_raise(
    log_dir, send_alert_mock, sentry_capture_mock
):
    send_alert_mock.side_effect = RuntimeError("resend down")
    sentry_capture_mock.side_effect = RuntimeError("sentry down")

    @its_error_log("test.script")
    def boom():
        raise ValueError("the real bug")

    # Must STILL re-raise the original ValueError — not any of the
    # side-channel failures.
    with pytest.raises(ValueError, match="the real bug"):
        boom()

    contents = _today_log(log_dir).read_text()
    # Both marker lines present so operator can see both legs failed.
    assert "[resend-alert-failed]" in contents
    assert "[sentry-capture-failed]" in contents


def test_sentry_recursion_guard_blocks_reentry(
    log_dir, send_alert_mock, sentry_capture_mock
):
    # If sentry_client.capture_exception somehow triggered another log()
    # call that hit CRITICAL, the inner _alert_critical's Sentry leg
    # must see the guard and skip.
    inner_call_count = {"value": 0}

    def reentrant_capture(script, message, exc_info, correlation_id=None):
        inner_call_count["value"] += 1
        log(Severity.CRITICAL, "inner", "reentered")

    sentry_capture_mock.side_effect = reentrant_capture

    @its_error_log("test.script")
    def boom():
        raise ValueError("outer")

    with pytest.raises(ValueError):
        boom()

    # Outer call fires Sentry once; inner log()'s CRITICAL path tries
    # to fire Sentry but the guard skips it. Net: 1 call.
    assert sentry_capture_mock.call_count == 1
    assert inner_call_count["value"] == 1


def test_sentry_guard_and_resend_guard_are_independent(
    log_dir, send_alert_mock, sentry_capture_mock
):
    # Sentry's guard being set shouldn't block Resend from firing on
    # the SAME _alert_critical call, and vice versa. The two guards are
    # leg-specific.
    import shared.error_log as el

    # Force Sentry's guard "on" before the call.
    el._in_sentry_capture = True
    try:
        @its_error_log("test.script")
        def boom():
            raise ValueError("kaboom")

        with pytest.raises(ValueError):
            boom()

        # Sentry skipped (guard was set); Resend still fired.
        sentry_capture_mock.assert_not_called()
        send_alert_mock.assert_called_once()
    finally:
        el._in_sentry_capture = False


# ---- Correlation ID threading (PR α) -------------------------------------


def _trigger_critical(script: str = "test.script", exc_message: str = "kaboom"):
    """Convenience: fire one CRITICAL via the decorator and let it re-raise."""
    @its_error_log(script)
    def boom():
        raise ValueError(exc_message)

    with pytest.raises(ValueError, match=exc_message):
        boom()


def test_alert_critical_generates_correlation_id(
    log_dir, send_alert_mock, sentry_capture_mock, add_rows_mock
):
    # Same UUID4 reaches all three legs. Single CRITICAL → single correlation.
    _trigger_critical()

    subject, _ = send_alert_mock.call_args.args
    # Sentry receives correlation_id as a kwarg.
    sentry_kwargs = sentry_capture_mock.call_args.kwargs
    sentry_corr = sentry_kwargs.get("correlation_id")
    assert sentry_corr is not None
    assert _UUID4_RE.match(sentry_corr)

    # Subject embeds the first 8 chars of the same UUID.
    assert f"[corr: {sentry_corr[:8]}]" in subject

    # Smartsheet row has the full UUID in Correlation_ID column.
    _, row = _row_payload(add_rows_mock)
    assert row["Correlation_ID"] == sentry_corr


def test_alert_critical_correlation_id_in_resend_subject(
    log_dir, send_alert_mock, sentry_capture_mock
):
    _trigger_critical()
    subject, _ = send_alert_mock.call_args.args
    # `[corr: ........]` — exactly 8 lowercase hex chars after the space.
    assert re.search(r"\[corr: [0-9a-f]{8}\]", subject)


def test_alert_critical_correlation_id_in_resend_body(
    log_dir, send_alert_mock, sentry_capture_mock
):
    _trigger_critical()
    _, body = send_alert_mock.call_args.args
    # `Correlation: <full-uuid>` line in body — full UUID, not short form.
    m = re.search(r"Correlation: ([0-9a-f-]+)", body)
    assert m is not None
    assert _UUID4_RE.match(m.group(1))


def test_alert_critical_correlation_id_in_sentry_tag(
    log_dir, send_alert_mock, sentry_capture_mock
):
    _trigger_critical()
    kwargs = sentry_capture_mock.call_args.kwargs
    assert "correlation_id" in kwargs
    assert _UUID4_RE.match(kwargs["correlation_id"])


def test_alert_critical_correlation_id_in_smartsheet_row(
    log_dir, send_alert_mock, sentry_capture_mock, add_rows_mock
):
    _trigger_critical()
    _, row = _row_payload(add_rows_mock)
    assert _UUID4_RE.match(row["Correlation_ID"])


def test_alert_critical_legacy_log_call_writes_blank_correlation_id(
    log_dir, add_rows_mock
):
    # log() called directly (not via decorator) — no correlation_id supplied;
    # the Correlation_ID cell must still be present (blank) so the column
    # write doesn't fail on the missing key.
    log(Severity.WARN, "test.script", "no corr here")
    _, row = _row_payload(add_rows_mock)
    assert row["Correlation_ID"] == ""


# ---- Resend dedupe gating -----------------------------------------------


def test_resend_suppressed_when_dedupe_says_no(
    log_dir, send_alert_mock, sentry_capture_mock, add_rows_mock, mocker
):
    mocker.patch("shared.alert_dedupe.should_fire", return_value=False)

    _trigger_critical()

    # Resend suppressed.
    send_alert_mock.assert_not_called()
    # Sentry + Smartsheet always write per §27.
    sentry_capture_mock.assert_called_once()
    add_rows_mock.assert_called_once()
    # Marker line surfaces the suppression.
    assert "[resend-alert-suppressed]" in _today_log(log_dir).read_text()


def test_resend_fires_when_dedupe_says_yes(
    log_dir, send_alert_mock, sentry_capture_mock, add_rows_mock, mocker
):
    should_fire = mocker.patch("shared.alert_dedupe.should_fire", return_value=True)
    record_fire = mocker.patch("shared.alert_dedupe.record_fire")

    _trigger_critical()

    should_fire.assert_called_once_with("test.script::uncaught_exception")
    send_alert_mock.assert_called_once()
    sentry_capture_mock.assert_called_once()
    record_fire.assert_called_once_with("test.script::uncaught_exception")


def test_dedupe_module_exception_falls_open(
    log_dir, send_alert_mock, sentry_capture_mock, mocker
):
    # If should_fire raises (e.g., flock failure, disk full, JSON error
    # we somehow didn't catch internally), the Resend leg must still send.
    # Fail-open is the load-bearing invariant — a bug in dedupe must never
    # silently drop a CRITICAL email.
    mocker.patch(
        "shared.alert_dedupe.should_fire",
        side_effect=RuntimeError("disk full"),
    )

    _trigger_critical()

    send_alert_mock.assert_called_once()
    sentry_capture_mock.assert_called_once()
    contents = _today_log(log_dir).read_text()
    assert "[alert-dedupe-state-error]" in contents
    assert "disk full" in contents


def test_record_fire_exception_after_successful_send_does_not_raise(
    log_dir, send_alert_mock, sentry_capture_mock, mocker
):
    # If record_fire raises after a successful send, the send itself
    # already happened — no behavioral regression. Marker line written.
    mocker.patch("shared.alert_dedupe.should_fire", return_value=True)
    mocker.patch(
        "shared.alert_dedupe.record_fire",
        side_effect=RuntimeError("write failed"),
    )

    _trigger_critical()

    send_alert_mock.assert_called_once()
    contents = _today_log(log_dir).read_text()
    assert "[alert-dedupe-state-error]" in contents
    assert "write failed" in contents


def test_five_same_key_critical_calls_one_resend_five_smartsheet_five_sentry(
    log_dir, send_alert_mock, sentry_capture_mock, add_rows_mock
):
    # Full integration through the real `alert_dedupe` module against a
    # tmp_path state file (per the autouse `alert_dedupe_state` fixture).
    # Five CRITICALs with the same `(script, error_code)` key. Expected:
    # - 1 Resend send (first one through; subsequent four suppressed).
    # - 5 Smartsheet rows (§27 — records every time).
    # - 5 Sentry captures (§27 — records every time).
    # All 5 rows share ONE correlation ID per CRITICAL (i.e., 5 distinct
    # UUIDs total, not 1 UUID reused).
    for _ in range(5):
        _trigger_critical()

    assert send_alert_mock.call_count == 1
    assert add_rows_mock.call_count == 5
    assert sentry_capture_mock.call_count == 5

    # 5 distinct correlation IDs landed across the Smartsheet writes.
    corr_ids = [call.args[1][0]["Correlation_ID"] for call in add_rows_mock.call_args_list]
    assert len(set(corr_ids)) == 5
    for c in corr_ids:
        assert _UUID4_RE.match(c)

    # The single Resend send carries one of those correlation IDs (the
    # first — the others were suppressed).
    sent_subject, _ = send_alert_mock.call_args.args
    short = corr_ids[0][:8]
    assert f"[corr: {short}]" in sent_subject


# ---- §3.1: ITS_Errors record write bypasses the circuit breaker ----------


def test_its_errors_write_runs_under_circuit_breaker_bypass(log_dir, add_rows_mock):
    """§3.1: the ITS_Errors record write is wrapped in circuit_breaker.bypass(),
    so an OPEN breaker can never short-circuit the forensic surface (and, since
    bypass also exempts failure-counting — see
    test_circuit_breaker.test_bypass_does_not_count_failures — the forensic leg
    can't drive the breaker). Captures _bypass_depth at the moment add_rows runs.
    """
    import shared.circuit_breaker as cb
    from shared import sheet_ids

    seen: dict[str, object] = {}

    def _capture(sheet_id, rows):
        seen["bypass_depth"] = cb._bypass_depth
        seen["sheet_id"] = sheet_id
        return [1]

    add_rows_mock.side_effect = _capture

    log(Severity.ERROR, "test.script", "boom", error_code="test_error")

    assert seen["bypass_depth"] == 1  # the write executed inside bypass()
    assert seen["sheet_id"] == sheet_ids.SHEET_ERRORS
