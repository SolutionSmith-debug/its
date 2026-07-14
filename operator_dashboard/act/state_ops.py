"""Class-B runtime-state operations for the operator dashboard (Block 3).

CLEAR THE CIRCUIT BREAKER — reset a stuck-OPEN breaker to CLOSED without waiting
for its cooldown. It reuses `shared.circuit_breaker`'s OWN `_blank_state()`
factory (the single source of truth for the breaker's state schema, so a future
field addition is respected here with no duplicated shape) written through the
CANONICAL `shared.state_io.atomic_write_json` path (crash-safe temp-file +
os.replace — the required writer for anything under ~/its/state/). The
elevated-confirm ceremony (re-PIN + typed confirmation) is verified by the router
before this runs. No secret, no send, no ITS_Config write.

LOCK CLEAR — PARKED (design note, not built). The ITS state-lock model
(`shared.state_io.with_path_lock`) uses a NON-BLOCKING `fcntl` flock on a
persistent `<path>.lock` sidecar. A live holder's flock is released by the OS the
instant its process dies, and the sidecar FILE is deliberately left behind
(existence != held). So there is **no stale-lock ARTIFACT to clear**: a lock the
dashboard's LocksSource probes as "HELD" is a genuinely-live holder (force-
removing the sidecar would not release the flock and could race a real acquire).
A generic lock-clear verb is therefore not-applicable in this architecture and is
intentionally NOT built. If some future lock ever leaves a stale artifact (a
different discipline), scope a targeted clear for that specific lock then.
"""
from __future__ import annotations

import importlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any


@dataclass
class StateOutcome:
    kind: str  # ok | noop | error (also CSS status class + test assertion)
    message: str


def _load(name: str) -> Any:
    return importlib.import_module(name)


def clear_circuit_breaker(operator: str) -> StateOutcome:
    """Reset the circuit breaker to CLOSED (skip the cooldown). Reads the PERSISTED
    state string directly (like the CircuitBreakerSource panel) rather than
    is_open() — which is gated by circuit_breaker.enabled — so a no-op clear is
    honest even when the breaker is disabled. Never leaves torn state (atomic
    write through the canonical state_io path)."""
    cb = _load("shared.circuit_breaker")
    sio = _load("shared.state_io")
    try:
        raw = json.loads(cb.STATE_FILE.read_text())
        prior = raw.get("state") if isinstance(raw, dict) else None
    except Exception:
        prior = None  # missing / corrupt state file → treated as already-CLOSED
    try:
        sio.atomic_write_json(cb.STATE_FILE, cb._blank_state())
    except Exception as exc:
        return StateOutcome("error", f"breaker reset failed: {type(exc).__name__}: {exc}")
    _audit_breaker_clear(operator, prior)
    if prior in (None, cb.CLOSED):
        return StateOutcome("noop", f"circuit breaker was already {prior or 'unset'}/CLOSED — reset written anyway")
    return StateOutcome("ok", f"circuit breaker reset ({prior} → CLOSED, cooldown skipped)")


def _audit_breaker_clear(operator: str, prior: Any) -> None:
    try:
        el = _load("shared.error_log")
        ts = datetime.now(UTC).isoformat()
        el.log(
            el.Severity.WARN,
            "operator_dashboard.config_editor",
            f"circuit breaker manually CLEARED to CLOSED by {operator} (prior={prior!r}, "
            f"elevated-confirm) at {ts}",
            error_code="config_breaker_cleared",
            alert=False,
        )
    except Exception:
        pass
