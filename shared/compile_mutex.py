"""Host-level compile mutex — serialize the safety + (future) progress weekly compiles.

Both the safety ``weekly_generate`` (Fri 14:00) and the future progress
``progress_weekly_generate`` (staggered, Stage-2 P4) compile per-job packets and
write them to Smartsheet/Box. If they overlap they contend on the Smartsheet API
rate limit (plan risk ``ss-rate-limit-contention``). This module serializes them
on ONE host-level advisory lock so the lower-priority compile defers while the
other runs.

§42 — why a *third* cross-process fence (beyond A6's per-job SIGALRM wall-clock
budget + the pre-merge memory guard, both in ``compile_core``): those bound a
SINGLE compile's resource use; this bounds the INTERACTION of two compiles. It
lives in ``shared/`` rather than ``compile_core`` (whose deliberate stdlib-only /
no-policy / capability-clean invariant must hold) and is built on the proven
``state_io.with_path_lock`` sidecar flock — the same primitive the A3 Box
refresh-lock uses.

FAIL-OPEN, asymmetric by CALLER policy. ``hold()`` is pure mechanism: it yields
``True`` when the lock was acquired and ``False`` when another compile already
holds it (``with_path_lock``'s bounded ~250ms retry backs off rather than blocking
for the other compile's full run). The caller decides what ``False`` means:

* safety   → proceed UNLOCKED (fail-open — blocking the live-critical Friday
  compile is worse than a rare contention window; the A3 rationale).
* progress → skip and rely on its watchdog catch-up (fail-safe — it is the
  lower-priority, deferrable compile).

On contention ``hold()`` logs a single WARN naming ``role`` (mirroring the A3
fail-open precedent in ``shared/box_client.py``, which logs inside the helper).
``flock`` auto-releases on process death, so a crashed holder never strands the
lock.
"""
from __future__ import annotations

import contextlib
from collections.abc import Iterator
from pathlib import Path

from . import state_io
from .error_log import Severity, log

# Host-global anchor: the sidecar lock lands at ``host_compile.lock``. SHARED by
# every compile role so they are mutually exclusive. Mirrors the STATE_DIR
# convention in shared/box_client.py + shared/alert_dedupe.py.
_STATE_DIR = Path.home() / "its" / "state"
_HOST_COMPILE_LOCK_ANCHOR = _STATE_DIR / "host_compile"


@contextlib.contextmanager
def hold(*, role: str) -> Iterator[bool]:
    """Host-level compile mutex for the duration of the ``with`` block.

    Yields ``True`` if the lock was acquired (the block runs with it held) or
    ``False`` if another compile already holds it (the block runs UNLOCKED — the
    caller's policy decides proceed-vs-skip; see module docstring). On contention
    a single WARN naming ``role`` is logged. Never raises on contention
    (fail-open).
    """
    lock_cm = state_io.with_path_lock(_HOST_COMPILE_LOCK_ANCHOR)
    try:
        lock_cm.__enter__()
    except state_io.StateLockTimeoutError:
        log(
            Severity.WARN,
            "shared.compile_mutex",
            f"host compile mutex contended (role={role!r}); another compile holds "
            "it — caller proceeds per its fail-open/skip policy.",
            error_code="compile_mutex.contended",
        )
        yield False
        return
    try:
        yield True
    finally:
        lock_cm.__exit__(None, None, None)
