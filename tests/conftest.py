"""Suite-wide test fixtures.

Two of the autouse fixtures below are the long-standing pair, default-applied to every
test under `tests/`:

1. `_mock_keychain` — replaces `shared.keychain.get_secret` with a stub
   that returns deterministic test tokens (`f"test-{service}"`).
   Eliminates the macOS-only `security` CLI dependency (which was failing
   on Linux CI runners since PR #68's R3 Session 3 introduced
   `safety_reports/weekly_send_poll.py`).

   NOTE: this fixture was long described here as preventing "accidental live
   network calls". It does not, and never did — a placeholder token makes an
   outbound call FAIL (401), it does not stop the socket from opening. That
   misreading is why thousands of real 401s/day reached Smartsheet, Resend and
   Sentry and polluted the live operator log. `_forbid_external_network` below
   is the control that actually holds that line.

2. `_mock_kill_switch_state` — patches the `get_setting` attribute on
   `shared.kill_switch.smartsheet_client` so the `@require_active`
   decorator sees `system.state="ACTIVE"` by default. Tests that
   exercise kill-switch behavior re-mock inside the test body; the
   re-mock takes precedence because pytest applies test-local
   `mocker.patch` after autouse fixtures.

Integration tests gated by `-m integration` need real keychain access AND
real Smartsheet calls. `_mock_keychain` auto-opts-out of the stub for any
test carrying the `integration` marker (module-level `pytestmark` OR a
per-test `@pytest.mark.integration` decorator) — so a new integration test
gets the real keychain automatically, with no filename list to maintain.
The marker check resolves per-test, so MIXED files (one whose unit tests
must keep the stub but whose `@pytest.mark.integration` tests must not)
are handled correctly.

Why conftest.py rather than per-test mocks: the per-call-site mock
strategy is fragile when a downstream library (`shared.kill_switch`)
makes calls on its OWN namespace binding rather than the importing
test's binding. The conftest patches the *source* attribute, so any
import path that resolves to `shared.keychain.get_secret` /
`shared.kill_switch.smartsheet_client.get_setting` gets the stub.

The conftest fix is the immediate hole-closer. A durable structural fix
(lazy keychain loading + dependency-injected kill_switch) is logged in
`docs/tech_debt.md` as a follow-on.
"""
from __future__ import annotations

import os
import socket
from pathlib import Path

import pytest

_KEYCHAIN_OPT_OUT_FILES = frozenset({"test_keychain.py", "test_helpers.py"})

# Tests whose SUBJECT is error_log's egress legs (the ITS_Errors row write, the
# Resend page, the Sentry capture). They mock those clients themselves and must
# keep the real functions under test, so `_neutralize_error_log_egress` skips them.
_EGRESS_OPT_OUT_FILES = frozenset(
    {
        "test_error_log.py",
        "test_error_log_redaction_backstop.py",
        "test_resend_client.py",
        "test_sentry_client.py",
        "test_alert_dedupe.py",
    }
)

# --- Live-state write guard (forensic class #8 / #294) --------------------
# A test that silently refreshed the REAL watchdog marker masked watchdog
# Checks C+I (#294); more broadly, a unit test writing live state couples CI to
# host state and can disable real safety checks. The `_forbid_live_state_writes`
# autouse fixture below is the DRIFT-PROOF catch: it wraps the two write idioms
# (`Path.write_text`/`write_bytes` for markers; `os.replace` for the sanctioned
# `state_io` atomic-write) and raises on any write resolving under a protected
# live dir — regardless of which module's per-daemon STATE_DIR / WATCHDOG_MARKER_DIR
# constant produced the path. Real paths captured ONCE here, before any test-local
# redirect, so the guard always compares against the genuine live locations.
_REAL_STATE_DIR = (Path.home() / "its" / "state").resolve()
_REAL_WATCHDOG_DIR = (Path.home() / "its" / ".watchdog").resolve()
_PROTECTED_LIVE_DIRS = (_REAL_STATE_DIR, _REAL_WATCHDOG_DIR)

_ORIG_WRITE_TEXT = Path.write_text
_ORIG_WRITE_BYTES = Path.write_bytes
_ORIG_OS_REPLACE = os.replace

# --- Live-log + external-network guards -----------------------------------
# The keychain stub above hands every client a PLACEHOLDER credential, which was
# long assumed to "prevent live network calls" (this file's own docstring said
# so). It does not — it only makes them FAIL. A unit test reaching a real client
# path still opened a real socket to api.smartsheet.com / api.resend.com / Sentry
# and got a 401 back. 13,850 such lines landed in the LIVE operator log on
# 2026-07-19 alone (80% of that day's WARN/ERROR/CRITICAL volume), and the same
# pollution was mis-diagnosed as a Smartsheet outage three separate times.
#
# Two compounding reasons that mattered: `error_log.LOG_DIR` is absolute
# (~/its/logs), so the noise lands in the OPERATOR's log no matter which checkout
# runs pytest; and a test-emitted CRITICAL genuinely attempts to PAGE the operator
# through Resend — stopped only by the placeholder key being rejected. A wrong
# credential was doing a boundary's job.
#
# These two fixtures make the boundary structural, mirroring
# `_forbid_live_state_writes` below: same fail-loud, same `integration` opt-out.
# Captured ONCE, before any redirect, so the constants-parity drift guard in
# tests/test_operator_dashboard.py can still compare the GENUINE live locations.
REAL_LOG_DIR = (Path.home() / "its" / "logs").resolve()
REAL_STATE_DIR = _REAL_STATE_DIR

# Loopback stays allowed: in-process ASGI clients and any local helper are not
# the hazard. Only egress OFF the host is.
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost", "", "0.0.0.0"})
_ORIG_SOCKET_CONNECT = socket.socket.connect
_ORIG_SOCKET_CONNECT_EX = socket.socket.connect_ex


def _is_loopback(address: object) -> bool:
    """True for AF_UNIX paths and loopback TCP/UDP targets."""
    if not isinstance(address, tuple) or not address:
        return True  # AF_UNIX (str path) / anything not host-port shaped
    host = address[0]
    return isinstance(host, str) and host in _LOOPBACK_HOSTS


def _is_under_protected_live_dir(target: object) -> bool:
    try:
        resolved = Path(target).resolve()  # type: ignore[arg-type]
    except (OSError, ValueError, TypeError):
        return False
    return any(resolved.is_relative_to(d) for d in _PROTECTED_LIVE_DIRS)


@pytest.fixture(autouse=True)
def _neutralize_circuit_breaker(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Keep the F08 circuit breaker out of the way of non-integration tests.

    The breaker now wraps all 16 `smartsheet_client` network methods. For unit
    tests we (1) redirect its state file to a per-test tmp path so nothing ever
    reads/writes the real `~/its/state/circuit_breaker.json`, and (2) pre-cache
    a DISABLED config so the guard is a pure pass-through that never issues a
    config read (a read would hit the mocked SDK and perturb call-count
    assertions in `tests/test_smartsheet_client.py`).

    Integration tests (`@pytest.mark.integration`) opt out — they exercise the
    real breaker (live trip/reset against the sandbox sheet), managing their
    own state file and config explicitly. `tests/test_circuit_breaker.py` is
    unaffected either way: it passes `state_path` + `config_loader` to every
    call, so neither the patched `STATE_FILE` nor the cache is consulted there.
    """
    if request.node.get_closest_marker("integration") is not None:
        return
    import shared.circuit_breaker as _cb
    import shared.smartsheet_client as _sc

    monkeypatch.setattr(_cb, "STATE_FILE", tmp_path / "circuit_breaker.json")
    monkeypatch.setattr(
        _sc,
        "_circuit_config_cache",
        _cb.CircuitConfig(enabled=False, failure_threshold=5, cooldown_seconds=300),
    )


@pytest.fixture(autouse=True)
def _neutralize_smartsheet_retry(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Keep `smartsheet_client._transient_retry` out of the way of unit tests.

    Same reasoning as `_neutralize_circuit_breaker` above, one layer down: with the
    shipped defaults a test that makes an enrolled read raise a 5xx/timeout would
    genuinely `time.sleep(2)` then `time.sleep(5)` before the expected raise — real
    wall-clock in a hermetic suite, plus three SDK calls where the assertion expects one.
    Pre-caching a DISABLED config makes the decorator a pure pass-through AND stops it
    issuing its own config read.

    `tests/test_smartsheet_retry.py` installs its own RetryConfig per test, which
    overwrites this cache — that is the deliberate opt-in. Integration tests opt out and
    exercise the real resolution path.
    """
    if request.node.get_closest_marker("integration") is not None:
        return
    import shared.smartsheet_client as _sc

    monkeypatch.setattr(
        _sc,
        "_retry_config_cache",
        _sc.RetryConfig(
            enabled=False, max_extra_attempts=0, backoff_seconds=(),
            source_summary="test-neutralized",
        ),
    )


@pytest.fixture(autouse=True)
def _mock_keychain(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Default keychain stub for all unit tests.

    Two auto-opt-out paths, both of which leave the REAL `get_secret` in
    place:

    1. Filename opt-out (`_KEYCHAIN_OPT_OUT_FILES`) — `tests/test_keychain.py`
       (tests `get_secret` itself) and `tests/test_helpers.py` (asserts
       `get_secret`'s real error message via a macOS-only test). Those files
       want the real keychain entry point under test; the stub would
       short-circuit assertions. They are NOT integration-marked, so the
       marker opt-out below would not cover them — the filename list is the
       mechanism they need.

    2. Marker opt-out — any test carrying the `integration` marker. Live
       integration tests make real Smartsheet calls, so `get_client()` must
       read the real `ITS_SMARTSHEET_TOKEN`, not the `f"test-..."` stub. This
       is the durable fix: it auto-covers every current and future
       `@pytest.mark.integration` test (there are ~10 such files) with no
       filename list to maintain, and resolves at PER-TEST granularity so a
       mixed file keeps the stub for its unit tests while bypassing it only
       for its integration tests.
    """
    if request.node.path.name in _KEYCHAIN_OPT_OUT_FILES:
        return
    if request.node.get_closest_marker("integration") is not None:
        return

    def _fake_get_secret(service: str, account: str | None = None) -> str:
        return f"test-{service}"

    monkeypatch.setattr("shared.keychain.get_secret", _fake_get_secret)


@pytest.fixture(autouse=True)
def _mock_kill_switch_state(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Default `system.state=ACTIVE` for `@require_active` checks.

    Patches `shared.kill_switch.check_system_state` directly rather than
    its underlying `smartsheet_client.get_setting`. The latter approach
    would mutate the `smartsheet_client` module's `get_setting` attribute
    (since `kill_switch.smartsheet_client` IS the same module) and break
    `tests/test_smartsheet_client.py::test_get_setting_*` which exercises
    that function directly.

    Auto-opt-out for `tests/test_kill_switch.py` since those tests
    exercise `check_system_state`'s own branches (happy paths, three
    fail-open modes) and need the real function under test. Any other
    test that needs real kill_switch behavior can opt out by re-patching
    explicitly inside the test body — `monkeypatch.undo()` undoes this
    fixture's patch, or `mocker.patch(...)` for the same target wins
    because it applies after autouse.
    """
    if request.node.path.name == "test_kill_switch.py":
        return
    from shared.kill_switch import SystemState

    monkeypatch.setattr(
        "shared.kill_switch.check_system_state", lambda: SystemState.ACTIVE
    )


@pytest.fixture(autouse=True)
def _redirect_live_log_dir(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Send `error_log`'s daily file to tmp so tests never write the OPERATOR log.

    `error_log.LOG_DIR` is absolute (~/its/logs) and read at call time, so without
    this every test that logs — including the many that deliberately exercise
    WARN/ERROR/CRITICAL paths — appended to the live operator log from whatever
    checkout ran pytest. That buried real signal (80% of 2026-07-19's alert-level
    lines were test noise) and got mis-read as a production incident three times.

    Redirect, not forbid: logging is what these tests are testing. They just must
    not do it in the operator's file. `integration` tests opt out, like every
    other guard here.
    """
    if request.node.get_closest_marker("integration") is not None:
        return
    import shared.error_log as _el

    # Only the WRITE side is redirected. `operator_dashboard.config.LOGS_DIR` is
    # deliberately left pointing at the live directory: the dashboard only READS
    # it (the log-tail panel), reads are harmless, and repointing it would make
    # tests/test_operator_dashboard.py's constants drift-guard vacuous — it exists
    # to catch exactly the case where a root silently stops pointing at ~/its.
    monkeypatch.setattr(_el, "LOG_DIR", tmp_path / "logs")


@pytest.fixture(autouse=True)
def _neutralize_error_log_egress(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Keep `error_log` LOCAL in unit tests: no ITS_Errors row, no Resend, no Sentry.

    `error_log.log()` has three egress legs beyond the local file — an ITS_Errors
    row write, a Resend page and a Sentry capture. A unit test that exercises any
    daemon path which logs (most of them) fired all three for real; only the
    placeholder credential made them fail. That is how unit tests came to attempt
    row writes against the PRODUCTION ITS_Errors sheet and to genuinely try to
    page the operator.

    Neutralizing the legs — rather than mocking Smartsheet per test — matches what
    those tests actually intend: they assert on BEHAVIOUR (that a WARN was raised,
    which error_code, that a fence held), never on the wire. The local file leg is
    untouched (redirected to tmp above), so log-content assertions still work.

    `_EGRESS_OPT_OUT_FILES` are the tests whose SUBJECT is these legs; they mock
    the clients themselves and must keep the real functions under test.
    """
    if request.node.path.name in _EGRESS_OPT_OUT_FILES:
        return
    if request.node.get_closest_marker("integration") is not None:
        return
    import shared.error_log as _el

    monkeypatch.setattr(_el, "_smartsheet_log", lambda *a, **k: None)
    for leg in ("_fire_resend_leg", "_fire_sentry_leg"):
        if hasattr(_el, leg):
            monkeypatch.setattr(_el, leg, lambda *a, **k: None)


@pytest.fixture(autouse=True)
def _forbid_external_network(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fail loud if a unit test opens a socket to anything off this host.

    The root fix for the pollution described above: the keychain stub only made
    outbound calls FAIL, it never stopped them. Unit tests were really reaching
    api.smartsheet.com, api.resend.com and Sentry — thousands of 401s a day from
    the operator's IP, with a test-emitted CRITICAL genuinely attempting to page
    through Resend. The placeholder credential was the only thing preventing a
    real page or a real write to the production ITS_Errors sheet.

    Guarding at `socket.connect` catches every client library at once (requests,
    urllib3, the Smartsheet SDK, resend, sentry_sdk) rather than needing a mock
    per SDK — the same drift-proof reasoning as `_forbid_live_state_writes`.
    Loopback and AF_UNIX stay allowed: in-process ASGI test clients are not the
    hazard; egress off the host is. A unit test that trips this must mock its
    client seam; a test that genuinely needs the network must be marked
    `@pytest.mark.integration` (which opts out).
    """
    if request.node.get_closest_marker("integration") is not None:
        return

    def _fail(address: object) -> None:
        raise AssertionError(
            f"test {request.node.nodeid} opened a REAL network connection to {address!r}.\n"
            "Unit tests must be hermetic: the keychain stub only makes such calls FAIL "
            "(401), it does not prevent them — that is how thousands of live 401s/day "
            "reached Smartsheet/Resend/Sentry and polluted the operator log. Mock the "
            "client seam, or mark the test @pytest.mark.integration if it genuinely "
            "needs the network."
        )

    def _guarded_connect(self: socket.socket, address: object) -> object:
        if not _is_loopback(address):
            _fail(address)
        return _ORIG_SOCKET_CONNECT(self, address)  # type: ignore[arg-type]

    def _guarded_connect_ex(self: socket.socket, address: object) -> object:
        if not _is_loopback(address):
            _fail(address)
        return _ORIG_SOCKET_CONNECT_EX(self, address)  # type: ignore[arg-type]

    monkeypatch.setattr(socket.socket, "connect", _guarded_connect)
    monkeypatch.setattr(socket.socket, "connect_ex", _guarded_connect_ex)


@pytest.fixture(autouse=True)
def _forbid_live_state_writes(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fail loud if a unit test writes under the REAL ~/its/state or ~/its/.watchdog.

    The drift-proof complement to the per-test state redirects (e.g.
    `_neutralize_circuit_breaker` above, which sends `circuit_breaker.STATE_FILE`
    to tmp). Where a redirect must enumerate every per-daemon STATE_DIR /
    WATCHDOG_MARKER_DIR constant (and silently drifts when a new one lands), this
    guard catches ANY write resolving under a protected live dir at the
    Path.write_text / write_bytes / os.replace layer — so a missed redirect fails
    loud here instead of silently mutating host state (forensic class #8 / the
    #294 watchdog-marker masking). A unit test that trips this must redirect its
    state-path constant to `tmp_path`; a test that genuinely needs live state must
    be marked `@pytest.mark.integration` (which opts out, like the other fixtures).
    """
    if request.node.get_closest_marker("integration") is not None:
        return

    def _guard(target: object, idiom: str) -> None:
        if _is_under_protected_live_dir(target):
            raise AssertionError(
                f"test {request.node.nodeid} wrote LIVE state via {idiom}: {target!r}\n"
                "Unit tests must not touch ~/its/state or ~/its/.watchdog (forensic "
                "class #8 / #294 — silently refreshing a real marker masked watchdog "
                "Checks C+I). Redirect the path constant to tmp_path with monkeypatch, "
                "or mark the test @pytest.mark.integration if it genuinely needs live state."
            )

    def _guarded_write_text(self: Path, *args: object, **kwargs: object) -> int:
        _guard(self, "Path.write_text")
        return _ORIG_WRITE_TEXT(self, *args, **kwargs)  # type: ignore[arg-type]

    def _guarded_write_bytes(self: Path, *args: object, **kwargs: object) -> int:
        _guard(self, "Path.write_bytes")
        return _ORIG_WRITE_BYTES(self, *args, **kwargs)  # type: ignore[arg-type]

    def _guarded_os_replace(src: object, dst: object, *args: object, **kwargs: object) -> None:
        _guard(dst, "os.replace")
        return _ORIG_OS_REPLACE(src, dst, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "write_text", _guarded_write_text)
    monkeypatch.setattr(Path, "write_bytes", _guarded_write_bytes)
    monkeypatch.setattr(os, "replace", _guarded_os_replace)
