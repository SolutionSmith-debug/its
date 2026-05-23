"""SPF / DKIM / DMARC + Return-Path mismatch analyzer for Graph messages.

Stage 2 of the Safety Reports intake pipeline calls `analyze()` on the
list-of-dicts under `internetMessageHeaders` returned by
`shared.graph_client.get_message(..., include_headers=True)`. The verdict
drives the trusted-sender routing matrix in `safety_reports.intake`:

    PASS       → proceed (gate is just one of two — scope check is separate)
    SOFT_FAIL  → trusted sender: review queue; unknown sender: quarantine
    HARD_FAIL  → quarantine

Per FM v8 Invariant 2 Layer 1: we do NOT re-validate DKIM signatures
locally (no DNS TXT lookup + RSA verify). We trust the inbound MTA's
Authentication-Results verdict for the chain it just walked. Re-validating
would require dkimpy or similar; tech-debt entry tracks that if a future
security review demands it.

Authentication-Results parsing follows RFC 8601 loosely:
  - Header value is `<mta-name>; spf=... ; dkim=... ; dmarc=...`
  - Multiple Authentication-Results headers may exist (one per hop).
    Graph emits them in the order received MTAs appended them; the
    one closest to the receiving server is FIRST in the list.

DMARC policy parsing extracts the `p=<policy>` clause when present
(`dmarc=fail (p=reject)` vs `dmarc=fail (p=none)`) — only `p=reject`
escalates DMARC fail to HARD_FAIL; `p=none` and `p=quarantine` are
SOFT_FAIL because the inbound MTA's quarantine decision isn't a hard
forgery signal on its own.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

# Each Authentication-Results value contains tokens of the form
# `keyword=value` where value may be a bare word or quoted. We extract the
# leading word after `=` and lowercase it.
_AUTH_RESULT_TOKEN_RE = re.compile(r"([a-z][a-z0-9-]*)\s*=\s*([a-z0-9-]+)", re.I)
# Pull `p=<policy>` out of any parenthesized DMARC comment.
_DMARC_POLICY_RE = re.compile(r"p\s*=\s*([a-z]+)", re.I)
# Pull the domain out of an angle-bracketed address (Return-Path / From).
_ANGLE_ADDR_RE = re.compile(r"<([^>]+)>")


class HeaderVerdict(StrEnum):
    """Composite verdict for the message's authentication posture."""

    PASS = "pass"
    SOFT_FAIL = "soft_fail"
    HARD_FAIL = "hard_fail"


@dataclass(frozen=True)
class HeaderAnalysis:
    """Per-message authentication snapshot."""

    verdict: HeaderVerdict
    spf: str
    dkim: str
    dmarc: str
    return_path_domain: str | None
    from_domain: str | None
    return_path_mismatch: bool
    raw_authentication_results: str | None


def _header_lookup(
    headers: list[dict[str, str]], name: str,
) -> list[str]:
    """Case-insensitive multi-value header lookup. Returns values in order."""
    target = name.lower()
    out: list[str] = []
    for h in headers:
        if not isinstance(h, dict):
            continue
        h_name = h.get("name")
        if isinstance(h_name, str) and h_name.lower() == target:
            value = h.get("value")
            if isinstance(value, str):
                out.append(value)
    return out


def _parse_auth_results(value: str) -> dict[str, str]:
    """Return `{spf|dkim|dmarc: result}` from one Authentication-Results value.

    Only the first occurrence of each token wins (multiple `dkim=...`
    clauses in one header are rare but a defensive strategy is to take
    the first, which is the first signer the verifying MTA checked).
    """
    found: dict[str, str] = {}
    for match in _AUTH_RESULT_TOKEN_RE.finditer(value):
        keyword = match.group(1).lower()
        result = match.group(2).lower()
        if keyword in ("spf", "dkim", "dmarc") and keyword not in found:
            found[keyword] = result
    return found


def _parse_dmarc_policy(value: str) -> str | None:
    """Return the DMARC `p=` policy from an Authentication-Results value, or None."""
    match = _DMARC_POLICY_RE.search(value)
    return match.group(1).lower() if match else None


def _domain_of(address_value: str) -> str | None:
    """Extract `domain` from `Name <local@domain>` / `<local@domain>` / `local@domain`."""
    if not address_value:
        return None
    # Strip angle brackets if present, otherwise treat the whole string as the addr.
    match = _ANGLE_ADDR_RE.search(address_value)
    raw_addr = match.group(1) if match else address_value
    raw_addr = raw_addr.strip()
    if not raw_addr or "@" not in raw_addr:
        return None
    domain = raw_addr.rsplit("@", 1)[1].strip().lower()
    # Strip a trailing `>` or whitespace that survived sloppy parsing.
    domain = domain.rstrip(">").strip()
    return domain or None


def _parse_received_spf(value: str) -> str:
    """Fallback parser for standalone `Received-SPF: <result>` headers."""
    first_word = value.strip().split(None, 1)
    if not first_word:
        return "missing"
    return first_word[0].lower()


def _classify(
    spf: str, dkim: str, dmarc: str, dmarc_policy: str | None,
    return_path_mismatch: bool,
) -> HeaderVerdict:
    """Apply the composite verdict rules."""
    if spf == "fail":
        return HeaderVerdict.HARD_FAIL
    if dkim == "fail":
        return HeaderVerdict.HARD_FAIL
    if dmarc == "fail" and dmarc_policy == "reject":
        return HeaderVerdict.HARD_FAIL

    spf_ok = spf == "pass"
    dkim_ok = dkim in ("pass", "none")
    dmarc_ok = dmarc in ("pass", "none")
    if spf_ok and dkim_ok and dmarc_ok:
        if return_path_mismatch and dmarc != "pass":
            return HeaderVerdict.SOFT_FAIL
        return HeaderVerdict.PASS
    return HeaderVerdict.SOFT_FAIL


def analyze(internet_message_headers: list[dict[str, str]]) -> HeaderAnalysis:
    """Parse Graph's internetMessageHeaders into a verdict.

    Input shape (per Graph): list of {"name": str, "value": str} dicts.
    Order is the order the MTAs appended (most recent first). Authentication-
    Results closest to the receiving server (first occurrence) wins.
    """
    auth_values = _header_lookup(internet_message_headers, "Authentication-Results")
    received_spf_values = _header_lookup(internet_message_headers, "Received-SPF")

    raw_auth = auth_values[0] if auth_values else None
    if raw_auth:
        tokens = _parse_auth_results(raw_auth)
        spf = tokens.get("spf", "missing")
        dkim = tokens.get("dkim", "missing")
        dmarc = tokens.get("dmarc", "missing")
        dmarc_policy = _parse_dmarc_policy(raw_auth)
    else:
        # Fallback: standalone Received-SPF is the next best signal for SPF.
        spf = _parse_received_spf(received_spf_values[0]) if received_spf_values else "missing"
        # DKIM-Signature presence alone doesn't tell us pass/fail — we'd need
        # to re-validate, which is out of scope per module docstring.
        dkim = "missing"
        dmarc = "missing"
        dmarc_policy = None

    return_path_values = _header_lookup(internet_message_headers, "Return-Path")
    from_values = _header_lookup(internet_message_headers, "From")
    return_path_domain = _domain_of(return_path_values[0]) if return_path_values else None
    from_domain = _domain_of(from_values[0]) if from_values else None
    return_path_mismatch = bool(
        return_path_domain
        and from_domain
        and return_path_domain != from_domain
    )

    verdict = _classify(spf, dkim, dmarc, dmarc_policy, return_path_mismatch)

    return HeaderAnalysis(
        verdict=verdict,
        spf=spf,
        dkim=dkim,
        dmarc=dmarc,
        return_path_domain=return_path_domain,
        from_domain=from_domain,
        return_path_mismatch=return_path_mismatch,
        raw_authentication_results=raw_auth,
    )
