"""§54 runtime secret / PII-leak backstop — mask well-known secret + PII shapes out of egress log text.

Purpose
-------
A defense-in-depth BACKSTOP (Op Stds v20 §54; the logging-path twin of `anomaly_logger`'s Layer-5
sentinel tripwire) for the `error_log` triple-fire. The PRIMARY control is not putting a secret into a
log message / traceback in the first place — e.g. `shared/keychain.py` scrubs the raw value out of a
`CalledProcessError` before re-raising. `redact()` catches what still slips through — a rotated-away
key or an operator email that lands inside an exception string — before it reaches the three
triple-fire surfaces that LEAVE the trusted Mac: the `ITS_Errors` Smartsheet row, the Resend operator
email, and the Sentry event. It shrinks the blast radius of an accidental secret-in-a-traceback (the
incident that forced a key rotation) from "exfiltrated to three external surfaces" to "on the Mac
only".

Invariants
----------
- **Backstop, NOT a guarantee.** A paraphrased or novel-format secret evades the patterns (exactly the
  anomaly_logger sentinel-substring limitation). It reduces the class, it does not eliminate it — the
  docstring must never let a reader believe otherwise.
- **Egress-only.** Applied to the three egress surfaces of the triple-fire; the on-Mac local log file
  (`error_log._local_log`) is deliberately left RAW — §54 scopes the guarantee to the triple-fire, and
  the local file is full-fidelity forensics behind Tailscale, where an operator diagnoses + rotates a
  leaked credential.
- **Never raises.** A redaction bug must NOT break error surfacing (a leaked secret is a strictly
  better failure than a swallowed CRITICAL), so a broad-except returns the input unchanged.
- **Structured-field-safe.** Masks only free-text `message` / `exc_info`; a UUID `correlation_id` and
  benign ids (`status=active`, sheet ids, `JOB-0007`) are left intact (no over-redaction).

Failure modes
-------------
- Pattern miss → a secret of an unrecognized shape egresses unmasked (accepted; see Invariants). The
  pattern set is operator-tunable — add a shape when a new provider secret enters the Keychain.
- Internal error (a pathological input crashing a regex engine) → returns the ORIGINAL text unredacted
  rather than raising; a `test_*_never_raises_*` case locks this.

Consumers
---------
- `shared.error_log._smartsheet_log` — redacts the ITS_Errors `Message` / `Traceback` cells.
- `shared.error_log._alert_critical` — redacts `message` / `exc_info` once, covering the Resend
  subject/body AND the args handed to `_fire_sentry_leg`.
- `tests/test_error_log_redaction_backstop.py` — locks the shapes, the egress-only design, and the
  never-raises guarantee (§54 `enforced` evidence).
"""
from __future__ import annotations

import re

_REDACTED = "<redacted>"
_REDACTED_EMAIL = "<redacted-email>"

# High-confidence SECRET shapes — a match is ~never legitimate log content. Replacements are plain
# re.sub strings (group backrefs where structure must be preserved) so the table stays a simple
# (compiled, replacement) tuple. The set is deliberately conservative + tunable, not exhaustive.
_SECRET_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    # `Authorization: Bearer <token>` and a bare `Bearer <token>`.
    (re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{8,}"), f"Bearer {_REDACTED}"),
    # AWS access-key id.
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), _REDACTED),
    # Provider-prefixed tokens: OpenAI sk-, Slack xox[baprs]-, classic GitHub gh[opsur]_, GitLab glpat-.
    (re.compile(r"\b(?:sk|xox[baprs]|gh[opsur]|glpat)[-_][A-Za-z0-9_-]{8,}"), _REDACTED),
    # GitHub fine-grained PAT — this repo's `gh auth token` shape (ops-stds review fast-follow).
    (re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}"), _REDACTED),
    # Resend API key (`ITS_RESEND_API_KEY`).
    (re.compile(r"\bre_[A-Za-z0-9]{20,}"), _REDACTED),
    # Sentry DSN (`ITS_SENTRY_DSN`) — embeds the project key before the `@`.
    (re.compile(r"https://[A-Za-z0-9._-]+@[A-Za-z0-9._-]*sentry\.io/\d+"), _REDACTED),
    # `key=value` / `key: value` secrets — keep the key + separator (group 1), mask the value.
    (
        re.compile(
            r"(?i)((?:password|passwd|secret|token|api[_-]?key|client[_-]?secret|access[_-]?token)"
            r"\s*[=:]\s*)(?:\"[^\"]*\"|'[^']*'|\S+)"
        ),
        r"\g<1>" + _REDACTED,
    ),
)

# PII: email addresses. Lower-precision (a legitimate log line CAN carry an email), so a separate,
# clearly-labelled leg — trades a little log context for the §54 PII backstop.
_EMAIL_PATTERN = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")


def redact(text: str | None) -> str:
    """Return `text` with known secret + PII shapes masked. `None` → `''`.

    Applied to `message` + `exc_info` at the `error_log` surfaces that egress the Mac (ITS_Errors row,
    Resend email, Sentry event). Idempotent on already-redacted text (the placeholders match no
    pattern). Never raises — a broad-except returns the original text unchanged (Invariants).
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
