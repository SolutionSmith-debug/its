"""Tests for shared/portal_client.py.

All HTTP is mocked — these tests never hit the network. Live coverage (a real
Worker round-trip) is the deploy-gated tests/test_portal_client_integration.py.

Run with: pytest -q tests/test_portal_client.py
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from shared import portal_client
from shared.portal_client import (
    PortalAuthError,
    PortalRateLimitError,
    PortalTransportError,
)

BASE = "https://portal.example.com"
TOKEN = "fake-bearer"


def _mock_response(*, status=200, json_body=None, headers=None, text=""):
    response = MagicMock()
    response.status_code = status
    response.json.return_value = json_body if json_body is not None else {}
    response.headers = headers if headers is not None else {}
    response.text = text
    return response


def _patch_requests(mocker, responses):
    if not isinstance(responses, list):
        responses = [responses]
    return mocker.patch(
        "shared.portal_client.requests.request",
        side_effect=responses if len(responses) > 1 else responses * 100,
    )


# ---- get_pending ---------------------------------------------------------


def test_get_pending_returns_rows_and_sends_bearer_and_limit(mocker):
    rows = [
        {"submission_uuid": "u1", "job_id": "JOB-1", "form_code": "jha-v1",
         "work_date": "2026-06-05", "payload_json": "{}", "amends_uuid": None,
         "hmac": "abc", "created_at": 1},
    ]
    req = _patch_requests(mocker, _mock_response(json_body={"pending": rows}))

    out = portal_client.get_pending(BASE, TOKEN, limit=25)

    assert out == rows
    args, kwargs = req.call_args
    assert args == ("GET", "https://portal.example.com/api/internal/pending")
    assert kwargs["headers"]["Authorization"] == "Bearer fake-bearer"
    assert kwargs["params"] == {"limit": 25}


def test_get_pending_empty_queue_returns_empty_list(mocker):
    _patch_requests(mocker, _mock_response(json_body={"pending": []}))
    assert portal_client.get_pending(BASE, TOKEN) == []


def test_get_pending_drops_non_dict_rows(mocker):
    _patch_requests(
        mocker,
        _mock_response(json_body={"pending": [{"submission_uuid": "u1"}, "garbage", 5]}),
    )
    out = portal_client.get_pending(BASE, TOKEN)
    assert out == [{"submission_uuid": "u1"}]


def test_get_pending_missing_pending_key_raises(mocker):
    _patch_requests(mocker, _mock_response(json_body={"unexpected": 1}))
    with pytest.raises(PortalTransportError, match="pending"):
        portal_client.get_pending(BASE, TOKEN)


def test_get_pending_401_raises_auth_error_without_retry(mocker):
    req = _patch_requests(mocker, _mock_response(status=401))
    with pytest.raises(PortalAuthError):
        portal_client.get_pending(BASE, TOKEN)
    assert req.call_count == 1  # 401 is NOT retried


def test_get_pending_non_200_raises_transport_error(mocker):
    _patch_requests(mocker, _mock_response(status=500, text="boom"))
    with pytest.raises(PortalTransportError, match="500"):
        portal_client.get_pending(BASE, TOKEN)


def test_get_pending_non_json_body_raises(mocker):
    resp = _mock_response(status=200, text="<html>")
    resp.json.side_effect = ValueError
    _patch_requests(mocker, resp)
    with pytest.raises(PortalTransportError, match="non-JSON"):
        portal_client.get_pending(BASE, TOKEN)


# ---- mark_filed ----------------------------------------------------------


def test_mark_filed_posts_body_and_returns_found(mocker):
    req = _patch_requests(mocker, _mock_response(json_body={"ok": True, "found": True}))

    found = portal_client.mark_filed(
        BASE, TOKEN, submission_uuid="u1", box_link="https://app.box.com/file/9"
    )

    assert found is True
    args, kwargs = req.call_args
    assert args == ("POST", "https://portal.example.com/api/internal/mark-filed")
    assert kwargs["json"] == {
        "submission_uuid": "u1",
        "box_link": "https://app.box.com/file/9",
    }
    assert kwargs["headers"]["Authorization"] == "Bearer fake-bearer"


def test_mark_filed_found_false_when_worker_has_no_row(mocker):
    _patch_requests(mocker, _mock_response(json_body={"ok": True, "found": False}))
    assert portal_client.mark_filed(BASE, TOKEN, submission_uuid="u1", box_link="x") is False


def test_mark_filed_401_raises_auth_error(mocker):
    _patch_requests(mocker, _mock_response(status=401))
    with pytest.raises(PortalAuthError):
        portal_client.mark_filed(BASE, TOKEN, submission_uuid="u1", box_link="x")


# ---- push_jobs -----------------------------------------------------------


def test_push_jobs_posts_full_set_and_returns_summary(mocker):
    jobs = [
        {"job_id": "JOB-000001", "project_name": "Bradley 1", "active": 1},
        {"job_id": "JOB-000007", "project_name": "Atlantis", "active": 0},
    ]
    req = _patch_requests(
        mocker, _mock_response(json_body={"ok": True, "upserted": 2, "deactivated": 1})
    )

    out = portal_client.push_jobs(BASE, TOKEN, jobs)

    assert out == {"ok": True, "upserted": 2, "deactivated": 1}
    args, kwargs = req.call_args
    assert args == ("POST", "https://portal.example.com/api/internal/sync")
    assert kwargs["json"] == {"jobs": jobs}
    assert kwargs["headers"]["Authorization"] == "Bearer fake-bearer"


def test_push_jobs_401_raises_auth_error(mocker):
    _patch_requests(mocker, _mock_response(status=401))
    with pytest.raises(PortalAuthError):
        portal_client.push_jobs(
            BASE, TOKEN, [{"job_id": "J", "project_name": "P", "active": 1}]
        )


def test_push_jobs_non_200_raises_transport_error(mocker):
    _patch_requests(mocker, _mock_response(status=500, text="boom"))
    with pytest.raises(PortalTransportError, match="500"):
        portal_client.push_jobs(
            BASE, TOKEN, [{"job_id": "J", "project_name": "P", "active": 1}]
        )


def test_push_jobs_503_then_success(mocker):
    mocker.patch("shared.portal_client.time.sleep")
    _patch_requests(
        mocker,
        [
            _mock_response(status=503),
            _mock_response(json_body={"ok": True, "upserted": 1, "deactivated": 0}),
        ],
    )
    out = portal_client.push_jobs(
        BASE, TOKEN, [{"job_id": "J", "project_name": "P", "active": 1}]
    )
    assert out["upserted"] == 1


# ---- retry / backoff -----------------------------------------------------


def test_503_then_success(mocker):
    sleeps = mocker.patch("shared.portal_client.time.sleep")
    _patch_requests(
        mocker,
        [_mock_response(status=503), _mock_response(json_body={"pending": []})],
    )
    assert portal_client.get_pending(BASE, TOKEN) == []
    sleeps.assert_called_once_with(1.0)  # 2**0 backoff (no Retry-After)


def test_429_honors_retry_after(mocker):
    sleeps = mocker.patch("shared.portal_client.time.sleep")
    _patch_requests(
        mocker,
        [
            _mock_response(status=429, headers={"Retry-After": "0.5"}),
            _mock_response(json_body={"pending": []}),
        ],
    )
    portal_client.get_pending(BASE, TOKEN)
    sleeps.assert_called_once_with(0.5)


def test_429_exhausted_raises_rate_limit(mocker):
    mocker.patch("shared.portal_client.time.sleep")
    _patch_requests(mocker, [_mock_response(status=429)] * 3)
    with pytest.raises(PortalRateLimitError):
        portal_client.get_pending(BASE, TOKEN)


def test_network_error_retried_then_raises(mocker):
    import requests as _r

    mocker.patch("shared.portal_client.time.sleep")
    mocker.patch(
        "shared.portal_client.requests.request",
        side_effect=_r.ConnectionError("no route"),
    )
    with pytest.raises(PortalTransportError, match="network failure"):
        portal_client.get_pending(BASE, TOKEN)


def test_network_error_then_success(mocker):
    import requests as _r

    mocker.patch("shared.portal_client.time.sleep")
    mocker.patch(
        "shared.portal_client.requests.request",
        side_effect=[_r.ConnectionError("blip"), _mock_response(json_body={"pending": []})],
    )
    assert portal_client.get_pending(BASE, TOKEN) == []


# ---- review-hardening: URL normalization, shape, truncation, backoff ------


@pytest.mark.parametrize("base", ["https://portal.example.com", "https://portal.example.com/"])
def test_base_url_trailing_slash_normalized(mocker, base):
    req = _patch_requests(mocker, _mock_response(json_body={"pending": []}))
    portal_client.get_pending(base, TOKEN)
    assert req.call_args.args[1] == "https://portal.example.com/api/internal/pending"


@pytest.mark.parametrize("body", [{"pending": {"x": 1}}, {"pending": "nope"}, {"pending": 5}, {}])
def test_get_pending_non_list_pending_raises(mocker, body):
    _patch_requests(mocker, _mock_response(json_body=body))
    with pytest.raises(PortalTransportError, match="pending"):
        portal_client.get_pending(BASE, TOKEN)


def test_network_error_backoff_sequence(mocker):
    import requests as _r

    sleeps = mocker.patch("shared.portal_client.time.sleep")
    mocker.patch("shared.portal_client.requests.request", side_effect=_r.ConnectionError("x"))
    with pytest.raises(PortalTransportError, match="network failure"):
        portal_client.get_pending(BASE, TOKEN)
    # MAX_RETRIES=3 → two backoff sleeps (2**0, 2**1) before the final raise.
    assert [c.args[0] for c in sleeps.call_args_list] == [1.0, 2.0]


def test_non_200_message_truncates_long_text(mocker):
    _patch_requests(mocker, _mock_response(status=500, text="x" * 500))
    with pytest.raises(PortalTransportError) as exc:
        portal_client.get_pending(BASE, TOKEN)
    msg = exc.value.args[0]
    assert "x" * 300 in msg          # the leading 300 chars are present
    assert "x" * 301 not in msg      # but the body was truncated, not dumped whole


def test_429_unparseable_retry_after_falls_back_to_backoff(mocker):
    sleeps = mocker.patch("shared.portal_client.time.sleep")
    _patch_requests(
        mocker,
        [
            _mock_response(status=429, headers={"Retry-After": "soon"}),
            _mock_response(json_body={"pending": []}),
        ],
    )
    portal_client.get_pending(BASE, TOKEN)
    sleeps.assert_called_once_with(1.0)  # 2**0 backoff when Retry-After is garbage
