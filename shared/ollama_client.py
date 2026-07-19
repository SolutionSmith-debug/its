"""Localhost-only Ollama client for schema-constrained local inference (ADR-0004).

Purpose
-------
The vendor-estimate importer's Tier-2 extraction (ADR-0004 decision 1) runs a LOCAL
Ollama model on the production MacBook — vendor pricing never leaves the machine, and
the "sole live Anthropic consumer is `intake.py`" invariant holds (this lane has NO
cloud AI). This module is the single audited egress to that local daemon:
`generate_structured` POSTs `/api/chat` with `format=<json schema>` (Ollama's
schema-constrained decoding) and `jsonschema.validate`s the reply BEFORE returning it,
so a caller can never receive a schema-nonconforming payload from here.

Invariants
----------
* LOCALHOST-ONLY, fail-closed: any `base_url` whose host is not exactly
  `127.0.0.1` or `localhost` raises `OllamaClientError` before any request is made
  (RED-tested). This client must never become a generic HTTP egress — a config
  typo or a poisoned `ITS_Config` row pointing it off-box is REFUSED, not honored.
* `keep_alive=0` on every generate call: the model is load-on-demand and unloads
  immediately after (the ~7-9B model fits the 18 GB host only transiently —
  ADR-0004 decision 1).
* `stream=false` + STRICT `json.loads` of `message.content` + `jsonschema.validate`
  against the caller's schema — a partial/chatty/nonconforming reply raises,
  never leaks upward.
* NO send capability, NO cloud AI: this module talks to the local daemon only.
  Enrolled in `tests/test_capability_gating.py` NETWORK_LIB_ALLOWLIST (it imports
  `requests`, a tracked needle) with the localhost-only rationale. Send scripts
  must never import it (ADR-0004 decision 12 — the send half is local-AI-free too).

Failure modes
-------------
* Every failure raises `OllamaClientError` (non-local URL, transport error,
  non-200, malformed envelope, non-JSON content, schema violation). The Tier-2
  caller (`po_materials/estimate_extract.py`) maps that to None → Tier-3 review.
* `is_available` never raises on an unreachable daemon (returns False) but DOES
  raise on a non-local URL — availability probing must not become an egress probe.

Consumers
---------
* `po_materials/estimate_extract.py` — the Tier-2 vendor-estimate extraction.
"""
from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlsplit

import jsonschema
import requests

# The ONLY hosts this client will ever talk to (ADR-0004 decision 1: local-only).
ALLOWED_HOSTS = frozenset({"127.0.0.1", "localhost"})

DEFAULT_TIMEOUT_S = 600
_AVAILABILITY_TIMEOUT_S = 5


class OllamaClientError(Exception):
    """Any Ollama client failure — non-local URL, transport, or nonconforming reply."""


def _require_local(base_url: str) -> str:
    """Validate + normalize the base URL; raise unless the host is loopback-local.

    Returns the base URL without a trailing slash. Fail-closed: an unparseable
    URL, a missing host, or ANY host other than 127.0.0.1/localhost raises —
    including lookalikes (`localhost.evil.com`) and other loopback spellings,
    which are deliberately NOT allowlisted (exact-match only, no cleverness).
    """
    try:
        parts = urlsplit(base_url)
    except ValueError as exc:
        raise OllamaClientError(f"unparseable Ollama base_url: {base_url!r}") from exc
    if parts.scheme not in ("http", "https"):
        raise OllamaClientError(
            f"Ollama base_url must be http(s), got {base_url!r}"
        )
    host = parts.hostname
    if host is None or host.lower() not in ALLOWED_HOSTS:
        raise OllamaClientError(
            f"REFUSED non-local Ollama base_url {base_url!r} — this client is "
            "localhost-only by design (ADR-0004: vendor pricing never leaves the machine)"
        )
    return base_url.rstrip("/")


def generate_structured(
    *,
    prompt: str,
    schema: dict[str, Any],
    model: str,
    system: str | None = None,
    base_url: str,
    timeout_s: int = DEFAULT_TIMEOUT_S,
) -> dict[str, Any]:
    """One schema-constrained, non-streaming chat generation against local Ollama.

    POSTs `{base}/api/chat` with `stream=false`, `format=<schema>` (constrained
    decoding) and `keep_alive=0` (unload after — load-on-demand model). The reply's
    `message.content` is strictly JSON-parsed and `jsonschema.validate`d against
    `schema` before being returned, so the caller's schema IS the value gate
    (explicit numeric maxima etc. — ADR-0004 decision 8).

    Raises OllamaClientError on ANY failure. Never returns a nonconforming dict.
    """
    base = _require_local(base_url)
    messages: list[dict[str, str]] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "stream": False,
        "format": schema,
        "keep_alive": 0,
        # Deterministic decoding for extraction — same input, same output.
        "options": {"temperature": 0},
    }
    try:
        resp = requests.post(f"{base}/api/chat", json=payload, timeout=timeout_s)
    except requests.RequestException as exc:
        raise OllamaClientError(f"Ollama transport failure: {exc}") from exc
    if resp.status_code != 200:
        raise OllamaClientError(
            f"Ollama /api/chat returned HTTP {resp.status_code}: {resp.text[:300]}"
        )
    try:
        envelope = resp.json()
    except ValueError as exc:
        raise OllamaClientError("Ollama reply is not valid JSON") from exc
    message = envelope.get("message") if isinstance(envelope, dict) else None
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, str) or not content.strip():
        raise OllamaClientError("Ollama reply missing message.content")
    try:
        parsed = json.loads(content)
    except ValueError as exc:
        raise OllamaClientError(
            "Ollama message.content is not strict JSON despite format-constrained decoding"
        ) from exc
    if not isinstance(parsed, dict):
        raise OllamaClientError("Ollama structured reply is not a JSON object")
    try:
        jsonschema.validate(instance=parsed, schema=schema)
    except jsonschema.ValidationError as exc:
        raise OllamaClientError(
            f"Ollama reply violates the extraction schema: {exc.message}"
        ) from exc
    return parsed


def is_available(base_url: str) -> bool:
    """True iff the LOCAL Ollama daemon answers `/api/tags`.

    Raises OllamaClientError on a non-local base_url (the localhost gate applies
    to probes too); returns False on any transport failure or non-200.
    """
    base = _require_local(base_url)
    try:
        resp = requests.get(f"{base}/api/tags", timeout=_AVAILABILITY_TIMEOUT_S)
    except requests.RequestException:
        return False
    return resp.status_code == 200
