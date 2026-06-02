"""Tests for shared/resend_client.py.

All HTTP and Keychain interactions are mocked — these tests never hit
the network and never read the real Keychain. The module-level API-key
cache is reset between tests via the autouse `reset_resend_state` fixture.

Run with: pytest -q tests/test_resend_client.py
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from shared import resend_client
from shared.resend_client import (
    ResendAuthError,
    ResendError,
    ResendNotFoundError,
    ResendRateLimitError,
)

# ---- Fixtures + helpers --------------------------------------------------


@pytest.fixture(autouse=True)
def reset_resend_state(mocker):
    """Reset the module's API-key cache and stub keychain reads."""
    mocker.patch.object(resend_client, "_api_key", None)
    mocker.patch(
        "shared.resend_client.keychain.get_secret",
        side_effect=lambda key, *a, **kw: f"fake-{key}",
    )


def _mock_response(
    *,
    status: int = 200,
    json_body: dict | None = None,
    headers: dict | None = None,
    text: str = "",
):
    response = MagicMock()
    response.status_code = status
    response.json.return_value = json_body if json_body is not None else {}
    response.headers = headers if headers is not None else {}
    response.text = text
    return response


def _patch_requests(mocker, responses):
    """Patch requests.request to return responses sequentially.

    `responses` is a list; each successive call to requests.request pops
    the next response. If only one is provided, that response is reused.
    """
    if not isinstance(responses, list):
        responses = [responses]
    return mocker.patch(
        "shared.resend_client.requests.request",
        side_effect=responses if len(responses) > 1 else responses * 100,
    )


# ---- get_client lazy load ------------------------------------------------


def test_get_client_lazy_loads_key_from_keychain():
    key1 = resend_client.get_client()
    key2 = resend_client.get_client()

    assert key1 == "fake-ITS_RESEND_API_KEY"
    assert key1 is key2  # cached on first call


def test_get_client_invokes_keychain_once(mocker):
    spy = mocker.patch(
        "shared.resend_client.keychain.get_secret",
        return_value="cached-key",
    )

    resend_client.get_client()
    resend_client.get_client()
    resend_client.get_client()

    spy.assert_called_once_with("ITS_RESEND_API_KEY")


# ---- send_alert payload + auth -------------------------------------------


def test_send_alert_builds_correct_payload_and_headers(mocker):
    req = _patch_requests(mocker, _mock_response(status=200, json_body={"id": "abc"}))

    resend_client.send_alert(
        subject="[ITS CRITICAL] hello",
        body="multi\nline body",
        to="ops@example.com",
    )

    req.assert_called_once()
    args, kwargs = req.call_args
    # Method + URL
    assert args == ("POST", "https://api.resend.com/emails")
    # Auth header
    assert kwargs["headers"]["Authorization"] == "Bearer fake-ITS_RESEND_API_KEY"
    assert kwargs["headers"]["Content-Type"] == "application/json"
    # JSON body shape
    body = kwargs["json"]
    assert body["to"] == "ops@example.com"
    assert body["subject"] == "[ITS CRITICAL] hello"
    assert body["text"] == "multi\nline body"
    assert "from" in body
    # Default-from must be a verified-domain placeholder — recipients see it
    assert "@" in body["from"]


def test_send_alert_defaults_to_from_its_config(mocker):
    # When `to` is omitted, the function reads system.operator_email from
    # ITS_Config via smartsheet_client.get_setting. Mock that boundary.
    get_setting = mocker.patch(
        "shared.smartsheet_client.get_setting",
        return_value="operator@evergreenmirror.com",
    )
    req = _patch_requests(mocker, _mock_response(status=200))

    resend_client.send_alert("s", "b")

    get_setting.assert_called_once_with(
        "system.operator_email", workstream="global"
    )
    assert req.call_args.kwargs["json"]["to"] == "operator@evergreenmirror.com"


def test_send_alert_falls_back_to_default_when_operator_email_none(mocker):
    """Missing system.operator_email → fall back to the build-time recipient
    (do NOT raise)."""
    mocker.patch("shared.smartsheet_client.get_setting", return_value=None)
    mocker.patch("shared.defaults.OPERATOR_EMAIL_FALLBACK", "fallback@example.com")
    req = _patch_requests(mocker, _mock_response(status=200))

    resend_client.send_alert("s", "b")

    assert req.call_args.kwargs["json"]["to"] == "fallback@example.com"


def test_send_alert_falls_back_on_breaker_short_circuit(mocker):
    """The operator_email read is a GUARDED Smartsheet call; when it
    short-circuits (breaker OPEN during an outage), send_alert falls back to the
    build-time recipient so the prolonged-open page still delivers."""
    from shared.smartsheet_client import SmartsheetCircuitOpenError

    mocker.patch(
        "shared.smartsheet_client.get_setting",
        side_effect=SmartsheetCircuitOpenError("breaker open"),
    )
    mocker.patch("shared.defaults.OPERATOR_EMAIL_FALLBACK", "fallback@example.com")
    req = _patch_requests(mocker, _mock_response(status=200))

    resend_client.send_alert("s", "b")

    assert req.call_args.kwargs["json"]["to"] == "fallback@example.com"


def test_send_alert_raises_when_no_recipient_anywhere(mocker):
    """Raise ONLY when system.operator_email is unreadable AND the build-time
    fallback is unset."""
    mocker.patch("shared.smartsheet_client.get_setting", return_value=None)
    mocker.patch("shared.defaults.OPERATOR_EMAIL_FALLBACK", "")
    _patch_requests(mocker, _mock_response(status=200))

    with pytest.raises(ResendError, match="no operator recipient"):
        resend_client.send_alert("s", "b")


# ---- HTTP error translation ----------------------------------------------


@pytest.mark.parametrize(
    "status,expected",
    [
        (401, ResendAuthError),
        (403, ResendAuthError),
        (404, ResendNotFoundError),
        (500, ResendError),
        (502, ResendError),
    ],
)
def test_error_status_translated_by_status(mocker, status, expected):
    _patch_requests(
        mocker,
        _mock_response(status=status, json_body={"message": "boom"}),
    )

    with pytest.raises(expected, match="boom"):
        resend_client.send_alert("s", "b", to="x@example.com")


def test_429_exhausted_after_retry_budget(mocker):
    # Three 429 responses → retry budget exhausted → ResendRateLimitError.
    responses = [_mock_response(status=429, json_body={"message": "slow down"})] * 3
    sleeps = mocker.patch("shared.resend_client.time.sleep")
    _patch_requests(mocker, responses)

    with pytest.raises(ResendRateLimitError, match="slow down"):
        resend_client.send_alert("s", "b", to="x@example.com")

    # Two sleeps between three attempts.
    assert sleeps.call_count == 2


def test_429_then_success_succeeds(mocker):
    sleeps = mocker.patch("shared.resend_client.time.sleep")
    _patch_requests(
        mocker,
        [
            _mock_response(status=429, headers={"Retry-After": "0.5"}),
            _mock_response(status=200, json_body={"id": "ok"}),
        ],
    )

    resend_client.send_alert("s", "b", to="x@example.com")

    sleeps.assert_called_once_with(0.5)  # honored Retry-After


def test_503_then_success_succeeds(mocker):
    mocker.patch("shared.resend_client.time.sleep")
    _patch_requests(
        mocker,
        [
            _mock_response(status=503),
            _mock_response(status=200),
        ],
    )

    resend_client.send_alert("s", "b", to="x@example.com")  # no exception


def test_retry_after_unparseable_falls_back_to_backoff(mocker):
    # Retry-After with garbage falls back to exponential backoff.
    sleeps = mocker.patch("shared.resend_client.time.sleep")
    _patch_requests(
        mocker,
        [
            _mock_response(status=429, headers={"Retry-After": "garbage"}),
            _mock_response(status=200),
        ],
    )

    resend_client.send_alert("s", "b", to="x@example.com")

    # First retry — attempt index 0 → 2^0 = 1.0s backoff.
    sleeps.assert_called_once_with(1.0)


# ---- Error-message extraction --------------------------------------------


def test_error_message_falls_back_to_response_text_on_invalid_json(mocker):
    response = _mock_response(
        status=500,
        text="<html>internal error</html>" * 50,
    )
    response.json.side_effect = ValueError  # not JSON
    _patch_requests(mocker, response)

    with pytest.raises(ResendError, match="internal error"):
        resend_client.send_alert("s", "b", to="x@example.com")


def test_error_message_truncated_to_200_chars(mocker):
    response = _mock_response(status=500, text="x" * 500)
    response.json.side_effect = ValueError
    _patch_requests(mocker, response)

    with pytest.raises(ResendError) as exc:
        resend_client.send_alert("s", "b", to="x@example.com")
    # The exception message includes "HTTP 500: <truncated text>". The
    # truncated text is the leading 200 chars of the body.
    assert exc.value.args[0].count("x") == 200
