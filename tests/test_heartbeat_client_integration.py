"""Live-API integration test for shared/heartbeat_client.py.

Purpose
-------
Exercises the full wired path: read the operator's real Healthchecks.io ping
URL from ITS_Config via ``smartsheet_client.get_setting``, then call
``heartbeat_client.ping(url)`` against the live endpoint. Targets the
SDK-vs-Live class of bug (Op Stds v13 §30) that mocks cannot catch — in this
case, a network library contract mismatch or a misconfigured URL that passes
unit tests but fails at runtime.

What it exercises
-----------------
- ``smartsheet_client.get_setting("system.heartbeat_url", workstream="global")``
  — proves the ITS_Config row is reachable and returns a non-placeholder URL.
- ``heartbeat_client.ping(url)`` against the live Healthchecks.io endpoint —
  proves the outbound GET reaches the monitor and receives a 2xx response.
- The fail-soft invariant: asserts that ``shared.heartbeat_client.log`` was
  NOT called with ``error_code="heartbeat_ping_failed"``, because ``ping``
  returning ``None`` alone is insufficient evidence (it returns ``None`` on
  both success AND failure).
- A secondary independent ``requests.get`` confirms the ping URL accepts
  traffic at the HTTP layer, independent of the client wrapper.

Requirements
------------
- ``ITS_SMARTSHEET_TOKEN`` present in macOS Keychain (same source the runtime
  SDK uses). The module skips cleanly when the token is absent.
- Network access to ``api.smartsheet.com`` and ``hc-ping.com`` (Healthchecks.io
  ping domain). NOT run in CI — GitHub Actions has no Keychain.
- pytest-mock (``mocker`` fixture) — already in the project dev dependencies.

Side-effect note
----------------
Pinging the operator's real Healthchecks.io URL registers one extra heartbeat
against the live monitor. This has ZERO adverse effect on the monitor's health
logic: Healthchecks.io only fires an alert when pings *stop* arriving within
the grace window. An extra ping is harmless and indistinguishable from the
watchdog's normal daily beacon.
"""
from __future__ import annotations

import pytest
import requests  # type: ignore[import-untyped]
from pytest_mock import MockerFixture

from shared import heartbeat_client, keychain, smartsheet_client
from shared.smartsheet_client import SmartsheetError

pytestmark = pytest.mark.integration

# Seed value written to ITS_Config during initial provisioning. A fork that
# hasn't yet configured a real Healthchecks.io monitor carries this token;
# attempting to ping it would 404, which is a false failure for the test.
_PLACEHOLDER = "PLACEHOLDER_uptimerobot_heartbeat_url"


@pytest.fixture(scope="module")
def _token_available() -> str:
    """Skip the whole module if ITS_SMARTSHEET_TOKEN isn't in Keychain."""
    try:
        token = keychain.get_secret("ITS_SMARTSHEET_TOKEN")
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"ITS_SMARTSHEET_TOKEN unavailable: {e!r}")
    if not token:
        pytest.skip("ITS_SMARTSHEET_TOKEN returned empty")
    return token


def test_ping_reaches_live_healthchecks_endpoint(
    _token_available: str,
    mocker: MockerFixture,
) -> None:
    """Full wired path: ITS_Config → ping URL → live GET → 2xx confirmed.

    Primary assertion: ``heartbeat_client.log`` was NOT called with
    ``error_code="heartbeat_ping_failed"``. Because ``ping()`` is fail-soft
    (returns ``None`` on both success and failure), the absence of the WARN log
    call is the load-bearing success signal — it proves ``raise_for_status()``
    did not raise, i.e. the monitor returned a 2xx.

    Secondary assertion: a direct ``requests.get`` to the same URL returns
    ``resp.ok``, confirming Healthchecks.io accepted the ping independently
    of our client wrapper.
    """
    # -- 1. Resolve the operator's real ping URL from ITS_Config. ------------
    try:
        url = smartsheet_client.get_setting("system.heartbeat_url", workstream="global")
    except SmartsheetError as e:
        pytest.skip(f"Could not read system.heartbeat_url from ITS_Config: {e!r}")

    if not url:
        pytest.skip("system.heartbeat_url is empty in ITS_Config — monitor not provisioned")

    if url == _PLACEHOLDER:
        pytest.skip(
            f"system.heartbeat_url is still the seed placeholder {_PLACEHOLDER!r} "
            "— no real Healthchecks.io monitor provisioned for this fork"
        )

    # -- 2. Primary assertion: ping() succeeds (no WARN log fired). ----------
    log_spy = mocker.patch("shared.heartbeat_client.log")

    heartbeat_client.ping(url)

    # Confirm the WARN branch was NOT taken. heartbeat_client.ping passes
    # error_code as a keyword (shared.error_log.log declares it keyword-only),
    # so the kwargs are the only place it can appear.
    for call in log_spy.call_args_list:
        error_code = (call.kwargs or {}).get("error_code")
        assert error_code != "heartbeat_ping_failed", (
            f"heartbeat_client.ping({url!r}) logged a WARN with "
            f"error_code='heartbeat_ping_failed' — live GET failed. "
            f"Full call: {call!r}"
        )

    # -- 3. Secondary assertion: direct GET confirms 2xx at the HTTP layer. --
    resp = requests.get(url, timeout=10)
    assert resp.ok, (
        f"Direct GET to Healthchecks.io URL {url!r} returned HTTP {resp.status_code} "
        "— the monitor endpoint is not accepting pings."
    )
