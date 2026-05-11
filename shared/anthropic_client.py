"""Anthropic API client wrapper.

Lazy-loads the API key from macOS Keychain on first call. Use the canonical default model
unless a specific workstream needs Haiku (cheap classification) or Opus (deep reasoning).

Example:
    from shared.anthropic_client import call

    response = call([
        {"role": "user", "content": "Summarize this email: ..."}
    ])
    print(response.content[0].text)
"""
from __future__ import annotations

from typing import Any

from anthropic import Anthropic

from . import keychain

# Defaults — see CLAUDE.md model-selection notes.
DEFAULT_MODEL = "claude-sonnet-4-6"
CLASSIFIER_MODEL = "claude-haiku-4-5-20251001"
DEEP_REASONING_MODEL = "claude-opus-4-7"

_client: Anthropic | None = None


def get_client() -> Anthropic:
    global _client
    if _client is None:
        api_key = keychain.get_secret("ITS_ANTHROPIC_KEY")
        _client = Anthropic(api_key=api_key)
    return _client


def call(
    messages: list[dict[str, Any]],
    *,
    system: str | None = None,
    model: str = DEFAULT_MODEL,
    max_tokens: int = 4096,
    tools: list[dict[str, Any]] | None = None,
    **extra: Any,
):
    """Standard Anthropic Messages API call.

    Pass `tools=[...]` for structured-output / tool-use calls; the response will contain
    tool_use blocks. Schema validation is the caller's responsibility — load schemas from
    `schemas/` and verify before trusting.
    """
    client = get_client()
    kwargs: dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": messages,
    }
    if system is not None:
        kwargs["system"] = system
    if tools is not None:
        kwargs["tools"] = tools
    kwargs.update(extra)
    return client.messages.create(**kwargs)
