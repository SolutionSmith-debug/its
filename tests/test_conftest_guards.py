"""Self-tests for the autouse hermeticity guards in `tests/conftest.py`.

A guard nobody verifies is a guard that quietly stops guarding. Each test here
commits the synthetic violation the corresponding fixture exists to catch, so if
a fixture is removed, reordered, or silently neutered, THIS file goes red rather
than the suite resuming its old habit of talking to production.

Why these guards exist: the keychain stub hands out placeholder credentials, and
that was long mistaken for "unit tests can't reach the network". It never was —
a bad token makes a call FAIL, it does not stop the socket opening. Unit tests
really were reaching api.smartsheet.com, api.resend.com and Sentry, writing 401
noise into the LIVE operator log (13,850 lines on 2026-07-19, ~80% of that day's
alert-level volume) and getting mis-diagnosed as a Smartsheet outage three times.
A test-emitted CRITICAL even attempted to page the operator through Resend.
"""
from __future__ import annotations

import socket
from pathlib import Path

import pytest


def test_external_network_is_blocked() -> None:
    """A real socket to an off-host address must fail loud and name the test."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    with pytest.raises(AssertionError, match="opened a REAL network connection"):
        sock.connect(("api.smartsheet.com", 443))
    sock.close()


def test_loopback_is_still_allowed() -> None:
    """Loopback must stay usable — in-process ASGI clients are not the hazard.

    Connecting to a closed local port raises ConnectionRefusedError (an OS-level
    refusal), NOT the guard's AssertionError. That distinction is the assertion:
    the guard let the call through and the kernel answered.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(0.5)
    try:
        with pytest.raises((ConnectionRefusedError, OSError)) as exc:
            sock.connect(("127.0.0.1", 1))
        assert not isinstance(exc.value, AssertionError), "guard wrongly blocked loopback"
    finally:
        sock.close()


def test_error_log_writes_go_to_tmp_not_the_operator_log() -> None:
    """`error_log.LOG_DIR` must be redirected away from ~/its/logs."""
    import shared.error_log as el

    live = (Path.home() / "its" / "logs").resolve()
    assert el.LOG_DIR.resolve() != live, (
        "error_log.LOG_DIR still points at the LIVE operator log — unit-test output "
        "would pollute the operator's own signal"
    )


def test_error_log_egress_legs_are_neutralized() -> None:
    """Logging must not attempt an ITS_Errors row, a Resend page, or a Sentry capture.

    Emitting a CRITICAL is the sharpest case: in production that fires the
    triple-fire. Here it must complete without touching a single client — proven
    by the network guard staying silent (it would raise if a socket opened).
    """
    import shared.error_log as el

    el.log(
        el.Severity.CRITICAL,
        "tests.conftest_guards",
        "synthetic CRITICAL — must not page anyone",
        error_code="synthetic_guard_probe",
    )


def test_live_state_writes_are_blocked() -> None:
    """The pre-existing state guard still covers ~/its/state (forensic class #8)."""
    target = Path.home() / "its" / "state" / "conftest_guard_probe.json"
    with pytest.raises(AssertionError, match="wrote LIVE state"):
        target.write_text("{}")
    assert not target.exists()
