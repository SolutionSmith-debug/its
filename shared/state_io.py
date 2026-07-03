"""Canonical entry point for daemon-managed state-file writes.

Three public helpers + one typed exception:

    atomic_write_json(path, data)   — serialize JSON, write via temp + os.replace.
    atomic_write_text(path, text)   — write raw text via temp + os.replace.
    with_path_lock(path)            — sidecar-flock context manager.

Atomic-write semantics:
    Serialize / write content to a sibling temp file in the same directory
    (same filesystem → POSIX rename is atomic), then ``os.replace(tmp, path)``.
    Concurrent readers always see a complete file — either the old contents
    or the new, never a torn write. On exception before the replace, the
    temp file is removed so we never leak ``.tmp.<pid>.<rand>`` artifacts.

Sidecar-lock semantics:
    ``with_path_lock(path)`` acquires an exclusive non-blocking ``fcntl.flock``
    on a sidecar file at ``{path}.lock``. The lock lives on a separate file
    because ``os.replace`` swaps the inode of ``path``; a flock held on the
    data file itself would be invalidated by every atomic write. The sidecar
    is created on first use and is NOT removed on exit (its existence is
    not a failure signal).

    Bounded retry: 5 attempts × 50ms backoff (~250ms ceiling). On exhaustion,
    raises ``StateLockTimeoutError``. Callers fail-open: log a WARN with
    ``error_code='daemon_health_write_failed'`` and skip this cycle's write
    rather than block the daemon's primary work
    (CLAUDE.md: "Heartbeat write must NEVER block daemon primary work").

Single-host single-writer assumption:
    Real contention is rare. The bounded retry exists for the narrow case
    where two daemons happen to overlap on the shared heartbeat-row state
    file (``~/its/state/heartbeat_row_ids.json`` is shared across every
    ``shared/heartbeat.py`` HeartbeatReporter consumer, keyed by daemon name).

Consumers:
    - ``shared/heartbeat.py`` — the shared HeartbeatReporter row-state writes
      (all polling daemons; the original per-daemon copies are consolidated).
    - ``safety_reports/weekly_send_poll.py`` — local heartbeat + row-state writes.
    - ``shared/alert_dedupe.py`` — dedupe-state read-modify-write under
      ``with_path_lock`` + ``atomic_write_json`` (migrated PR #104); its
      read-only ``list_expired_summaries`` reads lock-free, relying on the
      atomic-write inode-swap guarantee documented above.
"""
from __future__ import annotations

import contextlib
import fcntl
import json
import os
import secrets
import time
from collections.abc import Iterator
from pathlib import Path

# Bounded retry budget; mirrors ``shared/alert_dedupe.py`` so the two
# state-file callers share one timing knob.
_LOCK_RETRY_ATTEMPTS = 5
_LOCK_RETRY_DELAY_SECONDS = 0.05


class StateLockTimeoutError(Exception):
    """Raised by ``with_path_lock`` when bounded retry exhausts without acquire."""


def _make_temp_path(path: Path) -> Path:
    """Generate a per-process per-call sibling temp-file path.

    Same-directory placement keeps the subsequent ``os.replace`` on the
    same filesystem (atomic on POSIX). PID + random suffix avoids
    collisions between concurrent processes writing to the same target.
    """
    suffix = secrets.token_hex(4)
    return path.with_name(f"{path.name}.tmp.{os.getpid()}.{suffix}")


def atomic_write_json(path: Path, data: object) -> None:
    """Atomically write ``data`` as JSON to ``path``.

    Serializes first (``sort_keys=True, indent=2``) so serialization
    failures raise before any filesystem touch. Then writes to a sibling
    temp file and ``os.replace`` it onto the target — atomic on POSIX,
    so concurrent readers always see a complete file.

    Raises ``TypeError`` / ``ValueError`` from the serializer and
    ``OSError`` from the filesystem natively; callers decide.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = _make_temp_path(path)
    try:
        payload = json.dumps(data, sort_keys=True, indent=2)
        tmp.write_text(payload)
        os.replace(tmp, path)
    finally:
        # On success ``os.replace`` already removed the temp; ``missing_ok``
        # turns the cleanup into a no-op. On failure the temp may exist;
        # remove it so we don't leak ``.tmp.<pid>.<rand>`` files.
        with contextlib.suppress(OSError):
            tmp.unlink(missing_ok=True)


def atomic_write_text(path: Path, text: str) -> None:
    """Atomically write ``text`` to ``path`` (no JSON serialization).

    Same temp-file + ``os.replace`` semantics as ``atomic_write_json``;
    used for non-JSON content like the daemon-local heartbeat ISO
    timestamps. Raises ``OSError`` from the filesystem natively.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = _make_temp_path(path)
    try:
        tmp.write_text(text)
        os.replace(tmp, path)
    finally:
        with contextlib.suppress(OSError):
            tmp.unlink(missing_ok=True)


@contextlib.contextmanager
def with_path_lock(path: Path) -> Iterator[None]:
    """Hold an exclusive sidecar flock around a read-modify-write block.

    Sidecar pattern: the lock lives at ``{path}.lock`` rather than on
    ``path`` itself. ``atomic_write_json`` / ``atomic_write_text`` swap
    the inode of ``path`` via ``os.replace``, which would invalidate a
    flock held on the data file. A separate sidecar file is never
    replaced, so the lock survives every atomic write.

    Acquires ``fcntl.LOCK_EX | LOCK_NB`` with 5×50ms bounded retry.
    Raises ``StateLockTimeoutError`` on exhaustion — callers fail-open:
    log a WARN and skip this cycle's write rather than block the daemon's
    primary work.

    The sidecar file is created on first use and is NOT deleted on exit.
    """
    lock_path = path.with_name(path.name + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = lock_path.open("a+")
    acquired = False
    try:
        for _ in range(_LOCK_RETRY_ATTEMPTS):
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
                break
            except BlockingIOError:
                time.sleep(_LOCK_RETRY_DELAY_SECONDS)
        if not acquired:
            raise StateLockTimeoutError(
                f"could not acquire flock on {lock_path} after "
                f"{_LOCK_RETRY_ATTEMPTS} retries"
            )
        yield
    finally:
        if acquired:
            with contextlib.suppress(OSError):
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()
