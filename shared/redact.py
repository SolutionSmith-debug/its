"""§54 runtime secret / PII-leak backstop — mask well-known secret + PII shapes out of log text.

This is a **defense-in-depth BACKSTOP, not a guarantee** (Op Stds v20 §54; the logging-path twin of
`anomaly_logger`'s Layer-5 sentinel tripwire). The PRIMARY control is not putting a secret into a log
message / traceback in the first place — e.g. `shared/keychain.py` scrubs the raw value out of a
`CalledProcessError` before re-raising. `redact()` catches what still slips through — a rotated-away
key or an operator email that lands inside an exception string — on the `error_log` surfaces that
LEAVE the trusted Mac: the `ITS_Errors` Smartsheet row, the Resend operator email, and the Sentry
event (the "triple-fire" §54 names).

The on-Mac **local log file is deliberately NOT redacted** — it stays full-fidelity forensics behind
Tailscale and is where an operator diagnoses + rotates a leaked credential; §54 scopes the guarantee
to the triple-fire (the three surfaces that egress), not the local record.

Redaction is imperfect: a paraphrased or novel-format secret evades the patterns (exactly the
anomaly_logger sentinel-substring limitation). It shrinks the blast radius of an accidental
secret-in-a-traceback (the incident that forced a key rotation) from "exfiltrated to three external
surfaces" to "on the Mac only" — it does not eliminate the class. The pattern set is intentionally
conservative (high-confidence secret shapes + email PII) to limit over-redaction of legitimate log
context; it is operator-tunable if a false-redaction ever bites.
"""
from __future__ import annotations

import re

_REDACTED = "<redacted>"
_REDACTED_EMAIL = "<redacted-email>"

# High-confidence SECRET shapes — a match is ~never legitimate log content. All replacements are
# plain re.sub strings (group backrefs where structure must be preserved) so the pattern table
# stays a simple (compiled, replacement) tuple.
_SECRET_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    # `Authorization: Bearer <token>` and a bare `Bearer <token>`.
    (re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{8,}"), f"Bearer {_REDACTED}"),
    # AWS access-key id.
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), _REDACTED),
    # Provider-prefixed tokens: OpenAI sk-, Slack xox[baprs]-, GitHub gh[opsur]_, GitLab glpat-.
    (re.compile(r"\b(?:sk|xox[baprs]|gh[opsur]|glpat)[-_][A-Za-z0-9_-]{8,}"), _REDACTED),
    # `key=value` / `key: value` secrets — keep the key + separator (group 1), mask the value.
    (
        re.compile(
            r"(?i)((?:password|passwd|secret|token|api[_-]?key|client[_-]?secret|access[_-]?token)"
            r"\s*[=:]\s*)(?:\"[^\"]*\"|'[^']*'|\S+)"
        ),
        r"\g<1>" + _REDACTED,
    ),
)

# PII: email addresses. Lower-precision (a legitimate log line CAN carry an email), so it is a
# separate, clearly-labelled leg — trades a little log context for the §54 PII backstop.
_EMAIL_PATTERN = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")


def redact(text: str | None) -> str:
    """Return `text` with known secret + PII shapes masked. `None` → `''`.

    Applied to the `message` + `exc_info` (traceback) at the `error_log` surfaces that egress the Mac
    (ITS_Errors row, Resend email, Sentry event). Idempotent on already-redacted text (re-running is
    a no-op — the placeholders match no pattern). **Never raises** — a redaction bug must NOT break
    error surfacing, so a broad-except returns the original text unchanged (a leaked secret is a
    strictly better failure than a swallowed CRITICAL).
    """
    if text is None:
        return ""
    if not text:
        return text
    try:
        out = text
        for pattern, repl in _SECRET_PATTERNS:
            out = pattern.sub(repl, out)
        return _EMAIL_PATTERN.sub(_REDACTED_EMAIL, out)
    except Exception:  # noqa: BLE001 — never break error surfacing; a leak beats a swallowed CRITICAL
        return text
