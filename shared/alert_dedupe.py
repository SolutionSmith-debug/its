"""Alert-routing dedupe — push-leg suppression for flapping CRITICALs.

Purpose
-------
Gates BOTH push legs of the Op Stds §3 triple-fire CRITICAL alert path
behind a windowed dedupe: the Resend operator email (key
`f"{script}::{error_code}"`) and the Sentry structured event (namespaced
key `f"sentry::{script}::{error_code}"`). Sentry reclassified
record→deduped-push, operator-ratified 2026-07-03 (option 1); §3.1
rider: blueprint PR its-blueprint#55; ITS_Errors remains the sole
per-occurrence record. Without suppression a flapping CRITICAL produces
N emails into the inbox and burns the Sentry free-tier event quota.
Within `window_minutes` of the first fire of a given key, subsequent
fires are suppressed at that push leg only. The two legs deliberately do
NOT share a state entry — each opens its window only on its own success
(see `error_log._fire_sentry_leg` for the coupling rationale).

Two gates live here. The per-key dedupe (`should_fire`/`record_fire`)
suppresses ONE flapping `(script, error_code)`. F09 adds a SECOND, global
gate — an alerts-per-hour cap across ALL keys (`check_hourly_cap` /
`record_hourly_send` / `pop_due_window_summary`) — so a storm of DISTINCT
keys, which per-key dedupe cannot catch, still cannot fan out unbounded
operator email.

Invariants
----------
- **Push-vs-record separation (Op Stds §3.1, as amended 2026-07-03).**
  Dedupe AND the F09 cap apply ONLY to push, never to records. The
  Smartsheet `ITS_Errors` row — the SOLE per-occurrence record — fires
  every time (upstream of this module, in `error_log._smartsheet_log`);
  the push legs (Resend inbox + Sentry event stream) are dedupe-subject.
  This module must never gate a record-write.
- **F09 cap is the ONE deliberate fail-CLOSED point in this module.** At the
  clean `count >= max_alerts_per_hour` ceiling, `check_hourly_cap` suppresses a
  real email — *policy*-suppression (bounded + observable: §3.1 records still
  fire, plus a one-shot cap-reached alert and an end-of-window summary), NOT
  bug-suppression. Every ERROR path inside the cap still fails OPEN (sends),
  exactly like the per-key dedupe. The "false positives (extra emails) OK,
  false negatives (missed wake-ups) not" contract is preserved everywhere
  except this single, deliberate, capped ceiling.
- **State-file integrity is non-load-bearing for correctness.** Loss,
  truncation, or corruption of `~/its/state/alert_dedupe.json` degrades
  only to EXTRA pushes (emails / Sentry events), never to a missed
  CRITICAL. The file is an optimisation (suppress duplicates), not a
  source of truth.
- **All writes route through `shared/state_io.py`.** Writers use
  `state_io.atomic_write_json` (temp-file + `os.replace`) inside
  `state_io.with_path_lock` (sidecar `.lock`). Direct `Path.write_text`
  on any file under `~/its/state/` is forbidden (CLAUDE.md "What NOT to
  do"; Op Stds §42). The read-only `list_expired_summaries` is
  intentionally lock-free — see its rationale comment.

Failure modes
-------------
- **All functions fail OPEN.** Any exception is caught and routed to a
  per-function fail-open return: `should_fire` → `True` (fire the push);
  `record_fire` / `mark_summarized` / `delete_entry` → silent no-op;
  `list_expired_summaries` → `[]`. Each writes an
  `[alert-dedupe-state-error]` marker via `error_log._local_log`.
- **`StateLockTimeoutError` is caught, never propagated.** A stuck
  sidecar lock must not silently suppress a CRITICAL Resend wake-up
  (Op Stds §3.1). Each writer's `except state_io.StateLockTimeoutError`
  clause precedes its broad `except Exception` because
  `StateLockTimeoutError` subclasses `Exception` and would otherwise be
  shadowed; both route to the same fail-open value, split only so the
  timeout case can carry the §3.1 rationale.
- The contract: false positives (extra emails) are acceptable; false
  negatives (missed wake-ups) are not. The Resend leg fires regardless
  of state-file health.

Consumers
---------
- `shared/error_log._fire_resend_leg` — calls `should_fire` (the gate)
  and `record_fire` (opens the next window after a successful send).
- `shared/error_log._fire_sentry_leg` — same pair on the namespaced
  `sentry::`-prefixed key; `record_fire` only after a successful capture.
- `scripts/watchdog.py` Check G (`_check_alert_dedupe_summaries`) —
  calls `list_expired_summaries`, `mark_summarized`, `delete_entry` for
  the two-phase summary sweep (a `sentry::`-prefixed key gets its
  summary subject tagged `[sentry-leg]`).

State
-----
`~/its/state/alert_dedupe.json` — single writer per host, serialized by
the `state_io` sidecar lock (`with_path_lock` on
`~/its/state/alert_dedupe.json.lock`) and written via
`state_io.atomic_write_json` (temp-file + `os.replace`). JSON record
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

Public API (PR α — push-suppression):
    should_fire(key: str) -> bool
        True if the calling push leg (Resend or Sentry) should fire;
        False if suppressed.
        Increments `suppressed_count` on the suppressed path.
    record_fire(key: str) -> None
        Called by a push leg (`_fire_resend_leg` / `_fire_sentry_leg`)
        after ITS OWN successful send/capture to open (or no-op on
        already-open) the dedupe window for its key.

Public API (PR β — summary-sweep lifecycle):
    list_expired_summaries() -> list[ExpiredEntry]
        All entries whose `window_ends_at < now`. Watchdog's summary
        sweep consumes this to decide which entries need a summary email
        and which can be deleted.
    mark_summarized(key: str) -> None
        Atomically set `summarized=true` after a summary email has been
        sent. Two-phase deletion: marked entries get deleted on the
        next sweep (crash safety — a crash between Resend send and mark
        causes a duplicate email rather than silent loss).
    delete_entry(key: str) -> None
        Atomically remove one entry from the state file.

Out of scope:
    - Multi-machine state sync — file is per-host.
    - Smartsheet (ITS_Errors) leg dedupe — that is the sole always-write
      record surface and must NEVER be gated here (§3.1).

Cross-references:
    - `shared/error_log._fire_resend_leg` — the call site.
    - `shared/state_io.py` — atomic-write + sidecar-lock helpers (PR #88,
      merge `36932bd`); this module migrated onto them as PR 2 of the
      Phase 1.4 hardening cluster.
    - `docs/tech_debt.md` — dedupe-key granularity + multi-machine sync
      tracked as forward-looking debt.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any

from . import defaults, state_io


@dataclass(frozen=True)
class ExpiredEntry:
    """One state-file entry whose `window_ends_at` has passed.

    Returned by `list_expired_summaries()` for watchdog's summary sweep
    (PR β). Frozen so callers cannot accidentally mutate the snapshot
    between read and write — mutations always go through
    `mark_summarized()` / `delete_entry()` against the state file with
    the flock contract.
    """
    key: str
    first_fired_at: str
    last_fired_at: str
    suppressed_count: int
    window_ends_at: str
    summarized: bool

STATE_DIR = Path.home() / "its" / "state"
STATE_FILE = STATE_DIR / "alert_dedupe.json"


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


def _load_state_from_path() -> dict[str, dict[str, Any]]:
    """Read and parse the JSON state file. Fail-open to `{}`.

    Safe to call locked (writers, inside `state_io.with_path_lock`) OR
    unlocked (the read-only `list_expired_summaries` reader). A single
    `STATE_FILE.read_text()` reads exactly one inode; writers swap the
    inode via `state_io.atomic_write_json`'s `os.replace` rather than
    mutating in place, so a concurrent write can never tear this read —
    the reader sees the complete old file or the complete new file.

    Returns `{}` on missing file, empty file, malformed JSON (writes a
    marker), or a non-object JSON root (writes a marker). Same fail-open
    behavior and same marker text as the retired `_load_state(fh)`.
    """
    if not STATE_FILE.exists():
        return {}
    content = STATE_FILE.read_text()
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


def should_fire(key: str) -> bool:
    """Return True if the calling push leg should fire for this dedupe key.

    Keys are per-leg: the Resend leg passes `f"{script}::{error_code}"`,
    the Sentry leg the namespaced `f"sentry::{script}::{error_code}"`.
    Increments `suppressed_count` and refreshes `last_fired_at` on the
    suppressed path. Fail-open on any exception → returns True and
    writes a marker line so the operator sees the dedupe path degraded
    (and the push still fires).

    A True return DOES NOT open the dedupe window — that's `record_fire`'s
    job, called after the leg's own successful send/capture.
    """
    try:
        with state_io.with_path_lock(STATE_FILE):
            state = _load_state_from_path()
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
            state_io.atomic_write_json(STATE_FILE, state)
            return False
    except state_io.StateLockTimeoutError:
        # Rationale: StateLockTimeoutError must NOT propagate. Catching it and
        # returning True (fire the email) is load-bearing per Op Stds §3.1
        # (Push-vs-Record Separation): a stuck sidecar lock cannot be allowed
        # to silently suppress a CRITICAL Resend wake-up. "False positives
        # (extra emails) acceptable; false negatives (missed wake-ups) not."
        # Reference: Op Stds §3.1; this PR; predecessor PR #88.
        _write_marker(f"could not acquire flock on {STATE_FILE} after retries")
        return True
    except Exception as e:
        _write_marker(f"should_fire({key!r}) raised: {e!r}")
        return True


def record_fire(key: str) -> None:
    """Open a fresh dedupe window for this key (or no-op if already open).

    Called by `_fire_resend_leg` AFTER a successful Resend send, and by
    `_fire_sentry_leg` AFTER a successful Sentry capture (each on its own
    key), so the state file never claims a window for a push that didn't
    actually happen. No-op on any exception.
    """
    try:
        window_minutes = _resolve_window_minutes()
        with state_io.with_path_lock(STATE_FILE):
            state = _load_state_from_path()
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
            state_io.atomic_write_json(STATE_FILE, state)
    except state_io.StateLockTimeoutError:
        # Fail-open on lock timeout — see should_fire for the §3.1 rationale.
        # record_fire failing only risks an extra email next window, never a
        # missed CRITICAL.
        _write_marker(
            f"could not acquire flock on {STATE_FILE} after retries (record_fire)"
        )
    except Exception as e:
        _write_marker(f"record_fire({key!r}) raised: {e!r}")


# ---- PR β: summary-sweep lifecycle --------------------------------------


def list_expired_summaries() -> list[ExpiredEntry]:
    """Return all state entries where `window_ends_at < now`.

    Caller (watchdog summary sweep) filters by `summarized` and
    `suppressed_count` to decide whether to fire a summary, mark, or
    delete. Open windows are excluded.

    Fail-open: any state-read failure returns an empty list and writes
    a `[alert-dedupe-state-error]` marker. The sweep sees no work; the
    next sweep retries.
    """
    try:
        if not STATE_FILE.exists():
            return []
        # Rationale: read-only path, intentionally lock-free. atomic_write_json
        # swaps the file inode via os.replace, so a single open().read() here
        # always sees a complete file (old or new, never torn). A lock would
        # add cost without adding safety. Writers still lock (read-modify-write).
        # Reference: Op Stds §42; shared/state_io.py atomic-write semantics.
        state = _load_state_from_path()
        now = _now()
        expired: list[ExpiredEntry] = []
        for key, entry in state.items():
            if key.startswith("_"):
                # Reserved keys (F09's `_alerts_per_hour_window`) are not
                # dedupe entries and carry a different shape (no
                # `window_ends_at`). Skip them — without this they'd trip the
                # malformed-entry marker on every sweep.
                continue
            try:
                window_ends_at = datetime.fromisoformat(entry["window_ends_at"])
            except (KeyError, ValueError, TypeError):
                # Skip malformed entries rather than fail the whole sweep.
                _write_marker(
                    f"skipping malformed window_ends_at on key {key!r}"
                )
                continue
            if now < window_ends_at:
                continue
            expired.append(
                ExpiredEntry(
                    key=key,
                    first_fired_at=str(entry.get("first_fired_at", "")),
                    last_fired_at=str(entry.get("last_fired_at", "")),
                    suppressed_count=int(entry.get("suppressed_count", 0)),
                    window_ends_at=str(entry.get("window_ends_at", "")),
                    summarized=bool(entry.get("summarized", False)),
                )
            )
        return expired
    except Exception as e:
        _write_marker(f"list_expired_summaries raised: {e!r}")
        return []


def mark_summarized(key: str) -> None:
    """Atomically set `summarized=true` for one entry. No-op on missing key.

    Crash-safety property: if the watchdog crashes between the summary
    Resend send and this call, the next sweep re-fires the summary
    (duplicate email is acceptable; silent loss is not).

    Fail-open: any write failure writes a marker and returns; the entry
    stays unmarked so the next sweep retries.
    """
    try:
        if not STATE_FILE.exists():
            return
        with state_io.with_path_lock(STATE_FILE):
            state = _load_state_from_path()
            entry = state.get(key)
            if entry is None:
                return
            entry["summarized"] = True
            state[key] = entry
            state_io.atomic_write_json(STATE_FILE, state)
    except state_io.StateLockTimeoutError:
        # Fail-open on lock timeout — see should_fire for the §3.1 rationale.
        _write_marker(
            f"could not acquire flock on {STATE_FILE} after retries (mark_summarized)"
        )
    except Exception as e:
        _write_marker(f"mark_summarized({key!r}) raised: {e!r}")


def delete_entry(key: str) -> None:
    """Atomically remove one entry from state. No-op on missing key.

    Called by the watchdog summary sweep in phase 2 (the sweep after the
    summary fired and was marked, OR for clean-expired entries where
    `suppressed_count == 0` and no summary was needed).

    Fail-open: any write failure writes a marker and returns; the entry
    stays so the next sweep retries deletion.
    """
    try:
        if not STATE_FILE.exists():
            return
        with state_io.with_path_lock(STATE_FILE):
            state = _load_state_from_path()
            if key not in state:
                return
            del state[key]
            state_io.atomic_write_json(STATE_FILE, state)
    except state_io.StateLockTimeoutError:
        # Fail-open on lock timeout — see should_fire for the §3.1 rationale.
        _write_marker(
            f"could not acquire flock on {STATE_FILE} after retries (delete_entry)"
        )
    except Exception as e:
        _write_marker(f"delete_entry({key!r}) raised: {e!r}")


# ---- F09: global alerts-per-hour cap ------------------------------------
#
# A SECOND gate (after per-key `should_fire`) on the Resend leg: a global
# ceiling on operator emails across ALL dedupe keys, so a storm of DISTINCT
# keys cannot fan out unbounded email. State lives under the reserved key
# `_alerts_per_hour_window` in the SAME state file (a dict, not a dedupe
# entry — every iterator over the dedupe entries skips `_`-prefixed keys).

_HOURLY_WINDOW_KEY = "_alerts_per_hour_window"
_HOURLY_WINDOW_MINUTES = 60  # the cap is per-HOUR; the sliding window is fixed at 60 min


class CapDecision(Enum):
    """Outcome of the alerts-per-hour gate (`check_hourly_cap`)."""

    ALLOW = "ALLOW"                    # under cap — caller sends the alert
    SUPPRESS_FIRST = "SUPPRESS_FIRST"  # over cap, first of the episode — caller emits the exempt cap-reached alert
    SUPPRESS_QUIET = "SUPPRESS_QUIET"  # over cap, subsequent — caller logs local-INFO only


def _resolve_max_per_hour() -> int:
    """Read `alerting.max_alerts_per_hour` from ITS_Config; fall back to
    `defaults.ALERTING_MAX_ALERTS_PER_HOUR`. Fail-open on any read failure —
    a Smartsheet outage must not tighten (or loosen-to-unbounded) the cap."""
    try:
        from . import smartsheet_client

        raw = smartsheet_client.get_setting(
            "alerting.max_alerts_per_hour", workstream="global"
        )
        if raw is None:
            return defaults.ALERTING_MAX_ALERTS_PER_HOUR
        return int(raw)
    except Exception:
        return defaults.ALERTING_MAX_ALERTS_PER_HOUR


def _blank_window() -> dict[str, Any]:
    return {
        "sends": [],
        "suppressed_count": 0,
        "cap_alert_fired": False,
        "episode_ends_at": None,
        "summarized": False,
    }


def _prune_sends(sends: list[str], now: datetime) -> list[str]:
    """Drop send timestamps older than the 60-min sliding window (and any
    unparseable entries)."""
    cutoff = now - timedelta(minutes=_HOURLY_WINDOW_MINUTES)
    kept: list[str] = []
    for ts in sends:
        try:
            if datetime.fromisoformat(ts) >= cutoff:
                kept.append(ts)
        except (ValueError, TypeError):
            continue
    return kept


def check_hourly_cap() -> CapDecision:
    """Second-gate decision for the Resend leg (F09).

    ALLOW when the global sliding-window send count is under the cap. Otherwise
    SUPPRESS_FIRST on the first suppression of an episode (caller emits the one
    exempt cap-reached alert) or SUPPRESS_QUIET thereafter (caller logs
    local-INFO only). Mutates suppression bookkeeping under lock; does NOT
    append a send (`record_hourly_send` does that, after a confirmed send).

    FAILS OPEN (ALLOW) on every error path — only a clean over-cap count
    suppresses. The lock is NOT held across any network send (the caller sends
    after this returns)."""
    try:
        cap = _resolve_max_per_hour()
        with state_io.with_path_lock(STATE_FILE):
            state = _load_state_from_path()
            now = _now()
            window = state.get(_HOURLY_WINDOW_KEY) or _blank_window()
            window["sends"] = _prune_sends(list(window.get("sends", [])), now)

            if len(window["sends"]) < cap:
                # Under cap — persist the prune (cheap) and allow.
                state[_HOURLY_WINDOW_KEY] = window
                state_io.atomic_write_json(STATE_FILE, state)
                return CapDecision.ALLOW

            # Over cap → suppress. `fresh_episode` is the first suppression of a
            # new episode (none in flight, or the prior one already summarized).
            fresh_episode = not window.get("cap_alert_fired") or bool(
                window.get("summarized")
            )
            if fresh_episode:
                window["suppressed_count"] = 1
                window["cap_alert_fired"] = True
                window["episode_ends_at"] = (
                    now + timedelta(minutes=_HOURLY_WINDOW_MINUTES)
                ).isoformat()
                window["summarized"] = False
                decision = CapDecision.SUPPRESS_FIRST
            else:
                window["suppressed_count"] = int(window.get("suppressed_count", 0)) + 1
                decision = CapDecision.SUPPRESS_QUIET

            state[_HOURLY_WINDOW_KEY] = window
            state_io.atomic_write_json(STATE_FILE, state)
            return decision
    except state_io.StateLockTimeoutError:
        _write_marker(
            f"could not acquire flock on {STATE_FILE} (check_hourly_cap); allowing"
        )
        return CapDecision.ALLOW
    except Exception as e:
        _write_marker(f"check_hourly_cap raised: {e!r}; allowing")
        return CapDecision.ALLOW


def record_hourly_send() -> None:
    """Append `now` to the sliding send-window after a CONFIRMED normal-alert
    send (called alongside `record_fire`). Cap-EXEMPT meta-alerts (cap-reached,
    window-summary) deliberately do NOT call this — they must not accelerate the
    cap. No-op on any error (an undercount only risks an extra email)."""
    try:
        with state_io.with_path_lock(STATE_FILE):
            state = _load_state_from_path()
            now = _now()
            window = state.get(_HOURLY_WINDOW_KEY) or _blank_window()
            sends = _prune_sends(list(window.get("sends", [])), now)
            sends.append(now.isoformat())
            window["sends"] = sends
            state[_HOURLY_WINDOW_KEY] = window
            state_io.atomic_write_json(STATE_FILE, state)
    except state_io.StateLockTimeoutError:
        _write_marker(
            f"could not acquire flock on {STATE_FILE} after retries (record_hourly_send)"
        )
    except Exception as e:
        _write_marker(f"record_hourly_send raised: {e!r}")


def pop_due_window_summary() -> int | None:
    """If the current suppression episode has expired with un-summarized
    suppressions, return the suppressed count and mark it summarized (so neither
    the opportunistic path nor PR-2's watchdog sweep double-fires); else None.
    The caller emits the one exempt window-summary Resend. Fail-open → None."""
    try:
        with state_io.with_path_lock(STATE_FILE):
            state = _load_state_from_path()
            window = state.get(_HOURLY_WINDOW_KEY)
            if not window:
                return None
            ends_at = window.get("episode_ends_at")
            suppressed = int(window.get("suppressed_count", 0))
            if ends_at is None or window.get("summarized") or suppressed <= 0:
                return None
            try:
                if _now() < datetime.fromisoformat(ends_at):
                    return None
            except (ValueError, TypeError):
                return None
            window["summarized"] = True
            state[_HOURLY_WINDOW_KEY] = window
            state_io.atomic_write_json(STATE_FILE, state)
            return suppressed
    except state_io.StateLockTimeoutError:
        _write_marker(
            f"could not acquire flock on {STATE_FILE} after retries (pop_due_window_summary)"
        )
        return None
    except Exception as e:
        _write_marker(f"pop_due_window_summary raised: {e!r}")
        return None
