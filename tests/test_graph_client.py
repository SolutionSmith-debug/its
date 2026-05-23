"""Tests for shared/graph_client.py.

All MSAL and HTTP interactions are mocked — these tests never hit the network
and never read the real Keychain. The module-level token cache is reset
between tests via the autouse `reset_graph_state` fixture.

Run with: pytest -q tests/test_graph_client.py
"""
from __future__ import annotations

import base64
from unittest.mock import MagicMock

import pytest

from shared import graph_client
from shared.graph_client import (
    GraphNotFoundError,
    GraphPermissionError,
    GraphRateLimitError,
)

# ---- Fixtures + helpers ---------------------------------------------------


@pytest.fixture(autouse=True)
def reset_graph_state(mocker):
    """Reset the module's token cache and stub keychain reads for every test."""
    mocker.patch.object(graph_client, "_token", None)
    mocker.patch.object(graph_client, "_token_expires_at", 0.0)
    mocker.patch(
        "shared.graph_client.keychain.get_secret",
        side_effect=lambda key, *a, **kw: f"fake-{key}",
    )


def _mock_msal(mocker, *, expires_in: int = 3600, access_token: str = "test-token"):
    """Patch msal.ConfidentialClientApplication; return the patch object."""
    app = MagicMock()
    app.acquire_token_for_client.return_value = {
        "access_token": access_token,
        "expires_in": expires_in,
    }
    return mocker.patch(
        "shared.graph_client.msal.ConfidentialClientApplication",
        return_value=app,
    )


def _mock_response(
    *,
    status: int = 200,
    json_body: dict | None = None,
    headers: dict | None = None,
    content: bytes = b"",
    text: str = "",
):
    response = MagicMock()
    response.status_code = status
    response.json.return_value = json_body if json_body is not None else {}
    response.headers = headers or {}
    response.content = content
    response.text = text
    return response


# ---- Token caching --------------------------------------------------------


def test_get_token_caches_within_ttl(mocker):
    msal_patch = _mock_msal(mocker, expires_in=3600)

    assert graph_client._get_token() == "test-token"
    # Second call within TTL must return the cached token without re-acquiring.
    assert graph_client._get_token() == "test-token"

    assert msal_patch.return_value.acquire_token_for_client.call_count == 1


def test_get_token_refreshes_after_ttl(mocker):
    msal_patch = _mock_msal(mocker, expires_in=3600)
    fake_time = mocker.patch("shared.graph_client.time.time")

    # Acquire at t=1000 → cache valid until 1000 + 3600 - 600 = 4000.
    fake_time.return_value = 1000.0
    graph_client._get_token()

    # Jump past refresh margin → must re-acquire.
    fake_time.return_value = 4100.0
    graph_client._get_token()

    assert msal_patch.return_value.acquire_token_for_client.call_count == 2


# ---- HTTP error mapping ---------------------------------------------------


def test_list_inbox_unauthorized_raises_permission_error(mocker):
    _mock_msal(mocker)
    mocker.patch(
        "shared.graph_client.requests.request",
        return_value=_mock_response(
            status=403,
            json_body={"error": {"message": "Access policy denied"}},
        ),
    )

    with pytest.raises(GraphPermissionError, match="Access policy denied"):
        graph_client.list_inbox("blocked@example.com")


def test_list_inbox_not_found_raises_not_found_error(mocker):
    _mock_msal(mocker)
    mocker.patch(
        "shared.graph_client.requests.request",
        return_value=_mock_response(
            status=404,
            json_body={"error": {"message": "Mailbox not found"}},
        ),
    )

    with pytest.raises(GraphNotFoundError, match="Mailbox not found"):
        graph_client.list_inbox("ghost@example.com")


# ---- get_message include_headers extension --------------------------------


def test_get_message_default_does_not_pass_select(mocker):
    """include_headers=False (default) preserves the pre-refactor surface."""
    _mock_msal(mocker)
    req = mocker.patch(
        "shared.graph_client.requests.request",
        return_value=_mock_response(status=200, json_body={"id": "x"}),
    )
    graph_client.get_message("u@x.com", "mid-1")
    call_kwargs = req.call_args.kwargs
    # No $select param when headers aren't requested — Graph returns the
    # default field projection.
    assert call_kwargs["params"] is None


def test_get_message_include_headers_projects_internet_message_headers(mocker):
    _mock_msal(mocker)
    req = mocker.patch(
        "shared.graph_client.requests.request",
        return_value=_mock_response(status=200, json_body={"id": "x"}),
    )
    graph_client.get_message("u@x.com", "mid-1", include_headers=True)
    params = req.call_args.kwargs["params"]
    assert params is not None
    select = params["$select"]
    # Must include internetMessageHeaders and the other intake-read fields.
    for field in (
        "id", "subject", "from", "receivedDateTime",
        "hasAttachments", "body", "internetMessageHeaders",
    ):
        assert field in select


# ---- send_mail payload ----------------------------------------------------


def test_send_mail_payload_structure(mocker):
    _mock_msal(mocker)
    req = mocker.patch(
        "shared.graph_client.requests.request",
        return_value=_mock_response(status=202),
    )

    graph_client.send_mail(
        from_mailbox="safety@example.com",
        to=["a@example.com", "b@example.com"],
        subject="Test",
        body="hello",
        cc=["c@example.com"],
    )

    method, url = req.call_args.args
    assert method == "POST"
    assert url == "https://graph.microsoft.com/v1.0/users/safety@example.com/sendMail"

    payload = req.call_args.kwargs["json"]
    assert payload["saveToSentItems"] is True
    msg = payload["message"]
    assert msg["subject"] == "Test"
    assert msg["body"] == {"contentType": "Text", "content": "hello"}
    assert msg["toRecipients"] == [
        {"emailAddress": {"address": "a@example.com"}},
        {"emailAddress": {"address": "b@example.com"}},
    ]
    assert msg["ccRecipients"] == [{"emailAddress": {"address": "c@example.com"}}]
    # bcc + attachments omitted when not supplied — Graph rejects empty arrays
    # on some endpoints, so the cleanest contract is "absent key".
    assert "bccRecipients" not in msg
    assert "attachments" not in msg


def test_send_mail_with_attachments_base64_encodes(mocker):
    _mock_msal(mocker)
    req = mocker.patch(
        "shared.graph_client.requests.request",
        return_value=_mock_response(status=202),
    )

    raw_bytes = b"PDF\x00binary\x01payload"
    graph_client.send_mail(
        from_mailbox="safety@example.com",
        to=["x@example.com"],
        subject="With attachment",
        body="see attached",
        attachments=[
            {
                "name": "report.pdf",
                "contentType": "application/pdf",
                "contentBytes": raw_bytes,
            }
        ],
    )

    payload = req.call_args.kwargs["json"]
    att = payload["message"]["attachments"][0]
    assert att["@odata.type"] == "#microsoft.graph.fileAttachment"
    assert att["name"] == "report.pdf"
    assert att["contentType"] == "application/pdf"
    # Graph requires base64-encoded contentBytes, and it must round-trip.
    assert base64.b64decode(att["contentBytes"]) == raw_bytes


# ---- 429 retry behavior ---------------------------------------------------


def test_retry_on_429_succeeds_on_second_attempt(mocker):
    _mock_msal(mocker)
    sleep = mocker.patch("shared.graph_client.time.sleep")
    req = mocker.patch(
        "shared.graph_client.requests.request",
        side_effect=[
            _mock_response(status=429, headers={"Retry-After": "1"}),
            _mock_response(status=200, json_body={"value": [{"id": "msg-1"}]}),
        ],
    )

    result = graph_client.list_inbox("safety@example.com")

    assert result == [{"id": "msg-1"}]
    assert req.call_count == 2
    # Retry-After header was honored (1s, not exponential).
    sleep.assert_called_once_with(1.0)


def test_retry_on_429_gives_up_after_max_attempts(mocker):
    _mock_msal(mocker)
    mocker.patch("shared.graph_client.time.sleep")
    throttled = _mock_response(
        status=429,
        json_body={"error": {"message": "throttled"}},
        headers={"Retry-After": "1"},
    )
    req = mocker.patch(
        "shared.graph_client.requests.request",
        side_effect=[throttled, throttled, throttled],
    )

    with pytest.raises(GraphRateLimitError, match="throttled"):
        graph_client.list_inbox("safety@example.com")

    assert req.call_count == 3


# ---- fetch_latest_inbound_timestamp ---------------------------------------


def test_fetch_latest_inbound_timestamp_parses_iso_z(mocker):
    _mock_msal(mocker)
    mocker.patch(
        "shared.graph_client.requests.request",
        return_value=_mock_response(
            status=200,
            json_body={"value": [{"receivedDateTime": "2026-05-20T13:45:30Z"}]},
        ),
    )
    ts = graph_client.fetch_latest_inbound_timestamp("safety@example.com")
    assert ts is not None
    assert ts.year == 2026 and ts.month == 5 and ts.day == 20
    assert ts.hour == 13 and ts.minute == 45
    assert ts.tzinfo is not None  # UTC-aware


def test_fetch_latest_inbound_timestamp_returns_none_on_empty_mailbox(mocker):
    _mock_msal(mocker)
    mocker.patch(
        "shared.graph_client.requests.request",
        return_value=_mock_response(status=200, json_body={"value": []}),
    )
    assert graph_client.fetch_latest_inbound_timestamp("empty@example.com") is None


def test_fetch_latest_inbound_timestamp_sends_top1_orderby(mocker):
    """Query string must request only the most recent message (no over-fetching)."""
    _mock_msal(mocker)
    req = mocker.patch(
        "shared.graph_client.requests.request",
        return_value=_mock_response(
            status=200,
            json_body={"value": [{"receivedDateTime": "2026-05-20T13:45:30Z"}]},
        ),
    )
    graph_client.fetch_latest_inbound_timestamp("safety@example.com")

    call = req.call_args
    params = call.kwargs["params"]
    assert params["$top"] == "1"
    assert params["$orderby"] == "receivedDateTime desc"
    assert params["$select"] == "receivedDateTime"


def test_fetch_latest_inbound_timestamp_403_propagates_as_permission_error(mocker):
    _mock_msal(mocker)
    mocker.patch(
        "shared.graph_client.requests.request",
        return_value=_mock_response(
            status=403,
            json_body={"error": {"message": "ApplicationAccessPolicy denied"}},
        ),
    )
    with pytest.raises(GraphPermissionError, match="denied"):
        graph_client.fetch_latest_inbound_timestamp("forbidden@example.com")


def test_fetch_latest_inbound_timestamp_404_propagates_as_not_found(mocker):
    _mock_msal(mocker)
    mocker.patch(
        "shared.graph_client.requests.request",
        return_value=_mock_response(
            status=404,
            json_body={"error": {"message": "user not found"}},
        ),
    )
    with pytest.raises(GraphNotFoundError, match="not found"):
        graph_client.fetch_latest_inbound_timestamp("ghost@example.com")
