"""Typed per-key validators for the Class-A config editor (WS2 D1-2).

A runtime ITS_Config edit takes effect on the daemon's NEXT cycle with NO CI
checkpoint — so server-side validation + bounds + typed rejection on every
editable key IS the checkpoint. A self-serve config surface without this is a
self-serve outage surface.

Style mirrors po_materials/config_apply.py: ONE typed exception (its message is
the operator-facing rejection reason), module-level named bound/regex
constants, and per-field validators that validate FULLY and raise on any
failure — nothing is written when a validator raises. Each validator returns
the NORMALIZED string to write (e.g. 'TRUE' -> 'true', re-dumped JSON).
"""
from __future__ import annotations

import json
import re
from collections.abc import Callable

Validator = Callable[[str], str]

# --- bounds + shapes (named; a config surface without bounds is an outage surface)
# Anchored with \Z (not $, which matches before a trailing newline) and ASCII
# [0-9] (not \d, which matches Unicode digits) — a config write has no CI
# checkpoint, so a smuggled newline / Unicode-digit value must be rejected here.
_MAX_EMAIL = 254
_MAX_URL = 2_000
_MAX_EMAIL_LIST = 50  # recipient-list item cap
_MAX_LIST_JSON = 10_000  # bound the JSON string before json.loads (avoids pathological parses)
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+\Z")
# 'DDD HH:MM' Pacific wall-clock, HH 00-23, MM 00-59.
_SCHEDULE_RE = re.compile(r"^(MON|TUE|WED|THU|FRI|SAT|SUN) ([01][0-9]|2[0-3]):[0-5][0-9]\Z")
_ID_RE = re.compile(r"^[0-9]{1,20}\Z")  # Smartsheet / Box numeric id
_KEYCHAIN_KEY_RE = re.compile(r"^[A-Z0-9_]{1,64}\Z")  # a Keychain KEY NAME (pointer), never a secret
_INT_RE = re.compile(r"^-?[0-9]+\Z")
_URL_RE = re.compile(r"^https://\S{1,2000}\Z")

# Anthropic model IDs the intake classifier may use (CLAUDE.md "Model
# selection"). Fixed allow-set — an unknown model id is a typed rejection.
KNOWN_MODELS = frozenset(
    {
        "claude-sonnet-4-6",
        "claude-haiku-4-5-20251001",
        "claude-opus-4-7",
    }
)


class ConfigValidationError(Exception):
    """A proposed config value is invalid. The message IS the operator-facing
    rejection reason; on this error NOTHING is written to ITS_Config."""


def v_bool(value: str) -> str:
    v = value.strip().lower()
    if v not in ("true", "false"):
        raise ConfigValidationError(f"must be 'true' or 'false' (got {value!r})")
    return v


def v_int(lo: int, hi: int) -> Validator:
    """Integer in [lo, hi]. Rejects floats/bools/non-digits explicitly."""

    def _v(value: str) -> str:
        s = value.strip()
        if not _INT_RE.fullmatch(s):  # rejects '1.0', 'true', '5x', Unicode digits
            raise ConfigValidationError(f"must be a whole integer (got {value!r})")
        if len(s.lstrip("-")) > 19:  # guard int() against a pathological giant string
            raise ConfigValidationError(f"integer too large (got {value!r})")
        n = int(s)
        if n < lo or n > hi:
            raise ConfigValidationError(f"must be in range {lo}..{hi} (got {n})")
        return str(n)

    return _v


def v_float01(value: str) -> str:
    s = value.strip()
    try:
        f = float(s)
    except ValueError:
        raise ConfigValidationError(f"must be a number between 0.0 and 1.0 (got {value!r})") from None
    if f != f or f in (float("inf"), float("-inf")):  # reject nan/inf
        raise ConfigValidationError(f"must be a finite number (got {value!r})")
    if not (0.0 <= f <= 1.0):
        raise ConfigValidationError(f"must be between 0.0 and 1.0 (got {f})")
    return repr(f)  # canonical, round-trippable ('0.90' -> '0.9') so no-op detection is reliable


def v_enum(allowed: frozenset[str]) -> Validator:
    def _v(value: str) -> str:
        v = value.strip()
        if v not in allowed:
            raise ConfigValidationError(f"must be one of {sorted(allowed)} (got {value!r})")
        return v

    return _v


def v_schedule(value: str) -> str:
    v = value.strip().upper()
    if not _SCHEDULE_RE.match(v):
        raise ConfigValidationError(
            f"must be 'DDD HH:MM' (weekday + 24h Pacific), e.g. 'MON 07:00' (got {value!r})"
        )
    return v


def v_email(value: str) -> str:
    v = value.strip()
    if not _EMAIL_RE.match(v) or len(v) > _MAX_EMAIL:
        raise ConfigValidationError(f"must be a valid email address (got {value!r})")
    return v


def v_email_list(value: str) -> str:
    """A JSON list of valid emails. Empty list [] is allowed (the _default
    fallback is legitimately empty). Each item is stripped so a smuggled
    trailing newline can't be persisted into ITS_Config."""
    s = value.strip()
    if len(s) > _MAX_LIST_JSON:
        raise ConfigValidationError(f"recipient list too large (max {_MAX_LIST_JSON} chars)")
    try:
        parsed = json.loads(s)
    except json.JSONDecodeError as e:
        raise ConfigValidationError(f"must be a JSON list of emails (got {value!r}): {e}") from e
    if not isinstance(parsed, list):
        raise ConfigValidationError('must be a JSON list, e.g. ["a@b.com"]')
    if len(parsed) > _MAX_EMAIL_LIST:
        raise ConfigValidationError(f"too many recipients (max {_MAX_EMAIL_LIST})")
    cleaned: list[str] = []
    for item in parsed:
        if not isinstance(item, str):
            raise ConfigValidationError(f"every entry must be a string email (bad: {item!r})")
        addr = item.strip()
        if not _EMAIL_RE.match(addr) or len(addr) > _MAX_EMAIL:
            raise ConfigValidationError(f"every entry must be a valid email (bad: {item!r})")
        cleaned.append(addr)
    return json.dumps(cleaned)  # canonical re-dump of stripped addresses


def v_id(value: str) -> str:
    v = value.strip()
    if not _ID_RE.match(v):
        raise ConfigValidationError(f"must be a numeric id (got {value!r})")
    return v


def v_keychain_key(value: str) -> str:
    v = value.strip()
    if not _KEYCHAIN_KEY_RE.match(v):
        raise ConfigValidationError(
            f"must be an UPPER_SNAKE Keychain key NAME (a pointer, not a secret) (got {value!r})"
        )
    return v


def v_url(value: str) -> str:
    v = value.strip()
    if len(v) > _MAX_URL or not _URL_RE.match(v):
        raise ConfigValidationError(f"must be an https:// URL (got {value!r})")
    return v


# --- Class-B (D1-3) validators -------------------------------------------------
SYSTEM_STATES = frozenset({"ACTIVE", "PAUSED", "MAINTENANCE"})
_DOMAIN_PATTERN_RE = re.compile(r"^@[^@\s]+\.[^@\s]+\Z")  # e.g. '@evergreen.com'


def v_state(value: str) -> str:
    """system.state — the global brake. Exactly one of ACTIVE|PAUSED|MAINTENANCE."""
    v = value.strip().upper()
    if v not in SYSTEM_STATES:
        raise ConfigValidationError(f"must be one of {sorted(SYSTEM_STATES)} (got {value!r})")
    return v


def v_sender_list(value: str) -> str:
    """A JSON list where each item is a full email OR an '@domain.tld' pattern
    (the intake allowed-senders format). Each item is stripped."""
    s = value.strip()
    if len(s) > _MAX_LIST_JSON:
        raise ConfigValidationError(f"sender list too large (max {_MAX_LIST_JSON} chars)")
    try:
        parsed = json.loads(s)
    except json.JSONDecodeError as e:
        raise ConfigValidationError(f"must be a JSON list (got {value!r}): {e}") from e
    if not isinstance(parsed, list):
        raise ConfigValidationError('must be a JSON list, e.g. ["a@b.com", "@b.com"]')
    if len(parsed) > _MAX_EMAIL_LIST:
        raise ConfigValidationError(f"too many entries (max {_MAX_EMAIL_LIST})")
    cleaned: list[str] = []
    for item in parsed:
        if not isinstance(item, str):
            raise ConfigValidationError(f"every entry must be a string (bad: {item!r})")
        entry = item.strip()
        ok = (_EMAIL_RE.match(entry) or _DOMAIN_PATTERN_RE.match(entry)) and len(entry) <= _MAX_EMAIL
        if not ok:
            raise ConfigValidationError(f"every entry must be an email or '@domain.tld' (bad: {item!r})")
        cleaned.append(entry)
    return json.dumps(cleaned)


def v_reviewer_chain(value: str) -> str:
    """A JSON object the scheduler reads: primary/secondary/tertiary emails +
    delay_to_secondary_hours / delay_to_tertiary_hours (non-negative ints)."""
    s = value.strip()
    if len(s) > _MAX_LIST_JSON:
        raise ConfigValidationError("reviewer_chain too large")
    try:
        parsed = json.loads(s)
    except json.JSONDecodeError as e:
        raise ConfigValidationError(f"must be a JSON object (got {value!r}): {e}") from e
    if not isinstance(parsed, dict):
        raise ConfigValidationError("must be a JSON object")
    for role in ("primary", "secondary", "tertiary"):
        addr = parsed.get(role)
        if not isinstance(addr, str) or not _EMAIL_RE.match(addr.strip()) or len(addr) > _MAX_EMAIL:
            raise ConfigValidationError(f"{role!r} must be a valid email (got {addr!r})")
    for key in ("delay_to_secondary_hours", "delay_to_tertiary_hours"):
        n = parsed.get(key)
        if not isinstance(n, int) or isinstance(n, bool) or n < 0 or n > 8_760:
            raise ConfigValidationError(f"{key!r} must be a non-negative integer of hours (got {n!r})")
    # canonical rebuild — strip the emails and keep ONLY the 5 known keys (drop any
    # extras), matching the sibling list validators' strip-and-redump contract.
    cleaned: dict[str, object] = {role: parsed[role].strip() for role in ("primary", "secondary", "tertiary")}
    cleaned["delay_to_secondary_hours"] = parsed["delay_to_secondary_hours"]
    cleaned["delay_to_tertiary_hours"] = parsed["delay_to_tertiary_hours"]
    return json.dumps(cleaned)
