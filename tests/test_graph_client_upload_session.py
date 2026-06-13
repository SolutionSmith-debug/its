"""Tests for the Graph large-attachment upload-session path (PR-3).

Covers `shared.graph_client.send_mail_large_attachment` end to end with a fully
mocked Graph: create draft → createUploadSession → chunked PUT honoring
nextExpectedRanges → send. Mirrors the mock idiom in tests/test_graph_client.py
(autouse token-cache reset + keychain stub, `_mock_msal`, `_mock_response`).

The upload flow is verified against learn.microsoft.com/graph/outlook-large-attachments
(doc rev 2024-11-07): the PUT goes to the pre-authenticated `uploadUrl` with a
`Content-Range: bytes {s}-{e}/{total}` header and NO Authorization; intermediate
ranges return 200 with `nextExpectedRanges`, the final range returns 201.

Run with: pytest -q tests/test_graph_client_upload_session.py
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest
import requests

from shared import graph_client
from shared.graph_client import GraphAttachmentTooLargeError, GraphError


@pytest.fixture(autouse=True)
def reset_graph_state(mocker):
    """Reset the module token cache + stub keychain (same as test_graph_client)."""
    mocker.patch.object(graph_client, "_token", None)
    mocker.patch.object(graph_client, "_token_expires_at", 0.0)
    mocker.patch(
        "shared.graph_client.keychain.get_secret",
        side_effect=lambda key, *a, **kw: f"fake-{key}",
    )


def _mock_msal(mocker, *, expires_in: int = 3600, access_token: str = "test-token"):
    app = MagicMock()
    app.acquire_token_for_client.return_value = {
        "access_token": access_token,
        "expires_in": expires_in,
    }
    return mocker.patch(
        "shared.graph_client.msal.ConfidentialClientApplication", return_value=app
    )


def _mock_response(*, status=200, json_body=None, headers=None, text=""):
    response = MagicMock()
    response.status_code = status
    response.json.return_value = json_body if json_body is not None else {}
    response.headers = headers or {}
    response.text = text
    return response


UPLOAD_URL = "https://outlook.office.com/api/v2.0/Users('x')/Messages('m')/AttachmentSessions('s')?authtoken=tok"


# ---- happy path: multi-chunk, honoring nextExpectedRanges -----------------


def test_upload_session_full_flow_honors_next_expected_ranges(mocker):
    """draft → createUploadSession → chunked PUT (resume per nextExpectedRanges) → send."""
    _mock_msal(mocker)
    # Force a 3-chunk upload over 10 bytes with a 4-byte chunk size.
    mocker.patch.object(graph_client, "UPLOAD_CHUNK_SIZE", 4)
    payload = b"0123456789"  # 10 bytes

    req = mocker.patch(
        "shared.graph_client.requests.request",
        side_effect=[
            _mock_response(status=201, json_body={"id": "draft-1"}),         # create draft
            _mock_response(status=201, json_body={                            # createUploadSession
                "uploadUrl": UPLOAD_URL, "nextExpectedRanges": ["0-"],
            }),
            # PUT bytes 0-3 → server asks to RESUME at byte 2 (proves we honor it,
            # not the linear fallback of 4).
            _mock_response(status=200, json_body={"nextExpectedRanges": ["2"]}),
            # PUT bytes 2-5 → next expected 6.
            _mock_response(status=200, json_body={"nextExpectedRanges": ["6"]}),
            # PUT bytes 6-9 → final range accepted.
            _mock_response(status=201, json_body={}),
            _mock_response(status=202),                                       # send draft
        ],
    )

    result = graph_client.send_mail_large_attachment(
        from_mailbox="safety@example.com",
        to=["pm@example.com"],
        cc=["cc@example.com"],
        subject="Weekly Safety Report",
        body="packet attached",
        attachment_name="packet.pdf",
        attachment_bytes=payload,
    )
    assert result is None
    assert req.call_count == 6

    calls = req.call_args_list

    # Call 1 — create draft (envelope, NO attachment).
    assert calls[0].args[0] == "POST"
    assert calls[0].args[1].endswith("/users/safety@example.com/messages")
    # POST /messages takes the message resource directly (NOT wrapped like /sendMail).
    msg = calls[0].kwargs["json"]
    assert msg["toRecipients"] == [{"emailAddress": {"address": "pm@example.com"}}]
    assert msg["ccRecipients"] == [{"emailAddress": {"address": "cc@example.com"}}]
    assert "attachments" not in msg  # the big file is NOT inline

    # Call 2 — createUploadSession with the file's true size.
    assert "createUploadSession" in calls[1].args[1]
    item = calls[1].kwargs["json"]["AttachmentItem"]
    assert item["attachmentType"] == "file"
    assert item["name"] == "packet.pdf"
    assert item["size"] == len(payload)

    # Calls 3-5 — chunked PUTs to the pre-authed uploadUrl, honoring nextExpectedRanges.
    puts = calls[2:5]
    for c in puts:
        assert c.args[0] == "PUT"
        assert c.args[1] == UPLOAD_URL
        # Pre-authenticated URL: NO Authorization header on the PUT.
        assert "Authorization" not in c.kwargs["headers"]
        assert c.kwargs["headers"]["Content-Type"] == "application/octet-stream"
    ranges = [c.kwargs["headers"]["Content-Range"] for c in puts]
    # 0-3, then RESUME at 2 (honored, not 4), then 6-9.
    assert ranges == ["bytes 0-3/10", "bytes 2-5/10", "bytes 6-9/10"]
    # The bytes PUT match the ranges, and Content-Length matches each chunk.
    assert puts[0].kwargs["data"] == payload[0:4]
    assert puts[1].kwargs["data"] == payload[2:6]
    assert puts[2].kwargs["data"] == payload[6:10]
    for c in puts:
        assert int(c.kwargs["headers"]["Content-Length"]) == len(c.kwargs["data"])

    # Call 6 — send the draft.
    assert calls[5].args[0] == "POST"
    assert calls[5].args[1].endswith("/messages/draft-1/send")


def test_upload_session_single_chunk_linear(mocker):
    """A file smaller than one chunk → exactly one 201 PUT, then send."""
    _mock_msal(mocker)
    mocker.patch.object(graph_client, "UPLOAD_CHUNK_SIZE", 4096)
    payload = b"%PDF-small-packet"
    req = mocker.patch(
        "shared.graph_client.requests.request",
        side_effect=[
            _mock_response(status=201, json_body={"id": "draft-9"}),
            _mock_response(status=201, json_body={"uploadUrl": UPLOAD_URL,
                                                  "nextExpectedRanges": ["0-"]}),
            _mock_response(status=201, json_body={}),  # single final PUT
            _mock_response(status=202),
        ],
    )
    graph_client.send_mail_large_attachment(
        from_mailbox="safety@example.com", to=["pm@example.com"],
        subject="s", body="b", attachment_name="p.pdf", attachment_bytes=payload,
    )
    assert req.call_count == 4
    put = req.call_args_list[2]
    assert put.args[0] == "PUT"
    assert put.kwargs["headers"]["Content-Range"] == f"bytes 0-{len(payload) - 1}/{len(payload)}"
    assert put.kwargs["data"] == payload


def test_upload_session_forward_jump_range_is_clamped_no_skipped_bytes(mocker):
    """A nextExpectedRanges that JUMPS FORWARD past the linear next byte must be IGNORED —
    honoring it would skip bytes and build a TRUNCATED attachment. The clamp forces linear
    progress; every byte is uploaded gap-free."""
    _mock_msal(mocker)
    mocker.patch.object(graph_client, "UPLOAD_CHUNK_SIZE", 4)
    payload = b"0123456789"  # 10 bytes
    req = mocker.patch(
        "shared.graph_client.requests.request",
        side_effect=[
            _mock_response(status=201, json_body={"id": "draft-fj"}),
            _mock_response(status=201, json_body={"uploadUrl": UPLOAD_URL, "nextExpectedRanges": ["0-"]}),
            # PUT 0-3 → server (mis)reports a FORWARD jump to 8 (would skip 4-7). Clamp → 4.
            _mock_response(status=200, json_body={"nextExpectedRanges": ["8"]}),
            _mock_response(status=200, json_body={"nextExpectedRanges": ["8"]}),  # PUT 4-7 → linear 8
            _mock_response(status=201, json_body={}),                              # PUT 8-9 → final
            _mock_response(status=202),                                            # send
        ],
    )
    graph_client.send_mail_large_attachment(
        from_mailbox="safety@example.com", to=["pm@example.com"],
        subject="s", body="b", attachment_name="p.pdf", attachment_bytes=payload,
    )
    puts = req.call_args_list[2:5]
    ranges = [c.kwargs["headers"]["Content-Range"] for c in puts]
    # Gap-free 0-3, 4-7, 8-9 — the forward jump to 8 after the first PUT was NOT honored.
    assert ranges == ["bytes 0-3/10", "bytes 4-7/10", "bytes 8-9/10"]
    assert b"".join(c.kwargs["data"] for c in puts) == payload  # every byte uploaded, no skip


# ---- oversized: refuse BEFORE any network call ----------------------------


def test_oversized_attachment_raises_before_any_request(mocker):
    _mock_msal(mocker)
    mocker.patch.object(graph_client, "UPLOAD_SESSION_MAX_BYTES", 5)
    req = mocker.patch("shared.graph_client.requests.request")
    with pytest.raises(GraphAttachmentTooLargeError):
        graph_client.send_mail_large_attachment(
            from_mailbox="safety@example.com", to=["pm@example.com"],
            subject="s", body="b", attachment_name="big.pdf",
            attachment_bytes=b"0123456789",  # 10 > 5
        )
    req.assert_not_called()  # no draft created, nothing sent


def test_zero_byte_attachment_raises_before_any_request(mocker):
    """A 0-byte attachment would open a degenerate session (zero PUTs then /send) — refuse it."""
    _mock_msal(mocker)
    req = mocker.patch("shared.graph_client.requests.request")
    with pytest.raises(GraphError):
        graph_client.send_mail_large_attachment(
            from_mailbox="safety@example.com", to=["pm@example.com"],
            subject="s", body="b", attachment_name="empty.pdf", attachment_bytes=b"",
        )
    req.assert_not_called()  # degenerate session never opened


# ---- failure mid-upload: draft left UNSENT (fail toward not-sending) -------


def test_chunk_put_failure_does_not_send(mocker):
    """A failing PUT raises GraphError and the /send step is never reached."""
    _mock_msal(mocker)
    mocker.patch.object(graph_client, "UPLOAD_CHUNK_SIZE", 4)
    req = mocker.patch(
        "shared.graph_client.requests.request",
        side_effect=[
            _mock_response(status=201, json_body={"id": "draft-2"}),
            _mock_response(status=201, json_body={"uploadUrl": UPLOAD_URL,
                                                  "nextExpectedRanges": ["0-"]}),
            _mock_response(status=200, json_body={"nextExpectedRanges": ["4"]}),
            _mock_response(status=500, json_body={"error": {"message": "boom"}}),
        ],
    )
    with pytest.raises(GraphError):
        graph_client.send_mail_large_attachment(
            from_mailbox="safety@example.com", to=["pm@example.com"],
            subject="s", body="b", attachment_name="p.pdf",
            attachment_bytes=b"0123456789",
        )
    # 4 calls happened (draft, session, put1, put2-failed); the send (call 5) did NOT.
    assert req.call_count == 4
    assert not any(
        c.args[0] == "POST" and str(c.args[1]).endswith("/send")
        for c in req.call_args_list
    )


def test_chunk_put_timeout_is_typed(mocker):
    """A stalled chunk PUT surfaces as GraphTimeoutError (catchable as GraphError)."""
    _mock_msal(mocker)
    mocker.patch.object(graph_client, "UPLOAD_CHUNK_SIZE", 4096)
    mocker.patch(
        "shared.graph_client.requests.request",
        side_effect=[
            _mock_response(status=201, json_body={"id": "d"}),
            _mock_response(status=201, json_body={"uploadUrl": UPLOAD_URL,
                                                  "nextExpectedRanges": ["0-"]}),
            requests.Timeout("slow chunk"),
        ],
    )
    with pytest.raises(graph_client.GraphTimeoutError):
        graph_client.send_mail_large_attachment(
            from_mailbox="safety@example.com", to=["pm@example.com"],
            subject="s", body="b", attachment_name="p.pdf", attachment_bytes=b"data",
        )


# ---- defensive: missing uploadUrl / missing draft id ----------------------


def test_missing_upload_url_raises(mocker):
    _mock_msal(mocker)
    mocker.patch(
        "shared.graph_client.requests.request",
        side_effect=[
            _mock_response(status=201, json_body={"id": "d"}),
            _mock_response(status=201, json_body={}),  # no uploadUrl
        ],
    )
    with pytest.raises(GraphError, match="uploadUrl"):
        graph_client.send_mail_large_attachment(
            from_mailbox="safety@example.com", to=["pm@example.com"],
            subject="s", body="b", attachment_name="p.pdf", attachment_bytes=b"data",
        )


def test_missing_draft_id_raises(mocker):
    _mock_msal(mocker)
    mocker.patch(
        "shared.graph_client.requests.request",
        side_effect=[_mock_response(status=201, json_body={})],  # no id
    )
    with pytest.raises(GraphError, match="message id"):
        graph_client.send_mail_large_attachment(
            from_mailbox="safety@example.com", to=["pm@example.com"],
            subject="s", body="b", attachment_name="p.pdf", attachment_bytes=b"data",
        )


# ---- next-expected parser edge cases --------------------------------------


def test_parse_next_expected_start_variants():
    assert graph_client._parse_next_expected_start(
        _mock_response(json_body={"nextExpectedRanges": ["2097152"]}), 0
    ) == 2097152
    # range form "start-end" → take the start.
    assert graph_client._parse_next_expected_start(
        _mock_response(json_body={"nextExpectedRanges": ["100-200"]}), 0
    ) == 100
    # empty / missing → fallback.
    assert graph_client._parse_next_expected_start(
        _mock_response(json_body={"nextExpectedRanges": []}), 42
    ) == 42
    assert graph_client._parse_next_expected_start(
        _mock_response(json_body={}), 7
    ) == 7
