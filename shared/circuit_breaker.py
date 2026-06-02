"""Domain-agnostic circuit breaker for SDK-wrapper network methods (F08).

Purpose:
    Stop daemons from hammering a sustained-degraded backend (Smartsheet
    today; Box/Graph reuse the same generic guard with their own exception
    sets). The breaker sits STRICTLY ABOVE the typed-exception layer of an
    SDK wrapper: the SDK's own HTTP retry/backoff handles transient/per-call
    failures; this breaker handles SUSTAINED, CROSS-CALL — and, because state
    is persisted, CROSS-PROCESS — failure. It does NOT re-implement retry
    (Op Stds v16 §14: wrap, don't reimplement).

    Wire it by decorating the wrapper's network-issuing methods:

        @circuit_breaker.guard(
            open_exc=SmartsheetCircuitOpenError,   # raised when OPEN (short-circuit)
            count=SmartsheetError,                 # base class that counts as a failure
            ignore=(SmartsheetAuthError,           # deterministic / routine — never count
                    SmartsheetPermissionError,
                    SmartsheetNotFoundError),
        )
        def get_sheet(...): ...

Invariants:
    - ONE global breaker per backend, persisted to a single JSON state file
      (``~/its/state/circuit_breaker.json`` for Smartsheet). Persistence is
      load-bearing: launchd runs each daemon as a fresh process per cycle, so
      an in-memory counter could NEVER trip across cycles — the consecutive
      count and OPEN deadline must outlive the process.
    - CONSECUTIVE-failure tripping: ``failure_threshold`` consecutive
      counting-eligible failures trip OPEN; ANY success resets the count to 0.
      This is a hard-outage detector, not a brownout throttle (partial
      degradation is F09's alerts-per-hour cap's job, deliberately).
    - Lock-free reads on the hot path; ``with_path_lock`` + atomic-write ONLY
      on a state-transition write. A healthy system does zero failures → zero
      writes → zero lock contention. Reads rely on the ``os.replace``
      inode-swap guarantee in ``state_io`` (a reader sees old-or-new, never a
      torn file) — same lock-free-read precedent as
      ``alert_dedupe.list_expired_summaries``.
    - HALF_OPEN single-probe: after cooldown the FIRST process to flip
      HALF_OPEN (under lock) owns the lone probe; a concurrent process seeing
      HALF_OPEN short-circuits that cycle. Probe success → CLOSED; probe
      failure → OPEN with a fresh ``opened_at``.
    - ``ignore`` is checked before ``count`` (ignore classes subclass the
      count base): 401/403 must surface AS auth/permission (Tier-3), and 404
      is the routine "row not seeded" case — none indicate a degraded service.
    - ``open_exc`` raised from WITHIN a wrapped call (a nested short-circuit)
      is propagated WITHOUT counting — it is not a failure of the outer method.

Failure modes (all fail OPEN = let the call THROUGH, never wedge the system):
    - Missing/corrupt state file → treated as CLOSED.
    - Lock timeout on a transition write → skip the write, let the call
      proceed (an extra call to a degraded API is benign; wedging a daemon is
      not). Mirrors ``state_io``'s "fail-open + skip" contract.
    - Config unreadable → fall back to ``defaults.py`` (ENABLED=True): a
      degraded Smartsheet still trips. The escape hatch ``enabled=false`` only
      works when config reads succeed (it is layer 3 of 3 — see the runbook;
      layer 2, ``rm circuit_breaker.json``, works even during a total outage).
    - ``bypass()`` skips the open-check AND failure-counting and calls straight
      through. Used for three control-plane sites (the ``CIRCUIT_OPEN`` heartbeat
      status write; the ``ITS_Errors`` forensic record write in
      ``error_log._smartsheet_log`` — §3.1 always-write surface; and the
      breaker's own config reads) so an OPEN breaker can neither block the
      surfacing / forensic writes nor block reading the ``enabled=false`` flag
      that disables it.

Consumers:
    - ``shared/smartsheet_client.py`` — decorates its 16 network-issuing
      methods; defines ``SmartsheetCircuitOpenError`` and registers the
      bypass-wrapped config loader.
    - ``shared/error_log.py`` — wraps the ITS_Errors record write
      (``_smartsheet_log``) in ``bypass()`` so the §3.1 always-write forensic
      surface is never short-circuited by an OPEN breaker (the third bypass site).
    - ``safety_reports/intake_poll.py`` / ``weekly_send_poll.py`` — call
      ``is_open()`` (lock-free) to surface ``CIRCUIT_OPEN`` heartbeat status,
      and wrap their heartbeat write in ``bypass()``.
    - PR-2 ``scripts/watchdog.py`` — reads the LOCAL state file (works during a
      Smartsheet outage) for a prolonged-open alert.

Import discipline (cycle-break):
    ``error_log`` imports ``smartsheet_client`` at module top, and
    ``smartsheet_client`` imports THIS module to decorate. So this module
    imports ONLY ``state_io`` + ``defaults`` + stdlib at top level, and reaches
    ``error_log._local_log`` via a lazy import inside the logging path. It
    imports NOTHING from ``smartsheet_client`` — the exception classes flow in
    as ``guard`` arguments; the config source flows in via a registered loader.
"""
from __future__ import annotations

import contextlib
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import wraps
from pathlib import Path
from typing import Any, TypeVar

from . import defaults, state_io

STATE_FILE = Path.home() / "its" / "state" / "circuit_breaker.json"

# State machine values (persisted verbatim in the ``state`` field).
CLOSED = "CLOSED"
OPEN = "OPEN"
HALF_OPEN = "HALF_OPEN"
_VALID_STATES = frozenset({CLOSED, OPEN, HALF_OPEN})

_F = TypeVar("_F", bound=Callable[..., Any])


@dataclass(frozen=True)
class CircuitConfig:
    """Resolved breaker config for one evaluation (enabled + the two knobs)."""

    enabled: bool
    failure_threshold: int
    cooldown_seconds: int


# Registered config loader for the global Smartsheet breaker, so arg-free
# ``guard(...)`` / ``is_open()`` calls (daemons, decoration) resolve live
# config. ``smartsheet_client`` registers a bypass-wrapped, cached loader at
# import. Tests inject a loader explicitly instead.
_registered_config_loader: Callable[[], CircuitConfig] | None = None

# Bypass flag. A depth counter (supports nesting) is safe because launchd
# daemons are single-process / single-threaded — there is no other thread to
# observe a transiently-raised flag.
_bypass_depth = 0


# ---- config ---------------------------------------------------------------


def set_config_loader(loader: Callable[[], CircuitConfig] | None) -> None:
    """Register the global config loader (called once by ``smartsheet_client``)."""
    global _registered_config_loader
    _registered_config_loader = loader


def _defaults_config() -> CircuitConfig:
    return CircuitConfig(
        enabled=defaults.CIRCUIT_BREAKER_ENABLED,
        failure_threshold=defaults.CIRCUIT_BREAKER_FAILURE_THRESHOLD,
        cooldown_seconds=defaults.CIRCUIT_BREAKER_COOLDOWN_SECONDS,
    )


def _resolve_config(loader: Callable[[], CircuitConfig] | None) -> CircuitConfig:
    """Resolve config from the given loader, else the registered one, else
    defaults. Any loader failure falls back to defaults (ENABLED — safe)."""
    loader = loader or _registered_config_loader
    if loader is None:
        return _defaults_config()
    try:
        return loader()
    except Exception:  # noqa: BLE001 — config read must never wedge the guard
        return _defaults_config()


# ---- time (overridable for tests) -----------------------------------------


def _now() -> datetime:
    return datetime.now(UTC)


# ---- state I/O ------------------------------------------------------------


def _blank_state() -> dict[str, Any]:
    return {"state": CLOSED, "consecutive_failures": 0, "opened_at": None}


def _load_state(path: Path) -> dict[str, Any]:
    """Lock-free read of breaker state; missing/corrupt → CLOSED (fail-open).

    A plain ``read_text`` is safe: ``state_io`` writes via temp-file +
    ``os.replace``, so a concurrent reader always sees a complete file.
    """
    try:
        raw = path.read_text()
    except (FileNotFoundError, OSError):
        return _blank_state()
    try:
        import json

        data = json.loads(raw)
    except (ValueError, TypeError):
        return _blank_state()
    if not isinstance(data, dict) or data.get("state") not in _VALID_STATES:
        return _blank_state()
    data.setdefault("consecutive_failures", 0)
    data.setdefault("opened_at", None)
    return data


def _write_state(path: Path, state: dict[str, Any]) -> None:
    """Persist state via the canonical atomic-write path. Never raises —
    a write failure logs a WARN and is dropped (the breaker fails open)."""
    try:
        state_io.atomic_write_json(path, state)
    except Exception as exc:  # noqa: BLE001
        _warn(f"circuit_breaker state write failed: {exc!r}")


def _cooldown_elapsed(state: dict[str, Any], cfg: CircuitConfig) -> bool:
    """True if the OPEN cooldown has elapsed. A missing/unparseable
    ``opened_at`` is treated as elapsed (recovery-friendly: allow a probe)."""
    opened_at = state.get("opened_at")
    if not opened_at:
        return True
    try:
        opened = datetime.fromisoformat(opened_at)
    except (ValueError, TypeError):
        return True
    return _now() >= opened + timedelta(seconds=cfg.cooldown_seconds)


# ---- public read ----------------------------------------------------------


def is_open(
    state_path: Path | None = None,
    config_loader: Callable[[], CircuitConfig] | None = None,
) -> bool:
    """Lock-free: True iff OPEN and still within cooldown.

    HALF_OPEN counts as NOT open (a probe is in flight / allowed). Used by
    daemons to override their heartbeat status to ``CIRCUIT_OPEN`` — it is a
    best-effort signal, never a gate. ``state_path`` defaults to the module
    ``STATE_FILE`` resolved at CALL time (so tests can monkeypatch it).
    """
    cfg = _resolve_config(config_loader)
    if not cfg.enabled:
        # Breaker disabled (escape hatch) → the guard is a pass-through, so it is
        # never "open". Don't let a stale OPEN state file surface a spurious
        # CIRCUIT_OPEN — consistent with the guard, which also respects enabled.
        return False
    state = _load_state(STATE_FILE if state_path is None else state_path)
    if state["state"] != OPEN:
        return False
    return not _cooldown_elapsed(state, cfg)


# ---- bypass ---------------------------------------------------------------


@contextlib.contextmanager
def bypass() -> Iterator[None]:
    """Within this context, ``guard`` skips the open-check AND failure-counting
    and calls straight through. Wrap control-plane call sites that must reach
    the backend even when the breaker is OPEN — the three today are: the
    ``CIRCUIT_OPEN`` heartbeat write; the ``ITS_Errors`` forensic record write
    (``error_log._smartsheet_log``, §3.1 always-write surface); and the breaker's
    own ``enabled``/threshold/cooldown config reads."""
    global _bypass_depth
    _bypass_depth += 1
    try:
        yield
    finally:
        _bypass_depth -= 1


# ---- transitions (the only lock-acquiring paths) --------------------------


def _record_failure(path: Path, cfg: CircuitConfig) -> None:
    """Increment the consecutive count under lock; trip OPEN at threshold.
    Lock timeout → skip (fail-open): the breaker may need one more failure to
    trip, which is benign."""
    try:
        with state_io.with_path_lock(path):
            state = _load_state(path)
            failures = int(state.get("consecutive_failures", 0)) + 1
            if failures >= cfg.failure_threshold:
                _write_state(
                    path,
                    {"state": OPEN, "consecutive_failures": failures,
                     "opened_at": _now().isoformat()},
                )
            else:
                # Preserve current OPEN/HALF_OPEN markers' opened_at if present;
                # a CLOSED breaker just accrues the count.
                _write_state(
                    path,
                    {"state": state.get("state", CLOSED) if state.get("state") != HALF_OPEN else OPEN,
                     "consecutive_failures": failures,
                     "opened_at": state.get("opened_at")},
                )
    except state_io.StateLockTimeoutError:
        _warn("circuit_breaker: lock timeout recording failure (skipped, fail-open)")


def _record_probe_failure(path: Path, cfg: CircuitConfig) -> None:
    """A HALF_OPEN probe failed → re-OPEN with a fresh cooldown."""
    try:
        with state_io.with_path_lock(path):
            state = _load_state(path)
            failures = int(state.get("consecutive_failures", 0)) + 1
            _write_state(
                path,
                {"state": OPEN, "consecutive_failures": failures,
                 "opened_at": _now().isoformat()},
            )
    except state_io.StateLockTimeoutError:
        _warn("circuit_breaker: lock timeout recording probe failure (skipped)")


def _record_success(path: Path) -> None:
    """Reset to CLOSED/0. HOT-PATH SHORT-CIRCUIT: if already CLOSED with a zero
    count, return WITHOUT acquiring the lock or writing — a healthy system does
    zero state writes."""
    state = _load_state(path)
    if state["state"] == CLOSED and int(state.get("consecutive_failures", 0)) == 0:
        return
    try:
        with state_io.with_path_lock(path):
            current = _load_state(path)
            if current["state"] == CLOSED and int(current.get("consecutive_failures", 0)) == 0:
                return
            _write_state(path, _blank_state())
    except state_io.StateLockTimeoutError:
        _warn("circuit_breaker: lock timeout recording success (skipped)")


def _try_claim_probe(path: Path, cfg: CircuitConfig) -> bool:
    """Under lock, atomically flip OPEN→HALF_OPEN if cooldown elapsed and claim
    the single probe. Returns True if THIS caller may proceed (it owns the probe
    or the breaker was reset to CLOSED), False to short-circuit."""
    try:
        with state_io.with_path_lock(path):
            state = _load_state(path)
            s = state["state"]
            if s == CLOSED:
                return True
            if s == OPEN and _cooldown_elapsed(state, cfg):
                _write_state(
                    path,
                    {"state": HALF_OPEN,
                     "consecutive_failures": int(state.get("consecutive_failures", 0)),
                     "opened_at": state.get("opened_at")},
                )
                return True
            # OPEN-within-cooldown, or HALF_OPEN already claimed by another.
            return False
    except state_io.StateLockTimeoutError:
        # Fail-open: allow the call rather than wedge on lock contention.
        _warn("circuit_breaker: lock timeout claiming probe (allowing call)")
        return True


def _decide(path: Path, cfg: CircuitConfig) -> bool:
    """Lock-free decision: may this call proceed? Acquires a lock ONLY at the
    rare OPEN→HALF_OPEN transition moment (via ``_try_claim_probe``)."""
    state = _load_state(path)
    s = state["state"]
    if s == CLOSED:
        return True
    if s == HALF_OPEN:
        return False  # another caller owns the probe this cycle
    # s == OPEN
    if not _cooldown_elapsed(state, cfg):
        return False
    return _try_claim_probe(path, cfg)


# ---- the decorator --------------------------------------------------------


def guard(
    *,
    open_exc: type[BaseException],
    count: type[BaseException],
    ignore: tuple[type[BaseException], ...] = (),
    config_loader: Callable[[], CircuitConfig] | None = None,
    state_path: Path | None = None,
) -> Callable[[_F], _F]:
    """Wrap a network method with circuit-breaker behavior.

    On call: if bypassing → call straight through. If config ``enabled`` is
    false → call straight through (escape hatch). Else evaluate state — OPEN
    (within cooldown) or HALF_OPEN-claimed-elsewhere → raise ``open_exc``
    without calling. Otherwise call through; a ``count`` (but not ``ignore``,
    not ``open_exc``) exception records a failure; a clean return records a
    success.
    """

    def decorator(fn: _F) -> _F:
        @wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            if _bypass_depth > 0:
                return fn(*args, **kwargs)
            cfg = _resolve_config(config_loader)
            if not cfg.enabled:
                return fn(*args, **kwargs)
            # Resolve the state path at CALL time so tests / conftest can
            # monkeypatch ``STATE_FILE`` away from the real ~/its/state file.
            path = STATE_FILE if state_path is None else state_path
            # Was this call the HALF_OPEN probe? (determines failure handling)
            pre = _load_state(path)
            if not _decide(path, cfg):
                raise open_exc(
                    "Smartsheet circuit breaker OPEN — short-circuiting "
                    f"{fn.__name__} (sustained backend failure; see "
                    "docs/runbooks/circuit_breaker.md)"
                )
            is_probe = pre["state"] == OPEN and _cooldown_elapsed(pre, cfg)
            try:
                result = fn(*args, **kwargs)
            except open_exc:
                # Nested short-circuit from an inner guarded call — propagate
                # WITHOUT counting it as a failure of this method.
                raise
            except ignore:
                raise
            except count:
                if is_probe:
                    _record_probe_failure(path, cfg)
                else:
                    _record_failure(path, cfg)
                raise
            else:
                _record_success(path)
                return result

        return wrapper  # type: ignore[return-value]

    return decorator


# ---- internal logging (lazy cycle-break) ----------------------------------


def _warn(message: str) -> None:
    """Best-effort WARN to the local log via a lazy ``error_log`` import (which
    imports ``smartsheet_client`` at top level — hence lazy). Never raises."""
    try:
        from . import error_log

        error_log._local_log(error_log.Severity.WARN, "shared.circuit_breaker", message)
    except Exception:  # noqa: BLE001 — logging must never break the breaker
        pass
