"""Transient-vs-absent classification for a daemon's fail-closed credential read.

Every portal-pulling daemon resolves the same three things before it will poll: a
Worker base URL from `ITS_Config`, plus a bearer and an HMAC secret from the macOS
Keychain. All three must be present, and a daemon that cannot resolve them must
FAIL CLOSED — it does not poll.

The trap this module exists to close: the base-URL read goes over the network, so
"I could not READ the config row" and "the config row is genuinely empty" are two
completely different conditions that a naive `except: return default` collapses
into one. The first is a rate-limit/5xx blip that self-heals on the next cycle;
the second is a misconfig that never will. Collapsing them makes a daemon page
CRITICAL — naming CREDENTIALS, which are usually fine — every time Smartsheet
hiccups, and it points the operator's §43 repair at re-provisioning secrets that
were never missing. That is a false page AND a misdirection toward a
high-capability-class secrets action.

Bearer + secret come from the LOCAL Keychain and are unaffected by a Smartsheet
outage, so only the base-URL read needs this three-way classification:

    * ``str``                  — the row was read and is non-empty.
    * ``TransientUnavailable`` — the row could NOT be read right now (circuit OPEN,
                                 or a raw rate-limit/5xx before the breaker trips).
                                 Self-heals → the caller WARNs and skips the cycle.
    * ``None``                 — the row is genuinely absent or blank. A misconfig
                                 that will NOT self-heal → the caller pages.

Auth/permission errors deliberately do NOT count as transient: a revoked API token
or a lost share is a deterministic misconfig that must page, so they propagate.

This logic was first written inline in `safety_reports/portal_poll.py` (which had
been paging falsely on every blip) and is hoisted here unchanged so the other five
pullers — po_poll, rfq_poll, estimate_poll, subcontract_poll, fieldops_sync —
share ONE implementation instead of five drifting copies. `portal_poll` re-exports
these names, so its existing call sites and tests are unaffected.
"""
from __future__ import annotations

from shared import smartsheet_client


class TransientUnavailable:
    """Sentinel distinct from None: the config read failed because Smartsheet was
    TEMPORARILY unreachable (circuit OPEN, or a raw rate-limit/5xx blip before the
    breaker trips) — NOT a misconfig. The row is fine; Smartsheet is briefly down.
    Self-heals when the backend recovers, so the caller WARNs + skips the cycle
    instead of paging (CRITICAL). ``reason`` names the specific transient condition
    for the WARN log / heartbeat summary.

    ``circuit_open`` distinguishes the two transient sub-cases for a caller that keeps a
    sustained-failure counter (`sustained_failure.TransientFence`): a circuit-OPEN skip
    must NOT be counted, because the breaker already owns that page and counting it would
    turn one outage into a per-daemon CRITICAL storm on separate alert-dedupe keys. Every
    other transient IS counted.

    ``exc`` carries the ORIGINAL exception so a caller can re-classify more strictly than
    this module does. That matters because "transient" here is deliberately BROAD — it
    covers 429 and any other `SmartsheetError` with a body, which for the five portal
    pullers is the right skip-and-retry disposition. A caller that previously PAGED on
    those must be able to keep doing so: `publish_daemon`'s base-URL read did (its own
    reader caught only NotFound + CircuitOpen, so a 429/400 propagated to CRITICAL), and
    routing it through this sentinel would otherwise have SOFTENED it to an ERROR by
    accident. Narrow with `smartsheet_client.is_transient_error(exc)` at the call site.
    Silently widening a fail-closed disposition is exactly the bug class this module
    exists to prevent."""

    def __init__(
        self,
        reason: str = "Smartsheet circuit OPEN",
        *,
        circuit_open: bool = True,
        exc: BaseException | None = None,
    ) -> None:
        self.reason = reason
        self.circuit_open = circuit_open
        self.exc = exc


# Singleton sentinel (compared via isinstance, so the exact identity is not load-bearing).
CREDS_TRANSIENT = TransientUnavailable()


def read_base_url(setting: str, workstream: str) -> str | TransientUnavailable | None:
    """Read a Worker base-URL `ITS_Config` row, classifying failure three ways.

    Returns the non-empty URL, a `TransientUnavailable` (retry next cycle, do NOT
    page), or None (genuine misconfig — page). See the module docstring for why the
    distinction is load-bearing.
    """
    try:
        raw = smartsheet_client.get_setting(setting, workstream=workstream)
    except smartsheet_client.SmartsheetCircuitOpenError:
        return CREDS_TRANSIENT
    except smartsheet_client.SmartsheetNotFoundError:
        # The row is genuinely absent — a misconfig, NOT transient. Fall through to None.
        return None
    except (smartsheet_client.SmartsheetAuthError, smartsheet_client.SmartsheetPermissionError):
        # Deterministic misconfig (revoked API token / lost share) — will NOT self-heal,
        # so it must PAGE rather than read as transient. Propagate → @its_error_log
        # CRITICAL. Mirrors the circuit breaker's own ignore-list; must precede the
        # generic catch below.
        raise
    except smartsheet_client.SmartsheetError as exc:
        # TRANSIENT (non-circuit-open) failure — a rate-limit/5xx blip BEFORE the breaker
        # trips: the breaker needs `failure_threshold` CONSECUTIVE failures, so the first
        # cycles of any outage (and every one-cycle blip) raise the raw error class, not
        # SmartsheetCircuitOpenError. Same self-healing condition → same sentinel.
        return TransientUnavailable(
            reason=f"{type(exc).__name__}: {exc!r}", circuit_open=False, exc=exc
        )
    return raw if isinstance(raw, str) and raw else None
