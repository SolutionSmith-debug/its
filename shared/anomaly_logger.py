"""Output validation and anomaly logging for adversarial input handling.

Per Foundation Mission v8 Invariant 2, every extraction output gets checked for sentinel
patterns that suggest prompt injection succeeded at the AI layer. Items flagged route to
ITS_Review_Queue with security_flag=True; the owner is notified separately.

Sentinels (Phase 1 starter list — extend as patterns emerge):
- Field names matching injection-control sentinels: recipient_override, send_to,
  external_address, and the anchored ignore_/role_/system_ control names (e.g.
  system_prompt, role_override, ignore_previous — NOT legitimate system_version /
  role_description; §553). These are field names a legitimate schema wouldn't include — if
  they show up the AI invented them, which is a sign of injection.
- Field values exceeding 2KB. Suggests injection stuffed extra payload into a field.
- Well-known injection phrases in any string field value.
- Numeric values exceeding NUMERIC_ANOMALY_THRESHOLD (F21). An inflated count — e.g. a
  prompt-injected 99999 in a safety incident-count field — is the in-code Layer-5 backstop
  to the schema's per-field `maximum` bound (Layer 4): if the structured-output ceiling is
  ever bypassed, an absurd number still routes the extraction to human review.

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
#
# §553: the broad `^ignore_` / `^role_` / `^system_` PREFIX globs were a forward-dated
# false-positive source — a legitimate extraction schema can carry `system_version`,
# `system_id`, `system_serial_number` (machine pre-inspections), `role_description`,
# `role_name`, etc., every one of which would have fired `security_flag=True` and polluted
# ITS_Review_Queue. Narrowed to the injection-CONTROL names an extraction would never
# legitimately invent (system_prompt, role_override, ignore_previous, …), so detection of
# an AI-invented control field is preserved while the FP source is closed. Layer 5 is a
# post-hoc tripwire (evadable by paraphrase), so trading prefix breadth for zero-FP is the
# right call per the tech-debt entry — the real prevention is Layers 2-4 + the Send Gate.
SUSPICIOUS_FIELD_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"^recipient_override$", re.IGNORECASE),
    re.compile(r"^send_to$", re.IGNORECASE),
    re.compile(r"^external_address$", re.IGNORECASE),
    re.compile(r"^ignore_(previous|prior|above|all|instructions?|prompt|system|rules?)$", re.IGNORECASE),
    re.compile(r"^role_(override|overwrite|switch|change|escalat\w*|inject\w*|admin|system|prompt)$", re.IGNORECASE),
    re.compile(r"^system_(prompt|role|instructions?|message|override|command|directive)$", re.IGNORECASE),
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

# Numeric ceiling (F21). A value above this in any int/float field is suspicious for the
# current consumers (safety incident counts, confidence 0-1): a 10-50-person firm's monthly
# metrics and a 0-1 confidence both sit far below 1000, so the threshold never trips on
# legitimate data but catches an injected absurd count. Matched to the schema's per-field
# `maximum` so this Layer-5 detection backstops the Layer-4 hard ceiling. Overridable per
# call via `check(..., numeric_threshold=...)` for a consumer with legitimately larger numbers.
NUMERIC_ANOMALY_THRESHOLD = 1000


def check(
    extracted: Any, *, numeric_threshold: float = NUMERIC_ANOMALY_THRESHOLD
) -> list[str]:
    """Check an extracted structure for anomaly sentinels.

    Args:
        extracted: The structured output from an Anthropic extraction call. Typically a
            dict, but lists and primitives are handled too.
        numeric_threshold: int/float values strictly above this are flagged (F21).
            Defaults to NUMERIC_ANOMALY_THRESHOLD; override for a consumer with
            legitimately larger numbers.

    Returns:
        A list of human-readable anomaly descriptions. Empty list means clean.
    """
    anomalies: list[str] = []
    _walk(extracted, path="", anomalies=anomalies, numeric_threshold=numeric_threshold)
    return anomalies


def _walk(
    node: Any, *, path: str, anomalies: list[str], numeric_threshold: float
) -> None:
    if isinstance(node, dict):
        for key, value in node.items():
            here = f"{path}.{key}" if path else key
            for pattern in SUSPICIOUS_FIELD_PATTERNS:
                if pattern.match(key):
                    anomalies.append(f"suspicious field name: {here}")
                    break
            _walk(value, path=here, anomalies=anomalies, numeric_threshold=numeric_threshold)
    elif isinstance(node, list):
        for i, item in enumerate(node):
            _walk(
                item,
                path=f"{path}[{i}]",
                anomalies=anomalies,
                numeric_threshold=numeric_threshold,
            )
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
    elif isinstance(node, (int, float)) and not isinstance(node, bool):
        # F21: an int/float above the threshold is an inflated payload (e.g. a
        # prompt-injected 99999 incident count). `bool` is a subclass of `int`,
        # so exclude it — checkbox/flag values must never be flagged. This is
        # the Layer-5 detection backstop to the schema's per-field `maximum`
        # (Layer 4): the caller routes a flagged extraction to ITS_Review_Queue
        # with security_flag=True.
        if node > numeric_threshold:
            anomalies.append(
                f"out-of-range numeric value {node} at {path or '<root>'} "
                f"(> {numeric_threshold})"
            )
