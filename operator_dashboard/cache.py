"""A tiny thread-safe TTL cache for the (costly) Smartsheet read panels.

Only the two Smartsheet panels use this — local-file panels read fresh each
request. The producer runs OUTSIDE the lock (a Smartsheet fetch can take
seconds) so one slow read never blocks other panels, and failures are NOT
cached: the caller's fail-soft wrapper turns an error into an 'unavailable'
panel and the next request retries.
"""
from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import cast

_lock = threading.Lock()
_store: dict[str, tuple[float, object]] = {}


def cached[T](key: str, ttl_seconds: float, producer: Callable[[], T]) -> T:
    """Return the cached value for `key`, or produce + store it."""
    now = time.monotonic()
    with _lock:
        hit = _store.get(key)
        if hit is not None and hit[0] > now:
            return cast(T, hit[1])
    value = producer()
    with _lock:
        _store[key] = (now + ttl_seconds, value)
    return value
