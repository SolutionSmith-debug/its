"""Tests for shared/ollama_client.py — the localhost-only Ollama client (ADR-0004).

Fully mocked at the `requests` seam (no live daemon). RED musts covered:
  * a non-localhost base_url RAISES before any HTTP is attempted — delete the
    host gate and these tests fail (the client must never become generic egress);
  * a schema-nonconforming reply is REJECTED (jsonschema.validate before return);
  * the wire contract is pinned: stream=false, format=<schema>, keep_alive=0.

Run with: pytest -q tests/test_ollama_client.py
"""
from __future__ import annotations

import json
from typing import Any

import pytest
import requests

from shared import ollama_client
from shared.ollama_client import OllamaClientError, generate_structured, is_available

SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["a"],
    "properties": {"a": {"type": "integer", "maximum": 10}},
}
LOCAL = "http://127.0.0.1:11434"


class _FakeResp:
    def __init__(
        self, status_code: int = 200, envelope: Any = None, text: str = "",
        is_redirect: bool = False,
    ):
        self.status_code = status_code
        self._envelope = envelope
        self.text = text
        self.is_redirect = is_redirect

    def json(self) -> Any:
        if self._envelope is None:
            raise ValueError("not json")
        return self._envelope


def _envelope(content: str) -> dict[str, Any]:
    return {"message": {"role": "assistant", "content": content}}


@pytest.fixture
def post_capture(monkeypatch):
    """Capture requests.post calls; returns (calls, set_response)."""
    calls: list[dict[str, Any]] = []
    state = {"resp": _FakeResp(200, _envelope(json.dumps({"a": 3})))}

    def fake_post(url, *, json=None, timeout=None, allow_redirects=None):  # noqa: A002 — mirror requests kwarg
        calls.append({"url": url, "json": json, "timeout": timeout, "allow_redirects": allow_redirects})
        resp = state["resp"]
        if isinstance(resp, Exception):
            raise resp
        return resp

    monkeypatch.setattr(ollama_client.requests, "post", fake_post)
    return calls, lambda r: state.__setitem__("resp", r)


# ---- RED: the localhost gate --------------------------------------------------------


@pytest.mark.parametrize(
    "bad_url",
    [
        "http://10.0.0.5:11434",
        "http://ollama.internal:11434",
        "https://evil.example/api",
        "http://localhost.evil.com:11434",  # lookalike — exact-match only
        "http://[::1]:11434",  # other loopback spellings deliberately not allowlisted
        "ftp://127.0.0.1:11434",  # non-http scheme
        "not a url at all",
    ],
)
def test_red_non_local_base_url_refused_before_any_http(monkeypatch, bad_url):
    def _explode(*a, **k):  # pragma: no cover — the point is it must NOT run
        raise AssertionError("HTTP attempted against a non-local base_url")

    monkeypatch.setattr(ollama_client.requests, "post", _explode)
    monkeypatch.setattr(ollama_client.requests, "get", _explode)
    with pytest.raises(OllamaClientError):
        generate_structured(prompt="p", schema=SCHEMA, model="m", base_url=bad_url)
    with pytest.raises(OllamaClientError):
        is_available(bad_url)


@pytest.mark.parametrize("good_url", [LOCAL, "http://localhost:11434", LOCAL + "/"])
def test_local_base_urls_accepted(post_capture, good_url):
    calls, _ = post_capture
    out = generate_structured(prompt="p", schema=SCHEMA, model="m", base_url=good_url)
    assert out == {"a": 3}
    assert calls[0]["url"].endswith("/api/chat")
    assert "//" not in calls[0]["url"].split("://", 1)[1]  # trailing slash normalized


# ---- Wire contract ------------------------------------------------------------------


def test_wire_contract_stream_format_keepalive_model(post_capture):
    calls, _ = post_capture
    generate_structured(
        prompt="the prompt", schema=SCHEMA, model="qwen2.5:7b",
        system="the system", base_url=LOCAL, timeout_s=123,
    )
    payload = calls[0]["json"]
    assert payload["stream"] is False
    assert payload["format"] == SCHEMA  # constrained decoding gets THE schema
    assert payload["keep_alive"] == 0  # load-on-demand model unloads after
    assert payload["model"] == "qwen2.5:7b"
    assert calls[0]["timeout"] == 123
    roles = [m["role"] for m in payload["messages"]]
    assert roles == ["system", "user"]
    assert payload["messages"][0]["content"] == "the system"
    assert payload["messages"][1]["content"] == "the prompt"


def test_system_omitted_when_none(post_capture):
    calls, _ = post_capture
    generate_structured(prompt="p", schema=SCHEMA, model="m", base_url=LOCAL)
    assert [m["role"] for m in calls[0]["json"]["messages"]] == ["user"]


# ---- RED: reply gating --------------------------------------------------------------


def test_red_schema_nonconforming_reply_rejected(post_capture):
    """format-constrained decoding is TRUSTED NOWHERE: the reply is re-validated,
    and a nonconforming payload raises instead of leaking upward."""
    _, set_resp = post_capture
    set_resp(_FakeResp(200, _envelope(json.dumps({"a": 999}))))  # maximum 10
    with pytest.raises(OllamaClientError):
        generate_structured(prompt="p", schema=SCHEMA, model="m", base_url=LOCAL)


def test_red_redirect_refused_never_followed(post_capture):
    """Review F1: the localhost gate validates only the INITIAL url. A 3xx from a
    squatting loopback responder would re-issue the prompt off-box, so a redirect
    is a hard error — and allow_redirects=False is passed so requests never follows."""
    calls, set_resp = post_capture
    set_resp(_FakeResp(307, envelope=None, text="Location: https://evil/ingest", is_redirect=True))
    with pytest.raises(OllamaClientError, match="redirect"):
        generate_structured(prompt="secret vendor quote", schema=SCHEMA, model="m", base_url=LOCAL)
    assert calls[0]["allow_redirects"] is False  # requests told NOT to follow
    set_resp(_FakeResp(200, _envelope(json.dumps({"b": 1}))))  # missing required
    with pytest.raises(OllamaClientError):
        generate_structured(prompt="p", schema=SCHEMA, model="m", base_url=LOCAL)


def test_non_json_content_rejected(post_capture):
    _, set_resp = post_capture
    set_resp(_FakeResp(200, _envelope('Sure! Here is the JSON: {"a": 3}')))
    with pytest.raises(OllamaClientError):
        generate_structured(prompt="p", schema=SCHEMA, model="m", base_url=LOCAL)


def test_non_object_json_content_rejected(post_capture):
    _, set_resp = post_capture
    set_resp(_FakeResp(200, _envelope("[1, 2, 3]")))
    with pytest.raises(OllamaClientError):
        generate_structured(prompt="p", schema=SCHEMA, model="m", base_url=LOCAL)


def test_missing_message_content_rejected(post_capture):
    _, set_resp = post_capture
    set_resp(_FakeResp(200, {"done": True}))
    with pytest.raises(OllamaClientError):
        generate_structured(prompt="p", schema=SCHEMA, model="m", base_url=LOCAL)


def test_http_error_raises(post_capture):
    _, set_resp = post_capture
    set_resp(_FakeResp(500, None, text="boom"))
    with pytest.raises(OllamaClientError):
        generate_structured(prompt="p", schema=SCHEMA, model="m", base_url=LOCAL)


def test_transport_failure_raises(post_capture):
    _, set_resp = post_capture
    set_resp(requests.ConnectionError("refused"))
    with pytest.raises(OllamaClientError):
        generate_structured(prompt="p", schema=SCHEMA, model="m", base_url=LOCAL)


# ---- is_available -------------------------------------------------------------------


def test_is_available_true_on_200(monkeypatch):
    monkeypatch.setattr(
        ollama_client.requests, "get",
        lambda url, timeout, allow_redirects=None: _FakeResp(200, {}),
    )
    assert is_available(LOCAL) is True


def test_is_available_false_on_unreachable(monkeypatch):
    def _refuse(url, timeout, allow_redirects=None):
        raise requests.ConnectionError("refused")

    monkeypatch.setattr(ollama_client.requests, "get", _refuse)
    assert is_available(LOCAL) is False
