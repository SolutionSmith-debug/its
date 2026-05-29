"""Tests for shared/heartbeat_client.py.

`requests` is mocked at the module boundary (`shared.heartbeat_client.requests`)
so no network call is made. The contract under test: `ping()` issues a GET
with the given URL + timeout, and is FAIL-SOFT — it never raises, routing
every network / HTTP failure through a single WARN log under error_code
`heartbeat_ping_failed` (Op Stds v13 §3.1).

Run with: pytest -q tests/test_heartbeat_client.py
"""
from __future__ import annotations

import requests  # type: ignore[import-untyped]

from shared import heartbeat_client
from shared.error_log import Severity

_URL = "https://hc-ping.com/00000000-0000-0000-0000-000000000000"


# ---- GET dispatch --------------------------------------------------------


def test_ping_issues_get_with_url_and_explicit_timeout(mocker):
    mock_get = mocker.patch("shared.heartbeat_client.requests.get")
    mock_get.return_value.raise_for_status.return_value = None

    heartbeat_client.ping(_URL, timeout=4.0)

    mock_get.assert_called_once_with(_URL, timeout=4.0)


def test_ping_uses_default_timeout_when_omitted(mocker):
    mock_get = mocker.patch("shared.heartbeat_client.requests.get")
    mock_get.return_value.raise_for_status.return_value = None

    heartbeat_client.ping(_URL)

    _, kwargs = mock_get.call_args
    assert kwargs["timeout"] == 10.0


def test_ping_success_logs_nothing(mocker):
    """A clean 2xx (raise_for_status no-ops) must not emit any WARN."""
    mock_get = mocker.patch("shared.heartbeat_client.requests.get")
    mock_get.return_value.raise_for_status.return_value = None
    mock_log = mocker.patch("shared.heartbeat_client.log")

    heartbeat_client.ping(_URL)

    mock_log.assert_not_called()


# ---- Fail-soft: network errors ------------------------------------------


def test_ping_failsoft_on_request_exception(mocker):
    """Base RequestException → swallowed, WARN logged, no raise."""
    mocker.patch(
        "shared.heartbeat_client.requests.get",
        side_effect=requests.RequestException("boom"),
    )
    mock_log = mocker.patch("shared.heartbeat_client.log")

    # Must NOT raise.
    heartbeat_client.ping(_URL)

    assert mock_log.call_count == 1
    call = mock_log.call_args
    assert call.args[0] is Severity.WARN
    assert call.kwargs["error_code"] == "heartbeat_ping_failed"


def test_ping_failsoft_on_connection_error(mocker):
    mocker.patch(
        "shared.heartbeat_client.requests.get",
        side_effect=requests.ConnectionError("refused"),
    )
    mock_log = mocker.patch("shared.heartbeat_client.log")

    heartbeat_client.ping(_URL)

    assert mock_log.call_args.kwargs["error_code"] == "heartbeat_ping_failed"


def test_ping_failsoft_on_timeout(mocker):
    mocker.patch(
        "shared.heartbeat_client.requests.get",
        side_effect=requests.Timeout("slow"),
    )
    mock_log = mocker.patch("shared.heartbeat_client.log")

    heartbeat_client.ping(_URL)

    assert mock_log.call_args.args[0] is Severity.WARN
    assert mock_log.call_args.kwargs["error_code"] == "heartbeat_ping_failed"


# ---- Fail-soft: non-2xx response (raise_for_status path) -----------------


def test_ping_failsoft_on_http_error_via_raise_for_status(mocker):
    """A 5xx (or 404) surfaces as HTTPError from raise_for_status; since
    HTTPError is a RequestException subclass it routes through the same
    WARN-and-swallow path."""
    mock_get = mocker.patch("shared.heartbeat_client.requests.get")
    mock_get.return_value.raise_for_status.side_effect = requests.HTTPError(
        "500 Server Error"
    )
    mock_log = mocker.patch("shared.heartbeat_client.log")

    heartbeat_client.ping(_URL)

    assert mock_log.call_count == 1
    assert mock_log.call_args.kwargs["error_code"] == "heartbeat_ping_failed"


# ---- Error hierarchy -----------------------------------------------------


def test_heartbeat_error_is_exception_subclass():
    """Defined for symmetry with sibling clients + future opt-in callers,
    even though ping() itself never raises it."""
    assert issubclass(heartbeat_client.HeartbeatError, Exception)
