"""REAL-child-process tests for po_materials/estimate_sandbox.py — the killable
rlimited isolation boundary for hostile-document parsing (ADR-0004 red-team #5).

NO MOCKS, deliberately: every test spawns the actual
`python -m po_materials.estimate_sandbox` child through `run_sandboxed` and proves
the reaping contract against a LIVE process — a mocked subprocess would prove
nothing about the boundary that keeps the daemon alive (prove-the-control-bites).
The hostile behaviors come from the documented test-support child fns
(`_test_spin` / `_test_alloc` / `_test_crash` / `_test_echo` in the module's
`__main__` dispatch); timeouts stay at 2–3s so the suite stays fast.

Contract pinned (delete the sandbox's kill/timeout/exit handling and these fail):
  * CPU-spinning child → reaped within timeout_s (+ slack) → None; parent alive.
  * allocation-bomb child → dies to RLIMIT_AS where the platform enforces it
    (Linux) OR is reaped by the CPU/wall-clock bound (Darwin rejects lowering
    RLIMIT_AS — the module docstring's honesty note); either way → None, parent
    alive, and the child's own _TEST_ALLOC_CAP_BYTES bound keeps host memory safe.
  * crashing child (nonzero exit) → None.
  * unknown fn name → None (refused parent-side, no child ever spawned).
  * happy path: stdin bytes → child → JSON on stdout, round-tripped intact.

Run with: pytest -q tests/test_estimate_sandbox.py
"""
from __future__ import annotations

import hashlib
import json
import time

from po_materials import estimate_sandbox

# Wall-clock slack on top of timeout_s before a reap counts as "too slow":
# generous for a loaded CI runner spawning a fresh interpreter, but decisively
# below "wedged forever" — an unreaped child would blow well past this.
REAP_SLACK_S = 10.0


def test_cpu_spinning_child_reaped_within_timeout_parent_survives():
    """(a) A CPU-spinning parse child is killed at the budget (RLIMIT_CPU or the
    parent subprocess timeout, whichever lands first) and run_sandboxed returns
    None — the parent (this test process) simply continues."""
    start = time.monotonic()
    out = estimate_sandbox.run_sandboxed("_test_spin", b"", timeout_s=2)
    elapsed = time.monotonic() - start
    assert out is None
    assert elapsed >= 0  # parent alive and measuring — the reap did not hang us
    assert elapsed < 2 + REAP_SLACK_S


def test_allocation_bomb_child_dies_to_rlimit_or_reap_parent_survives():
    """(b) A large-allocation child either dies to the lowered RLIMIT_AS (where
    the kernel enforces it) or allocates only its bounded cap and is reaped at
    the wall-clock/CPU budget — None either way, parent alive."""
    start = time.monotonic()
    out = estimate_sandbox.run_sandboxed(
        "_test_alloc", b"", timeout_s=3, rlimit_bytes=256 * 1024 * 1024
    )
    elapsed = time.monotonic() - start
    assert out is None
    assert elapsed < 3 + REAP_SLACK_S


def test_crashing_child_nonzero_exit_returns_none():
    """(c) A child that dies mid-parse (uncaught exception → nonzero exit) maps
    to None — the caller's degrade signal, never an exception in the parent."""
    assert estimate_sandbox.run_sandboxed("_test_crash", b"", timeout_s=5) is None


def test_unknown_fn_name_refused_returns_none():
    """(d) An fn name outside _ALLOWED_FNS is refused parent-side (no spawn)."""
    assert estimate_sandbox.run_sandboxed("no_such_fn", b"", timeout_s=5) is None


def test_happy_path_round_trip_through_real_child():
    """(e) The full transport works end-to-end: stdin bytes reach the child
    intact (sha256-proven) and the JSON-on-stdout contract returns cleanly."""
    payload = b"estimate-sandbox round trip \x00\xff\x01 bytes"
    out = estimate_sandbox.run_sandboxed("_test_echo", payload, timeout_s=15)
    assert out is not None
    doc = json.loads(out)
    assert doc == {
        "echo_len": len(payload),
        "echo_sha256": hashlib.sha256(payload).hexdigest(),
    }
