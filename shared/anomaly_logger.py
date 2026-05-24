"""Output validation and anomaly logging for adversarial input handling.

Per Foundation Mission v8 Invariant 2, every extraction output gets checked for sentinel
patterns that suggest prompt injection succeeded at the AI layer. Items flagged route to
ITS_Review_Queue with security_flag=True; the owner is notified separately.

Sentinels (Phase 1 starter list — extend as patterns emerge):
- Field names matching: recipient_override, send_to, external_address, ignore_*, role_*,
  system_*. These are field names a legitimate schema wouldn't include — if they show up
  the AI invented them, which is a sign of injection.
- Field values exceeding 2KB. Suggests injection stuffed extra payload into a field.
- Well-known injection phrases in any string field value.

Usage:
    from shared.anomaly_logger import check

    anomalies = check(extracted_dict)
    if anomalies:
        review_queue.add(
            workstream="safety_reports",
            summary="anomaly in extraction",
            payload={"extracted": extracted_dict, "anomalies": anomalies},
            sla_tier=SlaTier.SAFETY_INTAKE,
            security_flag=True,
        )
"""
from __future__ import annotations

import re
from typing import Any

# Field-name patterns that should not appear in a legitimate schema response.
SUSPICIOUS_FIELD_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"^recipient_override$", re.IGNORECASE),
    re.compile(r"^send_to$", re.IGNORECASE),
    re.compile(r"^external_address$", re.IGNORECASE),
    re.compile(r"^ignore_", re.IGNORECASE),
    re.compile(r"^role_", re.IGNORECASE),
    re.compile(r"^system_", re.IGNORECASE),
]

# Substring-match injection phrases, case-insensitive.
INJECTION_PHRASES: list[str] = [
    "ignore previous",
    "ignore all previous",
    "disregard previous",
    "disregard all previous",
    "you are now",
    "system prompt",
    "act as",
    "new instructions",
    "forget your instructions",
]

# Per-field byte ceiling. Above this is suspicious for structured-extraction outputs.
MAX_FIELD_VALUE_BYTES = 2048


def check(extracted: Any) -> list[str]:
    """Check an extracted structure for anomaly sentinels.

    Args:
        extracted: The structured output from an Anthropic extraction call. Typically a
            dict, but lists and primitives are handled too.

    Returns:
        A list of human-readable anomaly descriptions. Empty list means clean.
    """
    anomalies: list[str] = []
    _walk(extracted, path="", anomalies=anomalies)
    return anomalies


def _walk(node: Any, *, path: str, anomalies: list[str]) -> None:
    if isinstance(node, dict):
        for key, value in node.items():
            here = f"{path}.{key}" if path else key
            for pattern in SUSPICIOUS_FIELD_PATTERNS:
                if pattern.match(key):
                    anomalies.append(f"suspicious field name: {here}")
                    break
            _walk(value, path=here, anomalies=anomalies)
    elif isinstance(node, list):
        for i, item in enumerate(node):
            _walk(item, path=f"{path}[{i}]", anomalies=anomalies)
    elif isinstance(node, str):
        if len(node.encode("utf-8")) > MAX_FIELD_VALUE_BYTES:
            anomalies.append(f"oversized field value at {path or '<root>'}")
        lower = node.lower()
        for phrase in INJECTION_PHRASES:
            if phrase in lower:
                anomalies.append(
                    f"injection phrase {phrase!r} at {path or '<root>'}"
                )
                break
