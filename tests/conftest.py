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
real Smartsheet calls; they re-mock or override via test-level fixtures
(see `tests/test_smartsheet_client_integration.py` for the pattern).

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

import pytest

_KEYCHAIN_OPT_OUT_FILES = frozenset({"test_keychain.py", "test_helpers.py"})


@pytest.fixture(autouse=True)
def _mock_keychain(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Default keychain stub for all unit tests.

    Auto-opt-out for `tests/test_keychain.py` (tests `get_secret` itself)
    and `tests/test_helpers.py` (asserts `get_secret`'s real error message
    via a macOS-only test). Those files want the real keychain entry
    point under test; the stub would short-circuit assertions.
    """
    if request.node.path.name in _KEYCHAIN_OPT_OUT_FILES:
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
