"""Suite-wide test fixtures.

Two autouse fixtures, both default-applied to every test under `tests/`:

1. `_mock_keychain` — replaces `shared.keychain.get_secret` with a stub
   that returns deterministic test tokens (`f"test-{service}"`).
   Eliminates the macOS-only `security` CLI dependency (which was failing
   on Linux CI runners since PR #68's R3 Session 3 introduced
   `safety_reports/weekly_send_poll.py`) and prevents accidental live
   network calls during unit tests.

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
from pathlib import Path

import pytest

_KEYCHAIN_OPT_OUT_FILES = frozenset({"test_keychain.py", "test_helpers.py"})

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
