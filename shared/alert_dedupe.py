"""Alert-routing dedupe — Resend-leg suppression for flapping CRITICALs.

Purpose:
    Third leg of the Op Stds v9 §3 triple-fire CRITICAL alert path
    (Smartsheet `ITS_Errors` + Resend operator email + Sentry) is the
    only one that wakes the operator. Without suppression, a flapping
    CRITICAL produces N emails into the inbox. This module gates the
    Resend leg behind a windowed dedupe: within `window_minutes` of the
    first fire of a given `(script, error_code)` key, subsequent fires
    are suppressed at the Resend leg.

    Per Op Stds v9 §27: **dedupe applies only to push, never to records.**
    `_smartsheet_log` and `_fire_sentry_leg` write every time. The
    persistent record stays complete; only the operator's inbox is
    suppressed.

State:
    `~/its/state/alert_dedupe.json` — single writer guarded by
    `fcntl.flock(LOCK_EX | LOCK_NB)` with bounded retry. JSON record
    per dedupe key:
        {
            "first_fired_at":  "<UTC isoformat>",
            "last_fired_at":   "<UTC isoformat>",
            "suppressed_count": <int>,
            "window_ends_at":  "<UTC isoformat>",
            "summarized":      false
        }
    `summarized` is reserved for PR β (watchdog summary sweep) — read by
    the future summarizer to skip entries it has already emailed. PR α
    never sets it to true; the field is initialized to false and lives
    there for forward-compat.

Public API:
    should_fire(key: str) -> bool
        True if the Resend leg should send; False if suppressed.
        Increments `suppressed_count` on the suppressed path.
    record_fire(key: str) -> None
        Called by `_fire_resend_leg` after a SUCCESSFUL send to open
        (or no-op on already-open) the dedupe window.

Failure isolation:
    Both functions fail OPEN. Any exception inside `should_fire` →
    returns `True` and writes a `[alert-dedupe-state-error]` marker to
    the local log; any exception inside `record_fire` → silent no-op
    with the same marker. The contract is: a bug here can never silently
    drop a CRITICAL Resend send. False positives (extra emails) are
    acceptable; false negatives (missed wake-ups) are not.

Out of scope (PR α):
    - Summary delivery / state-entry deletion-after-summary (PR β).
    - Multi-machine state sync — file is per-host.
    - Sentry / Smartsheet leg dedupe — single-leg suppression only.

Cross-references:
    - `shared/error_log._fire_resend_leg` — the call site.
    - `docs/tech_debt.md` — dedupe-key granularity + multi-machine sync
      tracked as forward-looking debt.
"""
from __future__ import annotations

import fcntl
import json
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from . import defaults

STATE_DIR = Path.home() / "its" / "state"
STATE_FILE = STATE_DIR / "alert_dedupe.json"

# fcntl LOCK_NB retry budget. Single-host single-writer model means real
# contention is essentially impossible; the retries exist to absorb the
# rare case where one CRITICAL fires while a smoke test is mid-write.
_LOCK_RETRY_ATTEMPTS = 5
_LOCK_RETRY_DELAY_SECONDS = 0.05


def _now() -> datetime:
    """UTC now. Wrapped so tests can monkeypatch the clock."""
    return datetime.now(UTC)


def _write_marker(message: str) -> None:
    """Write a `[alert-dedupe-state-error]` marker via error_log's local logger.

    Lazy import to avoid a circular at module load (`error_log` imports
    `smartsheet_client` which imports `defaults`; `alert_dedupe` also
    imports `defaults`). The lazy form decouples the import chain.
    """
    try:
        from . import error_log
        error_log._local_log(
            error_log.Severity.ERROR,
            "shared.alert_dedupe",
            f"[alert-dedupe-state-error] {message}",
        )
    except Exception:
        # Marker-write itself failing must not raise — fail-open contract.
        pass


def _resolve_window_minutes() -> int:
    """Read the dedupe window from ITS_Config; fall back to defaults.

    Any failure — Smartsheet unreachable, row missing, non-numeric value —
    falls back to `defaults.ALERTING_DEDUPE_WINDOW_MINUTES`. Fail-open
    on the config read means a Smartsheet outage cannot suppress alerts
    beyond the documented default window.
    """
    try:
        from . import smartsheet_client
        raw = smartsheet_client.get_setting(
            "alerting.dedupe_window_minutes", workstream="global"
        )
        if raw is None:
            return defaults.ALERTING_DEDUPE_WINDOW_MINUTES
        return int(raw)
    except Exception:
        return defaults.ALERTING_DEDUPE_WINDOW_MINUTES


def _load_state(fh) -> dict[str, dict[str, Any]]:
    """Read JSON state from an already-locked file handle.

    Returns `{}` on missing file (handled by caller), empty file, or
    malformed JSON. Callers MUST hold the lock before calling this.
    """
    fh.seek(0)
    content = fh.read()
    if not content:
        return {}
    try:
        loaded = json.loads(content)
    except json.JSONDecodeError:
        _write_marker(f"corrupt state file at {STATE_FILE}; resetting")
        return {}
    if not isinstance(loaded, dict):
        _write_marker(f"state file root is not an object at {STATE_FILE}; resetting")
        return {}
    return loaded


def _dump_state(fh, state: dict[str, dict[str, Any]]) -> None:
    """Write JSON state to an already-locked file handle. Truncate first."""
    fh.seek(0)
    fh.truncate()
    fh.write(json.dumps(state, indent=2, sort_keys=True))
    fh.flush()


def _acquire_lock(fh) -> bool:
    """Non-blocking flock with bounded retry. Returns True on acquire.

    Single-host single-writer means real contention is rare; the bounded
    retry is a defensive courtesy for the case where one CRITICAL fires
    while a smoke test is mid-write.
    """
    for _ in range(_LOCK_RETRY_ATTEMPTS):
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except BlockingIOError:
            time.sleep(_LOCK_RETRY_DELAY_SECONDS)
    return False


def should_fire(key: str) -> bool:
    """Return True if the Resend leg should send for this dedupe key.

    Increments `suppressed_count` and refreshes `last_fired_at` on the
    suppressed path. Fail-open on any exception → returns True and
    writes a marker line so the operator sees the dedupe path degraded
    (and still gets the email).

    A True return DOES NOT open the dedupe window — that's `record_fire`'s
    job, called after a successful Resend send.
    """
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        with STATE_FILE.open("a+") as fh:
            if not _acquire_lock(fh):
                _write_marker(f"could not acquire flock on {STATE_FILE} after retries")
                return True
            try:
                state = _load_state(fh)
                entry = state.get(key)
                now = _now()

                if entry is None:
                    return True

                window_ends_at = datetime.fromisoformat(entry["window_ends_at"])
                if now >= window_ends_at:
                    return True

                entry["last_fired_at"] = now.isoformat()
                entry["suppressed_count"] = int(entry.get("suppressed_count", 0)) + 1
                state[key] = entry
                _dump_state(fh, state)
                return False
            finally:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
    except Exception as e:
        _write_marker(f"should_fire({key!r}) raised: {e!r}")
        return True


def record_fire(key: str) -> None:
    """Open a fresh dedupe window for this key (or no-op if already open).

    Called by `_fire_resend_leg` AFTER a successful Resend send so the
    state file never claims a window for a send that didn't actually
    happen. No-op on any exception.
    """
    try:
        window_minutes = _resolve_window_minutes()
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        with STATE_FILE.open("a+") as fh:
            if not _acquire_lock(fh):
                _write_marker(f"could not acquire flock on {STATE_FILE} after retries (record_fire)")
                return
            try:
                state = _load_state(fh)
                now = _now()
                entry = state.get(key)

                if entry is not None:
                    try:
                        existing_window_end = datetime.fromisoformat(entry["window_ends_at"])
                        if now < existing_window_end:
                            return
                    except (KeyError, ValueError, TypeError):
                        pass

                state[key] = {
                    "first_fired_at": now.isoformat(),
                    "last_fired_at": now.isoformat(),
                    "suppressed_count": 0,
                    "window_ends_at": (now + timedelta(minutes=window_minutes)).isoformat(),
                    "summarized": False,
                }
                _dump_state(fh, state)
            finally:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
    except Exception as e:
        _write_marker(f"record_fire({key!r}) raised: {e!r}")
